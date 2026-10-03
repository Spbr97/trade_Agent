# Accuracy Milestone 11 — prospective same-stock random-timing protocol

Protocol: `accuracy-prospective-timing-v1`

M11 tests whether the frozen M8 signal date adds value, independently of M10's test of
which candidate the model selects. It is registered prospectively and cannot change M8.

## Why the daily control uses future-session offsets

The existing intraday null can randomize bars within the same stock and session. M8 is a
daily strategy, so only one daily entry opportunity exists per stock/session. Its honest
prospective analogue freezes placebo decision dates **1–20 NSE sessions after** each M8
signal, then enters the same stock at the following session's open. This changes timing
while retaining the stock, daily horizon, geometry, costs and sizing.

The offset assignments are saved before M8's earliest entry. They do not select dates after
viewing their outcomes. Because the longest placebo requires 20 offset sessions plus its
entry and three-session outcome window, M11 necessarily matures more slowly than M8/M10.

## Frozen construction

- Seed: `20261005`.
- Cohorts: 1,000.
- Offsets: uniform integer draws from 1 through 20 NSE sessions, with replacement across
  cohorts and frozen independently per selected M8 call.
- Instrument: exact same stock as the corresponding model call.
- Placebo decision information: candles through the placebo decision date only.
- Volatility: causal Wilder ATR(14) measured on the placebo decision date.
- Entry and outcome: next-session open plus slippage, 1 ATR stop, 0.5R target,
  three-session maximum hold, identical sizing and after-cost R.
- Missing decision/entry bars, invalid ATR, incomplete windows and unsizeable entries are
  explicit exclusions. A model call enters paired evidence only when all 20 unique placebo
  offsets resolve successfully.
- Resolved placebo input candles are hashed and rechecked on later runs.

## Frozen gate

M11 passes only with:

- at least 100 completely paired model calls;
- at least 30 active M8 sessions;
- model mean after-cost R at least +0.10R above mean random-timing R; and
- one-sided empirical p-value at most 0.05 with finite-sample correction.

M8's accuracy/Wilson/session/economic gates, M9 integrity/stress gates, and M10 selection
control remain independently mandatory. M11 never grants live authority by itself.

## Interpretation limitation

Offsets preserve the stock but can cross short-term market regimes; a same-day alternative
does not exist at daily resolution. M11 therefore reports the exact scope as
`same_stock_random_future_session_timing`. This is the registered broader daily
random-timing control, not an intraday same-session claim.
