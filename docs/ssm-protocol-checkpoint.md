# SSM Milestone 0: evidence-backed protocol freeze

Date: 29 September 2026

Branch: `codex/ssm-m0-protocol`

Status: **published hypothesis and bounded accuracy protocol frozen; not implemented
or evaluated; no baseline, live, dashboard, or management change**

## Outcome

The next research family is Same-Slot Micro-Momentum (SSM). It is not another AEM
breakout or OSR reclaim variation. It tests whether a stock's return during a given
half-hour persists during the same half-hour on later sessions.

This mechanism has direct NSE evidence. Murphy and Thirumalai report that half-hour
returns predict same-half-hour returns on later days and attribute the relation to
institutions splitting parent orders over multiple sessions ([Journal of Financial
Research](https://onlinelibrary.wiley.com/doi/10.1111/jfir.12131)). Heston,
Korajczyk, and Sadka independently document the corresponding cross-sectional
periodicity at exact daily multiples for as many as 40 sessions ([Journal of
Finance](https://onlinelibrary.wiley.com/doi/10.1111/j.1540-6261.2010.01573.x)).

That evidence makes SSM worth a bounded test; it does not establish a current tradable
edge. The NSE source study had order-level trader identities, while this project has
OHLCV candles. Costs, market evolution, and quote-versus-trade-price effects could
remove the published return pattern.

## What is frozen

- eleven non-overlapping 30-minute slots beginning at 09:45 and ending at 15:15;
- a prediction fixed one minute before the slot, with the current slot forbidden from
  its own features;
- `lag1_cross_sectional` and `multi_day_persistence` predictor modes;
- 40 prior sessions maximum, 20 complete sessions minimum, and at least 30 valid
  candidate-excluded peers;
- 24 decision-time features covering same-slot history, ranks, persistence, volume,
  pre-slot context, liquidity, impact, and costs;
- three 30-minute quick-profit geometries from 0.20% to 0.30% targets;
- a transparent rank gate and a regularized logistic challenger;
- exactly 12 specifications, with top-one/two/three policies as reporting views; and
- the unchanged 50% observed accuracy, 40% Wilson lower bound, sample, coverage,
  after-cost expectancy, matched-random, stress, and concentration gates.

The smaller-target geometries cannot pass through hit rate alone. They must be
positive after costs and beat a control with identical session and time-slot counts.
No more than 25% of wins may come from one stock or 35% from one slot.

## Verification and evidence

The contract validates its exact feature, slot, predictor, geometry, selector, and
trial registries; rejects outcome leakage and weakened sample/concentration gates;
fingerprints every frozen component; and writes a research-only artifact. Focused
contract tests cover structural separation from AEM/OSR, causal feature timing,
fingerprint sensitivity, geometry constraints, fixed gates, and artifact safety.

Frozen run: `95fda73fa00e4594acfeae995452bdc1`.

Machine-readable evidence is in
[`docs/evidence/ssm-protocol.json`](evidence/ssm-protocol.json). The complete frozen
roadmap is in [`docs/plan-ssm-50pct-baseline.md`](plan-ssm-50pct-baseline.md).

## Safety boundary

Only the isolated lab contract, lab CLI, tests, and research documentation change.
Production scanners, calls, dashboard, management, risk, broker, alerts, and orders do
not import SSM. The unrelated Groww working files are not part of this checkpoint.

## Next checkpoint

Build the causal same-slot feature, event, and conservative outcome engine. Before any
model race, reproduce the underlying periodicity with lag-1 through lag-40 tests and
adjacent-slot/shuffled placebos. If the mechanism is absent or smaller than costs, stop
SSM before spending another trial budget.
