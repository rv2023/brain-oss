#!/usr/bin/env python3
"""Bridge v0: look things up in the local Brain and get a small, deterministic evidence pack.

  python3 local/bridge.py lookup ID_OR_ALIAS
  python3 local/bridge.py neighbours ID [--depth 1] [--direction both|upstream|downstream] [--limit 40]
  python3 local/bridge.py impact ID [--depth 2] [--limit 40]   what depends on ID (neighbours, downstream only)
  python3 local/bridge.py serve            MCP server over stdio (tools: lookup, neighbours, impact); needs the mcp package

Packs are plain JSON, or a compact lossless text format when BRAIN_FOLD names a folder holding a fold.py module with
pack()/unfold(). Every row cites an ID and its file:line; one `omitted` line says what was left out. Claude-only items
are never returned. With BRAIN_PACK_LOG set, every pack's sha256, size and exact-decode result is appended to that file
as one JSON line.
Direction (layers.yaml): links read src -> dst. For the types in DOWNSTREAM the target depends on the source (affects,
delivers, closes, found); for every other type the target is something the source depends on (depends_on, decided_by,
about, reviews, replaces).
"""
from __future__ import annotations

import argparse
import functools
import hashlib
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from brainlib import FOLD_DIR  # noqa: E402

try:
    if FOLD_DIR is None:
        raise ImportError
    sys.path.insert(0, str(FOLD_DIR))
    from fold import pack as _fold_pack, unfold  # noqa: E402
except ImportError:                                   # default: plain JSON, same content, still deterministic
    def _fold_pack(obj: dict) -> str:
        return json.dumps(obj, ensure_ascii=False, indent=1)

    def unfold(text: str) -> dict:
        return json.loads(text)


SUMMARY = Path(__file__).resolve().parent / "out" / "import-summary.json"
# Kinds the plan describes that no importer reads yet (layers.yaml record_kinds + design/code layers).
PLANNED_KINDS = ("decision", "story", "goal", "run", "lesson", "finding", "research", "release", "design", "code")
IMPORTED_AS = {"decision": "decision", "story": "story", "finding": "finding", "lesson": "lesson", "review": "finding"}


@functools.lru_cache(maxsize=1)
def coverage() -> dict:
    """What this Brain has and has NOT imported, attached to every pack so an answer is never read as complete."""
    try:
        s = json.loads(SUMMARY.read_text())
    except (OSError, ValueError):
        return {"sources": "unknown", "not_imported": "unknown", "unresolved_ids": "unknown",
                "note": "No import summary: coverage is unknown and this answer is not complete; check the sources directly."}
    have = {IMPORTED_AS.get(k, k) for k, n in (s.get("nodes_by_kind") or {}).items() if n}
    missing = [k for k in PLANNED_KINDS if k not in have]
    return {"sources": f"decisions, stage map, review logs at source commit {str(s.get('ops_commit', ''))[:7]}",
            "not_imported": " ".join(missing) or "none",
            "unresolved_ids": int(s.get("unresolved", 0)),
            "note": "Only these sources are in the Brain; a fact from anything not imported, or an unresolved ID, is not here. Check those sources directly when the question touches them."}


def pack(obj: dict) -> str:
    """fold.pack(), plus one log line per pack when BRAIN_PACK_LOG is set (never changes the text).
    Every pack carries `coverage` (what is and is not imported) so a precise answer is not taken as a complete one."""
    if "pack" in obj and "coverage" not in obj:
        obj = {**obj, "coverage": coverage()}
    text = _fold_pack(obj)
    log = os.environ.get("BRAIN_PACK_LOG")
    if log:
        try:
            exact = unfold(text) == obj
        except Exception:  # fold.pack fell back to JSON: not folded, so not a folded-decode check
            exact = None
        rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "pack": obj.get("pack", ""),
               "sha256": hashlib.sha256(text.encode()).hexdigest(), "chars": len(text), "exact": exact}
        try:
            with open(log, "a") as fh:
                fh.write(json.dumps(rec) + "\n")
        except OSError:
            pass
    return text

DOWNSTREAM = ["affects", "delivers", "closes", "found"]
TIER_RANK = {"exact": 0, "structural": 1, "suggested": 2}
BODY_MAX = 1200
TITLE_MAX = 80
HIDDEN = ("claude-only",)


