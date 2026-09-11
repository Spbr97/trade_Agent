# tradedesk

Short-term momentum scanner for NSE stocks on the INDstocks (INDmoney) API.
Evening scan on daily charts, live trigger monitoring, holds of hours to 10 sessions.

**It alerts; a human places every order.** Order placement is not implemented and is
deliberately gated behind a milestone that hasn't been started. Full spec: [PLAN.md](PLAN.md).

## Status

| Milestone | State |
|---|---|
| M1 cost model | Verified to the paisa against 5 real delivery contract notes. Intraday is provisional (no real intraday trade exists yet) — hand-computed from the published rate card. |
| M2 broker client | **Signed off live.** Full-universe 10y load: 4.65M bars, 0 errors. Daily close matches NSE's official close exactly. |
| M3 data layer | **Signed off live.** Quality report reviewed across 2,639 codes; universe-by-date clean. |
| M4 indicators | Passing against an independent hand-derived reference (TradingView's CSV export is paywalled). |
| M5–M11 | Built and unit-tested: setups, scan, backtester, live monitor, alerts, journal, paper book, Claude advisor, prediction layer. |

Details: [docs/signoff-m2-m3.md](docs/signoff-m2-m3.md).

## Quickstart

```bash
uv sync                                   # pinned Python 3.12 + deps into .venv
uv run pytest
uv run ruff check . && uv run mypy src
uv run tradedesk config check
uv run tradedesk costs --type delivery --qty 20 --entry 842 --exit 890
```

## Credentials

```bash
uv run tradedesk auth setup    # Client ID, MPIN, TOTP secret -> OS keychain
uv run tradedesk auth check    # generates a token via TOTP, calls /user/profile
```

Get all three from indstocks.com/app/api-trading/access-tokens → **Setup TOTP** (log in first;
the direct URL redirects otherwise). The TOTP secret is shown exactly once. Nothing is ever
written to a file — secrets live in the OS keychain via `keyring`.

## Daily use

```bash
uv run tradedesk data load                  # incremental daily candles
uv run tradedesk scan --charts              # evening watchlist + PNGs
uv run tradedesk live                       # 09:15-15:35: triggers, position watch, dashboard
uv run tradedesk paper update               # after the close
uv run tradedesk journal stats
uv run tradedesk backtest --setup base_breakout --from 2023-09-01
uv run tradedesk train --from 2023-09-01    # prediction layer, shadow only
```

`uv run tradedesk --help` lists the rest (replay, alerts, review, dashboard, mcp).

## Notes that bite

- **The INDstocks feed already serves split/bonus-adjusted history.** The store therefore does
  *not* re-apply corporate-action factors — doing so double-adjusts and silently corrupts prices.
  See `CandleStore(apply_corporate_actions=...)`.
- A candle window narrower than ~6 days ending at "now" silently drops the newest bar — an
  undocumented API quirk. `candles_history()` pads around it.
- Windows Smart App Control blocks some brand-new wheels (numpy 2.5, pandas 3); hence the pins in
  `pyproject.toml`.
- This folder is OneDrive-synced — exclude `.venv/` from sync, or accept a slow first install.

## Scope

Personal project, one user, one account. Not investment advice, not a product, no warranty.
Backtests carry survivorship bias (the universe is built from the candles the API still serves)
and every reported result says so.
