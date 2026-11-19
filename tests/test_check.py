"""Tests for check.py: each rule's pass and fail case, on temp dirs. Run: python3 -m pytest -q tests"""
from __future__ import annotations

import copy
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

BRAIN = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BRAIN))
import check  # noqa: E402

REAL = yaml.safe_load((BRAIN / "layers.yaml").read_text())


def run(argv, capsys):
    code = check.main(argv)
    return code, capsys.readouterr().out


@pytest.fixture
def env(tmp_path):
    """A layers.yaml copy whose stores and Codex-readable paths point into tmp_path."""
    d = copy.deepcopy(REAL)
    proj, brain, lab = tmp_path / "project" / "records", tmp_path / "brain" / "records", tmp_path / "private" / "records"
    for p in (proj, brain, lab):
        (p / "decisions").mkdir(parents=True)
    d["stores"] = {
        "example": {"path": str(proj), "location_store": "example", "decision_code": "DEC"},
        "brain": {"path": str(brain), "location_store": "brain", "decision_code": "BDEC"},
        "private": {"path": str(lab), "location_store": "private-notes", "decision_code": "PDEC", "privacy": "claude-only", "read_values": False},
    }
    d["codex_readable_paths"] = [str(tmp_path / "project")]
    d["project_rules"] = [{"path": str(brain), "project": ["example"]}]
    d["brief_check"]["worktree_roots"] = [str(tmp_path / "wt") + "/"]

    class E:
        root, layers = tmp_path, tmp_path / "layers.yaml"
        project_dir, brain_dir, lab_dir = proj, brain, lab

        def write_layers(self, data=None):
            self.layers.write_text(yaml.safe_dump(data or d, sort_keys=False))
            return ["--layers", str(self.layers)]

    e = E()
    e.data = d
    return e


def decision(ident="DEC-0001", **over):
    r = {"id": ident, "project": ["example"], "date": "2026-11-04", "decided_by": "owner", "question": "q?", "answer": "a",
         "values": {"app_monthly_budget_usd": 60}, "replaces": None, "affects": ["STORY-201"], "source": "owner",
         "location": {"store": "example", "path": f"records/decisions/{ident}.yaml"},
         "not_checkable_reason": "judgment"}
    r.update(over)
    return {k: v for k, v in r.items() if v != "__drop__"}


def put(dirpath: Path, rec: dict, name=None) -> Path:
    f = dirpath / "decisions" / (name or f"{rec['id']}.yaml")
    f.write_text(yaml.safe_dump(rec, sort_keys=False))
    return f


# ------------------------------------------------------------------ layers
def test_layers_real_file_passes(capsys):
    code, out = run(["layers"], capsys)
    assert code == 0, out


def test_layers_parse_error(env, capsys):
    env.layers.write_text("layers: [unclosed\n")
    code, out = run(["--layers", str(env.layers), "layers"], capsys)
    assert code == 1 and "layers.yaml" in out


@pytest.mark.parametrize("mutate,rule", [
    (lambda d: d["legacy_codes"].update({"STORY": {"pattern": "^X$", "layer": "plan", "note": "n", "example": "X"}}), "code-unique"),
    (lambda d: d["layers"]["plan"]["codes"].update({"BAD": "^(unclosed$"}), "layers.regex"),
    (lambda d: d["layers"]["plan"].pop("privacy"), "layers.privacy"),
    (lambda d: d["layers"]["plan"].pop("importer"), "layers.importer"),
    (lambda d: d["layers"]["plan"]["links"].append("goes_to: nowhere"), "link-target"),
    (lambda d: d["fields"].update({"widget": {"required": ["id"]}}), "field-kind"),
    (lambda d: d["fields"]["decision"].update({"requird": ["id"]}), "field-key"),
    (lambda d: d["legacy_codes"]["DEC_DATED"].pop("note"), "legacy-note"),
    (lambda d: d["legacy_codes"]["DEC_DATED"].update({"example": "nope"}), "legacy-example"),
    (lambda d: d["legacy_codes"]["T_OWNER"].pop("collides_with"), "layers.overlap"),
    (lambda d: d["record_kinds"]["story"].append("NOPE"), "record-kind"),
    (lambda d: d["project_rules"].append({"path": "/x", "project": ["nope"]}), "project-rule"),
])
def test_layers_rule_fails(env, capsys, mutate, rule):
    mutate(env.data)
    code, out = run(env.write_layers() + ["layers"], capsys)
    assert code == 1 and rule in out, out


# ------------------------------------------------------------------ records
def test_records_valid_decision_passes(env, capsys):
    put(env.project_dir, decision())
    code, out = run(env.write_layers() + ["records", str(env.project_dir)], capsys)
    assert code == 0, out


