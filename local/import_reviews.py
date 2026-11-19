"""Importer (c): review findings from logs/rv<N>-*.log (agent review rounds) and logs/merge-review-*.md.

Log names: rv<round>-[alt-]<org>_<repo>__<pr>.log (alt- = a second reviewer's round), merge-review-<repo>-<pr>.md.

finding -[about]-> PR      structural (rule: the log's file name names the PR; the finding is in that review)
review  -[reviews]-> PR    structural (same rule)
review  -[found]-> finding exact (the finding is written in that review)
PR      -[delivers]-> story exact (the PR's branch, brief name, TASK line or review header names the story ID)
Outcome of an rv finding (meta.outcome): from the next parsed round of the same PR (that reviewer is told to confirm each
earlier finding): repeated at the same file -> still_open; not repeated -> fixed; no later parsed round -> unknown.
Merge-review files: outcome from the status cell of their delta table (Fixed / Still open / Declined), else unknown.
Logs may be untracked, so locations carry the file hash instead of a commit. Private (claude-only) logs are skipped.
"""
from __future__ import annotations

import re
from pathlib import Path

from brainlib import DEFAULT_PROJECT, GITHUB_ORG, SOURCE_STORE, Graph, file_sha, lab_mention, one_line, privacy_of, redact

IMPORTER = "reviews"
RV_NAME = re.compile(r"^rv(?P<round>\d+)-(?P<alt>alt-)?(?P<org>[A-Za-z0-9.-]+)_(?P<repo>[A-Za-z0-9._-]+?)__(?P<pr>\d+)(?P<suffix>[-.][\w.-]+)?\.log$")
FM_NAME = re.compile(r"^merge-review-(?P<repo>.+)-(?P<pr>\d+)\.md$")
FINDING = re.compile(r"^\s*[-*]?\s*\**\[(P[0-3])\]\**\s*(.+)$")
WHERE = re.compile(r"`?([\w./@+-]+):(\d+)")
FM_BOLD = re.compile(r"^\s*[-*]?\s*\*\*\[(P[0-3])\]\s*(.+?)\*\*")
FM_ROW = re.compile(r"^\|\s*\**(P[0-3])\b\**\s*(.+?)\**\s*\|\s*\**([^|]+?)\**\s*\|")
STATUS = [(re.compile(r"(?i)^(fixed|closed|resolved|done)"), "fixed"),
          (re.compile(r"(?i)^(still open|open|not fixed|partly|partial)"), "still_open"),
          (re.compile(r"(?i)^(declined|disputed|won't|wont|not a bug|rejected|false alarm)"), "declined")]
STORY_IN_NAME = [(re.compile(r"(?i)\bstory-(\d{3,})\b"), "STORY-{}"), (re.compile(r"(?i)\bfix-(\d{3,})\b"), "FIX-{}")]
STORY_UPPER = re.compile(r"\b(STORY-\d{3,}|FIX-\d{3,})\b")


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9-]", "", s.lower())


def stories_named(text: str) -> list[str]:
    out: list[str] = []
    for rx, fmt in STORY_IN_NAME:
        for m in rx.finditer(text):
            sid = fmt.format(*[x.upper() for x in m.groups()])
            if sid not in out:
                out.append(sid)
    for m in STORY_UPPER.finditer(text):
        if m.group(1) not in out:
            out.append(m.group(1))
    return out


def parse_findings_block(tail: str) -> tuple[str, list[dict]]:
    """Final output of a review round -> (status, findings). status: ok | unparsed."""
    found, seen = [], set()
    for ln in tail.splitlines():
        m = FINDING.match(ln)
        if not m:
            continue
        key = ln.strip()
        if key in seen:
            continue
        seen.add(key)
        rest = m.group(2).strip()
        w = WHERE.match(rest)
        found.append(dict(severity=m.group(1), text=rest, file=w.group(1) if w else "", line=int(w.group(2)) if w else None))
    if found:
        return "ok", found
    if re.search(r"(?im)^\s*\**no findings\**\.?\s*$", tail):
        return "ok", []
    return "unparsed", []


def final_tail(text: str) -> str | None:
    i = text.rfind("\ntokens used\n")
    if i < 0:
        return None
    return "\n".join(text[i:].split("\n")[3:])


def task_line(text: str) -> str:
    m = re.search(r"(?m)^TASK: (.*)$", text[:60000])
    return m.group(1) if m else ""


def similar(a: dict, b: dict) -> bool:
    if not a["file"] or a["file"] != b["file"]:
        return False
    if a["line"] is not None and b["line"] is not None and abs(a["line"] - b["line"]) <= 30:
        return True
    wa, wb = set(re.findall(r"[a-z]{4,}", a["text"].lower())), set(re.findall(r"[a-z]{4,}", b["text"].lower()))
    return bool(wa and wb) and len(wa & wb) / len(wa | wb) >= 0.3


