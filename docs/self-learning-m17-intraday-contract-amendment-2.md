# Self-learning Milestone 17 — protocol amendment 2

Amendment recorded: 2026-10-10T10:34:35+05:30, after acquisition and path-integrity
inspection but before any M17 contract outcome was replayed, scored or opened.

This amendment does not replace or mutate the hash-sealed base protocol. The acquisition
manifests remain bound to base protocol SHA-256
`8e5668340254a81411e910b1512512b1350c824e67aded3777e9907cf6e335b2`.

## Observed data-contract failure

All 872 sealed crypto paths had complete observed M1 and M15 timestamp grids. Comparing
the observed M15 feed with lossless M1 aggregation showed that only 245 paths had exact
M15 closes, 57 had exact volumes and 21 satisfied every tested close, volume and OHLC
containment invariant. All 72 primary paths failed the original M15 OHLC aggregation
check. No entry rule, target, stop, trade result, accuracy or net-R outcome was opened to
obtain these findings.

CoinDCX's observed M1 and M15 histories therefore cannot be treated as two lossless
resolutions of one candle stream. Keeping M15 as a mandatory equality oracle would make
the crypto experiment unavailable regardless of its M1 execution paths.

## Narrow crypto-only amendment

- Observed M1 remains the authoritative execution path and must retain its exact 1,440-bar
  sealed 24-hour grid, finite values and valid OHLC geometry.
- Derive both M5 and M15 deterministically from observed M1 with fixed UTC-day/IST-window
  alignment. Both derived grids must agree losslessly with M1.
- Preserve observed M15 as an auxiliary feed-disagreement audit. It must retain its exact
  96-bar timestamp grid, finite values and valid OHLC geometry, but its OHLCV values do not
  determine path validity and never enter a contract replay.
- Record the observed-versus-derived M15 mismatch reason and counts in every crypto path
  artifact so the disagreement cannot be hidden.
- NSE and BSE continue to require observed M1, M5 and M15 lossless agreement. Their missing
  paths remain unavailable and cannot be substituted or synthesized.
- Candidate selection, chronological splits, six contracts, cost model, controls,
  thresholds, outcome denominators and live-authority rules remain unchanged.

This amendment repairs a source-data contract, not a performance result. Any historical
pass still creates only a fresh prospective observer and cannot establish baseline
improvement or authorize a live call.
