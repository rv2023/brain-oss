#!/usr/bin/env python3
"""Run all importers (read-only on every source) and publish to the local database in one transaction.

  python3 local/run_import.py            import and publish
  python3 local/run_import.py --dry-run  parse only, print counts (no database needed)

The source project (BRAIN_SOURCE, default: example/) is read at its current HEAD commit (git show), never from the
working tree, so every item's location (file, line, commit) is reproducible. Review logs are read from <source>/logs.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import import_decisions  # noqa: E402
import import_reviews  # noqa: E402
import import_stagemap  # noqa: E402
from brainlib import BRAIN, OUT, SOURCE, SOURCE_STORE, Graph, head, load_layers, ls_files, show  # noqa: E402

DECISIONS = os.environ.get("BRAIN_DECISIONS_LOG", "DECISIONS.md")     # paths inside the source project
STAGE_MAP = os.environ.get("BRAIN_STAGE_MAP", "plans/stage-map.yaml")


def build(ops_commit: str | None = None, logs_root: Path | None = None, brain_records: bool = True) -> tuple[Graph, dict]:
    """Defaults: the source at HEAD, logs from <source>/logs, plus the Brain's own records. The options pin inputs for a benchmark."""
    L = load_layers()
    g = Graph()
    ops_sha, brain_sha = ops_commit or head(SOURCE), head(BRAIN)
    logs = logs_root or (SOURCE / "logs")
    import_decisions.parse_log(L, g, show(SOURCE, ops_sha, DECISIONS), DECISIONS, ops_sha)
    same = SOURCE == BRAIN.resolve()
    stores = ((SOURCE, ops_sha, SOURCE_STORE),) + (((BRAIN, brain_sha, "brain"),) if brain_records and not same else ())
    for store_root, sha, store in stores:
        for p in ls_files(store_root, sha, "records"):
            if p.endswith((".yaml", ".yml")):
                import_decisions.parse_record(L, g, show(store_root, sha, p), p, sha, store)
    import_stagemap.parse(L, g, show(SOURCE, ops_sha, STAGE_MAP), STAGE_MAP, ops_sha)
    import_reviews.parse_rv_logs(L, g, sorted(logs.glob("rv*.log")))
    for p in sorted(logs.glob("merge-review-*.md")):
        import_reviews.parse_merge_review(L, g, p)
    g.resolve(L)
    kinds: dict[str, int] = {}
    for n in g.nodes.values():
        kinds[n["kind"]] = kinds.get(n["kind"], 0) + 1
    tiers: dict[str, int] = {}
    for x in g.links.values():
        tiers[x["tier"]] = tiers.get(x["tier"], 0) + 1
    reasons: dict[str, int] = {}
    for u in g.unresolved:
        k = f"{u['field']}: {u['reason']}"
        reasons[k] = reasons.get(k, 0) + 1
    summary = dict(ops_commit=ops_sha, brain_commit=brain_sha, nodes=len(g.nodes), links=len(g.links),
                   nodes_by_kind=dict(sorted(kinds.items())), links_by_tier=tiers, unresolved=len(g.unresolved),
                   unresolved_by_reason=dict(sorted(reasons.items())), pk_clashes=len(g.pk_clashes),
                   pk_clash_ids=sorted(set(g.pk_clashes))[:50], alias_clashes=len(g.alias_clashes),
                   alias_clash_examples={a: v for a, v in sorted(g.alias_clashes.items())[:30]}, counts=dict(sorted(g.counts.items())))
    return g, summary


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--ops-commit", help="read the source project's tracked files at this commit (default: HEAD)")
    ap.add_argument("--logs-root", type=Path, help="read review logs from this folder (default: <source>/logs)")
    ap.add_argument("--no-brain-records", action="store_true", help="skip this repo's own records (benchmark parity)")
    a = ap.parse_args()
    started = datetime.now(timezone.utc)
    g, summary = build(a.ops_commit, a.logs_root.expanduser() if a.logs_root else None, not a.no_brain_records)
    if a.logs_root or a.ops_commit or a.no_brain_records:
        summary["inputs"] = dict(ops_commit=summary["ops_commit"], logs_root=str(a.logs_root or ""), brain_records=not a.no_brain_records)
    OUT.mkdir(exist_ok=True)
    (OUT / "import-summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False))
    (OUT / "unresolved.json").write_text(json.dumps(g.unresolved, indent=1, ensure_ascii=False))
    if not a.dry_run:
        import db
        db.publish(g, summary, started, summary["ops_commit"], summary["brain_commit"])
    print(json.dumps({k: summary[k] for k in ("nodes", "links", "nodes_by_kind", "links_by_tier", "unresolved", "pk_clashes", "alias_clashes")}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