def _pr_node(L, g: Graph, repo: str, pr: str, evidence_path: str) -> str:
    pid = f"PR-{repo}#{pr}"
    projects = [DEFAULT_PROJECT]
    g.add_node(dict(id=pid, kind="pr", layer="code", project=projects, privacy=privacy_of(L, projects, "code"),
                    title=f"{repo} PR #{pr}", body="", importer=IMPORTER, source_commit=None,
                    meta={"repo": repo}, location=dict(store=f"{GITHUB_ORG}/{repo}", path=f"pull/{pr}", url=f"https://github.com/{GITHUB_ORG}/{repo}/pull/{pr}")),
               keep_existing=True)
    return pid


def _add_finding(L, g, rid, n, f, loc, projects, outcome, basis):
    fid = f"FND-{rid[3:].lower()}-{n}"
    text, nred = redact(f["text"])
    g.count("redactions", nred)
    g.add_node(dict(id=fid, kind="finding", layer="audits", project=projects, privacy=privacy_of(L, projects, "audits"),
                    title=one_line(f"[{f['severity']}] {text}", 140), body=text, importer=IMPORTER, source_commit=None,
                    meta=dict(severity=f["severity"], file=f["file"], line=str(f["line"] or ""), outcome=outcome, outcome_basis=basis),
                    location=loc))
    g.count(f"findings_outcome_{outcome}")
    g.count("findings")
    return fid


def parse_rv_logs(L, g: Graph, logs: list[Path], rel: str = "logs") -> None:
    rounds: dict[tuple, list] = {}
    seen_rids: set[str] = set()
    for p in sorted(logs):
        g.count("rv_logs_seen")
        m = RV_NAME.match(p.name)
        if not m:
            g.count("rv_logs_not_a_pr_round")      # rvfix (fixer runs), rvpost, daemon logs, odd names
            continue
        head_txt = p.read_bytes()[:4000].decode("utf-8", "replace")
        if any(lp in p.name or lp in head_txt for lp in L.lab_paths):
            g.count("rv_logs_lab_skipped")
            continue
        text = p.read_text(errors="replace")
        repo, pr, rnd = m["repo"], m["pr"], int(m["round"])
        reviewer = "alt" if m["alt"] else "primary"
        rid = f"RV-{'alt-' if m['alt'] else ''}{slug(repo)}-{pr}-r{rnd}"
        if rid in seen_rids:      # e.g. rv1-..__12.log and rv1-..__12.r1-2025-01-20.log: first (plain name) wins
            g.count("rv_logs_duplicate_round")
            continue
        seen_rids.add(rid)
        tail = final_tail(text)
        if tail is None:
            status, found = "incomplete", []
        else:
            status, found = parse_findings_block(tail)
        g.count(f"rv_rounds_{status}")
        task = task_line(text)
        rounds.setdefault((repo, pr), []).append(dict(rnd=rnd, rid=rid, status=status, found=found, path=p, task=task,
                                                      reviewer=reviewer, name=p.name))
    for (repo, pr), rs in sorted(rounds.items()):
        rs.sort(key=lambda r: (r["rnd"], r["reviewer"]))
        projects = [DEFAULT_PROJECT]
        pid = _pr_node(L, g, repo, pr, rs[0]["name"])
        named: dict[str, str] = {}
        for r in rs:
            bm = re.search(r"branch ([\w./-]+)", r["task"])
            br = re.search(r"briefs/([\w.-]+\.md)", r["task"])
            for label, txt in (("branch", bm.group(1) if bm else ""), ("brief", br.group(1) if br else "")):
                for sid in stories_named(txt):
                    named.setdefault(sid, f"{rel}/{r['name']} TASK {label} {txt}")
        for sid, ev in named.items():
            g.ref(pid, sid, "delivers", "exact", ev, "review.task_names_story", IMPORTER)
        for i, r in enumerate(rs):
            sha = file_sha(r["path"])
            loc = dict(store=SOURCE_STORE, path=f"{rel}/{r['name']}", commit=None, untracked=True, file_hash=sha)
            g.add_node(dict(id=r["rid"], kind="review", layer="reviews", project=projects, privacy=privacy_of(L, projects, "reviews"),
                            title=f"{r['reviewer']} round {r['rnd']} of {repo}#{pr}: {len(r['found'])} findings ({r['status']})",
                            body=one_line(redact(r["task"])[0], 600), importer=IMPORTER, source_commit=None,
                            meta=dict(round=str(r["rnd"]), reviewer=r["reviewer"], status=r["status"]), location=loc))
            g.ref(r["rid"], pid, "reviews", "structural", f"{rel}/{r['name']}: file name names {repo} PR #{pr}", "review.file_name", IMPORTER, confidence=0.95)
            nxt = next((x for x in rs[i + 1:] if x["rnd"] > r["rnd"] and x["status"] == "ok"), None)
            text_lines = r["path"].read_text(errors="replace").splitlines()
            for n, f in enumerate(r["found"], 1):
                if lab_mention(L, f["text"]):
                    g.count("findings_lab_skipped")
                    continue
                if nxt is None:
                    outcome, basis = "unknown", "no later parsed round of this PR"
                elif any(similar(f, x) for x in nxt["found"]):
                    outcome, basis = "still_open", f"repeated at the same file in {nxt['name']}"
                else:
                    outcome, basis = "fixed", f"not repeated in {nxt['name']} (that round confirms earlier findings)"
                line = max((k for k, ln in enumerate(text_lines, 1) if f["text"][:60] in ln), default=None)
                floc = {**loc, "line": line}
                fid = _add_finding(L, g, r["rid"], n, f, floc, projects, outcome, basis)
                g.ref(r["rid"], fid, "found", "exact", f"{rel}/{r['name']}:{line} finding {n}", "review.finding", IMPORTER)
                g.ref(fid, pid, "about", "structural", f"{rel}/{r['name']}: finding in the review of {repo} PR #{pr}"
                      + (f" at {f['file']}" if f["file"] else ""), "finding.pr", IMPORTER, confidence=0.95)


