# TradingView golden files for indicators (PLAN.md M4 "done when")

1. On TradingView open a daily NSE chart (e.g. SBIN), add the indicators you want to check
   (EMA 20, RSI 14, ATR 14, ADX 14, MACD 12/26/9, BB 20/2 ...), then
   *chart menu → Export chart data* (CSV, ISO time).
2. Save the CSV in this folder and describe it in `manifest.yaml`:

```yaml
- file: sbin_daily.csv
  columns:            # CSV column -> tradedesk indicator
    EMA: ema:20
    RSI: rsi:14
    ATR: atr:14
    ADX: adx:14
    "MACD": macd_line:12:26:9
    "Signal": macd_signal:12:26:9
  warmup: 300         # bars to skip before comparing (seeding differences wash out)
  rtol: 0.001
```

Supported indicator specs: `ema:N`, `sma:N`, `rsi:N`, `atr:N`, `adx:N`, `plus_di:N`,
`minus_di:N`, `macd_line:F:S:G`, `macd_signal:F:S:G`, `bb_width:N:MULT`, `roc:N`, `obv`.

The test skips when `manifest.yaml` is absent. Keep files small (a year or two of daily
bars is enough) and never commit anything you are not allowed to redistribute.
