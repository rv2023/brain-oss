#!/usr/bin/env python3
"""check.py: the Brain's check script. All rules come from layers.yaml; this file only applies them.

  check.py layers                     validate layers.yaml itself
  check.py records DIR [DIR ...]      validate record files (decisions, stories, lessons, findings, runs, ...)
  check.py brief FILE [--records DIR ...]   check a Codex brief (header, IDs, worktree, lab, typed values)
  check.py fix --safe DIR [DIR ...]   safe auto-fixes only (project from project_rules, estimate_basis: size_band)
  check.py evidence [DIR]             verify evidence run manifests: file hashes, no unlisted files, the hash chain,
                                      public_safe present, no 12-digit numbers or lab material (default DIR: evidence/)

Options: --layers PATH (default: layers.yaml next to this script).
Output: one line per problem on stdout: `file:line | rule | message | fix hint` (rule starts with ERROR or WARN);
a summary on stderr. Exit: 0 ok, 1 errors, 2 warnings only, 3 the check itself failed. Python 3.11 + PyYAML only.
"""
# "lab" in rule names and messages means private, claude-only material (layers.yaml lab_paths and claude-only
# layers or stores); other agents must never see it.
from __future__ import annotations

import argparse
import fnmatch
import os
import re
import sys
from pathlib import Path

try:
    import yaml
except ImportError:                       # exit 3 = the check itself could not run (callers must not treat it as "brief bad")
    print("check.py: PyYAML is not installed", file=sys.stderr)
    sys.exit(3)

BRAIN = Path(__file__).resolve().parent
PRIVACY = ["open", "owner", "consent", "claude-only"]          # strictest last (privacy_rule)
KNOWN_KINDS = {"story", "goal", "run", "decision", "lesson", "finding", "research", "release"}
KIND_KEYS = {"required", "optional", "auto", "values", "one_of", "checks_syntax"}
FOLDER_KIND = {"decisions": "decision", "stories": "story", "goals": "goal", "runs": "run", "lessons": "lesson",
               "findings": "finding", "research": "research", "releases": "release"}
TWELVE_DIGITS = re.compile(r"(?<!\d)\d{12}(?!\d)")
HEX_HASH = re.compile(r"(?<![0-9A-Za-z])(?=[0-9a-f]*[a-f])[0-9a-f]{16,64}(?![0-9A-Za-z])")   # sha256 / git SHAs: not account numbers


def has_twelve_digits(text: str) -> bool:
    """12-digit run outside hex hashes (hashes and commit SHAs can contain 12 digits by chance)."""
    return bool(TWELVE_DIGITS.search(HEX_HASH.sub("#", text)))
STOPWORDS = {"the", "and", "for", "per", "with", "from", "into"}


# ---------------------------------------------------------------- output
class Report:
    def __init__(self) -> None:
        self.items: list[tuple[str, str, str, str, str]] = []

    def add(self, level: str, where: str, rule: str, msg: str, hint: str = "") -> None:
        self.items.append((level, where, rule, msg, hint))

    def error(self, *a: str) -> None:
        self.add("ERROR", *a)

    def warn(self, *a: str) -> None:
        self.add("WARN", *a)

    def emit(self, what: str) -> int:
        for level, where, rule, msg, hint in self.items:
            print(f"{where} | {level} {rule} | {msg} | {hint}")
        e = sum(1 for i in self.items if i[0] == "ERROR")
        w = len(self.items) - e
        sys.stdout.flush()
        print(f"check.py {what}: {e} error(s), {w} warning(s)", file=sys.stderr)
        return 1 if e else (2 if w else 0)


def expand(p: str) -> Path:
    return Path(os.path.expanduser(str(p).replace("{brain}", str(BRAIN)))).resolve()


def under(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root)
        return True
    except ValueError:
        return False


def line_of(text: str, key: str) -> int | None:
    m = re.search(rf"^\s*{re.escape(key)}\s*:", text, re.M)
    return text.count("\n", 0, m.start()) + 1 if m else None


def loc(path: Path | str, line: int | None = None) -> str:
    return f"{path}:{line}" if line else str(path)


