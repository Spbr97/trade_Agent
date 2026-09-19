# tradedesk

Short-term momentum scanner for NSE stocks using the INDstocks (INDmoney) API.
Evening scan on daily charts, live trigger monitoring, trades held hours to 10 sessions.
It alerts; the human places every order.

## Status

M1-M11 (NSE core: data, engine, live monitor, alerts, journal, paper book, prediction layer)
and M13 (crypto, CoinDCX) are built and tested. BSE was added as a third market via the
same INDstocks broker/credentials as NSE. M12 (order placement) has not started.

**The central, load-bearing finding, arrived at independently multiple times and not to be
re-litigated without new data:** none of the three live rule-based setups
(`base_breakout`/`trend_pullback`/`nr7_breakout`) show a real edge on NSE, BSE, or crypto.
Measured via a matched random-timing null (not just win rate/expectancy, which can look fine
while still losing to random): all three setups underperform random entry timing on every
market tested, and NSE's -0.557R / crypto's -0.515R backtested expectancy over 2023-2026
confirm it independently. `engine/scoring.py::eligibility()` therefore blocks all three from
ever alerting (500 resolved trades / 100 OOS / 80% win rate / beat-random-by-0.10R, fails
closed) until real evidence says otherwise. One real gross edge was found -
`rsi2<10 & above ema50` beats random by +0.067-0.077R (t>3-7) - but does not clear crypto/NSE
costs at policy-compliant position sizing (breakeven ~0.75% risk/trade); it is tracked forward
only, never live, via `research_tracker.py`.
The ML/prediction layer (M11) is methodologically rigorous (purged walk-forward, a genuinely
locked final test set, drift-checked, shadow-only) but its own honest result is "confirms no
edge to filter" (ROC-AUC ~0.5-0.56), not "found one" - it was never the broken part; it was
pointed at a population with nothing to find.
`eod_learning.py` runs a bounded, once-a-day retrain per market (NSE/BSE/crypto) against
`research_tracker.py`'s forward-collected evidence only, never the mined 2023-2026 window;
any candidate that ever clears the eligibility gate is a `review_queue.py` item, never an
automatic promotion. A separate isolated research sandbox, `tradedesk_lab/` (M14-M18), is
where further exploration (statistical-rigor re-checks, prospective forward scoring, crypto
intraday research) happens with hash-verified guarantees that it never touches or is promoted
into production - see its own `README`/`decision.py`/`forward.py` for that contract.
`tradedesk_lab/harness/` is a reusable strategy-validation gauntlet (random-entry benchmark,
walk-forward, parameter sensitivity, Monte Carlo trade-reorder, regime split, kill criteria)
built on those same tested primitives; its first real run correctly killed
`crypto_intraday_research.py`'s `MomentumContinuation` at the random-entry-benchmark stage
(p=0.986 - worse than random timing), and every run's report surfaces at `/lab`'s Harness tab.

**Automation**: ~15 Windows Scheduled Tasks (`Get-ScheduledTask -TaskName tradedesk-*`) run
the whole pipeline unattended - data loads, live sessions, after-close scans, per-market
signal trackers, research trackers, EOD learning, drift checks, an options snapshot, and a
permanently-running dashboard. Every `uv run` scheduled action uses `--no-sync` (the
always-on dashboard task holds a lock on `tradedesk.exe`; a normal dependency-sync step can
silently collide with it and exit 0 without running). Dashboard: one FastAPI app at
`127.0.0.1:8765` (Calls/Report-Research/Learning-Review tabs, a market dropdown per tab,
plus Lookup); the research lab is mounted at `/lab` under the same origin/port.

**Scope limits, by design, not oversight**: paper trading, the live risk manager and the
review-queue's self-analysis loop are NSE-only (crypto/BSE have no measured edge or no paper
book to grade against yet). Crypto/BSE calls are research/paper-only. Full spec and milestone
list: PLAN.md.

## Hard rules
- NEVER call or implement order placement, modification or cancellation unless the task explicitly says "Milestone M12".
- The API reference is docs/indstocks-api.md. Never guess endpoints, fields, formats or limits; read the doc.
- Every INDstocks call goes through broker/indstocks/ratelimit.py. Never exceed documented limits.
- NEVER hardcode tokens or the TOTP secret. Tokens live in memory; the TOTP secret comes from keyring.
- The live scanner and the backtester share engine/engine.py. No live-only or backtest-only signal logic.
- Fills are gap-aware everywhere: an open beyond a stop or trigger fills at the open.
- Overnight positions are long-only.
- No feature, signal or label may use data from after its timestamp. Every setup and feature has a look-ahead or leakage test.
- Portfolio limits (heat, positions, sector caps, daily and weekly limits) apply in backtests, paper book and live alike.
- Fail closed: stale data, a disconnected feed or API errors pause alerts and raise a health alert.
- Never block the asyncio event loop; CPU-heavy work runs in a worker thread.
- Timezone is Asia/Kolkata. Candle logic uses exchange timestamps, not the local clock.
- Claude output is advisory text only; it can never create a signal or change a price, level or quantity.
- The prediction layer can only lower grades or size, never raise size beyond config caps.
- Changes under engine/, setups/, live/ or data/ must pass tests/replay and tests/gaps.

