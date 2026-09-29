# SSM Milestone 2: real population and mechanism check

Date: 30 September 2026

Branch: `codex/ssm-m2-mechanism`

Status: **completed on the frozen real 50-stock population; mechanism rejected by
the registered stop rule; no selector race; no baseline, production, management, or
dashboard change**

## Outcome

Same-Slot Micro-Momentum does not proceed to Milestone 3. The real-data run found a
small positive average same-slot rank correlation across lags 1 through 40, but it
failed the two specificity checks that matter most:

- lag-1 same-slot continuation was negative (`-0.001242`); and
- the mean same-slot coefficient (`0.002756`) was weaker than the next-adjacent-slot
  placebo (`0.003416`).

The same-slot average did beat the 95th percentile of 128 candidate-shuffled controls
(`0.001260`, empirical `p = 0.00775`). That is evidence of weak serial structure, but
not evidence that the registered *same half-hour* mechanism is responsible. Because
the adjacent half-hour was stronger, fitting selectors now would search noise after a
failed mechanism test.

The lag-1 top-bucket gross return was only `0.00000881` in decimal-return units, or
about **0.088 basis points**, and its cross-sectional gross excess was about **0.284
basis points**, both before slippage and transaction charges. This is not a credible
quick-profit edge.

## Frozen population

The run reused the exact audited source behind the canonical 50-stock baseline:

- source dataset: `ec539cf66bea4c18ba994506f85a52d6`;
- universe audit: `154c62bc439347a2ae68c3e1bb7d915d`;
- 50 stocks, 20 warm-up sessions, and 120 evaluation sessions;
- 11 registered half-hour starts per stock/session;
- 66,000 planned stock/slot decisions;
- 65,989 eligible decisions; and
- 11 explicit exclusions from the one stock/session pair already rejected by the
  frozen universe audit.

No session disappeared because it produced an inconvenient result. The decision file
contains eligible and excluded coordinates together, with decision time, entry time,
history count, candidate-excluded peer count, and realized slot return kept as an
outcome diagnostic rather than a predictor.

## Registered controls and gates

`tradedesk_lab/ssm_mechanism.py` freezes and enforces:

- exact session lags 1 through 40;
- both previous- and next-adjacent-half-hour placebos;
- 128 within-session/slot candidate-label shuffles with seed `20260929`;
- at least 31 candidates per cross-sectional group;
- at least 60% positive lag coefficients;
- a descriptive lag-mean t-statistic of at least 1.96;
- empirical shuffled-control probability no greater than 5%; and
- positive lag-1 upper-tail gross excess.

All gates must pass together. Six focused Milestone-2 tests verify exact half-hour
construction, complete coordinate accounting, explicit exclusions, peer/history
minimums, real same-slot detection against both controls, artifact freezing, and
tracked evidence integrity. Together with Milestones 0 and 1, the SSM suite contains
25 tests.

## Gate result

| Gate | Result |
|---|---:|
| Lag-1 same-slot coefficient positive | **Fail** |
| Mean lag-1..40 coefficient positive | Pass |
| At least 60% positive lags | Pass (`70%`) |
| Lag-mean t-statistic at least 1.96 | Pass (`4.373`) |
| Beat both adjacent-slot placebos | **Fail** |
| Beat shuffled 95th percentile | Pass |
| Shuffled empirical p no greater than 0.05 | Pass (`0.00775`) |
| Lag-1 top-bucket gross excess positive | Pass, but economically tiny |

## Artifacts and safety boundary

The machine-readable tracked result is
[`docs/evidence/ssm-mechanism.json`](evidence/ssm-mechanism.json). The complete local
run is `data/m14_m18/ssm/mechanism/runs/6f0ee07df9c64019a951c209e4c4d320/`
and fingerprints the 66,000-row decisions file, lag diagnostics, shuffled controls,
source exclusions, implementation, tests, source dataset, and frozen contracts.

Only `tradedesk_lab`, `tests_lab`, and research documentation changed. SSM remains
unimported by production scanner, call, dashboard, management, risk, broker, alert,
and order paths.

## Decision

The canonical AEM v1 baseline remains **149 strict wins from 693 resolved fills =
21.50%**. SSM is rejected and parked at Milestone 2. Milestones 3 through 5 are not
authorized for SSM because running them after the failed stop gate would increase
selection bias without credible evidence of higher accuracy.