# ---------------------------------------------------------------- layers model
class Layers:
    def __init__(self, data: dict) -> None:
        self.d = data
        self.layers: dict = {**(data.get("layers") or {}), **(data.get("custom_layers") or {})}
        self.codes: dict[str, dict] = {}       # name -> {pattern, re, layer, legacy, ...}
        self.dupes: list[str] = []
        self.bad_regex: list[tuple[str, str]] = []
        for lname, layer in self.layers.items():
            for cname, pat in ((layer or {}).get("codes") or {}).items():
                self._add(cname, {"pattern": pat, "layer": lname, "legacy": False})
        for cname, spec in (data.get("legacy_codes") or {}).items():
            spec = spec or {}
            self._add(cname, {**spec, "pattern": spec.get("pattern", ""), "layer": spec.get("layer"), "legacy": True})
        self.projects: dict = {**(data.get("projects") or {}), **(data.get("custom_projects") or {})}
        self.fields: dict = {**(data.get("fields") or {}), **(data.get("custom_fields") or {})}
        self.kind_of_code = {c: k for k, cs in (data.get("record_kinds") or {}).items() for c in (cs or [])}
        self.stores = {n: {**s, "root": expand(s["path"])} for n, s in (data.get("stores") or {}).items() if s and s.get("path")}
        self.codex_paths = [expand(p) for p in data.get("codex_readable_paths") or []]
        self.lab_paths = list(data.get("lab_paths") or [])
        self.importer_globs = list(data.get("importer_owned_paths") or [])
        self.brief = data.get("brief_check") or {}
        self.folders: dict = dict(data.get("folders") or {})
        self.doc_folders: dict = dict(data.get("doc_folders") or {})

    def _add(self, name: str, spec: dict) -> None:
        if name in self.codes:
            self.dupes.append(name)
            return
        try:
            spec["re"] = re.compile(str(spec["pattern"]))
        except re.error as e:
            self.bad_regex.append((name, str(e)))
            spec["re"] = None
        self.codes[name] = spec

    def match(self, ident: str) -> list[str]:
        return [n for n, c in self.codes.items() if c["re"] is not None and c["re"].match(ident)]

    def privacy_of_layer(self, layer: str | None) -> str:
        return (self.layers.get(layer) or {}).get("privacy", "open") if layer else "open"

    def is_lab_code(self, name: str) -> bool:
        """A claude-only layer's code, or the decision code of a claude-only store (PDEC)."""
        if self.privacy_of_layer(self.codes[name].get("layer")) == "claude-only":
            return True
        return any(s.get("privacy") == "claude-only" and s.get("decision_code") == name for s in self.stores.values())

    def store_of(self, path: Path) -> tuple[str, dict] | tuple[None, None]:
        for n, s in self.stores.items():
            if under(path, s["root"]):
                return n, s
        return None, None

    def codex_reads(self, path: Path) -> bool:
        return any(under(path, r) for r in self.codex_paths)


def load_layers(path: Path, rep: Report) -> Layers | None:
    try:
        data = yaml.safe_load(path.read_text())
    except FileNotFoundError:
        rep.error(str(path), "layers.missing", "layers.yaml not found", "pass --layers PATH")
        return None
    except yaml.YAMLError as e:
        mark = getattr(e, "problem_mark", None)
        rep.error(loc(path, mark.line + 1 if mark else None), "layers.yaml", f"does not parse: {getattr(e, 'problem', e)}", "fix the YAML syntax")
        return None
    if not isinstance(data, dict):
        rep.error(str(path), "layers.yaml", "top level is not a mapping", "")
        return None
    return Layers(data)


