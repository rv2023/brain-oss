---
name: brain-planning
description: Use when writing or updating any plan, story, backlog entry, build brief, design doc, status report or goal file in a project that keeps Brain records, or when deciding where a new document or note should go.
---

# brain-planning: right format, right place, linked by ID

## Overview
A plan is a set of links, not a copy of other documents. Every document has one home, carries the IDs it is about, and points to records (see brain-records) for facts. REQUIRED: brain-records for any decision or value.

## Before writing anything
1. Search for what already exists: `grep -rl "<ID or topic>" plans records` and `plans/stage-map.yaml` in the project. Update the existing home instead of starting a new file.
2. Collect the IDs involved: stories, decisions, findings, design frames, repos/PRs.

## Where each kind of document goes
The folder names come from `folders:` and `doc_folders:` in `layers.yaml` (that file wins if this table disagrees). Paths are relative to the project root. Write only into the folder of the kind you are producing; read anything you are allowed to read. `check.py records` fails a record with an ID that sits outside its kind's folder.

| Kind | Path | Name |
|---|---|---|
| Story list and stage | `plans/stage-map.yaml` | row per ID |
| Story acceptance criteria | `records/stories/<ID>.md` | one per story |
| Plan | `plans/YYYY-MM-DD-<topic>.md` | dated |
| Design note (the design itself stays in the design tool; the note holds file key + node id) | `designs/YYYY-MM-DD-<topic>.md` | dated |
| Audit or review report written by an agent | `audits/YYYY-MM-DD-<scope>.md`; each finding also as `records/findings/FND-NNNN.yaml` | dated |
| Research note (METHOD.md template) | `research/YYYY-MM-DD-<question>.md` in the repo it is about | dated |
| Agent build brief | `briefs/<agent>-<repo>-<ID>-<slug>.md` | one per job |
| Review result | `logs/` (written by the review job) | never hand-edited |
| Goal file | `plans/YYYY-MM-DD-<goal>-goal.md` | generated list + "Done means" |
| Private (claude-only) plans, notes, records | the claude-only store | never in a folder other agents read |

## Every plan, story or brief has this header
```
Project: example                     # a project from layers.yaml projects
IDs: STORY-104, FIX-006              # what it is about
Decisions: DEC-0001, DEC-0002        # facts it relies on (values come from the records)
Design: <file key> <node id>         # frames to match, or "needs frame"
Depends on: STORY-102
Repo / path: example-org/app -> src/export.py
```
Body: what and why in plain words; acceptance criteria only in the story record; values only as `see DEC-NNNN`.

## Estimates on every story
Every story record carries the required fields from `layers.yaml` `fields.story`, including:
- `estimate_hours_p50` and `estimate_hours_p90` (typical and bad-case hours, build + reviews), `size` (xs–xl; xl means split it), `lane_kind`, `owner_kind`, `risk` (money stories always `money`).
- `estimate_cost_usd_p50` and `estimate_cost_usd_p90`: what the story's runs will cost (agent hours at the rate card + model use at list price + review runs). Never invent a rate: take rates and prices from their records (point to their IDs).
- `estimate_basis`: the IDs of the similar finished stories you compared with, or `size_band` when there is no history.
- Base estimates on actuals of similar past stories (`actual_hours` and `review_rounds`); say which ones you compared with. No history? Use the size band and mark `estimate_basis: size_band`.
- `done_means` lists checks that can be verified (PR merged, reviews clean, deployed, named tests passing), never "works well".
Actual start, finish, hours, review rounds and cost are recorded automatically from run events (never typed); the estimate error (actual − estimate) is computed, so estimate accuracy is measured.

## Build briefs
State the TASK, the worktree as an ABSOLUTE path (under a root listed in `brief_check.worktree_roots`), the branch and base, then the header above. Copy acceptance criteria from the story record at queue time; never paste decided values — name the DEC and the setting.
Before queuing, run `python3 check.py brief <brief>`: errors (no Project line, no ID, no absolute worktree, any private path or private ID) should refuse the job; typed decided values are warnings.

## Audits
Audit any design against a written list of the owner's questions: every question marked answered / partial / gap with evidence. Brief reviewers without your conclusions; use separate viewpoints (user, data model, operations, cost, privacy); repeat until two rounds find nothing new; add every new owner catch to the questions list.

## Red flags
- A new plan file for a topic that already has a home.
- Story text pasted into a plan instead of its ID.
- A brief with a relative worktree path, no IDs, or a typed decided value.
- Private (claude-only) content in an open folder or another agent's brief.
- A plan, story or brief with no `Project:` line, or mixing projects whose privacy differs (split it).
