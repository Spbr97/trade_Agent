# Self-learning Milestone 8 checkpoint — paired exit contracts

Completed: 2026-10-06

New NSE, BSE and crypto candidates are now logged twice from the same watchlist entry:

- `quick-profit-v1`: 0.75R target, three-session entry validity and three held sessions;
- `swing-v1`: 2R target, three-session entry validity and ten held sessions.

Each copy has its own immutable signal ID, contract hash and outcome lifecycle. Existing
legacy rows are preserved unchanged. The deterministic resolver applies the same actual
entry and stop logic, conservative stop-first same-bar rule and market cost model to both.

## Paired comparison

The exit race accepts a pair only when market, symbol, setup, arming session, entry, stop,
source and evidence class match exactly and both outcomes are mature valid calls. Missing,
pending, invalid and geometry-mismatched pairs are reported as exclusions and cannot affect
performance.

For each exit contract the report includes strict accuracy, Wilson lower bound, gross/net
and reporting-only after-tax R, holding time, maximum losing streak and calls per observed
entry session. Setup and regime breakdowns remain within the selected market. Quick-profit
and swing outcomes are never pooled for accuracy, drift or challenger training.

A quick-profit contract becomes only a *research candidate* when all three are true:

1. its paired strict accuracy is higher than swing;
2. its mean net R is positive after costs; and
3. its mean net R is at least as high as swing on the identical entries.

Thus a higher hit rate caused by a smaller target is never enough by itself. The result
cannot change a live model or authorize promotion.

## Contract-specific training

The scheduled learner now counts its 20-new-outcome threshold separately for every contract.
For example, ten mature quick-profit rows plus ten mature swing rows do not trigger a
20-row challenger. Each contract receives a separate model directory, locked-test registry,
experiment history and weekly clock.

## Remaining exit experiments

The 0.5R, 1R, partial-profit/breakeven, 1.5R, trailing, one-session and five-session variants
remain unimplemented. No-call frequency also requires the full session opportunity calendar,
not merely sessions containing paired entries. They remain unchecked rather than inferred.

At activation time there are still no mature sealed live pairs, so this checkpoint makes no
claim that either exit improved the actual NSE, BSE or crypto baseline.