def at(loc: dict) -> str:
    """file:line for a location (commit given once in the pack header)."""
    s = f"{loc.get('store', '')}/{loc.get('path', '')}"
    if loc.get("line"):
        s += f":{loc['line']}"
    return s


def short(s: str, n: int) -> str:
    s = " ".join(str(s).split())
    return s if len(s) <= n else s[: n - 1] + "…"


def commits_of(rows: list[dict]) -> str:
    cs = sorted({(r["location"] or {}).get("commit") or "" for r in rows} - {""})
    return " ".join(c[:10] for c in cs) or "none"


def centre_fields(n: dict, body_max: int = BODY_MAX) -> dict:
    body = n["body"] or ""
    c = {"id": n["id"], "kind": n["kind"], "layer": n["layer"], "project": ",".join(n["project"]), "title": n["title"],
         "at": at(n["location"])}
    if n["aliases"]:
        c["aliases"] = " ".join(n["aliases"])
    for k in sorted(n["meta"] or {}):
        v = n["meta"][k]
        if v not in ("", None) and k not in ("source",):
            c[k] = str(v)
    if body and body != n["title"]:
        c["body"] = body if len(body) <= body_max else body[: body_max - 1] + "…"
        if len(body) > body_max:
            c["body_rest"] = f"{len(body) - body_max + 1} more characters at {c['at']}"
    return c


def build_pack(centre: dict, rows: list[dict], limit: int, direction: str, depth: int) -> str:
    """Pure and deterministic: same centre + rows (any order) -> the same text."""
    rows = sorted(rows, key=lambda r: (r["depth"], TIER_RANK.get(r["tier"], 9), r["type"], r["dir"], r["id"]))
    shown = rows[:limit]
    obj = {"pack": f"neighbours {centre['id']} depth={depth} direction={direction}",
           "read_at": commits_of([centre] + shown),
           "centre": centre_fields(centre),
           "neighbours": [{"id": r["id"], "kind": r["kind"], "dir": r["dir"], "type": r["type"], "tier": r["tier"],
                           "depth": r["depth"], "via": r["via"], "title": short(r["title"], TITLE_MAX),
                           "at": at(r["location"])} for r in shown]}
    left = len(rows) - len(shown)
    obj["omitted"] = (f"{left} more neighbours not shown (limit {limit}); ask neighbours with a higher limit or on a listed id"
                      if left else "0 (nothing left out)")
    return pack(obj)


def lookup_pack(n: dict, candidates: list[str]) -> str:
    obj = {"pack": f"lookup {n['id']}", "read_at": commits_of([n]), "item": centre_fields(n)}
    if candidates:
        obj["also_named_by_this_alias"] = " ".join(candidates)
    return pack(obj)


# ------------------------------------------------------------------ database side
NODE_COLS = "id, kind, layer, project, privacy, title, body, meta, location, aliases"


def _node(row) -> dict:
    keys = ["id", "kind", "layer", "project", "privacy", "title", "body", "meta", "location", "aliases"]
    return dict(zip(keys, row))


def resolve(conn, ident: str) -> list[dict]:
    r = conn.execute(f"SELECT {NODE_COLS} FROM nodes WHERE id = %s", (ident,)).fetchall()
    if not r:
        r = conn.execute(f"SELECT {NODE_COLS} FROM nodes WHERE %s = ANY(aliases) ORDER BY id", (ident,)).fetchall()
    return [n for n in map(_node, r) if n["privacy"] not in HIDDEN]


WALK = """
WITH RECURSIVE walk(id, depth, path, dir, type, tier, via) AS (
  SELECT %(id)s::text, 0, ARRAY[%(id)s::text], ''::text, ''::text, ''::text, ''::text
  UNION ALL
  SELECT e.nb, w.depth + 1, w.path || e.nb, e.dir, e.type, e.tier, w.id
  FROM walk w
  JOIN LATERAL (
    SELECT l.dst AS nb, CASE WHEN l.type = ANY(%(down)s) THEN 'down' ELSE 'up' END AS dir, l.type, l.tier
      FROM links l WHERE l.src = w.id
    UNION ALL
    SELECT l.src AS nb, CASE WHEN l.type = ANY(%(down)s) THEN 'up' ELSE 'down' END AS dir, l.type, l.tier
      FROM links l WHERE l.dst = w.id
  ) e ON (%(direction)s = 'both' OR e.dir = %(want)s)
  WHERE w.depth < %(depth)s AND NOT e.nb = ANY(w.path)
)
SELECT DISTINCT ON (w.id) w.id, w.depth, w.dir, w.type, w.tier, w.via, n.kind, n.title, n.location, n.privacy
FROM walk w JOIN nodes n ON n.id = w.id
WHERE w.depth > 0
ORDER BY w.id, w.depth, w.tier, w.type, w.dir, w.via
"""