# ---------------------------------------------------------------- check.py layers
def cmd_layers(path: Path, rep: Report) -> Layers | None:
    L = load_layers(path, rep)
    if L is None:
        return None
    text = path.read_text()
    w = lambda key: loc(path, line_of(text, key))  # noqa: E731
    if not isinstance(L.d.get("version"), int):
        rep.error(w("version"), "layers.version", "version missing or not an integer", "set version: N and add a changelog line")
    if not L.layers:
        rep.error(str(path), "layers.layers", "no layers defined", "")
    for name in L.dupes:
        rep.error(w(name), "layers.code-unique", f"code prefix {name} is defined more than once", "rename one; codes are unique across layers and legacy_codes")
    for name, err in L.bad_regex:
        rep.error(w(name), "layers.regex", f"code {name} regex does not compile: {err}", "fix the pattern")
    for name, c in L.codes.items():
        p = str(c["pattern"])
        if not (p.startswith("^") and p.endswith("$")):
            rep.warn(w(name), "layers.regex-anchor", f"code {name} pattern is not anchored ^...$", "anchor it so it matches the whole ID")
    for lname, layer in L.layers.items():
        layer = layer or {}
        if layer.get("privacy") not in PRIVACY:
            rep.error(w(lname), "layers.privacy", f"layer {lname} has no valid privacy ({layer.get('privacy')!r})", f"one of {', '.join(PRIVACY)}")
        if not layer.get("importer"):
            rep.error(w(lname), "layers.importer", f"layer {lname} has no importer", "name the importer script, or manual")
        if not layer.get("codes"):
            rep.error(w(lname), "layers.codes", f"layer {lname} has no codes", "add at least one ID code")
        for link in layer.get("links") or []:
            lk, sep, targets = str(link).partition(":")
            if not sep:
                rep.error(w(lname), "layers.link", f"layer {lname} link {link!r} is not 'name: targets'", "")
                continue
            for t in (x.strip() for x in targets.split(",")):
                if t != "any" and t not in L.layers:
                    rep.error(w(lname), "layers.link-target", f"layer {lname} link {lk.strip()} points at unknown layer {t!r}", "use a layer name or any")
    for name, c in L.codes.items():
        if not c["legacy"]:
            continue
        if c.get("layer") not in L.layers:
            rep.error(w(name), "layers.legacy-layer", f"legacy code {name} maps to unknown layer {c.get('layer')!r}", "map it to a layer")
        if not c.get("note"):
            rep.error(w(name), "layers.legacy-note", f"legacy code {name} has no note", "say where these IDs live and why they stay")
        if c.get("canonical") and c["canonical"] not in L.codes:
            rep.error(w(name), "layers.legacy-canonical", f"legacy code {name} canonical {c['canonical']} is not a code", "")
        if c.get("kind") and c["kind"] not in L.fields:
            rep.error(w(name), "layers.legacy-kind", f"legacy code {name} kind {c['kind']} has no fields entry", "")
        ex = c.get("example")
        if ex is None or c["re"] is None:
            rep.error(w(name), "layers.legacy-example", f"legacy code {name} has no example", "add one real ID")
            continue
        if not c["re"].match(str(ex)):
            rep.error(w(name), "layers.legacy-example", f"legacy code {name} example {ex} does not match its pattern", "")
        allowed = set(c.get("collides_with") or [])
        for other in L.match(str(ex)):
            if other != name and other not in allowed:
                rep.error(w(name), "layers.overlap", f"legacy code {name} example {ex} also matches code {other}", "narrow the pattern or declare collides_with")
    for pname, p in L.projects.items():
        if (p or {}).get("privacy") not in PRIVACY:
            rep.error(w(pname), "layers.project-privacy", f"project {pname} has no valid privacy", f"one of {', '.join(PRIVACY)}")
    if not L.d.get("privacy_rule"):
        rep.error(str(path), "layers.privacy-rule", "privacy_rule missing", "")
    for kind, spec in L.fields.items():
        if kind not in KNOWN_KINDS and kind not in (L.d.get("custom_fields") or {}):
            rep.error(w(kind), "layers.field-kind", f"fields kind {kind!r} is unknown", f"one of {', '.join(sorted(KNOWN_KINDS))} or custom_fields")
        for key in (spec or {}):
            if key not in KIND_KEYS and not str(key).startswith("notes"):
                rep.error(w(kind), "layers.field-key", f"fields.{kind} has unknown key {key!r}", f"one of {', '.join(sorted(KIND_KEYS))}")
        for alias, opts in ((spec or {}).get("one_of") or {}).items():
            if alias not in ((spec or {}).get("required") or []):
                rep.warn(w(kind), "layers.one-of", f"fields.{kind}.one_of {alias} is not in required", "")
            if not opts:
                rep.error(w(kind), "layers.one-of", f"fields.{kind}.one_of {alias} lists no fields", "")
    for kind, codes in (L.d.get("record_kinds") or {}).items():
        if kind not in L.fields:
            rep.error(w(kind), "layers.record-kind", f"record_kinds {kind} has no fields entry", "")
        for c in codes or []:
            if c not in L.codes:
                rep.error(w(kind), "layers.record-kind", f"record_kinds.{kind} names unknown code {c}", "")
    for sname, s in (L.d.get("stores") or {}).items():
        s = s or {}
        if not s.get("path"):
            rep.error(w(sname), "layers.store", f"store {sname} has no path", "")
        if s.get("decision_code") and s["decision_code"] not in L.codes:
            rep.error(w(sname), "layers.store", f"store {sname} decision_code {s['decision_code']} is not a code", "")
        if s.get("privacy") and s["privacy"] not in PRIVACY:
            rep.error(w(sname), "layers.store", f"store {sname} privacy {s['privacy']!r} is not valid", "")
    for rule in L.d.get("project_rules") or []:
        for p in (rule or {}).get("project") or []:
            if p not in L.projects:
                rep.error(w("project_rules"), "layers.project-rule", f"project_rules names unknown project {p}", "")
        if not (rule or {}).get("path"):
            rep.error(w("project_rules"), "layers.project-rule", "a project rule has no path", "")
    return L


