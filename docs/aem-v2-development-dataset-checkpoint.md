# AEM v2 Milestone 4, Stage 1: development-dataset assembly (real run complete)

Date: 27 September 2026

Branch: `codex/aem-v2-accuracy-first`

Status: **assembly code implemented and tested; the real frozen 50-stock/120-session
run is complete; this dataset alone is not an accuracy result; no live change**

## Outcome

This is the first place in the project that runs Milestones 1 (causal event/outcome
engine), 2 (causal feature registry) and the Milestone 2 universe audit's frozen
inclusion decision together, against real data, to produce one labeled row per
(opportunity, quick-profit geometry). It is still not an accuracy result: a labeled
dataset is not a fitted, validated selector. Stage 2 of Milestone 4 subsequently ran the
bounded pipeline race and nested chronological walk-forward evaluation; no registered
specification cleared its development gate.

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

Run `ed9fa509541741cca668bbb4a1eeb380` completed against 5,999 included
symbol-sessions. It found 66,668 opportunities and produced 177,351 resolved
(opportunity, geometry) rows. Another 14,995 events were retained as exclusions:
10,233 unfilled, 3,829 feature-computation failures and 933 unresolved. Mode counts
were 116,694 breakout-retest rows, 60,267 confirmed-pullback rows and only 390
anticipatory-impulse rows.

The pooled raw label rate was 22.659%. This is the unselected label frequency across
all opportunity-by-geometry rows, not selector accuracy, and the repeated geometries
mean it must not be presented as 177,351 independent trades. The subsequent real
pipeline race is the relevant model result: all 24 specifications ran and none
qualified. Machine-readable dataset evidence is in
[`docs/evidence/aem-v2-development-dataset.json`](evidence/aem-v2-development-dataset.json).

## Safety boundary

The module is read-only against the production DuckDB and exists only in
`tradedesk_lab`. It is not imported by production scanning, ranking, alerts,
management, risk, dashboard or order code.

## Next checkpoint

Stage 2 is complete with a real negative result, recorded in
[`aem-v2-pipeline-race-checkpoint.md`](aem-v2-pipeline-race-checkpoint.md). With no
nominated specification, there is no candidate for stress replay or independent locked
evaluation. Any next experiment must register a genuinely different causal signal
family, and previously inspected history remains development-only evidence.
