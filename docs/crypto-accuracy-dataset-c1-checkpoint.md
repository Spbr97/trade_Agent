# Crypto accuracy C1 — all-pair point-in-time dataset checkpoint

Status: **implemented and latest real materialization verified; collecting/not ready; no
accuracy improvement claimed**

C1 now has the machinery needed to turn exact future crypto-universe observations into an
auditable all-pair research dataset. It does not infer membership before activation, and it
does not treat the currently stored candle catalogue as proof that a pair was historically
active.

Implemented in this checkpoint:

- append-only, hash-chained snapshots containing every exact active CoinDCX INR pair ID,
  known inactive IDs and observed additions/removals;
- fully closed daily-bar materialization with per-pair stale, missing and invalid-data
  accounting rather than silent imputation;
- canonical IST-day de-duplication of 876 known CoinDCX filler rows, retained in a separate
  hashed audit artifact rather than allowed into ATR or labels;
- immutable source, universe, contract, configuration and artifact hashes with later
  closed-source verification;
- three frozen ATR-based quick-profit geometries, 14-session contiguous-valid ATR,
  causal next-open entry, gap-aware exits with correct open timestamps, stop-wins-ties
  ambiguity handling and exact fixed outcome horizons;
- separate gross R, CoinDCX fee/GST/TDS-and-slippage net R, and reporting-only Indian VDA
  after-tax R for every resolved label; and
- research-only manifests and rows with live eligibility permanently false.

Contract tests exercise append-only/delisting transitions, the no-preactivation rule,
all-pair missingness, forming/future-bar exclusion, closed-source mutation detection,
deterministic next-open labels, favorable/adverse gaps and same-bar ordering.

## Current evidence

The first exact-code observation and freeze ran on 4 October 2026. The verified R2
collection run then added the next fully closed point-in-time session:

| Measure | Result |
|---|---:|
| Dataset ID | `2026-10-04-9a42a7f4d9f8-f991d060-394b1f39` |
| Observed-active INR pairs | 339 |
| Configured exclusions retained for audit | 2 |
| Raw / canonical closed daily rows | 355,617 / 354,741 |
| Duplicate IST-day fillers removed | 876 |
| Explicit absent daily sessions | 7,058 |
| Invalid OHLCV sessions | 673 |
| No-history / stale pairs | 0 / 0 |
| Global / minimum-per-pair PIT sessions | 2 / 2 of 30 minimum |
| Required pairs meeting 30-session gate | 0 of 337 |
| Geometry rows | 2,022 total: 1,014 excluded, 1,008 pending, 0 resolved |
| Source verification | passed |
| Live eligibility | false |

Universe activation occurred at `2026-10-03T23:13:41Z`. Earlier candles remain useful for
coverage and data-quality inspection, but their active membership is unknown and they are
not relabelled as point-in-time evidence. Across the two accepted sessions, 1,014 geometry
rows are explicitly excluded and 1,008 remain pending; none is resolved. Accuracy,
expectancy and after-tax expectancy therefore remain unavailable—not zero, passed, failed
or improved.

The earlier C1 prospective timing observer remains a separate forward evidence stream. Its
calls cannot be pooled with this historical/development dataset, with another setup, or
with NSE/BSE evidence.

## Interpretation and next checkpoint

This is data-and-label integrity progress, not predictive progress. It does not change the
crypto baseline, qualify any setup or authorize calls. Readiness now requires every current
nonexcluded pair—not merely one global date count—to reach 30 point-in-time sessions; a
properly observed inactive/delisted pair does not block that gate. After enough
post-activation C1 sessions exist, C2 may evaluate cross-sectional momentum, pullback continuation,
liquidity/volatility compression and BTC-relative strength against the frozen random and
placebo controls.

See the [frozen dataset protocol](crypto-accuracy-dataset-c1-protocol.md) and the
[crypto accuracy plan](plan-crypto-accuracy.md).
