"""Tests for the local Brain: importers on small synthetic fixtures, pack determinism, evidence chain. No database needed.

Fixtures in fixtures/ are made up for the tests (a decisions log, a stage map, two review rounds of app#12, a review
with no findings, and a merge review of web#10). Run: python3 -m pytest -q
"""
from __future__ import annotations

import json
import random
import shutil
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
FIX = HERE / "fixtures"
sys.path.insert(0, str(HERE.parent / "local"))
sys.path.insert(0, str(HERE.parent))

import check  # noqa: E402
import evidence  # noqa: E402
import import_decisions  # noqa: E402
import import_reviews  # noqa: E402
import import_stagemap  # noqa: E402
from brainlib import Graph, load_layers, redact  # noqa: E402

L = load_layers()


def test_decisions_split_one_record_per_entry_with_line_and_aliases():
    g = Graph()
    import_decisions.parse_log(L, g, (FIX / "decisions-snippet.md").read_text(), "DECISIONS.md", "abc123")
    entries = [n for n in g.nodes.values() if n["kind"] == "decision"]
    text = (FIX / "decisions-snippet.md").read_text().splitlines()
    body_lines = [ln for ln in text if ln.strip() and not ln.startswith("#")]
    assert len(entries) == len(body_lines)
    for n in entries:                       # original text kept, location points at the exact line
        assert text[n["location"]["line"] - 1].endswith(n["body"]) or n["body"] in text[n["location"]["line"] - 1]
        assert n["location"]["commit"] == "abc123"
    t1 = next(n for n in entries if n["meta"]["label"] == "T-1")
    assert "T-1" in t1["aliases"] and "DEC-20250115-T1" in t1["aliases"] and t1["meta"]["date"] == "2025-01-15"
    first = next(n for n in entries if n["location"]["line"] == 4)
    assert first["id"] == "DEC-20250110-E1" and first["meta"]["date_from"] == "carried from an earlier heading"   # the file title heading


def test_index_line_links_record_to_entry_and_mentions_become_affects():
    g = Graph()
    import_decisions.parse_log(L, g, (FIX / "decisions-snippet.md").read_text(), "D.md", "abc")
    rep = [r for r in g.refs if r["type"] == "replaces"]
    assert {r["raw"] for r in rep} >= {"DEC-0002", "DEC-0003"} and all(r["reverse"] for r in rep)
    g2 = Graph()
    import_decisions.parse_log(L, g2, "# Decisions log, 2025-01-10\n- O2.2 / STORY-797: yes, register the app (FIX-022).\n", "D.md", "c")
    assert {r["raw"] for r in g2.refs if r["type"] == "affects"} == {"STORY-797", "FIX-022"}
    assert g2.nodes["DEC-20250110-E1"]["aliases"] == ["DEC-20250110-O2-2"]      # O2.2 fits no bare code; dated alias only


def test_private_entries_are_skipped():
    g = Graph()
    import_decisions.parse_log(L, g, "# Decisions log, 2025-01-10\n\n## X (2025-01-12)\n- read ~/private-notes/notes for this\n- plain one\n", "D.md", "c")
    assert g.counts["decision_entries_lab_skipped"] == 1 and g.counts["decision_entries"] == 1


def test_stage_map_rows_links_and_private_rows():
    items = (FIX / "stagemap-items.yaml").read_text()
    lab_row = "- id: PRIV-k9\n  title: private row\n  kind: private\n  stage: after-3\n  why: private\n  source: private-notes/BACKLOG.md\n"
    dep_on_lab = "- id: STORY-999\n  title: t\n  kind: story\n  stage: '1'\n  why: w\n  source: s\n  depends_on:\n  - PRIV-k9\n  - STORY-106\n"
    text = "items:\n" + items + lab_row + dep_on_lab
    g = Graph()
    import_stagemap.parse(L, g, text, "plans/stage-map.yaml", "sha1")
    assert "PRIV-k9" not in g.nodes and g.counts["stage_rows_lab_skipped"] == 1 and g.counts["stage_links_to_lab_skipped"] == 1
    assert g.nodes["STORY-104"]["location"]["line"] == 2
    deps = {(r["src"], r["raw"], r["type"]) for r in g.refs}
    assert ("STORY-104", "STORY-102", "depends_on") in deps and ("STORY-104", "O4", "decided_by") in deps
    assert ("STORY-104", "hop:signup:J1:02", "closes") in deps
    g.resolve(L)
    assert ("STORY-999", "STORY-106", "depends_on") in g.links
    assert any(u["raw_id"] == "DEC-20250110-28" and u["reason"] == "no item with this ID imported" for u in g.unresolved)


