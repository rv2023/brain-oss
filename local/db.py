"""Database access for the local Brain (psycopg 3). DSN from BRAIN_DSN (default: the local compose database)."""
from __future__ import annotations

import os
from pathlib import Path

DSN = os.environ.get("BRAIN_DSN", "postgresql://brain:%s@127.0.0.1:55444/brain" % os.environ.get("BRAIN_DB_PASSWORD", "brain-local"))
SCHEMA = Path(__file__).resolve().parent / "schema.sql"


def connect():
    import psycopg
    return psycopg.connect(DSN)


def publish(g, counts: dict, started, ops_commit: str, brain_commit: str) -> None:
    """Replace everything in ONE transaction: a failure leaves the previous import untouched."""
    from datetime import datetime, timezone

    from psycopg.types.json import Jsonb
    with connect() as conn:
        with conn.transaction():
            conn.execute(SCHEMA.read_text())
            conn.execute("TRUNCATE links, unresolved, nodes")
            with conn.cursor() as cur:
                cur.executemany(
                    "INSERT INTO nodes (id, kind, layer, project, privacy, title, body, meta, location, aliases, source_commit, importer)"
                    " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    [(n["id"], n["kind"], n["layer"], n["project"], n["privacy"], n["title"], n["body"], Jsonb(n["meta"]),
                      Jsonb(n["location"]), n["aliases"], n.get("source_commit"), n["importer"]) for n in g.nodes.values()])
                cur.executemany(
                    "INSERT INTO links (src, dst, type, tier, evidence, confidence, read_at_commit, importer)"
                    " VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                    [(x["src"], x["dst"], x["type"], x["tier"], x["evidence"], x["confidence"], x["read_at_commit"], x["importer"])
                     for x in g.links.values()])
                cur.executemany("INSERT INTO unresolved (src, raw_id, field, importer, reason) VALUES (%s,%s,%s,%s,%s)",
                                [(u["src"], u["raw_id"], u["field"], u["importer"], u["reason"]) for u in g.unresolved])
                cur.execute("INSERT INTO import_runs (started_at, finished_at, ops_commit, brain_commit, counts) VALUES (%s,%s,%s,%s,%s)",
                            (started, datetime.now(timezone.utc), ops_commit, brain_commit, Jsonb(counts)))
