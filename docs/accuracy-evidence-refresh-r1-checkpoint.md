# Accuracy evidence refresh R1 — first-look locks and scheduled evaluation

Implemented: 4 October 2026

Status: **implemented and verified; all three markets collecting; no baseline change**

## Why this is the next accuracy checkpoint

NSE, BSE and crypto already have frozen experiments, but they do not yet have enough fresh
evidence for a valid result. Opening another retrospective search would reuse consumed
outcomes and increase false discoveries. R1 instead closes two statistical and operational
gaps: every scheduled market run refreshes its own evidence, and a mature experiment is
decided once at its first registered look rather than rerun until it eventually passes.

Evidence remains fully separate across NSE, BSE and crypto. A missing run, failed refresh,
zero-call session or unavailable metric is never converted into a pass.

## Implemented safeguards

### NSE

- M8–M11 still collect the unchanged frozen trend-pullback challenger.
- The first fully mature atomic qualification result is saved exclusively in
  `terminal-first-look.json`.
- Only `human_review_authorized` or `prospective_rejected` can be terminal.
- Later samples cannot reverse that first decision. A changed hypothesis must start a new
  future-only cohort.
- The terminal envelope verifies its canonical report hash, exact version, market, mature
  denominators, parity and hard-false baseline/live authority before reuse.
- Collecting and degraded states remain recomputable and create no terminal lock.

### BSE

- The unchanged B1 five-series experiment is refreshed after a successful
  `research_tracker.py run --market bse` execution.
- Derived evaluator failure is isolated and cannot fail the established tracker.
- A first `research_qualified` or `rejected` result is hash-stamped and cannot be
  overwritten by a later, larger sample.
- Scheduled state lives under `data/m14_m18/bse_accuracy_quick_profit/state.json`; the
  committed development snapshot in `docs/evidence/` remains immutable.

### Crypto

- The daily crypto tracker now runs one ordered C1-to-C2 refresh after recording the
  point-in-time universe and completing the established tracker/timing work.
- C2 is skipped unless C1 explicitly reports `source_integrity.passed=true`.
- Refresh failures do not erase the durable universe observation or stop the base tracker;
  the next scheduled run can retry.
- C2 already freezes the first 30-session window and returns its verified terminal result
  after a pass or rejection, so no second terminal mechanism race is opened.

## Dashboard

The independent NSE, BSE and crypto cards now show a separate **First-look decision**
field. `collecting · not latched` is distinct from `latched · terminal`; neither wording
grants live or promotion authority. Unavailable percentages remain
`not available — not a pass`.

## Real refresh on implementation day

| Market | State | Fresh readiness | First look | Baseline/live |
|---|---|---:|---|---|
| NSE | `collecting_insufficient_evidence` | 0/4 components ready | not latched | false / false |
| BSE | `collecting` | 0/30 sessions; 0/4 rules ready | not latched | false / false |
| Crypto | `collecting_c1_point_in_time_history` | 0/12 trials evaluated | not latched | false / false |

The crypto C1 source-integrity refresh succeeded against all 337 required pairs, but they
still have only one of 30 required point-in-time sessions. No accuracy percentage is
available from any new prospective cohort.

## Verification

- 122 focused tracker, dashboard, NSE, BSE, crypto and geometry tests passed.
- Scoped Ruff, compilation and Git whitespace checks passed.
- Real NSE, BSE and crypto refreshes completed with every baseline/live authority flag
  false.

## Next decision

Do not add a new selector while these registered cohorts are immature. Let the scheduled
runs collect fresh sessions. At each first mature look, accept only the already-frozen
accuracy, Wilson-confidence, session-consistency, after-cost, stress and matched-control
gates. A rejection closes that version; it does not authorize retuning on the same cohort.