def test_records_required_field_missing(env, capsys):
    put(env.project_dir, decision(question="__drop__"))
    code, out = run(env.write_layers() + ["records", str(env.project_dir)], capsys)
    assert code == 1 and "required field question missing" in out


@pytest.mark.parametrize("field", ["project", "location"])
def test_records_project_and_location_required(env, capsys, field):
    put(env.project_dir, decision(**{field: "__drop__"}))
    code, out = run(env.write_layers() + ["records", str(env.project_dir)], capsys)
    assert code == 1 and f"required field {field} missing" in out


def test_records_checks_or_reason(env, capsys):
    put(env.project_dir, decision(not_checkable_reason="__drop__"))
    args = env.write_layers() + ["records", str(env.project_dir)]
    code, out = run(args, capsys)
    assert code == 1 and "checks or not_checkable_reason" in out
    put(env.project_dir, decision(not_checkable_reason="__drop__", checks=["setting(SET-x) == 60"]))
    assert run(args, capsys)[0] == 0


def test_records_unknown_project(env, capsys):
    put(env.project_dir, decision(project=["nope"]))
    code, out = run(env.write_layers() + ["records", str(env.project_dir)], capsys)
    assert code == 1 and "unknown project nope" in out


def test_records_id_must_match_a_code(env, capsys):
    put(env.project_dir, decision("XYZ-1"), name="x.yaml")
    code, out = run(env.write_layers() + ["records", str(env.project_dir)], capsys)
    assert code == 1 and "records.id-code" in out


def test_records_legacy_id_accepted(env, capsys):
    put(env.project_dir, decision("DEC-20250315-PRICING", location={"store": "example", "path": "records/decisions/DEC-20250315-PRICING.yaml"}))
    code, out = run(env.write_layers() + ["records", str(env.project_dir)], capsys)
    assert code == 0, out


def test_records_ambiguous_legacy_id_warns(env, capsys):
    put(env.project_dir, decision("T-12", location={"store": "example", "path": "records/decisions/T-12.yaml"}))
    code, out = run(env.write_layers() + ["records", str(env.project_dir)], capsys)
    assert code == 2 and "id-ambiguous" in out


def test_records_duplicate_id_across_stores(env, capsys):
    put(env.project_dir, decision("DEC-0001"))
    put(env.brain_dir, decision("DEC-0001"))
    code, out = run(env.write_layers() + ["records", str(env.project_dir), str(env.brain_dir)], capsys)
    assert code == 1 and "duplicate-id" in out


def test_records_decision_code_in_wrong_store(env, capsys):
    put(env.brain_dir, decision("DEC-0001", project=["example"]))
    code, out = run(env.write_layers() + ["records", str(env.brain_dir)], capsys)
    assert code == 1 and "store-code" in out
    (env.brain_dir / "decisions" / "DEC-0001.yaml").unlink()
    put(env.brain_dir, decision("BDEC-0001", project=["example"], location={"store": "brain", "path": "records/decisions/BDEC-0001.yaml"}))
    assert run(env.write_layers() + ["records", str(env.brain_dir)], capsys)[0] == 0


def test_records_auto_field_hand_typed(env, capsys):
    put(env.project_dir, decision(check_results=["ok"]))
    code, out = run(env.write_layers() + ["records", str(env.project_dir)], capsys)
    assert code == 1 and "auto-field" in out


def test_records_auto_field_ok_under_importer_path(env, capsys):
    d = env.project_dir / "imported"
    (d / "decisions").mkdir(parents=True)
    put(d, decision(check_results=["ok"], location={"store": "example", "path": "records/imported/decisions/DEC-0001.yaml"}))
    code, out = run(env.write_layers() + ["records", str(d)], capsys)
    assert code == 0, out


def test_records_privacy_claude_only_in_codex_path(env, capsys):
    put(env.project_dir, decision(project=["private-notes"]))
    code, out = run(env.write_layers() + ["records", str(env.project_dir)], capsys)
    assert code == 1 and "records.privacy" in out


def test_records_privacy_claude_only_outside_codex_path(env, capsys):
    put(env.lab_dir, decision("PDEC-0001", project=["private-notes"], location={"store": "private-notes", "path": "records/decisions/PDEC-0001.yaml"}))
    code, out = run(env.write_layers() + ["records", str(env.lab_dir)], capsys)
    assert code == 0, out


def test_records_lab_path_in_codex_file_warns(env, capsys):
    put(env.project_dir, decision(affects=["STORY-201"], source="see private-notes notes"))
    code, out = run(env.write_layers() + ["records", str(env.project_dir)], capsys)
    assert code == 2 and "lab-path" in out


