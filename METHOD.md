# How we do research here (the method)

Every research note follows this method. Notes live in a `research/` folder of the repository they are about.

## 1. Start from an observed problem, with a number
Name the pain with a measured example before proposing anything. Example: "one decided value is typed by hand into
~20 files, and two different values are live at once." No measurement means it is a guess, and the note says so.

## 2. State a hypothesis that can fail
Write what we expect and what result would prove us wrong. Example: "Asking Brain for a story's neighbourhood uses
≤3,000 tokens per lookup, ≥20× fewer than reading the decisions file. Wrong if the median lookup is >6,000 tokens."

## 3. Measure the baseline first, with a control
Run the current way (or a no-guidance agent) before building the new way. Skills are tested the same way: a fresh agent
without the skill (control) and with it (treatment), same task. Without a baseline, an improvement cannot be claimed.

## 4. Fix the metric and the adoption bar before the run
Write down, before any result is seen: the metric (precision, recall, nDCG@10, recall@20, tokens per step, cost per
1,000 calls, catch rate), the data split, and the bar to adopt (e.g. "small model adopted only if within 1 point of the
frontier model on the held-out set at ≤10% of the cost"). Changing the bar after seeing results is not allowed; a
changed bar starts a new experiment.

## 5. Hold out data honestly
Split by time (train on older changes, test on newer ones) and keep a frozen test set no one tunes on. Build graphs for
a test case only from information that existed before that change (no leakage from the future). Report the size of
every set.

## 6. Independent reviewers, then adjudication
At least two independent reviewers, from different model families, look at plans and results without seeing each
other's answers; their agreement and disagreement are recorded side by side; where they disagree the claim is tested,
not voted on. Reviewers' measured accuracy (bug seeding) decides how much weight each gets.

## 7. Prefer the simplest method that meets the bar
Order of trying: plain rules and code → off-the-shelf models → tuned small models → new model types. A complex method
must beat the simple one on the same held-out set to be kept.

## 8. Report uncertainty, not just averages
Give ranges or confidence intervals, sample sizes, and failures. Use methods that give guarantees where possible
(conformal prediction for coverage; capture–recapture for "how many are still unfound"). Say what the result does not
show.

## 9. Make it reproducible
Each note lists the exact command or script, the data snapshot (commit SHA), the model and version, and the date.
Another agent must be able to re-run it and get the same numbers.

## 10. Privacy and ethics
Private (claude-only) material stays in its own store. Customer data is used only with consent (default off). Every
export is scanned for secrets and account numbers. Each experiment has a compute cap.

## 11. Record the outcome either way
A negative result is written up like a positive one (what was tried, the numbers, why it was dropped). Decisions that
follow from research become records (see the brain-records skill) that cite the research note.

## 12. Evidence capture
Every metric run, baselines included, is kept as evidence, so a claim can be checked later.
- One immutable folder per run: `evidence/<YYYY-MM-DDTHHMMZ>-<metric>/` (UTC). Never overwritten or edited; a
  correction is a new run.
- Each folder holds: `hypothesis.md` (the hypothesis and bar copied from the research note, with the commit that added
  the note before any run), `command.txt` (the exact command), `inputs.json` (repo + commit SHA for every git input,
  file hashes for untracked inputs), `environment.json` (OS, Python, library, Postgres and image versions/digests),
  `raw/` (every sample, not only the summary), `summary.json`, and `manifest.json`.
- `manifest.json`: sha256 of every file, the metric, result vs bar, sample size, commits, `public_safe` and `sequence`
  (1, 2, … in write order) and `previous_manifest_sha256` (the sha256 of the previous run's manifest by sequence): a
  hash chain, so history cannot be edited silently.
- Baselines (today's approach: tokens to read whole files, stale-copy counts) are their own runs, so before/after
  comparisons can be re-run and checked.
- `evidence/INDEX.md` (generated): date, metric, result vs bar, sample size, run folder, commit, public_safe.
- Privacy: no secrets, no 12-digit numbers, no private (claude-only) content; the writer refuses them. Every run starts
  `public_safe: false`; only runs a person marks safe are quoted publicly, and the mark is itself a new run (metric
  `public-safe`, naming the earlier run folder); old manifests are never edited.
- Enforced: `python3 check.py evidence` verifies every hash, that no file is unlisted, the chain, `public_safe` present,
  and the privacy scans. Writer: `local/evidence.py`.

## Note template
```
# Research note: <question> (YYYY-MM-DD)
Problem (measured):
Hypothesis (and what would prove it wrong):
Baseline / control:
Metric, data split, adoption bar (fixed before running):
Method and command:
Results (numbers, ranges, sample sizes):
Reviewers: agreement and disagreement:
Conclusion and decision record:
Limits and next measurement:
```