# ---------------------------------------------------------------- record files
def read_record(path: Path) -> tuple[dict | None, str, int, str | None]:
    """Returns (record, text, line offset of the YAML, parse error)."""
    text = path.read_text(errors="replace")
    body, offset = text, 0
    if path.suffix == ".md":
        m = re.match(r"---\n(.*?\n)---\n", text, re.S)
        if not m:
            return None, text, 0, None            # plain Markdown (e.g. acceptance criteria): not a record file
        body, offset = m.group(1), 1
    try:
        data = yaml.safe_load(body)
    except yaml.YAMLError as e:
        mark = getattr(e, "problem_mark", None)
        return None, text, offset, f"does not parse at line {mark.line + 1 + offset if mark else '?'}: {getattr(e, 'problem', e)}"
    return (data if isinstance(data, dict) else None), text, offset, None


def record_files(dirs: list[str], rep: Report) -> list[Path]:
    out: list[Path] = []
    for d in dirs:
        p = Path(d).expanduser()
        if not p.exists():
            rep.error(d, "records.dir", "directory does not exist", "")
            continue
        files = [p] if p.is_file() else sorted(x for x in p.rglob("*") if x.suffix in (".yaml", ".yml", ".md") and x.is_file())
        out += [f.resolve() for f in files]
    return out


def as_list(v) -> list:
    return v if isinstance(v, list) else ([] if v is None else [v])


def kind_for(L: Layers, codes: list[str], path: Path) -> str | None:
    for c in codes:
        if c in L.kind_of_code:
            return L.kind_of_code[c]
    for part in reversed(path.parts[:-1]):
        if part in FOLDER_KIND:
            return FOLDER_KIND[part]
    return None


def lab_hits(L: Layers, text: str) -> tuple[list[tuple[int, str]], list[tuple[int, str]]]:
    """(lab path hits, lab ID hits) as (line, token)."""
    paths, ids = [], []
    lab_codes = [n for n in L.codes if L.is_lab_code(n) and L.codes[n].get("brief_scan", True) is not False and L.codes[n]["re"]]
    for i, line in enumerate(text.splitlines(), 1):
        for lp in L.lab_paths:
            if lp in line:
                paths.append((i, lp))
        for tok in tokens(line):
            if any(L.codes[n]["re"].match(tok) for n in lab_codes):
                ids.append((i, tok))
    return paths, ids


def tokens(line: str) -> list[str]:
    out = []
    for raw in re.split(r"[\s,;()\[\]{}<>\"'`|*]+", line):
        t = raw.strip(".:!?")
        if t:
            out.append(t)
    return out


