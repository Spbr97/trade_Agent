# AEM v2 Milestone 4, Stage 2: the bounded pipeline race

*Update, 27 September 2026: run for real against the full 177,351-row development
dataset. See "Real run, 27 September 2026" below for the result and two hard-veto bugs
found and fixed along the way. The rest of this document is the original, point-in-time
synthetic-only checkpoint - left unchanged as a historical record.*

Date: 26 September 2026

Branch: `codex/aem-v2-accuracy-first`

Status: **pipeline race implemented and tested on synthetic data; not yet run against
the real development dataset (Stage 1's real run is still in progress); no accuracy
result exists; no live change**

## Outcome

This completes the code for Milestone 4's first three checklist items - registering
the bounded (24) pipeline specifications, running a nested chronological walk-forward
evaluation, and checking every candidate against the frozen Milestone-0 development
gates - but has not yet been pointed at real data, because Stage 1's real run (see the
[development-dataset checkpoint](aem-v2-development-dataset-checkpoint.md)) has not
finished. Everything in this checkpoint is verified against synthetic data only.

## What is implemented

`tradedesk_lab/aem_v2_pipeline_race.py`:

- **Exactly 24 registered pipeline specifications**: 4 entry-mode groupings
  (`anticipatory_impulse`, `confirmed_pullback`, `breakout_retest`, `pooled`) x 3
  quick-profit geometries x 2 models (the Precision Ladder, the logistic control) -
  `registered_pipeline_specs()` asserts this never exceeds the frozen protocol's own
  24-trial budget.
