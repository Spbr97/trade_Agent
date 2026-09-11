# tradedesk

Short-term momentum scanner for NSE stocks using the INDstocks (INDmoney) API.
Evening scan on daily charts, live trigger monitoring, trades held hours to 10 sessions.
It alerts; the human places every order.

**Status: M1-M11 built and unit-tested. Live credentials in place (`tradedesk auth setup`, keychain). M2 PASS: full-universe 10y history load, 0 errors/429s; daily close matches NSE's official close to the paisa; found and fixed a real API defect (silent data loss on narrow candle windows near "now" - commit 67f39c9). Close-finality timing (PLAN.md 5.2) scheduled unattended for the next trading day, not yet measured. M3 PASS: quality report reviewed on 2,639 real codes (issues attributable to late listings/illiquid names/one feed anomaly, not pipeline bugs); split/bonus days don't crash; universe-by-date computes cleanly. Full writeup: docs/signoff-m2-m3.md. M1 cost model verified against 5 FY25-26 ledger bills (delivery, paisa-exact); intraday has NO real note yet - provisionally covered by tests/golden/test_rate_card.py (rate-card examples, not a real bill; replace with a real note the moment one exists). M4 indicator check passes provisionally against an independent hand-derived reference (no TradingView premium available - see tests/golden/indicators/README.md); replace with a real TradingView export if premium access exists later.** Next: paper trading, then M12 (order placement) only after Phase 3. Open non-blocking items: corporate-actions CSV import (real split adjustment), results-calendar CSV (earnings blackout). Full spec and milestone list: PLAN.md.

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
- Live session: uv run tradedesk live [--watchlist data/watchlists/<date>.json] [--until 15:35]   -> records data/sessions/<date>.jsonl
- Replay: uv run tradedesk replay data/sessions/<date>.jsonl --watchlist data/watchlists/<date>.json [--alerts]
- Claude: uv run tradedesk scan --claude notify|veto ; uv run tradedesk review week ; uv run tradedesk mcp (also .mcp.json + .claude/skills analyze|review|backtest)
- Journal: uv run tradedesk journal fill <signal_id> --qty --price | journal exit <id> --qty --price --reason | journal stop <id> --to | journal positions | journal tag <id> rule_break "note" | journal stats | journal orders
- Paper book: uv run tradedesk paper update [--date]   (run after the close and `data load`)
- Alerts: uv run tradedesk alerts setup-telegram | alerts telegram-chat-id | alerts test ; dashboard: uv run tradedesk dashboard (live embeds it by default at 127.0.0.1:8765)
- Evening scan: uv run tradedesk scan [--date YYYY-MM-DD] [--setup base_breakout] [--charts]   -> data/watchlists/<date>.json (+ PNGs)
- Backtest: uv run tradedesk backtest --setup base_breakout --from 2023-09-01 [--to ...] [--split 2025-09-01] [--out data/reports/trades.csv]
- Train model (shadow): uv run tradedesk train --from 2023-09-01 [--to ...] [--setup base_breakout] [--splits 4] [--dataset data/reports/ds.csv]   -> data/models/<version>.joblib (+ .json); `scan` then logs shadow probabilities to data/models/shadow.jsonl