def check_records(L: Layers, dirs: list[str], rep: Report) -> list[tuple[Path, dict]]:
    files = record_files(dirs, rep)
    recs: list[tuple[Path, dict, str]] = []
    for f in files:
        data, text, _, err = read_record(f)
        if err:
            rep.error(str(f), "records.yaml", err, "fix the YAML syntax")
            continue
        if data is None:
            if f.suffix != ".md":
                rep.error(str(f), "records.shape", "not a record (top level is not a mapping)", "a record is a YAML mapping with id:")
            continue
        recs.append((f, data, text))

    seen: dict[str, list[Path]] = {}
    for f, r, _ in recs:
        if r.get("id") is not None:
            seen.setdefault(str(r["id"]), []).append(f)
    for ident, fs in seen.items():
        if len(fs) > 1:
            for f in fs:
                rep.error(str(f), "records.duplicate-id", f"{ident} is also used in {', '.join(str(x) for x in fs if x != f)}", "give one of them a new ID in its store's own code")

    for f, r, text in recs:
        w = lambda key: loc(f, line_of(text, key))  # noqa: E731
        ident = r.get("id")
        if ident is None:
            rep.error(str(f), "records.id", "record has no id", "add id: using a code from layers.yaml")
            continue
        ident = str(ident)
        codes = L.match(ident)
        if not codes:
            rep.error(w("id"), "records.id-code", f"{ident} matches no code or legacy code in layers.yaml", "use a code from layers.yaml, or add a documented legacy_codes entry")
        elif len(codes) > 1:
            rep.warn(w("id"), "records.id-ambiguous", f"{ident} matches several codes ({', '.join(codes)})", "use the canonical code for new records")
        kind = kind_for(L, codes, f)
        layer = L.codes[codes[0]]["layer"] if codes else None
        spec = L.fields.get(kind) or {}
        if kind is None:
            rep.warn(w("id"), "records.kind", f"{ident}: record kind unknown; only id, project and location checked", "add the code to record_kinds")

        one_of = spec.get("one_of") or {}
        for name in ["project", "location", *(spec.get("required") or [])]:
            if name in one_of:
                if not any(r.get(x) not in (None, "", []) for x in one_of[name]):
                    rep.error(w("id"), "records.required", f"{ident}: needs one of {' or '.join(one_of[name])}", f"add {one_of[name][0]}: (testable rules) or {one_of[name][-1]}:")
            elif name not in r:
                hint = {"project": "add project: [<name>] (check.py fix --safe fills it where a project_rule applies)",
                        "location": "add location: {store: <store>, path: <path in store>}",
                        "estimate_basis": "IDs of similar finished stories, or size_band"}.get(name, f"add {name}: (unknown is allowed, plus a questions: entry)")
                rep.error(w("id"), "records.required", f"{ident}: required field {name} missing", hint)

        store_name, store = L.store_of(f)
        want = L.folders.get(kind) if kind else None
        if store and want and not (under(f, store["root"] / want) or under(f, store["root"] / "imported" / want)):
            rep.error(w("id"), "records.folder", f"{ident}: a {kind} record lives in records/{want}/ of its store, not here",
                      f"move it to {store['root'] / want}/ and update location.path")

        locv = r.get("location")
        if "location" in r:
            if not isinstance(locv, dict) or not locv.get("store") or not locv.get("path"):
                rep.error(w("location"), "records.location", f"{ident}: location needs store and path", "location: {store: ..., path: ...}")
            elif not str(f).endswith(str(locv["path"]).lstrip("./")):
                rep.warn(w("location"), "records.location-path", f"{ident}: location.path {locv['path']} is not this file", "point location.path at the file the record lives in")

        projects = [str(p) for p in as_list(r.get("project"))]
        for p in projects:
            if p not in L.projects:
                rep.error(w("project"), "records.project", f"{ident}: unknown project {p}", f"one of {', '.join(L.projects)}")

        auto = set(spec.get("auto") or [])
        estimates = [k for k in r if str(k).startswith("estimate_") and k not in auto
                     and k != "estimate_basis" and k != "estimate_model_mix"]
        if estimates and "estimate_basis" not in r and "estimate_basis" not in (spec.get("required") or []):
            rep.error(w(estimates[0]), "records.estimate-basis", f"{ident}: has {', '.join(estimates)} but no estimate_basis", "IDs of similar finished stories, or size_band")

        rel = f.as_posix()
        if not any(fnmatch.fnmatch(rel, g) for g in L.importer_globs):
            for k in r:
                if k in auto:
                    rep.error(w(k), "records.auto-field", f"{ident}: {k} is an auto field (importers write it)", "remove it; importers fill it from run events and billing")

        sname, store = L.store_of(f)
        privs = [L.privacy_of_layer(layer)] + [(L.projects.get(p) or {}).get("privacy", "open") for p in projects]
        if store and store.get("privacy"):
            privs.append(store["privacy"])
        privacy = max(privs, key=lambda p: PRIVACY.index(p) if p in PRIVACY else 0)
        codex = L.codex_reads(f)
        if privacy == "claude-only" and codex:
            rep.error(w("id"), "records.privacy", f"{ident}: claude-only record in a path Codex reads", "move it to the claude-only store (layers.yaml stores); leave only its ID here")
        if codex:
            paths, ids = lab_hits(L, text)
            for ln, _ in paths:
                rep.warn(loc(f, ln), "records.lab-path", f"{ident}: names a lab path in a file Codex reads", "write the lab record's ID only, never its path")
            for ln, tok in ids:
                if tok != ident:
                    rep.warn(loc(f, ln), "records.lab-id", f"{ident}: names lab ID {tok} in a file Codex reads", "check it is an ID only, with no lab content beside it")

        if store and kind == "decision" and codes and store.get("decision_code"):
            canon = [c for c in codes if c in {s.get("decision_code") for s in L.stores.values()}]
            if canon and store["decision_code"] not in canon:
                rep.error(w("id"), "records.store-code", f"{ident}: decision code {canon[0]} does not belong in store {sname} (uses {store['decision_code']})", f"re-issue it as {store['decision_code']}-NNNN")

        for ln, line in enumerate(text.splitlines(), 1):
            if TWELVE_DIGITS.search(line):
                rep.error(loc(f, ln), "records.account-number", f"{ident}: a 12-digit number (account number?) is in the record", "remove it; name the account, never its number")

        for a in as_list(r.get("affects")):
            if not L.match(str(a)):
                rep.warn(w("affects"), "records.affects-id", f"{ident}: affects entry {str(a)[:60]!r} is not an ID", "use IDs (DOC-<path>#<anchor> for a document section)")
        rp = r.get("replaces")
        if rp not in (None, "null") and str(rp) not in seen:
            rep.warn(w("replaces"), "records.replaces", f"{ident}: replaces {rp} which is not in the checked stores", "check the ID, or check its store too")
    return [(f, r) for f, r, _ in recs]