- **Nested chronological evaluation**: the OUTER split reuses
  `tradedesk_lab.validation.walk_forward` unmodified (the same chronological, purged,
  embargoed splitter M14's own CPCV/PBO work already relies on) rather than
  re-deriving a split routine; the INNER split, for the Precision Ladder, reuses
  Milestone 3's `fit_oof_calibration` unmodified. Neither model ever sees a label for a
  row it is later scored on.
- **The hard veto layer** (Milestone 3) is applied before any candidate becomes
  eligible for scoring or for the "unfiltered baseline" comparator - vetoed candidates
  never enter either population.
- **Three required controls per spec**: the unfiltered opportunity-generator baseline
  (every eligible candidate, no ranking), the matched-random-timing control (same
  per-session count as the model would select, chosen at random instead of ranked),
  and the logistic control (as one of the 24 specs' own model choices). The AEM v1
  canonical baseline is referenced as a constant, not re-run.
- **The frozen development gates** (`aem_v2_contract.DEFAULT_AEM_V2_PROTOCOL`, reused
  directly rather than redefined): resolved-fill floor, active-session floor, coverage
  floor, 50% strict-success floor, 40%-and-above-baseline Wilson lower bound, positive
  mean net R, and a required advantage over the matched-random control, plus a
  disclosed single-symbol concentration proxy the frozen protocol doesn't itself
  number.
- Reports every one of the 24 trials, including a spec that had no matching rows or
  too few sessions for a valid walk-forward split - nothing is silently omitted from
  the published curve.

**Explicitly deferred**: the frozen protocol's mandatory execution stresses (cost
x1.25/x1.5, slippage x2, one-bar delay, adversarial missed fills) are not evaluated by
this module. This mirrors AEM v1's own staging - a baseline scorecard first, registered
stress gates as a separate later checkpoint - rather than silently dropping the
requirement. A candidate that clears every gate here is only *nominated*: it still
needs a stress pass, and Milestone 5's independent locked evaluation, before it could
be called a research baseline.

## Verification

Eight tests, all against synthetic data (the real dataset does not exist yet): the
Wilson-lower-bound formula is checked against the exact known AEM v1 canonical value
(149/693 -> 18.60350...%) as a cross-check against an already-established calculation
elsewhere in the project; every registered-gate failure condition is triggered
individually and confirmed to report a specific reason; the mode/geometry filter
(including `"pooled"`) is verified; the matched-random control's per-session
deduplication and count cap are verified; a full 24-spec race runs end to end on a
1,620-row informative synthetic dataset and every trial reports the required shape; and
- the most direct behavioral check - the Precision Ladder shows a real, measured net-R
advantage over its own matched-random control on a synthetic signal it was never told
the shape of.

`uv run --no-sync ruff check tradedesk_lab/aem_v2_pipeline_race.py
tests_lab/test_aem_v2_pipeline_race.py` is clean, as is the full repository suite.

Machine-readable evidence is in
[`docs/evidence/aem-v2-pipeline-race.json`](evidence/aem-v2-pipeline-race.json),
honestly marked `real_run_completed: false`.

## Safety boundary

The module reads only an already-frozen CSV (Stage 1's `events.csv`) and exists only in
`tradedesk_lab`. It is not imported by production scanning, ranking, alerts,
management, risk, dashboard or order code. `select_calls`'s `portfolio_filter` hook
(Milestone 3) is still not wired to the real risk manager here either - a real
production integration decision remains out of scope for this entire milestone.

## Next checkpoint (superseded - see below)

Once Stage 1's real run finishes: run
`uv run --no-sync python -m tradedesk_lab aem-v2-pipeline-race` against it and publish
the actual 24-trial accuracy/availability/economics curve - the first checkpoint that
can honestly report a real AEM v2 accuracy number, whatever it turns out to be. If any
specification clears every development gate, it is a nomination, not a baseline: the
mandatory stress gates and Milestone 5's independent locked evaluation on unconsumed
data come next, in that order, before AEM v2 could be called a 50% research baseline.

## Real run, 27 September 2026

Stage 1's real run finished after ~7 hours (5,999 symbol-sessions, 66,668 opportunities,
177,351 resolved (opportunity, geometry) rows). Running the pipeline race against it
immediately surfaced a real problem, not a real result: **only 1 of 177,351 rows survived
Milestone 3's hard-veto layer.**

### Two bugs found and fixed

1. **`entry_requires_excess_chase` (99.8% of rows vetoed) - tautological, removed.**
   `aem_v2_events.py::opportunities_at` computes every opportunity's `entry_limit` as
   `ceil_tick(intended_entry * (1 + maximum_chase_pct))`, unconditionally. The veto then
   compared the resulting `limit_distance` back against that exact same
   `maximum_chase_pct` with a strict `>` - a near-tautology, since `limit_distance` is
   mathematically pinned to `maximum_chase_pct` plus tick-rounding noise for every single
   row, regardless of anything real about the opportunity. It fired on 99.8% of rows and
   added no discriminating signal. Real chase rejection already happens correctly, at fill
   time, in `resolve_opportunity`'s own `chase_rejected` outcome, using the REALIZED fill
   price - the only point this can be meaningfully evaluated. Removed entirely.
2. **`no_remaining_causal_price_room` (99.3% of rows vetoed) - applied to the wrong modes,
   now gated to `anticipatory_impulse` only.** `causal_level_distance` measures distance
   to a backward-looking recent high. For `anticipatory_impulse`, that is the literal
   entry thesis (price is still below the level, anticipating a break through it - a
   positive reading is genuine "room before the level"). For `breakout_retest` and
   `confirmed_pullback`, the opportunity only arms AFTER that same level is already
   broken (both require a completed close above it first - see `opportunities_at`), so
   the identical feature reads negative or near-zero for them BY CONSTRUCTION, not
   because the trade lacks room to run. There is no currently-registered feature that
   correctly measures "room to the next level beyond an already-broken one," so the fix
   is to not apply this specific check to those two modes, rather than guess at a
   replacement formula.

Neither bug was caught by Milestone 3's own tests, because every existing test injects
hand-picked synthetic feature values directly into `hard_veto_reasons()` - none ever ran
a real `compute_features()` output through it. This is the same class of gap the
Milestone-2 timezone bug slipped through earlier in this project: a real bug that can
only surface once real, multi-session data actually flows through the full pipeline.
`hard_veto_reasons()` also gained a required `mode` parameter as part of this fix.

After the fix, the same real dataset shows **168,494/177,351 rows (95.0%) clean** - a
plausible, non-degenerate rate, confirmed before spending any more real-run compute.
Since the bug was entirely in Milestone 3's veto logic (consumed only at Stage 2 time),
Stage 1's own dataset needed no re-run - only Stage 2 was re-run, against the same real
`events.csv`.

### The real, trustworthy result

All 24 registered specifications were evaluated against a real, non-degenerate eligible
population (13,649-40,610 eligible candidates for the three dominant modes;
`anticipatory_impulse` stayed small at 4-55, since it is genuinely rare in the real data -
390 of 177,351 rows). **No specification cleared every frozen development gate.**
`qualified_candidates: []`.

- The unfiltered baseline strict-success rate for the three dominant modes
  (`confirmed_pullback`, `breakout_retest`, `pooled`) ranges 19.0%-24.5% - roughly in
  line with the AEM v1 canonical baseline (21.50%) - but mean net R is negative for every
  one of them (-0.35R to -0.52R, unfiltered).
- The Precision Ladder's own calibrated selector is conservative: its 50%
  absolute-probability threshold is rarely cleared, so it often selects zero candidates
  at all for a given spec.
- The logistic control does select candidates, but their mean net R is deeply negative
  (-0.39R to -1.31R) with strict-success rates of 20-50% on small, unstable samples.

This is now a genuine, trustworthy negative result, not a bug artifact - the same honest
outcome this project has found everywhere else it has looked (the three live rule-based
setups, the daily mean-reversion exit search, crypto momentum continuation): no
structurally different signal shape tried so far shows real, cost-surviving edge on this
population. **AEM v1 remains the canonical baseline (149/693 = 21.50%, 18.60% Wilson
lower bound, -0.27471R mean net R). No live change. Nothing is nominated for the
mandatory stress gates**, since nothing cleared the development gate that precedes them.

Full evidence: [`docs/evidence/aem-v2-pipeline-race.json`](evidence/aem-v2-pipeline-race.json)
(`real_run_completed: true`), regression tests
`test_no_remaining_causal_price_room_only_applies_to_anticipatory_impulse` and
`test_entry_requires_excess_chase_veto_was_removed_as_a_tautology` in
`tests_lab/test_aem_v2_precision_ladder.py`.

## Next checkpoint (current)

Milestone 4 is complete with a real, negative result. The two open paths from here are:
(a) Milestone 5's independent locked evaluation is moot with zero nominated candidates,
so there is nothing to lock-evaluate yet; (b) a genuinely different signal shape (not a
parameter variant of what was just tried) would need to be hypothesized, built and run
through this same real pipeline before AEM v2 could plausibly beat 21.50%.
