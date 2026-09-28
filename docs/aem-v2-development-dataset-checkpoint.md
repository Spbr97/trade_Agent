# AEM v2 Milestone 4, Stage 1: development-dataset assembly (real run in progress)

Date: 26 September 2026

Branch: `codex/aem-v2-accuracy-first`

Status: **assembly code implemented and tested; a real run against the frozen
50-stock/120-session cohort is in progress in the background; no accuracy result yet;
no live change**

## Outcome

This is the first place in the project that runs Milestones 1 (causal event/outcome
engine), 2 (causal feature registry) and the Milestone 2 universe audit's frozen
inclusion decision together, against real data, to produce one labeled row per
(opportunity, quick-profit geometry). It is still not an accuracy result: a labeled
dataset is not a fitted, validated selector. That is Stage 2 of Milestone 4 (the bounded
pipeline race and nested chronological walk-forward evaluation), not yet built.

## A real bug this surfaced and fixed

Every Milestone 1-3 test used synthetic fixtures built with one consistent timezone
backend (`pd.date_range(tz="Asia/Kolkata")`, which resolves through `pytz`). The real
staged data pipeline (`aem_data.py`) instead uses `zoneinfo.ZoneInfo("Asia/Kolkata")`.
Both correctly represent the same real-world timezone, but when
`aem_v2_features.compute_features` concatenated a `zoneinfo`-backed
`intraday_history` frame with a `pytz`-backed `bars` frame (produced internally by
`_validated_bars`'s own `.tz_convert(IST)`, where `IST` is the plain string
`"Asia/Kolkata"`), `pandas.concat` silently degraded the combined index to `object`
dtype, which then crashed `time_of_day_rvol`'s own datetime parsing. No synthetic test
caught this because every synthetic fixture happened to use the same backend on both
sides of that exact `concat`.

Fixed by normalizing `intraday_history`'s index onto the exact same tz object as `bars`
before concatenating (`tradedesk_lab/aem_v2_features.py`, inside `compute_features`).
No feature formula, contract, or existing test assertion changed - this is a pure
correctness fix, and the Milestone 2 feature evidence file's `implementation_sha256`
has been updated to the corrected file's hash. All seven existing feature tests still
pass unchanged.

## What is implemented

`tradedesk_lab/aem_v2_development_experiment.py`:

- Loads the exact frozen universe-audit result (`included_pairs.csv`) and the same
  staged source it audited, verifying every fingerprint still matches before assembling
  anything.
- For each included (symbol, session): reconstructs opportunities (Milestone 1),
  computes one feature vector per opportunity using every OTHER included symbol at the
  same session as the peer universe and every earlier included session of that same
  symbol as the relative-volume history (Milestone 2), then resolves each opportunity
  under all three frozen quick-profit geometries (Milestone 1) to get a strict-success
  label and net R.
- Sizing for accuracy determination uses a simple fixed 0.5%-of-capital risk per trade
  (matching CLAUDE.md's stated breakeven reference), not the portfolio-constrained
  replay a later stage could add if this result is promising enough to warrant it.
- Every excluded opportunity (unfilled, unresolved, or a feature-computation failure
  such as an undersized peer universe) is recorded with its specific reason - nothing
  is silently dropped.
- Freezes `events.csv` (the labeled rows), `excluded_events.json` and a `report.json`
  summary, wired into the CLI as `tradedesk_lab aem-v2-development-dataset`.

## Verification

Ten tests: two pure-logic unit tests (fixed-risk sizing, session-slicing), and four
orchestration tests using a **real, previously-verified anticipatory-impulse pattern**
(not a mocked outcome) embedded in a small synthetic 6-session, 6-symbol environment -
proving the assembly actually finds and resolves a genuine opportunity end-to-end,
correctly excludes it with a specific reason when the peer universe is undersized,
rejects a source that no longer matches its pinned manifest, and freezes all three
output files correctly.

`uv run --no-sync ruff check tradedesk_lab/aem_v2_development_experiment.py
tests_lab/test_aem_v2_development_experiment.py` is clean, as is the full repository
suite.

## The real run

Profiling on the real frozen data (not the synthetic tests) showed roughly 2.3 seconds
per included symbol-session, split roughly evenly between Milestone 1's own per-minute
opportunity search and the same-time relative-volume calculation - neither added by
this stage. Across 5,999 included symbol-sessions that is a genuinely multi-hour
computation, launched as a background job
(`uv run --no-sync python -m tradedesk_lab aem-v2-development-dataset`) rather than run
inline. Machine-readable evidence for the **code** is in
[`docs/evidence/aem-v2-development-dataset.json`](evidence/aem-v2-development-dataset.json)
(`real_run_completed: false`); it will be updated with the real row counts, label rate
and exclusion breakdown once that run finishes - this checkpoint does not claim a
result it does not yet have.

## Safety boundary

The module is read-only against the production DuckDB and exists only in
`tradedesk_lab`. It is not imported by production scanning, ranking, alerts,
management, risk, dashboard or order code.

## Next checkpoint

Once the real run completes: publish the actual dataset statistics (rows, label rate,
mode/geometry breakdown, exclusion reasons) as a follow-up to this checkpoint, then
build Stage 2 - register the bounded (≤24) pipeline specification race and run the
first real nested chronological walk-forward evaluation, reusing
`tradedesk_lab.validation.walk_forward` for the outer chronological splits and
Milestone 3's `fit_oof_calibration` for the inner fold-local fit. That is the first
point an actual AEM v2 accuracy number can exist.
