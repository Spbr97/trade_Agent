# Self-learning M21 — frozen crypto momentum-ignition protocol

Protocol frozen: 2026-10-12 IST, before the first production M21 snapshot, plan, or
outcome was collected.

Implementation clarification: the first coverage-only snapshot registered zero plans
and exposed the shared crypto candle model's documented integer-volume rounding. Before
the first M21 plan, `V` was bound explicitly to CoinDCX's raw fractional base-asset
volume. M20 still receives its unchanged rounded shared candles. No M21 threshold,
ranking weight, geometry, first-look rule, or already-recorded artifact was altered.
Snapshots collected after this correction carry the machine-readable source marker
`m15_volume_contract=coindcx_raw_fractional_base_asset_v1`.

Status: preregistered, forward-only crypto challenger. It is a research observer only.
It cannot authorize an alert, a personal call, an order, sizing, model promotion, or a
baseline-improvement claim.

## Purpose and causal hypothesis

M20 measures completed-M15 compression below resistance. Its crypto ledger showed that
a small fixed `+0.75R` target can be uneconomic after configured CoinDCX costs and that a
compression-only grammar can reject an early expansion or a shakeout which later
recovers. M21 tests a different and deliberately narrow hypothesis:

1. a controlled first expansion with high cross-sectional three-bar momentum, positive
   one-bar acceleration, broad participation, true-range expansion, a strong close and
   bounded VWAP/resistance extension is more likely to continue after a later trigger;
2. after a previously recorded pre-trigger structure failure, a prompt, high-volume
   VWAP reclaim may identify a fresh continuation opportunity without rewriting the
   failed plan; and
3. selecting the smallest frozen target whose after-cost payoff needs no more than a 70%
   win rate can prevent a high gross hit rate from being mistaken for positive economics.

Sonic and LINK are motivating inspected failures only. They are not training rows,
backfilled successes, threshold-validation examples, or members of the M21 prospective
denominator. Their already-known paths cannot prove this hypothesis. Only plans written
before their eligible future bars may enter M21 evidence.

## Frozen scope and information boundary

- M21 runs only for crypto. NSE and BSE return `not_applicable`; their evidence cannot be
  borrowed by M21.
- Every snapshot requests the complete currently materialized set of active CoinDCX INR
  pairs. Daily-ranker availability is optional metadata and cannot remove a pair from the
  M15 scan or manufacture an eligibility pass.
- Directionless stablecoin/INR pairs remain visible to coverage diagnostics but cannot
  register a directional plan.
- The only decision-time price information is completed M15 OHLCV. The decision bar is
  unavailable until its close; it can never trigger the plan created from it.
- M21 volume is the raw fractional base-asset volume returned by CoinDCX. It is fetched
  through the existing rate-limited public client and is never rounded into the shared
  equity-compatible integer `Candle.volume` field.
- At least 32 completed M15 bars are required for a valid feature row. Non-finite prices,
  invalid OHLC geometry, negative volume, non-positive close, or undefined required
  ratios make the row invalid, never eligible.
- `valid feature rows / requested active INR pairs` must be at least **0.85**. Below this
  floor the snapshot is fail-closed for new registrations. Existing plans must still be
  evaluated and resolved from valid later bars.
- Cross-sectional percentiles use all valid feature rows in the same completed-M15
  snapshot, ranked in ascending value order so the largest value approaches `1.0`, with
  average ranks for ties. Invalid rows and absent pairs never receive a percentile.

M21 has its own snapshot, plan, and qualification hash chains under
`data/m14_m18/momentum_ignition_watch/crypto/`. M20 remains an immutable benchmark under
`data/m14_m18/pre_takeoff_watch/crypto/`. No M20 plan or outcome is copied into, relabelled
as, or pooled with M21 evidence.

## Frozen feature definitions

For decision bar `t`, let `C`, `H`, `L`, `V` be close, high, low, and volume. Define:

- `return_1 = C[t] / C[t-1] - 1`;
- `return_3 = C[t] / C[t-3] - 1`;
- `acceleration = return_1 - return_3 / 3`;
- `TR[t] = max(H[t]-L[t], abs(H[t]-C[t-1]), abs(L[t]-C[t-1]))`;
- `tr_expansion = TR[t] / mean(TR[t-20:t])`, where the denominator excludes `TR[t]`;
- `volume_ratio_1v20 = V[t] / mean(V[t-20:t])`, excluding `V[t]` from the mean;
- `volume_acceleration = V[t] / mean(V[t-3:t])`, excluding `V[t]`;
- `close_location = (C[t]-L[t]) / (H[t]-L[t])`; a zero-range bar has `0.5`;
- `ATR14 = mean(TR[t-13:t+1])`;
- `VWAP24` is the volume-weighted mean of `(H+L+C)/3` over the last 24 completed bars;
- `prior_high_20 = max(H[t-20:t])`, excluding the decision bar;
- `vwap_extension_atr = (C[t]-VWAP24) / ATR14`; and
- `prior_high_distance_atr = (C[t]-prior_high_20) / ATR14`.