## Commands
- Tests: uv run pytest
- Lint and types: uv run ruff check . && uv run mypy src
- Config check: uv run tradedesk config check
- Cost breakdown: uv run tradedesk costs --type delivery --qty 20 --entry 842 --exit 890
- Credentials: uv run tradedesk auth setup [--stdin] | auth status | auth check | auth clear   (keychain only; hidden input only works in a real Windows console, not Git Bash/mintty)
- Instruments: uv run tradedesk instruments refresh
- Candles / quote / stream: uv run tradedesk candles NSE_3045 --interval 1day --days 30 | quote NSE_3045 | stream NSE:3045 --seconds 30
- Data store: uv run tradedesk data sync-instruments | data load [--interval 1day] | data import-actions <nse.csv> | data import-results <nse.csv> | data quality [--out data/reports/q.csv] | data universe [--on YYYY-MM-DD] | data status
- Crypto data (M13, CoinDCX, no credentials needed): same data subcommands with `--market crypto` -> data/crypto.duckdb by default; scrip codes are `CDX_<coindcx_name>` e.g. CDX_BTCINR
- Live session: uv run tradedesk live [--watchlist data/watchlists/<date>.json] [--until 15:35] [--market nse|bse]   -> records data/sessions/<date>.jsonl (or data/sessions/<market>/<date>.jsonl, own journal, for bse/crypto)
- Replay: uv run tradedesk replay data/sessions/<date>.jsonl --watchlist data/watchlists/<date>.json [--alerts]
- Claude: uv run tradedesk scan --claude notify|veto ; uv run tradedesk review week ; uv run tradedesk mcp (also .mcp.json + .claude/skills analyze|review|backtest)
- Journal: uv run tradedesk journal fill <signal_id> --qty --price | journal exit <id> --qty --price --reason | journal stop <id> --to | journal positions | journal tag <id> rule_break "note" | journal stats | journal orders
- Paper book: uv run tradedesk paper update [--date]   (run after the close and `data load`)
- Alerts: uv run tradedesk alerts setup-telegram | alerts telegram-chat-id | alerts test ; dashboard: uv run tradedesk dashboard [--watchlist ...] [--session ...] [--journal data/journal.sqlite] (live embeds it by default at 127.0.0.1:8765; Today tab is the live view, Performance tab is EOD + weekly/monthly from the paper book)
- Evening scan: uv run tradedesk scan [--date YYYY-MM-DD] [--setup base_breakout] [--charts]   -> data/watchlists/<date>.json (+ PNGs)
- Backtest: uv run tradedesk backtest --setup base_breakout --from 2023-09-01 [--to ...] [--split 2025-09-01] [--out data/reports/trades.csv]
- Train model (shadow): uv run tradedesk train --from 2023-09-01 [--to ...] [--setup base_breakout] [--splits 4] [--dataset data/reports/ds.csv] [--final-test-frac 0.2] [--auto-threshold/--fixed-threshold]   -> data/models/<version>.joblib (+ .json); prints validation vs FINAL TEST metrics separately, a per-setup breakdown and a rules-vs-rules+ML economic comparison. `scan` then logs shadow probabilities to data/models/shadow.jsonl.
- Drift check: uv run tradedesk ml check-drift [--shadow-log data/models/shadow.jsonl] [--review-path data/reviews/queue.jsonl]   (also runs as a step of tradedesk-after-close; flags a review-queue item, never edits config/ml.yaml)