# ---------------------------------------------------------------- check.py brief
def decision_values(L: Layers, extra_dirs: list[str]) -> list[tuple[str, str, float]]:
    """(record id, value key, number) for numeric values in readable stores (never a claude-only store)."""
    dirs = [str(s["root"]) for s in L.stores.values() if s.get("read_values", True) is not False and s.get("privacy") != "claude-only"]
    out = []
    for d in dirs + list(extra_dirs):
        p = Path(d).expanduser()
        if not p.is_dir():
            continue
        for f in sorted(p.rglob("*.y*ml")):
            data, _, _, _ = read_record(f)
            if not data or not isinstance(data.get("values"), dict):
                continue
            for k, v in data["values"].items():
                if isinstance(v, (int, float)) and not isinstance(v, bool):
                    out.append((str(data.get("id")), str(k), float(v)))
    return out


def number_in(line: str, value: float) -> bool:
    for m in re.finditer(r"(?<![\w.])(\d+(?:\.\d+)?)(?![\w]|\.\d)", line):
        if float(m.group(1)) == value:
            return True
    return False


def cmd_brief(L: Layers, path: Path, extra_records: list[str], rep: Report) -> None:
    if not path.is_file():
        rep.error(str(path), "brief.missing", "brief file not found", "")
        return
    text = path.read_text(errors="replace")
    cfg = L.brief
    template = any(fnmatch.fnmatch(path.name, g) for g in cfg.get("template_briefs") or [])
    soft = rep.warn if template else rep.error
    tsuffix = " (template brief: warning until its generator writes the header)" if template else ""

    m = re.search(r"^\s*Project:\s*(.+)$", text, re.M)
    if not m:
        soft(str(path), "brief.project", "no Project: line" + tsuffix, f"add 'Project: <{'|'.join(L.projects)}>' to the header")
    else:
        line = text.count("\n", 0, m.start()) + 1
        for p in re.split(r"[,\s]+", m.group(1).split("#")[0].strip()):
            if p and p not in L.projects:
                rep.error(loc(path, line), "brief.project", f"unknown project {p}", f"one of {', '.join(L.projects)}")

    open_codes = [n for n in L.codes if not L.is_lab_code(n) and L.codes[n].get("brief_scan", True) is not False and L.codes[n]["re"]]
    if not any(L.codes[n]["re"].match(t) for t in tokens(text) for n in open_codes):
        soft(str(path), "brief.ids", "no ID from layers.yaml in the brief" + tsuffix, "add 'IDs: STORY-NNN, DEC-NNNN' to the header")

    roots = [str(expand(r)) for r in cfg.get("worktree_roots") or []]
    found = False
    for cand in re.findall(r"(?<![\w.])(~?/[\w./-]+)", text):
        full = str(Path(os.path.expanduser(cand)))
        if any(full == r.rstrip("/") or full.startswith(r.rstrip("/") + "/") or full.startswith(r) for r in roots):
            found = True
            break
    if not found:
        soft(str(path), "brief.worktree", "no absolute worktree path" + tsuffix, f"state the worktree as an absolute path under {roots[0] if roots else '<worktree root>/'}")

    paths, ids = lab_hits(L, text)
    for ln, _ in paths:
        rep.error(loc(path, ln), "brief.lab-path", "names a lab path (Codex must never see lab paths)", "remove the line; lab work stays in Claude-only files")
    for ln, tok in ids:
        rep.error(loc(path, ln), "brief.lab-id", f"names lab ID {tok}", "remove it; lab IDs and content stay Claude-only")

    need = int(cfg.get("value_topic_min_words", 2))
    vals = decision_values(L, extra_records)
    lines = text.splitlines()
    for rid, key, val in vals:
        words = [x for x in key.lower().split("_") if len(x) >= 3 and x not in STOPWORDS]
        if not words:
            continue
        for ln, line in enumerate(lines, 1):
            low = line.lower()
            hits = sum(1 for x in words if re.search(rf"\b{re.escape(x)}", low))
            if hits >= min(need, len(words)) and number_in(line, val):
                rep.warn(loc(path, ln), "brief.typed-value", f"types a decided value of {rid} ({key}) next to its topic words",
                         f"write 'see {rid} (setting {key})' instead of the number")