def neighbour_rows(conn, nid: str, depth: int, direction: str) -> list[dict]:
    want = {"upstream": "up", "downstream": "down"}.get(direction, "")
    rows = conn.execute(WALK, dict(id=nid, depth=depth, direction=direction, want=want, down=DOWNSTREAM)).fetchall()
    keys = ["id", "depth", "dir", "type", "tier", "via", "kind", "title", "location", "privacy"]
    out = [dict(zip(keys, r)) for r in rows]
    return [r for r in out if r["privacy"] not in HIDDEN]


def do_lookup(conn, ident: str) -> str:
    found = resolve(conn, ident)
    if not found:
        hits = conn.execute("SELECT id FROM nodes WHERE tsv @@ plainto_tsquery('english', %s) AND privacy <> ALL(%s)"
                            " ORDER BY ts_rank(tsv, plainto_tsquery('english', %s)) DESC, id LIMIT 5",
                            (ident, list(HIDDEN), ident)).fetchall()
        return pack({"pack": f"lookup {ident}", "found": "no item has this ID or alias",
                     "search_hits": " ".join(h[0] for h in hits) or "none"})
    return lookup_pack(found[0], [n["id"] for n in found[1:]])


def do_neighbours(conn, ident: str, depth: int = 1, direction: str = "both", limit: int = 40) -> str:
    found = resolve(conn, ident)
    if not found:
        return do_lookup(conn, ident)
    if len(found) > 1:
        return pack({"pack": f"neighbours {ident}", "found": "this alias names several items; ask again with one id",
                     "candidates": " ".join(n["id"] for n in found)})
    centre = found[0]
    return build_pack(centre, neighbour_rows(conn, centre["id"], depth, direction), limit, direction, depth)


def serve() -> int:
    from mcp.server.fastmcp import FastMCP

    import db
    app = FastMCP("brain-local")

    @app.tool()
    def lookup(id: str) -> str:
        """Look up one Brain item by ID or alias (e.g. STORY-101, T-12, DEC-0002). Returns a compact evidence pack."""
        with db.connect() as conn:
            return do_lookup(conn, id)

    @app.tool()
    def neighbours(id: str, depth: int = 1, direction: str = "both", limit: int = 40) -> str:
        """Items linked to ID up to `depth` hops; direction both|upstream|downstream. Rows cite id and file:line."""
        with db.connect() as conn:
            return do_neighbours(conn, id, max(1, min(depth, 3)), direction, max(1, min(limit, 200)))

    @app.tool()
    def impact(id: str, depth: int = 2, limit: int = 40) -> str:
        """What depends on ID (downstream items up to `depth` hops): what a change to ID could affect."""
        with db.connect() as conn:
            return do_neighbours(conn, id, max(1, min(depth, 3)), "downstream", max(1, min(limit, 200)))

    app.run()
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("lookup")
    s.add_argument("id")
    s = sub.add_parser("neighbours")
    s.add_argument("id")
    s.add_argument("--depth", type=int, default=1)
    s.add_argument("--direction", choices=["both", "upstream", "downstream"], default="both")
    s.add_argument("--limit", type=int, default=40)
    s = sub.add_parser("impact")
    s.add_argument("id")
    s.add_argument("--depth", type=int, default=2)
    s.add_argument("--limit", type=int, default=40)
    sub.add_parser("serve")
    a = ap.parse_args(argv)
    if a.cmd == "serve":
        return serve()
    import db
    with db.connect() as conn:
        if a.cmd == "lookup":
            out = do_lookup(conn, a.id)
        elif a.cmd == "impact":
            out = do_neighbours(conn, a.id, a.depth, "downstream", a.limit)
        else:
            out = do_neighbours(conn, a.id, a.depth, a.direction, a.limit)
    print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