def test_review_rounds_findings_outcomes_and_story(tmp_path):
    logs = tmp_path / "logs"
    logs.mkdir()
    for f in FIX.glob("rv*.log"):
        shutil.copy(f, logs / f.name)
    g = Graph()
    import_reviews.parse_rv_logs(L, g, sorted(logs.glob("rv*.log")))
    r1 = [n for n in g.nodes.values() if n["id"].startswith("FND-app-12-r1-")]
    assert len(r1) == 4
    assert all(L.match(n["id"]) for n in r1)                     # FND_REVIEW legacy code
    by_n = {n["id"]: n["meta"]["outcome"] for n in r1}
    assert by_n["FND-app-12-r1-2"] == "fixed"                     # docs/settings.md:40 not repeated in round 2
    assert by_n["FND-app-12-r1-1"] == "still_open"                # src/export.py:120 repeated near line 130
    assert set(by_n.values()) <= {"fixed", "still_open"}
    assert g.nodes["FND-app-12-r2-1"]["meta"]["outcome"] == "unknown"   # no round 3 in the fixture
    assert any(r["type"] == "delivers" and r["raw"] == "FIX-006" and r["tier"] == "exact" for r in g.refs)
    assert all(r["tier"] == "structural" for r in g.refs if r["type"] in ("about", "reviews"))
    assert g.nodes["RV-app-12-r1"]["location"]["file_hash"].startswith("sha256:")
    assert g.nodes["RV-web-10-r1"]["meta"]["status"] == "ok" and not any(n["id"].startswith("FND-web-10-") for n in g.nodes.values())


def test_merge_review_delta_table_gives_outcome(tmp_path):
    g = Graph()
    import_reviews.parse_merge_review(L, g, FIX / "merge-review-web-10.md")
    f = [n for n in g.nodes.values() if n["kind"] == "finding"]
    assert len(f) == 2 and all(n["meta"]["outcome"] == "fixed" for n in f)
    assert any(r["type"] == "delivers" and r["raw"] == "STORY-106" for r in g.refs)


def test_redact_removes_twelve_digit_numbers():
    fake = "".join(["1234", "5678", "9012"])            # built from fragments (no real number in the repo)
    out, n = redact(f"account {fake} here")
    assert n == 1 and fake not in out


# ------------------------------------------------------------------ pack determinism
def _rows():
    loc = {"store": "example", "path": "plans/stage-map.yaml", "commit": "c0ffee1234"}
    return [{"id": f"STORY-{100 + i}", "kind": "story", "dir": "up" if i % 2 else "down", "type": "depends_on" if i % 3 else "closes",
             "tier": "exact" if i % 4 else "structural", "depth": 1 + (i % 2), "via": "STORY-106", "title": f"Story {i}, with, commas",
             "location": {**loc, "line": i * 7}} for i in range(60)]