## Conventions
- Money and rates are `Decimal`, never float. Config percentages are fractions (0.001 = 0.1%). Market-data prices are float (pandas/DuckDB bound); accounting money is Decimal.
- Cost lines are rounded half-up to the paisa individually before summing; STT and stamp duty are rounded to the nearest RUPEE per trade. GST = 18% x (brokerage + exchange txn + SEBI) - not IPFT/STT/stamp. Brokerage = 0.1% capped at Rs 5 with a Rs 2 minimum.
- Net R:R = (gross reward − costs to target) / (gross risk + costs to stop). A stop-out loses more than 1R.
- The cost calculator never infers intraday vs delivery; the caller passes `TradeType`. Same-day buy+sell is INTRADAY.
- Charge rates live in config/risk.yaml under `costs:`. Real contract-note figures live in tests/golden/contract_notes.yaml; if INDmoney changes pricing, update both. tests/golden/rate_card_examples.yaml is a provisional stand-in for a missing real intraday note - delete once a real one lands.
- INDstocks facts baked into broker/indstocks/: REST codes are `NSE_3045`, WebSocket codes `NSE:3045`; candle `ts` is the OPEN time in epoch seconds, requests use epoch ms; ≤5 codes per candle call, ≤1000 per quote call, ≤3000 instruments per WS connection; one TOTP token live at a time (24h, 1 generation/min). Follow the endpoint pages and the OpenAPI spec, never the doc's FAQ (it contradicts them).
- Indicators (engine/indicators.py) are hand-written pandas with TradingView conventions (ema seeded on first value; rma = Wilder, SMA-seeded, na until n finite values; population stdev). No TA-Lib: its C DLL hits Windows Smart App Control on this machine.
- Pattern detectors (engine/patterns.py) evaluate the LAST bar of the frame they receive and return geometry models; look-ahead freedom comes from slicing the frame to the evaluation date. Pivots carry `confirmed_at`.
- tests/engine/test_indicators.py::test_daily_features_have_no_look_ahead is the leakage test for every feature column; extend `daily_features` and it is covered automatically.
- engine/engine.py::scan_day is THE daily scan; engine/intraday_engine.py::scan_bar is its intraday sibling. Signals are created ONLY by these two functions. scan_bar takes an IntradaySnapshot and slices every frame to the last CLOSED bar itself via engine/mtf.py::last_closed_bar before any setup sees it.
- Entry confirmation is live/confirmation.py::confirm_trigger (15-minute CLOSE above the level; the 09:15 bar never triggers; bars starting >= 15:00 defer to the daily close). The backtester falls back to daily bars when no 15-minute candles exist (open beyond trigger+1 ATR = chased; open past the stop = out at the open; a bar touching both stop and target = stop).
- live/trigger_monitor.py fails closed: no ticks for 120s pauses alerts (DATA STALE); alerts raised while paused are delivered marked [delayed] on resume; after a WebSocket reconnect `resync()` checks triggers/stops against REST day high/low before alerts resume.
- Alerts (M8): alerts/router.py routes by the entry's grade (A -> desktop+Telegram+dashboard, B -> dashboard, C -> log only). Telegram sends are queued and drained by AlertRouter.worker so the monitor never blocks. Dashboard = FastAPI + SSE on 127.0.0.1 only; DashboardState.publish() pushes every change. "Took it"/"Skip" -> live/session.py::apply_decision moves TRIGGERED -> TAKEN/SKIPPED. httpx's ASGITransport buffers responses: test SSE against a real uvicorn server on a free port.
- Journal (M9) is SQLite at data/journal.sqlite. Real fills are entered by hand or via Telegram; the broker order-updates feed is only captured raw (messages carry no instrument).
- Paper book (paper/book.py) simulates EVERY triggered signal with the live exit rules and full costs, sized per risk.yaml against its own equity, deliberately WITHOUT portfolio crowding limits. A setup with a negative rolling-30 paper expectancy is benched (never alerts).
- Claude (M10) lives in claude/: output schemas in claude/models.py are TEXT ONLY (tests assert no int/float field exists); claude/chart_read.py::apply_reads can only add a note, lower a grade or reject an entry - never a price, level, quantity or score. Spend is logged to data/claude_usage.jsonl and capped by claude.yaml monthly_spend_limit_usd.
- mcp_server.py exposes read-only tools (get_watchlist, get_position, analyze, journal_stats, backtest) via the mcp 2.x MCPServer API.
- risk/limits.py::RiskManager rebuilds Portfolio counters from journaled live trades in exit order so restarts are deterministic; it adds the results blackout on top of Portfolio.can_enter.
- Signal lifecycle is engine/lifecycle.py (ARMED -> TRIGGERED/CHASED/EXPIRED/INVALIDATED -> TAKEN/SKIPPED -> OPEN -> CLOSED); illegal transitions raise.
- Evening scan = backtest/runner.py::prepare_market + build_snapshot + engine.scan_day, then engine/scoring.py (grade), risk/sizing.py, engine/filters.py.
- Charts: alerts/charts.py renders mplfinance PNGs headless (Agg); geometry bar indices refer to the FULL feature frame, so pass the unsliced frame up to the arming session.
- The INDstocks feed ALREADY serves split/bonus-adjusted daily history, so `CandleStore` defaults to `apply_corporate_actions=False` and `load(adjusted=True)` does NOT rescale. Applying factors on top double-adjusts and corrupts history. The corporate_actions table still drives the `suspected_unadjusted_split` heuristic/audit only - always confirm against it before acting (rights issues and genuine crashes also trip the ratio heuristic).
- The trading calendar is the benchmark index's daily candle dates (config/universe.yaml `benchmark`); universe membership is computed as-of-date from stored candles (survivorship caveat stays in every backtest report).
- Windows Smart App Control is ON on this machine: it blocks unsigned DLLs in brand-new wheels. Pins in pyproject.toml exist for that reason; if a new package fails with "Application Control policy has blocked this file", pin an older, widely-distributed version. mypy itself is currently blocked here for the same reason - ruff + pytest are the reliable local checks.
- Prediction layer (M11) lives in prediction/: labeling.py (triple barrier: T1 -> 1, stop or gap-through-stop -> 0, vertical barrier max_hold -> 0), features.py (feature_version-tagged, all known at the arming close), train.py (purged walk-forward BY DATE with ml.yaml embargo_sessions, a genuinely locked final test set carved out before any fold, calibrated logistic/xgboost race, per-setup breakdown, compare_strategies() for the rules-vs-rules+ML economic comparison), predict.py (apply_probability can only LOWER a grade or halve qty, gated by ml.yaml enabled+not shadow; refuses to score a bundle whose feature_version doesn't match), calibration.py (drift_check pauses the layer when a bucket with >= 30 resolved signals drifts > 0.15). `tradedesk train` refuses --no-shadow; switching on is a config edit, never a CLI flag.
- Markets (M13) share one `Market` bundle (markets/market.py: name, code_prefix, CostModel, session/universe rules, benchmark, qty_step, min_notional, vix_required) so engine/backtest/scan code takes a `Market` instead of assuming NSE. `EquityCostModel` wraps risk/costs.py verbatim (NSE/BSE); `CryptoCostModel` computes CoinDCX's real charges directly (0.2% maker/taker, 1% Section 194S TDS on the sell leg only, 18% GST on the fee not the TDS, no STT/stamp/DP/SEBI). `qty_step`/`min_notional` support fractional crypto sizing (NSE stays whole-unit, qty_step=1.0). `Portfolio.sector_of` defaults to an empty dict, so a market with no sector mapping (crypto) simply never triggers the sector cap - no special-casing needed.
- CoinDCX quirks (broker/coindcx/): candles endpoint silently ignores startTime/endTime unless BOTH are passed together; `CoinDcxClient.candles_history()` takes tradedesk scrip codes, needing `register_pairs()` first or every call 422s; some 2019-2020 history has a synthetic duplicate-calendar-day phantom bar, deduped by `backtest/runner.py::_dedupe_by_calendar_day` (no-op for NSE). Crypto's INR-stablecoin pairs (USDTINR/USDCINR) are excluded from its universe (`Market.universe_rules.exclude_codes`) - their near-zero volatility otherwise pollutes breakout scans since they're also the most liquid pairs.
- A missing VIX reading fails the regime closed (RISK_OFF, size 0) only when `Market.vix_required` is True (NSE); crypto/BSE have no VIX and stay False so `vix=None` degrades quietly instead of blocking.
- Intraday infrastructure (mtf.py::align/last_closed_bar, intraday_regime.py::classify_intraday_regime, intraday_engine.py::scan_bar, intraday_signals.py, backtest/null_baseline.py::run_null_baseline) is real, tested against live NSE data, and NOT wired into any live session, CLI command or the eligibility gate. `null_baseline.py` is the reusable matched-random-timing gate any new intraday setup must clear before being trusted - built after VWAP Reclaim (setups/intraday/vwap_reclaim.py) was tested and found to NOT beat random timing (p=0.44), consistent with every rule-based setup tested in this project so far.
- `tradedesk_lab/` (M14-M18) is an isolated research sandbox: `verify-base`/`base_manifest.json` hash-checks the pre-existing repo to prove no production path was touched; its own SQLite Registry and `data/m14_m18/` output dir; CPCV/PBO/DSR statistical rigor for re-checks of the mined 2023-2026 window; `forward.py` scores new candidates prospectively against a frozen, hash-verified model from a fixed activation date - the only place in this sandbox generating genuinely new (not re-mined) evidence. `harness/gauntlet.py::run_gauntlet` reuses `backtest/null_baseline.py`, `validation.py::walk_forward`, and `markets/costs.py` rather than re-deriving them, stops at the first failing stage, and checks `spec.py::KillCriteria` (net expectancy, sample size, drawdown, losing streak) against the realised trade sequence, never a reshuffle. Anything here that clears the same eligibility bar as production becomes a `review_queue.py` item, never an automatic promotion. Served at `/lab` under the main dashboard's port via `app.mount()`; its own `fetch()` calls are prefixed at runtime (`API_BASE`) so they resolve correctly whether served standalone or mounted.
- `tradedesk-*` scheduled tasks that need repo-root imports from a console-script entry point (e.g. the dashboard importing `tradedesk_lab`) must explicitly extend `sys.path` - unlike `python -m`/pytest, a console script does not add the repo root automatically.
