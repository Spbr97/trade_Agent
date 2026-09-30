# Self-review applied-change log

Every entry below was written by `self_review/apply.py`, and only after a human approved the
specific `ReviewItem` it corresponds to via the dashboard's Review tab. Each entry names the
git commit that made the change and the evidence file (`data/reviews/applied/<item_id>.json`)
holding the before/after file hashes and the full gauntlet report that justified it - see
[the self-review checkpoint](self-review-checkpoint.md) for the feature's own design and
safety boundary.

Nothing is written here by hand; this file is append-only from `apply()`'s own code.

## RETIRE base_breakout on crypto

- item: `crypto:2026-09-29T00:20:21.604586+05:30`
- applied: 2026-09-30T10:04:11.446952+00:00
- commit: `b6f53f14372d9855dd6b83fcdd795ea2a33a95b0`
- files: config\setups.yaml

## RETIRE nr7_breakout on crypto

- item: `crypto:2026-09-29T00:20:22.236687+05:30`
- applied: 2026-09-30T10:04:15.534856+00:00
- commit: `2529c6c8bf13738170ad7b798e137c8d533060d7`
- files: config\setups.yaml

## RETIRE trend_pullback on crypto

- item: `crypto:2026-09-29T00:20:22.555511+05:30`
- applied: 2026-09-30T10:04:18.147635+00:00
- commit: `262591a9ba21d47df76171aedab00768eb4a6758`
- files: config\setups.yaml

## RETIRE trend_pullback on nse (shadow-tracked)

- item: `nse:2026-09-30T16:25:05.246791+05:30`
- applied: 2026-09-30T12:32:51.989668+00:00
- commit: `d8df1a745e913b1bded649f02b7d404fbc0d3ddb`
- files: config\setups.yaml

## RETIRE nr7_breakout on nse (shadow-tracked)

- item: `nse:2026-09-30T16:25:05.240258+05:30`
- applied: 2026-09-30T12:32:55.504811+00:00
- commit: `359a8a8327d2ffbc2cf1cf761b29326210b36e76`
- files: config\setups.yaml

## NEW DETECTOR r_donchian55_rsi_momentum_trend_trail on crypto

- item: `crypto:2026-09-30T15:54:26.185650+05:30`
- applied: 2026-09-30T12:32:59.342648+00:00
- commit: `5a1ef321a6d5854898a0ad773e218f0c15d9ffdd`
- files: src\tradedesk\engine\signals.py, src\tradedesk\setups\r_donchian55_rsi_momentum_trend_trail.py, src\tradedesk\setups\__init__.py, config\setups.yaml
