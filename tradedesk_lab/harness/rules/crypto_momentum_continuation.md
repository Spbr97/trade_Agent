# Rule set: crypto_momentum_continuation

Phase 0 of the Strategy Validation Harness template, filled in for the harness's first real
run. The mechanical rule itself is `tradedesk_lab/crypto_intraday_research.py::MomentumContinuation`
(already built and tested this session) - this document is its pseudocode restatement plus
the kill criteria, not a new implementation.

## Instrument, timeframe, market

- Pairs: the 10-coin CoinDCX watchlist (`CDX_BTCINR`, `ETHINR`, `SOLINR`, `XRPINR`, `DOGEINR`,
  `ADAINR`, `TRXINR`, `XLMINR`, `HBARINR`, `BNBINR`) - already backfilled to H1.
- Timeframe: H1 (1-hour candles). CoinDCX has no M5; an hourly-scale move is what this rule
  is trying to catch.
- Market: 24/7 crypto - no session clock, no market-hours eligibility window.

## Rules (pseudocode, zero discretion)

```
ENTRY CONDITION (arming):
  last 3 consecutive H1 bars all close > their own open (up-closes)
  AND each close > the prior bar's close (monotonic upward)
  AND the latest bar's vol_ratio20 >= 1.3
  AND the latest bar's rsi14 < 80                      (not already exhausted)
  AND (latest close - 20-bar low) / atr14 <= 4.0        (not overextended vs ATR)
  -> ARMS at trigger = latest bar's high

CONFIRMATION (within 3 bars of arming):
  a later bar's low <= stop  -> invalidated, no trade
  a later bar's close >= trigger -> confirmed; enter at the NEXT bar's open
  else (3 bars pass with neither) -> expired unconfirmed

STOP  = min(low of the 3 arming bars, latest close - 1.5 * atr14)
TARGET = trigger + 2.0 * (trigger - stop)                (2R, preregistered)
SIZE = risk.max_risk_per_trade_pct of equity / (entry - stop), quantized to CoinDCX's
       qty_step, floored to 0 below CoinDCX's min_notional (Rs 100)
MAX HOLD = 48 H1 bars (~48h) - not a same-session cap; a real move can span a calendar day
TIME-IN-FORCE = GTC within the confirmation window above; expires unconfirmed after 3 bars
```

## Invalidation condition

The setup is inapplicable whenever CoinDCX H1 data for a pair is stale, missing, or fails
`tradedesk_lab.harness.data_layer.validate_candles` (gap/zero-volume/big-jump checks) - no
trade is evaluated against data flagged by that check.

## Kill criteria

| Criterion | Threshold | Notes |
|---|---|---|
| Min net expectancy per trade | > 0.0R | After CryptoCostModel's real fees (0.2% maker/taker, 1% Section 194S TDS on the sell leg, 18% GST on the fee) |
| Min sample size | 300 trades | The raw executable-candidate population (855 in the last research run), not the handful that clear a portfolio-level reward/risk filter - too small a sample to found a criterion on |
| Max acceptable drawdown | 30% | Of a compounding equity curve at 0.25% risk/trade (risk.yaml's own default) |
| Max acceptable losing streak | 10 trades | Consecutive losses, chronological order |

## Prior evidence (already measured this session, before this harness existed)

`tradedesk_lab/crypto_intraday_research.py`'s own `run_research()` already measured this exact
rule's raw population at **855 executable candidates, mean net R -0.69, 28.3% net win rate**,
and its `matched_random` gross-R timing test at **p=0.032** (beats random timing on gross R,
but that is a pre-cost comparison). This harness run is the first time that population is put
through the FULL gauntlet (walk-forward, sensitivity, Monte Carlo, kill criteria) rather than
just the null-timing test and portfolio replay `crypto_intraday_research.py` already reports.
