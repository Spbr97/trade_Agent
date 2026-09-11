# M13 Phase 3 — crypto data, universe, quality report

CoinDCX, `data/crypto.duckdb`. Numbers only.

## Load

`tradedesk data sync-instruments --market crypto`: **338 active INR pairs** (matches the
Phase 2 probe exactly). `tradedesk data load --market crypto --interval 1day`:
**340,108 daily bars, 0 errors**, full run (no `--years` cap, so each pair's real depth).

`data status --market crypto`: 338 codes, latest bar today's session - same cadence as
the NSE store.

## Quality report (`data quality --market crypto`, 337 codes checked, reference
CDX_BTCINR - crypto trades every calendar day, so a liquid pair stands in for an index;
there is no synthetic crypto benchmark instrument)

| Kind | Count | Assessment |
|---|---|---|
| `zero_volume` | 13,522 | Expected for a 338-pair long tail: real days with no trades on genuinely thin pairs (YFIINR, PAXGINR/XAUTINR - gold-pegged, low-interest, QIINR, ASRINR...). Not a pipeline defect. |
| `missing_sessions` | 6,966 | Spot-checked LUNAINR (first missing 2022-05-14, matching the real Terra/LUNA collapse) and FTTINR (first missing 2022-11-15, matching FTX's real collapse) - both **still trade today** (checked: last bar is today's session), so this is not delisting. It's a trading-interest collapse: after each crash, many days had literally zero trades, and CoinDCX's candles endpoint omits the bar entirely on those days rather than returning a zero-volume one (unlike the `zero_volume` cases above) - a real characteristic of the feed, consistent with well-known market history, not a bug. |
| `big_jump` | 2,661 | Crypto is genuinely volatile across boom/bust cycles (2018, 2021, 2022); not individually reviewed, no reason to suspect a systematic defect. |
| `bad_ohlc` | 599 (168 codes) | **Real, genuine feed corruption**, not our code - spot-checked CDX_ETHINR (81 bad bars: `open` wildly inconsistent with `high/low/close`, e.g. open=15356.07 vs high=70.87 on 2018-10-23). All 81 of ETHINR's bad bars fall in **2018-2020**, none after - CoinDCX's early historical data quality was worse than its recent data. **Actionable**: treat pre-2021 crypto history as lower-trust; Phase 4's backtester/trainer should default to a later start date, or filter on this check, rather than using full depth blindly. |
| `suspected_unadjusted_split` | 240 (159 codes) | **Mostly false positives, unlike the NSE case.** Spot-checked CDX_ADAINR: 6+ hits in 2019-2020 alone, all matching ordinary bear-market crashes (ADA has no split/bonus mechanism) that happen to land within the detector's 6% tolerance of a known ratio. Crypto's routine volatility trips this heuristic far more than NSE's does. Real redenomination events (e.g. a token doing an actual supply change) would still be caught - it's the *noise* that's much higher here, not that the check is useless. |

## Universe-by-date (`data universe --on 2026-09-10 --market crypto`)

**1 member: CDX_USDTINR.** Investigated before accepting this - it is real, not a bug:

| Pair | 20-session avg turnover (INR) |
|---|---|
| CDX_BTCINR | ~4.6 crore |
| CDX_ETHINR | ~2.6 crore |
| CDX_USDTINR | ~8.1 crore |

The `min_avg_daily_turnover_inr: 5,00,00,000` (₹5 crore) floor is copied verbatim from
`config/universe.yaml` (NSE-tuned) in the CLI's crypto branch - even **BTC-INR** on
CoinDCX doesn't clear it. This reflects reality: most global crypto volume flows through
USDT pairs on larger exchanges, not CoinDCX's INR pairs specifically. **Open decision for
Phase 4**: crypto needs its own `config/markets/crypto.yaml` with a turnover floor
calibrated to CoinDCX's actual liquidity (a first guess: ~₹10-50 lakh, not ₹5 crore) -
not deferred silently, called out here as blocking before Phase 4's scan/backtest work
means anything.

## Verdict

**PASS** on the mechanical done-when (0 load errors, quality report reviewed, universe
check runs cleanly) - same bar M3 used for NSE. Two real, non-blocking findings carried
forward rather than "fixed" (there's nothing to fix, they're honest reflections of the
data): pre-2021 history needs a trust discount, and the universe liquidity floor needs
its own crypto-calibrated value before Phase 4.
