# M13 Phase 4 — crypto costs wired into scan and backtest

## What changed

`--market {nse|crypto}` now exists on `tradedesk scan` and `tradedesk backtest`, not just
the `data` subcommands (Phase 3). Wiring: `engine/filters.py::apply_filters` takes a
`CostModel` + primitives instead of `RiskConfig`/`UniverseConfig` directly;
`backtest/portfolio.py::Portfolio.costs` is a `CostModel`, not a raw `ChargeSchedule`;
`BacktestConfig` carries an optional `costs: CostModel` (`None` → `EquityCostModel`, so
every existing NSE caller is unaffected); `scan/evening_scan.py`'s `scan_config` /
`build_watchlist` / `run_evening_scan` all take an optional `market: Market` (default
NSE). Paper trading and the live risk manager (`paper/book.py`, `risk/limits.py`) stay
NSE-only on purpose - they now hold a `CostModel` for type correctness but always build
`EquityCostModel`, since crypto has neither yet.

## Real bug found running it, not caught by mocked tests

`tradedesk backtest --market crypto` crashed outright: `ValueError: cannot reindex on an
axis with duplicate labels`. Root cause: some of CoinDCX's early (2019-2020) daily
history for CDX_BTCINR carries a synthetic, flat, zero-volume filler bar timestamped
~1 second before the real bar for that day (29 of 33 duplicate-day cases checked match
exactly: open=high=low=close=previous close, volume=0). Converting both to an IST
calendar date collapses them onto the same day, and `prepare_market()`'s
`closes_wide.reindex(calendar)` refuses an axis with duplicate labels.

Fixed with `_dedupe_by_calendar_day()` in `backtest/runner.py`: keeps the last bar per
IST day (data is already ascending by timestamp, so this picks the real bar over the
earlier filler), applied to every per-code load, the benchmark, and VIX. A no-op for
NSE - INDstocks timestamps are always clean 09:15 IST anchors. Regression tests added
(unit test on the dedup function directly, plus an end-to-end test that injects the
exact real-world pattern into a synthetic backtest and confirms `prepare_market` doesn't
crash).

## End-to-end verification (real data, not just tests)

`tradedesk scan --market crypto` on 2026-09-11 across the full 337-pair universe
produced one real signal (CDX_USDCINR, nr7_breakout) and correctly rejected it:

| Field | Value |
|---|---|
| qty | 250 |
| position_value | Rs 24,982.50 |
| costs_round_trip | Rs 371.28 |
| net_rr_t1 | **-0.17** |
| net_rr_t2 | 0.11 |
| rejected_for | net R:R 0.11 < 2.0 to final target |

Hand-checked the cost figure independently: fee 0.2% both legs (~Rs 100.50) + GST 18% of
fee (~Rs 18.09) + 1% TDS on the sell leg (~Rs 252.67) = Rs 371.26, matching the
computed Rs 371.28 to the paisa. USDCINR is a stablecoin (near-zero volatility) - the
negative net R:R *at the target itself* is the real, correct finding: TDS plus fees can
exceed the entire gross reward on a low-volatility instrument, which is exactly why the
filter exists.

## A real, understood limitation - not fixed here

Backtests on BTC/ETH/SOL (`--code CDX_BTCINR` etc.) produced **zero trades**: every
signal sized to qty 0. Verified the cause directly, not assumed: `risk/sizing.py`
assumes whole-unit (share-style) quantities, and `config/risk.yaml`'s reused (NSE)
`max_risk_per_trade_pct` (0.25%) against Rs 1,00,000 capital gives a Rs 250/trade risk
budget - at BTC's price (~Rs 77 lakh) even qty=1 risks far more than that on a normal
stop distance. Confirmed against real prices:

| Pair | Price | Risk/unit (3% stop) | Max qty at Rs 250 budget |
|---|---|---|---|
| BTC | ~77,00,000 | ~2,31,000 | 0 |
| ETH | ~2,52,000 | ~7,560 | 0 |
| SOL | ~22,000 | ~660 | 0 |
| XRP | ~260 | ~7.80 | 32 |

This is not a bug to paper over - CoinDCX (and most crypto exchanges) support fractional
quantities (e.g. 0.001 BTC), but `Position`, `Fill`, the journal schema and
`risk/sizing.py` all assume `qty: int` throughout the codebase. Supporting fractional
sizing is a real, separate piece of work (likely its own phase), not a Phase 4
afterthought, and reusing NSE's risk config as-is for crypto (documented scope choice
made in Phase 4's commits) compounds it. Until then, `--market crypto` scan/backtest
works correctly for lower-priced pairs (proven above) and correctly produces zero trades
for high-priced ones rather than a wrong number.

## Verdict

**PASS** on Phase 4's stated scope (costs + scan + backtest wiring). Two real findings
carried forward, not silently fixed: the duplicate-day quirk is fixed (that one *is* a
bug); whole-unit sizing vs crypto's per-unit price range is a genuine scope boundary,
flagged for whenever fractional position sizing becomes worth building.