# ---------------------------------------------------------------- check.py fix --safe
def insert_after_id(text: str, offset_md: bool, line_to_add: str) -> str | None:
    lines = text.splitlines(keepends=True)
    start = 1 if offset_md else 0
    for i in range(start, len(lines)):
        if offset_md and lines[i].rstrip("\n") == "---":
            break
        if re.match(r"^id\s*:", lines[i]):
            lines.insert(i + 1, line_to_add + "\n")
            return "".join(lines)
    return None


def cmd_fix(L: Layers, dirs: list[str], rep: Report) -> None:
    files = record_files(dirs, rep)
    recs = []
    for f in files:
        data, text, _, err = read_record(f)
        if data and not err:
            recs.append((f, data, text))
    history = any("actual_hours" in r for _, r, _ in recs)
    rules = [(expand(x["path"]), x.get("project") or []) for x in L.d.get("project_rules") or [] if x and x.get("path")]
    for f, r, text in recs:
        md = f.suffix == ".md"
        adds: list[str] = []
        if "project" not in r:
            proj = next((p for root, p in rules if under(f, root)), None)
            if proj:
                adds.append(f"project: [{', '.join(proj)}]")
            else:
                rep.warn(str(f), "fix.project", f"{r.get('id')}: no project_rule covers this path; project left empty", "add project: by hand")
        codes = L.match(str(r.get("id", "")))
        kind = kind_for(L, codes, f)
        auto = set((L.fields.get(kind) or {}).get("auto") or [])
        has_est = any(str(k).startswith("estimate_") and k not in auto and k not in ("estimate_basis", "estimate_model_mix") for k in r)
        if has_est and "estimate_basis" not in r:
            if history:
                rep.warn(str(f), "fix.estimate-basis", f"{r.get('id')}: finished stories with actuals exist; estimate_basis not filled", "name the similar stories by hand")
            else:
                adds.append("estimate_basis: size_band")
        if not adds:
            continue
        new = text
        for a in reversed(adds):
            new = insert_after_id(new, md, a) if new is not None else None
        if new is None:
            rep.warn(str(f), "fix.skip", f"{r.get('id')}: no top-level id: line to insert after; not changed", "")
            continue
        body = re.match(r"---\n(.*?\n)---\n", new, re.S).group(1) if md else new
        after = yaml.safe_load(body)
        if any(after.get(k) != v for k, v in r.items()):     # never change an existing value (money included)
            rep.warn(str(f), "fix.skip", f"{r.get('id')}: insertion would change an existing value; not changed", "")
            continue
        f.write_text(new)
        for a in adds:
            print(f"{f} | FIXED | added {a} | check.py fix --safe")


# ---------------------------------------------------------------- check.py evidence
EVIDENCE_RUN = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{4}Z-[a-z0-9-]+$")


