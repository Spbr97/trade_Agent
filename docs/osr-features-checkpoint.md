# OSR Milestone 2A: causal features and development-dataset assembly

Date: 29 September 2026

Branch: `codex/osr-m2-features`

Status: **30-feature layer and audited dataset assembly implemented and tested; real
5,999-symbol-session run pending; no accuracy result; no live or dashboard change**

## Accuracy purpose

This checkpoint does not raise the baseline. It creates the measurements needed to
reject weak opening reclaims rather than taking every mechanically valid pattern. The
30 frozen features quantify failed-auction depth and recovery, reclaim-bar quality,
VWAP context, time-of-day participation, candidate-excluded cross-sectional recovery,
daily context, liquidity, impact, and modeled costs.

The later selector still must demonstrate at least 50% strict success, a 40% Wilson
lower bound, useful session coverage, positive after-cost mean net R, and at least
+0.10R versus matched random timing. A feature layer passing tests is not predictive
improvement.

## What is implemented

`tradedesk_lab/osr_features.py`:

- computes exactly the 30 names frozen in the OSR contract;
- uses only completed candidate and peer M1 bars before the decision timestamp;
- excludes the candidate from peer ranks, breadth, and dispersion;
- requires at least five valid same-time peers;
- requires the opportunity's prior-close/prior-low context to match the prior completed
  daily bar;
- requires time-of-day relative-volume history strictly before the current session; and
- fails closed on stale, overlapping, missing, mismatched, undersized, or non-finite data.

`tradedesk_lab/osr_development_experiment.py`:

- reuses the fingerprint-verified 50-stock universe audit and staged candles rather
  than silently creating a different cohort;
- reconstructs OSR opportunities, computes the 30 features, and resolves all three
  geometries with the real cost model;
- retains every unfilled, unresolved, feature-failed, or data-failed exclusion;
- preserves a stable CSV schema even if the new signal finds no opportunities; and
- labels all existing history `consumed_historical_development` so it cannot certify a
  new baseline.

Full-session reconstruction gained a necessary-condition prefilter for performance.
The definitive event detector remains unchanged. A parameterized regression proves
the optimized result equals exhaustive minute-by-minute detection for both OSR modes
and a no-signal session.

## Verification

The combined OSR engine, feature, and synthetic end-to-end dataset suite passes 28
test cases. It covers future candidate/peer mutation, self-exclusion from peers, daily
and intraday causality, context/contract mismatches, real event-to-feature-to-outcome
assembly, insufficient peers, explicit exclusions, stable empty output, and artifact
freezing. Lint is clean.

Machine-readable evidence is in
[`docs/evidence/osr-features.json`](evidence/osr-features.json). It is explicitly marked
`real_run_completed: false` and `baseline_improved: false`.

## Safety boundary

Only `tradedesk_lab`, `tests_lab`, and documentation are changed. No production setup,
scanner, dashboard, manager, risk, broker, alert, or order path imports this work.

## Next checkpoint

Run and freeze the real audited 5,999-symbol-session OSR development population. Publish
opportunities, resolved rows, strict raw label rate, mode/geometry counts, and every
exclusion. That raw rate is still not selector accuracy. Only the subsequent bounded
12-specification chronological walk-forward race can nominate a candidate.
