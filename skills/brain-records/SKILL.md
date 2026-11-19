---
name: brain-records
description: Use when recording, changing or referring to any decision, setting value, story fact, finding or rule in a project that keeps Brain records — including when the owner answers a question, changes their mind, or a value (days, dollars, limits, roles) must reach builds, briefs, docs or code.
---

# brain-records: write a fact once, point to it everywhere else

## Overview
Every fact lives in exactly ONE record with an ID. Everything else points to the ID. A decided value typed into a second place is a bug, because copies go stale (a typical case: one retention period typed by hand into ~20 files, with two different values live at once).

## Where records live
Stores are listed under `stores:` in `layers.yaml` (in the Brain repository). Each store owns its own decision ID prefix so IDs never clash: the project store uses `DEC-`, Brain's own records use `BDEC-`, a private (claude-only) store uses `PDEC-`.

Folders per kind come from `folders:` in `layers.yaml`; a record outside its kind's folder fails `check.py records` (`records.folder`).

| Kind | Path (inside the store) | ID |
|---|---|---|
| Owner decision | `decisions/DEC-NNNN.yaml` | `DEC-NNNN` (next free number: highest + 1) |
| Setting value | `settings/<setting_name>.yaml` | the setting name |
| Finding | `findings/FND-NNNN.yaml` | `FND-NNNN` (older ID shapes listed under `legacy_codes` stay valid; never reuse them for new findings) |
| Story | `plans/stage-map.yaml` (row) + `stories/<ID>.md` for acceptance criteria | `STORY-NNN`, `FIX-NNN`, … |
| Lesson / gotcha | `lessons/L-NNNN.yaml`: `lesson`, `taught_by` (finding, incident or owner correction ID), `applies_to`, `enforced_by` (skill name or check; empty = flagged) | `L-NNNN` |
| Incident / retro | `incidents/I-NNNN.yaml` linking findings, fixes and the lessons it produced | `I-NNNN` |

If the project also keeps a running decisions log (`DECISIONS.md`), add one line there per new record: `- DEC-NNNN (date): <one-line summary> -> records/decisions/DEC-NNNN.yaml`.

## ID codes
Every ID uses a code from `layers.yaml`. Never invent a prefix: to add a new kind, add a custom layer there first (`custom: true`, codes, allowed links, privacy). Older ID shapes already in use (dated `DEC-20250315-…`, `T-NN`, `F-NNN`, `hop:…`) are listed under `legacy_codes`; new records use the canonical code.

After writing or changing a record, run `python3 check.py records <store>/records` and fix every ERROR (exit 0 ok, 1 errors, 2 warnings only).

## Project and privacy
Every record names its `project` (one from `layers.yaml` `projects`; several allowed). Its privacy is the STRICTEST of its project's and its layer's: a decision in a claude-only project is claude-only even though decisions are normally open. Anything you derive from it (summary, brief, plan line, log, export) inherits that privacy — so a claude-only record's content never goes into a folder other agents read; there you write only its ID.

## Required fields
Each record kind's required, optional and auto fields are in `layers.yaml` under `fields`. Fill every required field when you create a record; never write `auto` fields by hand (importers do). Unknown? Write `unknown` and add it to the record's `questions:` list — never guess a number.

## A decision record
```yaml
id: DEC-0123
project: [example]
date: 2025-01-20
decided_by: owner            # owner | coordinator
question: How long are exported rows kept?
answer: 30 days; the nightly cleanup removes older rows
values: {export_retention_days: 30}
replaces: DEC-0087           # the record it overrides, or null
affects: [STORY-104, FIX-006]   # your best list; Brain also computes it
source: "planning meeting 2025-01-20"
checks:                      # testable rules a daily check can run for free (forms in layers.yaml checks_syntax)
  - setting(SET-export_retention_days) == 30
  - text(docs/**) never "90 days".*export
# or, when no rule fits:  not_checkable_reason: "judgment about design quality"
location: {store: example, path: records/decisions/DEC-0123.yaml}
```
Changing a decision = a NEW record with `replaces:`. Never edit an old record's answer.

## How to refer to a fact anywhere else
Write the ID and the setting name, never the value:
- Brief / plan / backlog / doc: `Export retention: see DEC-0123 (setting export_retention_days).`
- Code: read the setting; its default comes from the record. Tests reference the setting, not a literal.
- Acceptance criteria may show the value once, as `{DEC-0123.values.export_retention_days}`; a generator fills it in.

## When a decision changes
1. Write the new record with `replaces:`.
2. Find every place that typed the OLD value next to the same topic (grep the topic words AND the value together, e.g. `export.*(90|30)`), never a blind replace of the number: "30 days" can also mean backups or closed accounts.
3. In hand-written files, replace the typed value with the pointer. Generated files and review logs are history or outputs: do not edit them; list them under "regenerate" in your report.
4. Also check design-tool text and cost or sizing estimates that used the old value; list them for the designer or the owner instead of guessing.
5. Prove the sweep: run a final search for topic + old value across plans, briefs, records and the stage map, and paste the count of remaining hits, each marked "pointer fixed", "generated: regenerate", or "history: leave". An unproven sweep is not done.
6. Report: the record ID, files changed to pointers, generated files left to regenerate, design/cost items flagged, and the final search result.

## Red flags — stop and use a record
- About to type a decided number, price, limit or role list into a brief, plan or doc.
- About to run a global search-and-replace on a number.
- "I'll just add it to the decisions file as a paragraph."
- Editing a review log or generated diagram to "fix" a value.
- A new decision with no ID, or with no `replaces:` when it overrides an older one.
- A record with no `project`, or claude-only content copied into an open file.
- A decision with neither `checks:` nor `not_checkable_reason:`.
- An estimate (hours or cost) with no `estimate_basis`, or an actual (hours, cost) typed by hand.

## Privacy
Private facts go in the claude-only store (`stores:` in `layers.yaml`), never in a folder other agents read. Never put secrets, tokens or 12-digit account numbers in a record.