## Conventions
- Money and rates are `Decimal`, never float. Config percentages are fractions (0.001 = 0.1%).
- Cost lines are rounded half-up to the paisa individually before summing; STT and stamp duty are rounded to the nearest RUPEE per trade (ledger-verified: Rs 0.86 STT -> 1, Rs 0.13 stamp -> 0). GST = 18% x (brokerage + exchange txn + SEBI) - not IPFT/STT/stamp. Brokerage = 0.1% capped at Rs 5 with a Rs 2 minimum (Muhurat reversal 2.36 = 2 + GST).
- Net R:R = (gross reward − costs to target) / (gross risk + costs to stop). A stop-out loses more than 1R. PLAN.md §7's worked numbers (1.4R/2.3R, ~₹110) assume ₹20/order; INDmoney actually charges 0.1% capped at ₹5 (min ₹2) per order, so the same card is ₹72 and 1.61R/2.48R.
- The cost calculator never infers intraday vs delivery; the caller passes `TradeType`. Same-day buy+sell is INTRADAY.
- Charge rates live in config/risk.yaml under `costs:`. Real contract-note figures live in tests/golden/contract_notes.yaml; if INDmoney changes pricing, update both.
- tests/golden/rate_card_examples.yaml + test_rate_card.py are a PROVISIONAL stand-in for the missing intraday golden notes: hand-computed from the documented rate card, not a real bill. They are never the M1 done-when count (test_golden_coverage_report reads contract_notes.yaml only) - delete rate_card_examples.yaml once a real intraday note lands there.
- INDstocks facts baked into broker/indstocks/: REST codes are `NSE_3045`, WebSocket codes `NSE:3045`; candle `ts` is the OPEN time in epoch seconds, requests use epoch ms; ≤5 codes per candle call, ≤1000 per quote call, ≤3000 instruments per WS connection; one TOTP token live at a time (24 h, 1 generation/min).
- The doc's FAQ section contradicts the endpoint pages (different WS URL, `symbols` param). Follow the endpoint pages and the OpenAPI spec, never the FAQ.
- Market-data prices are float (pandas/DuckDB bound); accounting money is Decimal.
- Indicators (engine/indicators.py) are hand-written pandas with TradingView conventions (ema seeded on first value; rma = Wilder, SMA-seeded, na until n finite values; population stdev). No TA-Lib: its C DLL would hit Smart App Control.
- Pattern detectors (engine/patterns.py) evaluate the LAST bar of the frame they receive and return geometry models; look-ahead freedom comes from slicing the frame to the evaluation date. Pivots carry `confirmed_at`.
- tests/engine/test_indicators.py::test_daily_features_have_no_look_ahead is the leakage test for every feature column; extend `daily_features` and it is covered automatically.
- engine/engine.py::scan_day is THE scan. The backtester (backtest/runner.py) calls it with frames sliced to each session; the live evening scan (M6) calls it with today's frames. Never add signal logic anywhere else.
- Entry confirmation is live/confirmation.py::confirm_trigger (15-minute CLOSE above the level; the 09:15 bar never triggers; bars starting >= 15:00 defer to the daily close; optional volume-vs-slot-norm). The live monitor and the backtester both call it; the backtester uses it whenever 15-minute candles are in the store, else falls back to daily bars (open beyond trigger+1 ATR = chased; open past the stop = out at the open; a bar touching both stop and target = stop). Exits are daily-bar in the backtester either way.
- live/trigger_monitor.py fails closed: no ticks for 120 s pauses alerts (DATA STALE); alerts raised while paused are delivered marked [delayed] on resume; after a WebSocket reconnect `resync()` checks triggers/stops against REST day high/low before alerts resume.
- tests/live/test_live.py::test_replay_matches_backtester_triggers is the M7 parity test (same 15-minute bars as ticks -> same triggered ids and fill prices).
- Alerts (M8): alerts/router.py routes by the entry's grade (A -> desktop+Telegram+dashboard, B -> dashboard, C -> log only) or by level for non-signal alerts; Telegram sends are queued and drained by AlertRouter.worker so the monitor never blocks. Desktop = PowerShell/WinRT toast + winsound (no packages). Telegram = raw Bot API over httpx; token in keychain `tradedesk-telegram`; only alerts.yaml `allowed_chat_id` is answered. Dashboard = FastAPI + SSE on 127.0.0.1 only; DashboardState.publish() pushes every change.
- Telegram "Took it"/"Skip" -> live/session.py::apply_decision moves TRIGGERED -> TAKEN/SKIPPED (the journal records it in M9).
- httpx's ASGITransport buffers responses: test SSE against a real uvicorn server on a free port (see tests/alerts).
- Journal (M9) is SQLite at data/journal.sqlite (journal/db.py). Real fills are entered by hand (`journal fill/exit`) or via Telegram Took it/Skip; the broker order-updates feed is only captured raw (`order_updates` table) because its messages carry no instrument - matching to signals is manual until an order-book endpoint is wired.
- Paper book (paper/book.py) simulates EVERY triggered signal with the live exit rules and full costs, sized per risk.yaml against its own equity, deliberately WITHOUT portfolio crowding limits (it measures setups, not queue order). `journal.stats.track_record` -> TrackRecord -> scoring; a setup with a negative rolling-30 paper expectancy is benched (never alerts).
- Claude (M10) lives in claude/: output schemas in claude/models.py are TEXT ONLY (tests assert no int/float field exists); claude/chart_read.py::apply_reads is the only place a read touches the watchlist and it can only add a note, lower a grade or reject an entry - never a price, level, quantity or score. Trigger notes are queued by TriggerNoteWorker.on_alert and produced in a background task AFTER the alert is out. The SDK is called via client.messages.parse(output_format=<pydantic>); credentials resolve from ANTHROPIC_API_KEY / `ant auth login`; spend is logged to data/claude_usage.jsonl and capped by claude.yaml monthly_spend_limit_usd. Models are those PLAN.md names (sonnet-5 for reads/review, haiku-4-5 for notes); change in config/claude.yaml.
- mcp_server.py exposes read-only tools (get_watchlist, get_position, analyze, journal_stats, backtest) via the mcp 2.x MCPServer API (`from mcp.server.mcpserver import MCPServer`; FastMCP no longer exists).
- risk/limits.py::RiskManager rebuilds the Portfolio counters from journaled live trades in exit order (weekly start equity, consecutive-loss pause, re-entry cooldowns) so restarts are deterministic; it adds the results blackout on top of Portfolio.can_enter.
- Signal lifecycle is engine/lifecycle.py (ARMED -> TRIGGERED/CHASED/EXPIRED/INVALIDATED -> TAKEN/SKIPPED -> OPEN -> CLOSED); illegal transitions raise.
- Evening scan = backtest/runner.py::prepare_market + build_snapshot + engine.scan_day, then engine/scoring.py (grade), risk/sizing.py, engine/filters.py. tests/scan proves watchlist signals for a date == the backtester's for that date (given the same exclusion set).
- Scoring weights are in engine/scoring.py::WEIGHTS; TrackRecord (rolling paper expectancy, benched flag) is supplied by M9 and neutral until 30 trades.
- Charts: alerts/charts.py renders mplfinance PNGs headless (Agg); geometry bar indices refer to the FULL feature frame, so pass the unsliced frame up to the arming session.
- Portfolio limits live in backtest/portfolio.py::Portfolio.can_enter and are the same object the paper book and live risk manager will use (M9).
- Backtest e2e fixtures live in tests/backtest/synth_universe.py; the ATR stop rule (2x) is deliberately relaxed to 3x there because ATR decays inside a synthetic tight base.
- Candle store keeps RAW candles; split/bonus adjustment is applied on read (`CandleStore.load(adjusted=True)`) from the corporate_actions table. Never write adjusted prices back.
- The trading calendar is the benchmark index's daily candle dates (config/universe.yaml `benchmark`); universe membership is computed as-of-date from stored candles (survivorship caveat stays in every backtest report).
- Windows Smart App Control is ON on this machine: it blocks unsigned DLLs in brand-new wheels (numpy 2.5 / pandas 3 failed). Pins in pyproject.toml exist for that reason; if a new package fails with "Application Control policy has blocked this file", pin an older, widely-distributed version.
- Prediction layer (M11) lives in prediction/: labeling.py (triple barrier: T1 -> 1, stop or gap-through-stop -> 0, vertical barrier max_hold -> 0; a bar touching both = stop, as in the backtester), features.py (31 features, all known at the arming close; tests assert a truncated frame gives identical features), train.py (build_dataset from a BacktestResult, purged walk-forward BY DATE with ml.yaml embargo_sessions, sigmoid-calibrated logistic baseline; LightGBM only if importable AND better OOS Brier - it is NOT installed here, Smart App Control), predict.py (apply_probability can only LOWER a grade or halve qty, gated by ml.yaml enabled+not shadow; shadow mode only appends a score note and logs), calibration.py (drift_check pauses the layer when a bucket with >= 30 resolved signals drifts > 0.15). `tradedesk train` refuses --no-shadow; switching on is a config edit, never a CLI flag.
- pydantic models: Candle, Signal, Position, TradeCard, RiskStatus, RegimeSnapshot, HealthStatus
- One module per setup, implementing the Setup protocol in setups/base.py
- New setups ship disabled until their backtest report and first 30 paper trades are reviewed
