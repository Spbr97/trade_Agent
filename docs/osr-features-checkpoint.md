# OSR Milestone 2: causal features and real development dataset

Date: 29 September 2026

Branch: `codex/osr-m2-real-dataset`

Status: **30-feature layer implemented; real 5,999-symbol-session development dataset
frozen and integrity-checked; no selector result; no baseline, live, or dashboard change**

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

The combined OSR engine, feature, and synthetic end-to-end dataset suite passes 34
test cases. It covers future candidate/peer mutation, self-exclusion from peers, daily
and intraday causality, context/contract mismatches, real event-to-feature-to-outcome
assembly, insufficient peers, explicit exclusions, stable empty output, and artifact
freezing. Lint is clean.

Machine-readable evidence is in
[`docs/evidence/osr-features.json`](evidence/osr-features.json). It is explicitly marked
`real_run_completed: true`, `algorithm_evaluated: false`, and
`baseline_improved: false`.

## Real development population

Run `2ece3a469de84a3aad74f8afa9192f59` completed against the fingerprint-verified
source dataset `ec539cf66bea4c18ba994506f85a52d6` and universe audit
`154c62bc439347a2ae68c3e1bb7d915d`.

- 5,999 audited stock-sessions were scanned across the complete frozen cohort;
- 2,697 causal opportunities were found;
- 2,441 opportunities produced at least one resolved geometry;
- 7,303 opportunity/geometry rows cover all 120 sessions and 49 stocks;
- the dataset has 39 columns, zero null cells, and zero duplicate
  opportunity/geometry identities;
- 788 geometry outcomes were retained as exclusions: 720 unfilled and 68 unresolved;
- 1,564 rows were strict successes and 5,739 were failures, for a raw 21.42% label
  prevalence; and
- the unselected population's after-cost mean net R is -0.529.

The three raw geometry rates are 21.39%, 22.91%, and 19.95%; their mean net R values
are -0.669, -0.515, and -0.404 respectively. Gap-down reclaims have a raw 24.39%
label rate, while opening-low sweep reclaims have 20.08%. Every unfiltered slice is
negative expectancy. These are development-population diagnostics, not model accuracy
and not a baseline improvement.

Artifact integrity is frozen by SHA-256 in the machine-readable evidence: the event
CSV, complete exclusion ledger, report, source dataset, universe audit, strategy
contract, feature code, and test code are all identified.

## Safety boundary

Only `tradedesk_lab`, `tests_lab`, and documentation are changed. No production setup,
scanner, dashboard, manager, risk, broker, alert, or order path imports this work.

## Next checkpoint

Run the frozen bounded 12-specification chronological walk-forward selector race over
this development population. Nominate nothing unless a candidate clears every gate
together: at least 50% strict success, at least a 40% Wilson lower bound, at least 100
fills over 30 sessions, at least 40% session coverage, positive after-cost mean net R,
at least +0.10R versus matched random timing, and no more than 25% of wins from one
symbol. A failed race rejects OSR rather than changing the canonical 21.50% baseline.