def parse_merge_review(L, g: Graph, path: Path, rel: str = "logs") -> None:
    g.count("merge_review_seen")
    m = FM_NAME.match(path.name)
    text = path.read_text(errors="replace")
    if not m:
        g.count("merge_review_bad_name")
        return
    if lab_mention(L, text):
        g.count("merge_review_lab_skipped")
        return
    repo, pr = m["repo"], m["pr"]
    projects = [DEFAULT_PROJECT]
    pid = _pr_node(L, g, repo, pr, path.name)
    lines = text.splitlines()
    header = lines[0] if lines else ""
    rid = f"RV-merge-{slug(repo)}-{pr}-r1"
    loc = dict(store=SOURCE_STORE, path=f"{rel}/{path.name}", commit=None, untracked=True, file_hash=file_sha(path))
    verdict = next((ln for ln in lines if ln.startswith("## Verdict") or ln.startswith("### Verdict")), "")
    g.add_node(dict(id=rid, kind="review", layer="reviews", project=projects, privacy=privacy_of(L, projects, "reviews"),
                    title=one_line(header.lstrip("# "), 140), body=one_line(redact(verdict)[0], 600), importer=IMPORTER,
                    source_commit=None, meta=dict(reviewer="merge", round="merge"), location={**loc, "line": 1}))
    g.ref(rid, pid, "reviews", "structural", f"{rel}/{path.name}: file name names {repo} PR #{pr}", "review.file_name", IMPORTER, confidence=0.95)
    for sid in stories_named(header):
        g.ref(pid, sid, "delivers", "exact", f"{rel}/{path.name}:1 header names {sid}", "merge.header_names_story", IMPORTER)
    rows, bold = [], []
    for no, ln in enumerate(lines, 1):
        r = FM_ROW.match(ln)
        if r:
            status_txt = r.group(3).strip().strip("*").strip()
            outcome = next((o for rx, o in STATUS if rx.match(status_txt)), None)
            if outcome:
                rows.append((no, dict(severity=r.group(1), text=r.group(2).strip("* "), file="", line=None), outcome, status_txt))
                continue
        b = FM_BOLD.match(ln)
        if b:
            w = WHERE.match(b.group(2))
            bold.append((no, dict(severity=b.group(1), text=b.group(2).strip(), file=w.group(1) if w else "",
                                  line=int(w.group(2)) if w else None)))
    g.count("merge_review_findings_from_delta_table" if rows else "merge_review_findings_from_headings", len(rows) or len(bold))
    items = [(no, f, o, f"delta table status '{one_line(s, 60)}'") for no, f, o, s in rows] if rows else \
            [(no, f, "unknown", "no delta table") for no, f in bold]
    for n, (no, f, outcome, basis) in enumerate(items, 1):
        fid = _add_finding(L, g, rid, n, f, {**loc, "line": no}, projects, outcome, basis)
        g.ref(rid, fid, "found", "exact", f"{rel}/{path.name}:{no} finding {n}", "review.finding", IMPORTER)
        g.ref(fid, pid, "about", "structural", f"{rel}/{path.name}: finding in the merge review of {repo} PR #{pr}", "finding.pr",
              IMPORTER, confidence=0.95)
