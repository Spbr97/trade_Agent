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

## NEW DETECTOR r_tsmom252_rsi_momentum_trend_trail on nse

- item: `nse:2026-10-01T16:45:31.216357+05:30`
- applied: 2026-10-01T15:46:03.344920+00:00
- commit: `28eead6230abde48e1ef484c9a250dcdb85aea7e`
- files: src\tradedesk\engine\signals.py, src\tradedesk\setups\r_tsmom252_rsi_momentum_trend_trail.py, src\tradedesk\setups\__init__.py, config\setups.yaml

## NEW DETECTOR r_tsmom252_rsi_momentum_trend_trail on nse

- item: `nse:2026-10-01T16:45:31.216357+05:30`
- applied: 2026-10-01T15:46:04.072757+00:00
- commit: `737cf74b3acba0a137c4be866ece5a4233a3ae4a`
- files: src\tradedesk\engine\signals.py, src\tradedesk\setups\r_tsmom252_rsi_momentum_trend_trail.py, src\tradedesk\setups\__init__.py, config\setups.yaml

## NEW DETECTOR r_high52_break_strong_trend_trend_trail on nse

- item: `nse:2026-10-01T16:45:31.200642+05:30`
- applied: 2026-10-01T15:46:07.480186+00:00
- commit: `032287ccdceae3523a4d43bec187cc468d87d2b2`
- files: src\tradedesk\engine\signals.py, src\tradedesk\setups\r_high52_break_strong_trend_trend_trail.py, src\tradedesk\setups\__init__.py, config\setups.yaml

## NEW DETECTOR r_tsmom20_strong_trend_trend_trail on nse

- item: `nse:2026-10-01T16:45:31.200642+05:30:a4b47196`
- applied: 2026-10-01T15:46:10.507749+00:00
- commit: `f1e80056ab3ad83380edba7f0a04362231fe3a63`
- files: src\tradedesk\engine\signals.py, src\tradedesk\setups\r_tsmom20_strong_trend_trend_trail.py, src\tradedesk\setups\__init__.py, config\setups.yaml

## NEW DETECTOR r_adx_thrust_tsmom_up_trend_trail on crypto

- item: `crypto:2026-10-01T00:40:22.563036+05:30`
- applied: 2026-10-01T15:46:13.376604+00:00
- commit: `1d517c11251bf4aef3030a7388a25ab485aa3ddd`
- files: src\tradedesk\engine\signals.py, src\tradedesk\setups\r_adx_thrust_tsmom_up_trend_trail.py, src\tradedesk\setups\__init__.py, config\setups.yaml

## RETIRE base_breakout on bse (shadow-tracked)

- item: `bse:2026-10-03T16:40:13.193782+05:30`
- applied: 2026-10-03T17:45:48.729734+00:00
- commit: `7555ff717fae83cb7ff96fd937e8e980d0bcad7e`
- files: config\setups.yaml

## RETIRE nr7_breakout on bse (shadow-tracked)

- item: `bse:2026-10-03T16:40:13.210354+05:30`
- applied: 2026-10-03T17:45:49.590768+00:00
- commit: `c405d7f8fdf32e60f8f2c1653fe5b9d66303c6d6`
- files: config\setups.yaml

## RETIRE trend_pullback on bse (shadow-tracked)

- item: `bse:2026-10-03T16:40:13.224119+05:30`
- applied: 2026-10-03T17:45:55.150727+00:00
- commit: `53a06c8a7f89f1b85c8b25b78935f27866db702f`
- files: config\setups.yaml