Every denominator must be finite and strictly positive. A missing ratio is unavailable,
not zero and not a pass.

## Frozen breakout-ignition gate

A row is eligible for the `breakout_ignition` mechanism only when every condition below
is true:

| Observation | Frozen requirement |
|---|---:|
| One-bar acceleration | `return_1 - return_3 / 3 > 0` |
| Three-bar return percentile | `>= 0.80` |
| Current/prior-20 volume | `volume_ratio_1v20 >= 1.50` |
| Volume-ratio percentile | `>= 0.75` |
| True-range expansion | `tr_expansion >= 1.10` |
| Close location | `>= 0.65` |
| VWAP support | `C[t] >= VWAP24` |
| VWAP extension | `vwap_extension_atr <= 2.00` |
| Prior-high distance | `-0.50 <= prior_high_distance_atr <= +0.75` |
| Blow-off guard | `TR[t] / ATR14 <= 2.50` |

There is no post-collection threshold search. In particular, an appealing move outside
the prior-high or VWAP-extension bounds remains rejected.

## Frozen shakeout-reclaim gate

`shakeout_reclaim` is a new plan, not a repair of a losing plan. It is eligible only if:

- the same symbol has an immutable M21 terminal event with reason
  `structure_failed_before_trigger` no more than eight completed M15 bars earlier;
- the reclaim decision bar is strictly later than that terminal event;
- `C[t-1] <= VWAP24[t-1]` and `C[t] > VWAP24[t]`;
- `return_1 > 0`;
- `volume_acceleration >= 1.25`;
- the current volume-ratio cross-sectional percentile is at least `0.60`; and
- `close_location >= 0.65`.

The 85% universe-coverage floor, valid-row rules, non-directional-pair exclusion, plan
economics, active-plan rule, cooldown, and daily caps also apply. At most one reclaim plan
may be armed for a symbol in any rolling 24-hour interval.

## Frozen ranking, registration, and display policy

For each valid row, percentile components are clipped to `[0,1]` and the diagnostic rank
score is:

```text
momentum_score =
    0.30 * return_3_percentile
  + 0.25 * volume_ratio_1v20_percentile
  + 0.20 * acceleration_percentile
  + 0.15 * tr_expansion_percentile
  + 0.10 * close_location
```

Rows sort by `momentum_score` descending, then daily opportunity probability descending
with unavailable values last, then `scrip_code` ascending. The first **12** rows are a
dashboard display only. Display truncation never defines the registration population.

After mechanism, economics, coverage, cooldown, and cap checks, at most the first **three**
eligible rows in this deterministic order may register a plan per completed-M15 snapshot.
Therefore a qualifying symbol ranked 13th in the display table can still be considered
for registration; the top-12 UI is not an evidence filter.

Only one pending or triggered M21 plan may exist per symbol. After any terminal M21 event,
the symbol has a four-completed-M15-bar cooldown. No symbol may register more than two M21
plans per IST calendar date. Repeated collection of the same decision bar is idempotent.

## Frozen plan, fill, and adaptive-target economics

Each registration freezes the decision bar, mechanism, trigger, structural stop, target
price, selected target multiple, feature snapshot, rank, cost assumptions, and hashes.
The plan expires after eight later completed M15 bars without a valid trigger. A triggered
plan times out after sixteen completed M15 bars.

For `breakout_ignition`, raw trigger is
`max(decision close, prior_high_20) * 1.001`. For `shakeout_reclaim`, raw trigger is
`max(decision close, decision VWAP24) * 1.001`. The raw structural stop for either
mechanism is the minimum of:

- `lowest low of the decision bar and its prior three bars * 0.999`;
- `decision VWAP24 * 0.995`; and
- `decision close - ATR14`.

Non-positive or non-finite risk rejects registration. A trigger occurs only when a
strictly later completed M15 bar closes at or above the frozen trigger **and** its volume
is at least `1.25 * mean(volume of its prior 20 completed bars)`. The raw entry is that
confirmation close, not its open, high, or the frozen trigger. Execution entry then adds
configured buy-side slippage. Outcome barriers start on the following completed M15 bar;
the confirmation bar's earlier high and low cannot be reused as post-entry evidence.