def test_pack_is_deterministic_and_counts_omitted():
    import bridge
    centre = {"id": "STORY-106", "kind": "story", "layer": "plan", "project": ["example"], "title": "Costs page", "body": "x" * 1500,
              "meta": {"stage": "1"}, "location": {"store": "example", "path": "plans/stage-map.yaml", "line": 91, "commit": "c0ffee1234"},
              "aliases": []}
    rows = _rows()
    a = bridge.build_pack(centre, rows, 40, "both", 2)
    for seed in range(5):
        shuffled = rows[:]
        random.Random(seed).shuffle(shuffled)
        assert bridge.build_pack(centre, shuffled, 40, "both", 2) == a
    decoded = bridge.unfold(a)
    assert len(decoded["neighbours"]) == 40
    assert decoded["omitted"].startswith("20 more")
    assert decoded["centre"]["body_rest"].startswith("301 more characters")
    assert all(r["at"].startswith("example/plans/stage-map.yaml") for r in decoded["neighbours"])


# ------------------------------------------------------------------ evidence chain
def test_evidence_runs_chain_and_tamper_detection(tmp_path, capsys):
    from datetime import datetime, timezone
    root = tmp_path / "evidence"
    root.mkdir()
    t0 = datetime(2026, 11, 4, 12, 0, tzinfo=timezone.utc)
    r1 = evidence.write_run("baseline-x", {"summary.json": "{}", "raw/a.csv": "a,b\n1,2\n"}, {"result": "r", "bar": "b"}, t0, root)
    r2 = evidence.write_run("metric-y", {"summary.json": '{"n": 1}'}, {}, t0.replace(minute=1), root)
    m2 = json.loads((r2 / "manifest.json").read_text())
    assert m2["previous_manifest_sha256"] == evidence.sha256_bytes((r1 / "manifest.json").read_bytes())
    assert m2["public_safe"] is False
    with pytest.raises(FileExistsError):
        evidence.write_run("metric-y", {"summary.json": "{}"}, {}, t0.replace(minute=1), root)
    with pytest.raises(ValueError):
        evidence.write_run("metric-z", {"x.txt": "see ~/private-notes/x"}, {}, t0.replace(minute=2), root)
    evidence.write_run("a-same-minute", {"summary.json": "{}"}, {}, t0.replace(minute=1), root)   # sorts before r2 by name
    assert [p.name for p in evidence.runs(root)][-1].endswith("a-same-minute")
    assert check.main(["evidence", str(root)]) == 0
    (r1 / "raw" / "a.csv").write_text("a,b\n1,3\n")                     # edit a raw file
    assert check.main(["evidence", str(root)]) == 1
    out = capsys.readouterr().out
    assert "evidence.hash" in out
    (r1 / "raw" / "a.csv").write_text("a,b\n1,2\n")
    (r1 / "manifest.json").write_text((r1 / "manifest.json").read_text().replace('"r"', '"R"'))   # rewrite history
    assert check.main(["evidence", str(root)]) == 1
    assert "evidence.chain" in capsys.readouterr().out


def test_every_pack_states_coverage_and_what_is_not_imported(tmp_path, monkeypatch):
    import json as _json
    import bridge
    _unfold = bridge.unfold
    summary = tmp_path / "import-summary.json"
    summary.write_text(_json.dumps({"ops_commit": "abcdef1234567", "nodes_by_kind": {"decision": 3, "story": 2, "review": 1},
                                    "unresolved": 7}))
    monkeypatch.setattr(bridge, "SUMMARY", summary)
    bridge.coverage.cache_clear()
    cov = _unfold(bridge.pack({"pack": "lookup X", "found": "no"}))["coverage"]
    assert "abcdef1" in cov["sources"] and cov["unresolved_ids"] == 7
    assert "design" in cov["not_imported"] and "goal" in cov["not_imported"]
    assert "decision" not in cov["not_imported"]
    assert cov["note"].startswith("Only these sources")


def test_coverage_unknown_when_no_import_summary(tmp_path, monkeypatch):
    import bridge
    _unfold = bridge.unfold
    monkeypatch.setattr(bridge, "SUMMARY", tmp_path / "missing.json")
    bridge.coverage.cache_clear()
    cov = _unfold(bridge.pack({"pack": "lookup X", "found": "no"}))["coverage"]
    assert cov["sources"] == "unknown" and "not complete" in cov["note"]