def cmd_evidence(L: Layers, root: Path, rep: Report) -> None:
    """METHOD.md "Evidence capture": each run folder is immutable; manifest.json lists sha256 of every file and the
    previous run's manifest hash (runs ordered by folder name), so an edited or removed run breaks the chain."""
    import hashlib
    import json
    if not root.is_dir():
        rep.warn(str(root), "evidence.missing", "no evidence folder", "runs are written by local/metrics.py")
        return
    loaded = []
    for run in sorted(p for p in root.iterdir() if p.is_dir()):
        m = run / "manifest.json"
        if not EVIDENCE_RUN.match(run.name):
            rep.error(str(run), "evidence.name", "folder name is not <YYYY-MM-DDTHHMMZ>-<metric>", "")
        if not m.is_file():
            rep.error(str(run), "evidence.manifest", "run folder has no manifest.json", "")
            continue
        raw = m.read_bytes()
        try:
            man = json.loads(raw)
        except json.JSONDecodeError as e:
            rep.error(str(m), "evidence.manifest", f"does not parse: {e}", "")
            continue
        if not isinstance(man.get("sequence"), int):
            rep.error(str(m), "evidence.sequence", "sequence missing (chain position, 1, 2, ...)", "")
            continue
        loaded.append((man["sequence"], run, m, raw, man))
    loaded.sort(key=lambda x: x[0])
    prev = None
    for want, (seq, run, m, raw, man) in enumerate(loaded, 1):
        if seq != want:
            rep.error(str(m), "evidence.sequence", f"sequence {seq}, expected {want}", "a run was removed or inserted")
        if not isinstance(man.get("public_safe"), bool):
            rep.error(str(m), "evidence.public_safe", "public_safe missing (must be true/false; default false)", "")
        if man.get("previous_manifest_sha256") != prev:
            rep.error(str(m), "evidence.chain", f"previous_manifest_sha256 is {man.get('previous_manifest_sha256')}, expected {prev}",
                      "a run was edited, removed or inserted; never rewrite runs, add a correction run")
        listed = man.get("files") or {}
        actual = sorted(str(f.relative_to(run)) for f in run.rglob("*") if f.is_file() and f.name != "manifest.json")
        for f in actual:
            if f not in listed:
                rep.error(str(run / f), "evidence.unlisted", "file is not in manifest.json", "")
        for f, h in sorted(listed.items()):
            fp = run / f
            if not fp.is_file():
                rep.error(str(fp), "evidence.missing-file", "listed in manifest.json but missing", "")
                continue
            data = fp.read_bytes()
            if hashlib.sha256(data).hexdigest() != h:
                rep.error(str(fp), "evidence.hash", "sha256 differs from manifest.json (file was changed)", "add a correction run instead")
            text = data.decode("utf-8", "replace")
            if has_twelve_digits(text):
                rep.error(str(fp), "evidence.12digit", "contains a 12-digit number", "redact and write a new run")
            paths, ids = lab_hits(L, text)
            if paths or ids:
                rep.error(str(fp), "evidence.lab", "names lab paths or IDs", "lab material stays in the lab store")
        prev = hashlib.sha256(raw).hexdigest()


# ---------------------------------------------------------------- main
class Parser(argparse.ArgumentParser):
    def error(self, message: str):        # argparse would exit 2, which means "warnings only" here
        self.print_usage(sys.stderr)
        print(f"check.py: {message}", file=sys.stderr)
        sys.exit(3)


def main(argv: list[str] | None = None) -> int:
    ap = Parser(prog="check.py", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--layers", default=str(BRAIN / "layers.yaml"))
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("layers")
    p = sub.add_parser("records"); p.add_argument("dirs", nargs="+")
    p = sub.add_parser("brief"); p.add_argument("file"); p.add_argument("--records", nargs="*", default=[])
    p = sub.add_parser("fix"); p.add_argument("--safe", action="store_true"); p.add_argument("dirs", nargs="+")
    p = sub.add_parser("evidence"); p.add_argument("dir", nargs="?", default=str(BRAIN / "evidence"))
    a = ap.parse_args(argv)

    rep = Report()
    lpath = Path(a.layers).expanduser()
    if a.cmd == "layers":
        cmd_layers(lpath, rep)
        return rep.emit("layers")
    L = load_layers(lpath, rep)
    if L is None:
        return rep.emit(a.cmd)
    if a.cmd == "records":
        check_records(L, a.dirs, rep)
    elif a.cmd == "brief":
        cmd_brief(L, Path(a.file).expanduser(), a.records, rep)
    elif a.cmd == "evidence":
        cmd_evidence(L, Path(a.dir).expanduser(), rep)
    elif a.cmd == "fix":
        if not a.safe:
            print("fix needs --safe (only safe fixes exist)", file=sys.stderr)
            return 1
        cmd_fix(L, a.dirs, rep)
    return rep.emit(a.cmd)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:  # noqa: BLE001 - a crash must not look like "errors found" (exit 1)
        print(f"check.py internal error: {type(e).__name__}: {e}", file=sys.stderr)
        sys.exit(3)
