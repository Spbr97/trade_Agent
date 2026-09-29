# SSM Milestone 1: causal feature, opportunity, and outcome engine

Date: 29 September 2026

Branch: `codex/ssm-m1-engine`

Status: **implemented and tested on complete synthetic stock/peer histories; no real
dataset or selector run; no accuracy result; no production or dashboard change**

## Outcome

The research-only SSM engine now constructs the exact pre-slot information frozen in
Milestone 0, issues transparent same-slot opportunities, and resolves the following
30-minute paper outcome conservatively. This is necessary infrastructure, not evidence
that SSM improves the 21.50% canonical AEM v1 baseline.

## Causal feature engine

`tradedesk_lab/ssm_engine.py` computes exactly the 24 registered features:

- lag-1/2/3 same-slot returns and mean returns over 5, 20, and up to 40 sessions;
- positive-return persistence over 5, 20, and up to 40 sessions;
- candidate-excluded lag-1/5/20 cross-sectional ranks and same-slot dispersion;
- historical same-slot volume and turnover;
- current-session return, rank, and breadth measured before prediction; and
- prior daily liquidity, current impact, and modeled round-trip costs.

The prediction timestamp is one minute before the registered half-hour. Entry is the
next M1 open at the half-hour boundary. Candidate, peer, and daily rows at or after the
causal cutoff are discarded before their values are validated, so corrupt or extreme
future bars cannot affect an already-issued call. At least 20 matching historical
slots and 30 valid candidate-excluded peers are mandatory.

## Transparent opportunities

The frozen `lag1_cross_sectional` mode requires a positive previous-session same-slot
return and an upper-decile candidate-excluded rank. `multi_day_persistence` requires a
positive 20-session same-slot mean, at least 65% positive matching slots, and an
upper-decile 20-session rank.

These rules create research opportunities, not promoted calls. A stock may satisfy
both modes because the later 12-specification race treats them as separately registered
hypotheses. Session ranking and the top-one/two/three cap remain later selector work.

## Conservative outcome engine

- fills at the next slot open with configured buy-side slippage and NSE tick rounding;
- zero entry volume is explicitly unfilled;
- stop/target geometry is derived from the actual slipped entry;
- same-bar target/stop ambiguity is a stop loss;
- adverse gaps exit at the opening price, while favorable target gaps are capped;
- configured Indian transaction charges are deducted from every resolved trade;
- a target is a strict success only when net P&L remains positive;
- positive time exits are reported separately and never relabeled as target wins; and
- missing entry or outcome bars remain unresolved and can never become successes.

## Verification

Thirteen focused engine tests use 40 complete historical sessions for a candidate and
31 independent peer stocks rather than injecting precomputed feature values. They
cover all 24 features, candidate exclusion, future candidate/peer/daily mutations,
history and peer minima, missing pre-decision bars, registered-slot enforcement,
post-prediction entry ordering, target/cost accounting, ambiguity, missing outcome
bars, zero-volume entries, time exits, post-slot invariance, and contract mismatches.

Together with the six Milestone-0 tests, the SSM suite contains 19 tests. Machine-
readable evidence is in
[`docs/evidence/ssm-engine.json`](evidence/ssm-engine.json), fingerprinting the strategy
contract, engine contract, implementation, and tests.

## Safety boundary

Only `tradedesk_lab`, `tests_lab`, and research documentation change. Production
scanner, call, dashboard, management, risk, broker, alert, and order modules do not
import SSM. The engine cannot issue a live call or place an order. Unrelated Groww
working files remain outside this checkpoint.

## Next checkpoint

Milestone 2 must assemble every eligible stock/slot decision on the frozen real
50-stock history and test the underlying same-slot continuation mechanism before any
selector is allowed to run. Lag-1 through lag-40 same-slot estimates must beat
adjacent-slot and shuffled controls; otherwise SSM stops before another model race.