def test_records_estimate_needs_basis(env, capsys):
    f = env.project_dir / "lessons" / "L-0001.yaml"
    f.parent.mkdir()
    rec = {"id": "L-0001", "project": ["example"], "lesson": "x", "taught_by": "FND-0001", "applies_to": ["app"], "enforced_by": ["brain-records"],
           "location": {"store": "example", "path": "records/lessons/L-0001.yaml"}, "estimate_hours_p50": 2}
    f.write_text(yaml.safe_dump(rec))
    args = env.write_layers() + ["records", str(env.project_dir)]
    code, out = run(args, capsys)
    assert code == 1 and "estimate-basis" in out
    rec["estimate_basis"] = "size_band"
    f.write_text(yaml.safe_dump(rec))
    assert run(args, capsys)[0] == 0


def test_records_twelve_digit_number_not_printed(env, capsys):
    secret = "1234" + "5678" + "9012"
    put(env.project_dir, decision(source=f"account {secret}"))
    code, out = run(env.write_layers() + ["records", str(env.project_dir)], capsys)
    assert code == 1 and "account-number" in out and secret not in out


def test_records_affects_not_an_id_warns(env, capsys):
    put(env.project_dir, decision(affects=["STORY hosting"]))
    code, out = run(env.write_layers() + ["records", str(env.project_dir)], capsys)
    assert code == 2 and "affects-id" in out


def test_records_yaml_error(env, capsys):
    (env.project_dir / "decisions" / "DEC-0009.yaml").write_text("id: DEC-0009\nanswer: wait: for it\n")
    code, out = run(env.write_layers() + ["records", str(env.project_dir)], capsys)
    assert code == 1 and "does not parse" in out


def test_records_plain_markdown_skipped(env, capsys):
    (env.project_dir / "decisions" / "notes.md").write_text("# acceptance criteria\n- x\n")
    put(env.project_dir, decision())
    assert run(env.write_layers() + ["records", str(env.project_dir)], capsys)[0] == 0


# ------------------------------------------------------------------ brief
def brief_text(env, **drop):
    lines = {
        "project": "Project: example",
        "ids": "IDs: STORY-740",
        "wt": f"TASK: worktree {env.root}/wt/story-740, branch feat/x from origin/main.",
        "body": "Budget: see DEC-0001 (setting app_monthly_budget_usd).",
    }
    return "\n".join(v for k, v in lines.items() if k not in drop) + "\n"


def brief(env, capsys, text, name="codex-app-STORY-740-x.md"):
    put(env.project_dir, decision())
    f = env.root / name
    f.write_text(text)
    return run(env.write_layers() + ["brief", str(f)], capsys)


def test_brief_good(env, capsys):
    code, out = brief(env, capsys, brief_text(env))
    assert code == 0, out


@pytest.mark.parametrize("drop,rule", [("project", "brief.project"), ("ids", "brief.ids"), ("wt", "brief.worktree")])
def test_brief_header_rules(env, capsys, drop, rule):
    text = brief_text(env, **{drop: True})
    if drop == "ids":
        text = text.replace("DEC-0001", "the decision")
    code, out = brief(env, capsys, text)
    assert code == 1 and rule in out, out


def test_brief_relative_worktree_fails(env, capsys):
    text = brief_text(env, wt=True) + "TASK: worktree codex-wt/story-740\n"
    code, out = brief(env, capsys, text)
    assert code == 1 and "brief.worktree" in out


def test_brief_template_missing_header_warns(env, capsys):
    code, out = brief(env, capsys, "Review the PR in this worktree.\n", name="codex-rvfix-example-org_app__12-r1.md")
    assert code == 2 and "template brief" in out


def test_brief_lab_path_fails(env, capsys):
    code, out = brief(env, capsys, brief_text(env) + "Read ~/private-notes/BACKLOG.md\n")
    assert code == 1 and "brief.lab-path" in out


def test_brief_lab_id_fails_even_for_templates(env, capsys):
    code, out = brief(env, capsys, "Check PRIV-run-flow and PDEC-0004\n", name="codex-rv1-x.md")
    assert code == 1 and out.count("brief.lab-id") == 2


def test_brief_typed_decided_value_warns(env, capsys):
    code, out = brief(env, capsys, brief_text(env) + "The app monthly budget is $60.\n")
    assert code == 2 and "typed-value" in out and "DEC-0001" in out


def test_brief_number_without_topic_words_ok(env, capsys):
    code, out = brief(env, capsys, brief_text(env) + "Run 60 tests on the app.\n")
    assert code == 0, out


