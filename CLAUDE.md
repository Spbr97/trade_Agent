# tradedesk

Short-term momentum scanner for NSE stocks using the INDstocks (INDmoney) API.
Evening scan on daily charts, live trigger monitoring, trades held hours to 10 sessions.
It alerts; the human places every order.

**Status: M1-M4 built and unit-tested. Pending live data (needs `tradedesk auth setup`): M2 sign-off (NSE close check), M3 sign-off (quality report on real history). M1 golden tests pending contract-note figures; M4 TradingView golden tests pending an export in tests/golden/indicators/.** Next: M5 (three setups + event-driven backtester). Full spec and milestone list: PLAN.md.

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
- Live session: uv run tradedesk live            (M7+)
- Evening scan: uv run tradedesk scan --date today   (M6+)
- Backtest: uv run tradedesk backtest --setup base_breakout --from 2023-09-01   (M5+)
- Train model (shadow): uv run tradedesk train --shadow   (M11+)

## Conventions
- Money and rates are `Decimal`, never float. Config percentages are fractions (0.001 = 0.1%).
- Cost lines are rounded half-up to the paisa individually before summing, matching contract notes.
- Net R:R = (gross reward − costs to target) / (gross risk + costs to stop). A stop-out loses more than 1R. PLAN.md §7's worked numbers (1.4R/2.3R, ~₹110) assume ₹20/order; INDmoney actually charges 0.1% capped at ₹5 (min ₹2) per order, so the same card is ₹72 and 1.61R/2.48R.
- The cost calculator never infers intraday vs delivery; the caller passes `TradeType`. Same-day buy+sell is INTRADAY.
- Charge rates live in config/risk.yaml under `costs:`. Real contract-note figures live in tests/golden/contract_notes.yaml; if INDmoney changes pricing, update both.
- INDstocks facts baked into broker/indstocks/: REST codes are `NSE_3045`, WebSocket codes `NSE:3045`; candle `ts` is the OPEN time in epoch seconds, requests use epoch ms; ≤5 codes per candle call, ≤1000 per quote call, ≤3000 instruments per WS connection; one TOTP token live at a time (24 h, 1 generation/min).
- The doc's FAQ section contradicts the endpoint pages (different WS URL, `symbols` param). Follow the endpoint pages and the OpenAPI spec, never the FAQ.
- Market-data prices are float (pandas/DuckDB bound); accounting money is Decimal.
- Indicators (engine/indicators.py) are hand-written pandas with TradingView conventions (ema seeded on first value; rma = Wilder, SMA-seeded, na until n finite values; population stdev). No TA-Lib: its C DLL would hit Smart App Control.
- Pattern detectors (engine/patterns.py) evaluate the LAST bar of the frame they receive and return geometry models; look-ahead freedom comes from slicing the frame to the evaluation date. Pivots carry `confirmed_at`.
- tests/engine/test_indicators.py::test_daily_features_have_no_look_ahead is the leakage test for every feature column; extend `daily_features` and it is covered automatically.
- Candle store keeps RAW candles; split/bonus adjustment is applied on read (`CandleStore.load(adjusted=True)`) from the corporate_actions table. Never write adjusted prices back.
- The trading calendar is the benchmark index's daily candle dates (config/universe.yaml `benchmark`); universe membership is computed as-of-date from stored candles (survivorship caveat stays in every backtest report).
- Windows Smart App Control is ON on this machine: it blocks unsigned DLLs in brand-new wheels (numpy 2.5 / pandas 3 failed). Pins in pyproject.toml exist for that reason; if a new package fails with "Application Control policy has blocked this file", pin an older, widely-distributed version.
- pydantic models: Candle, Signal, Position, TradeCard, RiskStatus, RegimeSnapshot, HealthStatus
- One module per setup, implementing the Setup protocol in setups/base.py
- New setups ship disabled until their backtest report and first 30 paper trades are reviewed
