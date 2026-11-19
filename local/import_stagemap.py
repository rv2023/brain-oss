"""Importer (b): the stage map (story list), one node per row; depends_on / decisions / closes_hops become exact links.

Private rows (a claude-only ID, a private path in `source`, or kind `private`) are skipped and only counted; links to
them are dropped.
"""
from __future__ import annotations

import re

import yaml

from brainlib import DEFAULT_PROJECT, SOURCE_STORE, Graph, is_lab_id, one_line, privacy_of, redact

IMPORTER = "stage_map"
KIND_LAYER = {"story": "plan", "fix": "plan", "cleanup": "plan", "team-story": "plan", "roadmap": "plan", "ops-loop": "plan",
              "private": "private", "hop": "audits", "finding": "audits", "audit-item": "audits", "pr": "code", "journey-gap": "product"}
ID_LINE = re.compile(r"^- id: (.+)$")


def is_lab_row(L, item: dict) -> bool:
    return (is_lab_id(L, item.get("id", "")) or any(lp in str(item.get("source", "")) for lp in L.lab_paths)
            or item.get("kind") in ("lab", "private"))


def parse(L, g: Graph, text: str, path: str, commit: str, store: str = SOURCE_STORE) -> None:
    lines = {}
    for no, ln in enumerate(text.splitlines(), 1):
        m = ID_LINE.match(ln)
        if m:
            lines[str(yaml.safe_load(m.group(1)))] = no
    items = (yaml.safe_load(text) or {}).get("items") or []
    lab_ids = {str(i.get("id")) for i in items if is_lab_row(L, i)}
    for item in items:
        iid = str(item.get("id"))
        g.count("stage_rows_seen")
        if iid in lab_ids:
            g.count("stage_rows_lab_skipped")
            continue
        codes = L.match(iid)
        if len({L.codes[c]["layer"] for c in codes}) > 1 or len(codes) > 1:
            g.count("stage_ids_matching_several_codes")
        if not codes:
            g.count("stage_ids_matching_no_code")
        kind = str(item.get("kind", "item"))
        layer = KIND_LAYER.get(kind, "plan")
        projects = [str(p) for p in (item.get("project") or [])] or [DEFAULT_PROJECT]
        body = str(item.get("why", ""))
        if item.get("ac"):
            body += "\nAcceptance: " + (" | ".join(map(str, item["ac"])) if isinstance(item["ac"], list) else str(item["ac"]))
        body, nred = redact(body)
        g.count("redactions", nred)
        title, nred = redact(str(item.get("title", iid)))
        g.count("redactions", nred)
        meta = {k: str(item[k]) for k in ("stage", "kind", "source", "owner", "finishes_in") if k in item}
        g.add_node(dict(
            id=iid, kind=kind, layer=layer, project=projects, privacy=privacy_of(L, projects, layer),
            title=one_line(title, 120), body=body, aliases=[], source_commit=commit, importer=IMPORTER, meta=meta,
            location=dict(store=store, path=path, line=lines.get(iid), commit=commit)))
        g.count(f"stage_rows_{kind}")
        for field, type_ in (("depends_on", "depends_on"), ("decisions", "decided_by"), ("closes_hops", "closes")):
            for dst in item.get(field) or []:
                dst = str(dst)
                if dst in lab_ids or is_lab_id(L, dst):
                    g.count("stage_links_to_lab_skipped")
                    continue
                g.ref(iid, dst, type_, "exact", f"{path}:{lines.get(iid)} {iid} {field}: {dst}", f"stage.{field}",
                      IMPORTER, commit=commit)