# ------------------------------------------------------------------ fix --safe
def test_fix_fills_project_from_rule(env, capsys):
    rec = decision("BDEC-0001", location={"store": "brain", "path": "records/decisions/BDEC-0001.yaml"})
    rec.pop("project")
    f = put(env.brain_dir, rec)
    before = f.read_text()
    code, out = run(env.write_layers() + ["fix", "--safe", str(env.brain_dir)], capsys)
    assert code == 0 and "added project: [example]" in out
    after = f.read_text()
    assert after.replace("project: [example]\n", "") == before
    assert yaml.safe_load(after)["values"] == {"app_monthly_budget_usd": 60}      # money untouched
    assert run(env.write_layers() + ["records", str(env.brain_dir)], capsys)[0] == 0


def test_fix_no_rule_leaves_file(env, capsys):
    rec = decision()
    rec.pop("project")
    f = put(env.project_dir, rec)
    before = f.read_text()
    code, out = run(env.write_layers() + ["fix", "--safe", str(env.project_dir)], capsys)
    assert code == 2 and "fix.project" in out and f.read_text() == before


def _story(ident, **extra):
    r = {"id": ident, "project": ["example"], "title": "t", "estimate_hours_p50": 2, "estimate_cost_usd_p50": 12.28}
    r.update(extra)
    return r


def test_fix_estimate_basis_size_band_without_history(env, capsys):
    d = env.project_dir / "stories"
    d.mkdir()
    f = d / "STORY-740.yaml"
    f.write_text(yaml.safe_dump(_story("STORY-740"), sort_keys=False))
    code, out = run(env.write_layers() + ["fix", "--safe", str(env.project_dir)], capsys)
    data = yaml.safe_load(f.read_text())
    assert "estimate_basis: size_band" in out and data["estimate_basis"] == "size_band"
    assert data["estimate_cost_usd_p50"] == 12.28


def test_fix_estimate_basis_not_filled_with_history(env, capsys):
    d = env.project_dir / "stories"
    d.mkdir()
    (d / "STORY-700.yaml").write_text(yaml.safe_dump(_story("STORY-700", estimate_basis="size_band", actual_hours=3)))
    f = d / "STORY-740.yaml"
    f.write_text(yaml.safe_dump(_story("STORY-740")))
    before = f.read_text()
    code, out = run(env.write_layers() + ["fix", "--safe", str(env.project_dir)], capsys)
    assert "fix.estimate-basis" in out and f.read_text() == before


def test_fix_requires_safe(env, capsys):
    assert run(env.write_layers() + ["fix", str(env.project_dir)], capsys)[0] == 1


# ------------------------------------------------------------------ real files
def test_real_layers_cli():
    r = subprocess.run([sys.executable, str(BRAIN / "check.py"), "layers"], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr


@pytest.mark.parametrize("store", [BRAIN / "example" / "records"])
def test_real_records_run_cleanly(store):
    """Real stores: the check must run without crashing; problems it reports are listed in the hand-back, not hidden here."""
    if not store.is_dir():
        pytest.skip(f"{store} not on this machine")
    r = subprocess.run([sys.executable, str(BRAIN / "check.py"), "records", str(store)], capture_output=True, text=True)
    assert r.returncode in (0, 1, 2) and "Traceback" not in r.stderr, r.stderr


def test_usage_error_is_exit_3_not_warning():
    r = subprocess.run([sys.executable, str(BRAIN / "check.py"), "bogus"], capture_output=True, text=True)
    assert r.returncode == 3


# ------------------------------------------------------------------ folders (each kind in its own folder)
def test_records_wrong_folder_is_error(env, capsys):
    (env.project_dir / "stories").mkdir()
    f = env.project_dir / "stories" / "DEC-0001.yaml"
    f.write_text(yaml.safe_dump(decision(location={"store": "example", "path": "records/stories/DEC-0001.yaml"}), sort_keys=False))
    code, out = run(env.write_layers() + ["records", str(env.project_dir)], capsys)
    assert code == 1 and "records.folder" in out and "records/decisions/" in out


def test_records_loose_in_store_root_is_error(env, capsys):
    f = env.project_dir / "DEC-0001.yaml"
    f.write_text(yaml.safe_dump(decision(location={"store": "example", "path": "records/DEC-0001.yaml"}), sort_keys=False))
    code, out = run(env.write_layers() + ["records", str(env.project_dir)], capsys)
    assert code == 1 and "records.folder" in out


def test_layers_folders_cover_every_record_kind():
    kinds = set(REAL["record_kinds"])
    assert kinds <= set(REAL["folders"]), kinds - set(REAL["folders"])