The only target multiples tested at registration are, in order:

`0.75R, 1.00R, 1.25R, 1.50R, 2.00R, 2.50R, 3.00R`.

For each multiple, compute the raw target as
`trigger + multiple * (trigger - structural stop)`,
then apply the configured crypto slippage and full `CryptoCostModel` round-trip costs.
For quantity `q`, execution entry `E`, stop `S`, and execution exit `X`:

```text
gross_risk       = q * (E - S)
net_R(X)         = ((X - E) * q - round_trip_cost(E, X, q)) / gross_risk
target_net_R     = net_R(target after sell slippage)
stop_net_R       = net_R(stop after sell slippage)
break_even_rate  = (-stop_net_R) / (target_net_R - stop_net_R)
```

Quantity before rounding is
`min(INR 250 / (E-S), INR 100,000 / E)`, rounded down to the configured `0.00000001`
crypto quantity step; the configured INR 100 minimum notional still applies. The frozen
v1 cost assumptions are 0.20% trading fee on each side, 18% GST on each trading fee,
1.00% TDS on sell turnover, and 0.05% adverse slippage on each side. Every plan records
the assumptions and contract hash. Reporting-only tax treatment cannot replace this
cash-outflow economics gate.

Choose the first target multiple in the ordered grid for which `target_net_R > 0`,
`stop_net_R < 0`, and `break_even_rate <= 0.70`. If no target passes, no plan is
registered. The target multiple and absolute target price are then immutable.

On the later completed confirmation bar, economics are recomputed using the actual
confirmation-close execution entry. The frozen target is never moved to rescue a gap or
an above-trigger confirmation. If the target is no longer above entry or any economics
condition fails, invalidate the plan; do not count it as a loss or success.

Because entry is confirmed only at the completed bar's close, that bar cannot both
trigger and resolve the plan. Starting with the following completed bar, a bar that
opens at or below the stop exits at that open, and one that opens at or above the target
exits at that open. Otherwise, a bar that touches both stop and target is a stop. Gaps,
slippage, expiry, timeout, quantities, and all terminal outcomes remain explicit. Only
`target`, `stop`, and `timeout` with complete finite net-R evidence enter the performance
denominator. Invalidated, pending, and expired plans remain visible but are never passes.
A strict success is a `target` outcome with finite `net_R > 0`; every other denominator
member is a strict failure.

## Forward-only evidence and first-look decision

M21 starts a new cohort at its first production registration. Historical M20 events,
Sonic/LINK paths, and pre-activation snapshots are excluded. IST calendar dates are the
session unit because crypto trades continuously.

There is no interim statistical or futility decision. Before maturity the status is
`collecting`, even if a temporary percentage looks attractive or poor. The one frozen
core first look occurs at the first observation with both:

- at least **60 resolved M21 calls**; and
- at least **40 distinct IST decision dates**.

Core qualification requires every condition below:

- strict success rate at least **70%**;
- 95% Wilson lower bound at least **55%**;
- positive mean after-cost net R;
- zero resolved rows with missing/non-finite net R;
- maximum chronological losing streak no greater than **six**; and
- zero timing, hash-chain, market-mixing, duplicate-denominator, or contract-integrity
  violations.

Failure of any core gate at that first look is a terminal `rejected` decision. A core pass
is only `controls_pending` until M21-specific matched-random accuracy and net-R controls
are complete. Both matched-random upper-tail probabilities must be no greater than
`0.05`; the controls must use M21's own eligible universe, timestamps, entry/outcome
contract, and costs rather than borrowing M20 controls. Unavailable controls can never
pass. Even full qualification means `qualified_for_review`, not live authority; human
approval remains mandatory.

## Abort and fail-closed rules

New registrations pause immediately on sub-85% snapshot coverage, stale or malformed
input, non-completed decision bars, ledger-chain failure, contract/hash mismatch, or
non-finite required economics. Valid existing plans may be evaluated only with causally
later valid completed bars. A hash or timing violation blocks qualification pending an
integrity review; it is not converted into a performance loss.

There is no discretionary abort because a provisional accuracy changes, no threshold
edit after seeing outcomes, and no optional second first look. A rejected M21 cohort may
only be followed by a separately named and preregistered version with a new prospective
boundary.

## Authority boundary

Every M21 artifact must keep `tradeable_calls = 0`, `eligible_for_live = false`,
`baseline_accuracy_improved = false`, and research-only authority until the complete
forward and control process passes and the user explicitly approves a later promotion.
The dashboard may compare M20 and M21 side by side; it may not pool their evidence or
describe a watch as a call.
