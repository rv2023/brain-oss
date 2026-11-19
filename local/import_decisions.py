"""Importer (a): the decisions log split into one record per entry, plus the structured decision/lesson records.

Read-only. An entry is one bullet (`- ` / `1. `) or one paragraph line under a heading (the log has no wrapped lines).
Each entry keeps its original text, date and line. Keys: `DEC-<yyyymmdd>-E<n>` (n = order of the entry among entries of
that date, in file order). Aliases: the entry's leading label when it matches a legacy decision code (`T-12`, `O22`, `D6`)
plus a dated form (`DEC-20250115-T12`). Entries that name a private (claude-only) path or ID are skipped and counted.
"""
from __future__ import annotations

import re

import yaml

from brainlib import DEFAULT_PROJECT, SOURCE_STORE, Graph, lab_mention, one_line, privacy_of, redact

IMPORTER = "decisions"
DATE = re.compile(r"(20\d\d)-(\d\d)-(\d\d)")
ENTRY = re.compile(r"^(?:- |\d+\. )(.*)$")
LABEL = re.compile(r"^(?:\*\*)?(T-\d+|O\d+(?:\.\d+)?|D\d+)(?=[\s:/*.,)]|$)")
INDEX_LINE = re.compile(r"^(B?DEC-\d{4})\s*\(")
PLAN_CODES = ("STORY", "FIX", "RM")


def _ymd(m: re.Match) -> str:
    return f"{m.group(1)}{m.group(2)}{m.group(3)}"


def project_for(text: str) -> list[str]:
    """Log entries carry no project field; they get the source's default project (BRAIN_PROJECT)."""
    return [DEFAULT_PROJECT]


def plan_mentions(L, text: str) -> list[str]:
    seen: list[str] = []
    for tok in re.split(r"[\s,;()\[\]{}<>\"'`|*/]+", text):
        tok = tok.strip(".:!?")
        if tok and tok not in seen and any(c in PLAN_CODES for c in L.match(tok)):
            seen.append(tok)
    return seen


def parse_log(L, g: Graph, text: str, path: str, commit: str, store: str = SOURCE_STORE) -> None:
    lines = text.splitlines()
    file_date = None
    heading, heading_date, last_date = "", None, None
    per_date: dict[str, int] = {}
    for no, raw in enumerate(lines, 1):
        line = raw.rstrip()
        if not line.strip():
            continue
        if line.startswith("#"):
            heading = line.lstrip("#").strip()
            m = DATE.search(heading)
            if line.startswith("# ") and m and file_date is None:
                file_date = _ymd(m)
            heading_date = _ymd(m) if m else None
            if heading_date:
                last_date = heading_date
            continue
        m = ENTRY.match(line)
        body = m.group(1) if m else line.strip()
        g.count("decision_entries_seen")
        if lab_mention(L, body):
            g.count("decision_entries_lab_skipped")
            continue
        dm = DATE.match(body.lstrip("*"))
        if dm:
            date, date_src = _ymd(dm), "entry"
        elif heading_date:
            date, date_src = heading_date, "heading"
        elif last_date:
            date, date_src = last_date, "carried from an earlier heading"
        else:
            date, date_src = file_date or "00000000", "file title"
        per_date[date] = per_date.get(date, 0) + 1
        nid = f"DEC-{date}-E{per_date[date]}"
        clean, nred = redact(body)
        g.count("redactions", nred)
        aliases: list[str] = []
        lm = LABEL.match(body)
        label = lm.group(1) if lm else None
        if label:
            dated = f"DEC-{date}-{label.replace('-', '').replace('.', '-')}"
            for a in ([label] if L.match(label) else []) + [dated]:
                if L.match(a):
                    aliases.append(a)
            if not L.match(label):
                g.count("decision_labels_matching_no_code")
        projects = project_for(body)
        title = one_line((heading + " — " if heading and not m else "") + clean, 120)
        g.add_node(dict(
            id=nid, kind="decision", layer="decisions", project=projects, privacy=privacy_of(L, projects, "decisions"),
            title=title, body=clean, aliases=aliases, source_commit=commit, importer=IMPORTER,
            meta=dict(date=f"{date[:4]}-{date[4:6]}-{date[6:]}", date_from=date_src, section=one_line(heading, 120),
                      label=label or "", entry_kind="bullet" if m else "paragraph"),
            location=dict(store=store, path=path, line=no, commit=commit)))
        g.count("decision_entries")
        im = INDEX_LINE.match(body)
        if im:   # "- DEC-0002 (2025-01-20): ... -> records/decisions/DEC-0002.yaml": the record replaces this log line
            g.ref(nid, im.group(1), "replaces", "exact", f"{path}:{no} index line names {im.group(1)}", "index_line",
                  IMPORTER, reverse=True, commit=commit)
            continue
        for pid in plan_mentions(L, body):
            g.ref(nid, pid, "affects", "exact", f"{path}:{no} entry text names {pid}", "text_mention", IMPORTER, commit=commit)


def parse_record(L, g: Graph, text: str, path: str, commit: str, store: str) -> None:
    import check
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError:
        g.count("records_unparseable")
        return
    if not isinstance(data, dict) or not data.get("id"):
        g.count("records_without_id")
        return
    rid = str(data["id"])
    codes = L.match(rid)
    if not codes:
        g.count("records_id_matches_no_code")
    if any(L.is_lab_code(c) for c in codes):
        g.count("records_lab_skipped")
        return
    from pathlib import Path
    kind = check.kind_for(L, codes, Path(path)) or "record"
    layer = next((L.codes[c]["layer"] for c in codes), "decisions")
    projects = [str(p) for p in (data.get("project") or [])] or [DEFAULT_PROJECT]
    title_src = data.get("question") or data.get("lesson") or data.get("title") or rid
    body_yaml = yaml.safe_dump({k: v for k, v in data.items() if k not in ("location",)}, sort_keys=False, allow_unicode=True, width=1000)
    clean, nred = redact(body_yaml)
    g.count("redactions", nred)
    lines = text.splitlines()
    line = next((i for i, ln in enumerate(lines, 1) if ln.startswith("id:")), 1)
    g.add_node(dict(
        id=rid, kind=kind, layer=layer, project=projects, privacy=privacy_of(L, projects, layer),
        title=one_line(f"{rid}: {title_src}", 120), body=clean, aliases=[], source_commit=commit, importer=IMPORTER,
        meta=dict(date=str(data.get("date", "")), decided_by=str(data.get("decided_by", ""))),
        location=dict(store=store, path=path, line=line, commit=commit)))
    g.count(f"records_{kind}")
    for field, type_ in (("affects", "affects"), ("replaces", "replaces")):
        vals = data.get(field)
        for v in (vals if isinstance(vals, list) else [vals] if vals else []):
            g.ref(rid, str(v), type_, "exact", f"{store}/{path} {field}: {v}", f"record.{field}", IMPORTER, commit=commit)
