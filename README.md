# tradedesk

Short-term momentum scanner for NSE stocks using the INDstocks (INDmoney) API.
It alerts; the human places every order. Full spec: [PLAN.md](PLAN.md).

## Quickstart

```
uv sync                      # installs pinned Python 3.12 + deps into .venv
uv run pytest                # unit, property and golden (contract-note) tests
uv run ruff check . && uv run mypy src
uv run tradedesk config check
uv run tradedesk costs --type delivery --qty 20 --entry 842 --exit 890
```

## INDstocks setup (M2)

```
uv run tradedesk auth setup          # Client ID, MPIN, TOTP secret → Windows Credential Manager
uv run tradedesk auth check          # generates a token via TOTP, calls /user/profile and /funds
uv run tradedesk instruments refresh # data/instruments/{equity,fno,index}.csv
uv run tradedesk candles NSE_3045 --interval 1day --days 30
uv run tradedesk quote NSE_3045 NSE_2885
uv run tradedesk stream NSE:3045 --seconds 30   # market hours only
```

Get the three secrets from indstocks.com/app/api-trading/access-tokens → "Setup TOTP".
The TOTP secret is shown exactly once.

## Data store (M3)

```
uv run tradedesk data sync-instruments                 # instruments table (NSE EQ + indices)
uv run tradedesk data load                             # 10 y daily for the universe + benchmark
uv run tradedesk data load --interval 15minute         # 2 y of 15-minute bars (~10k calls)
uv run tradedesk data import-actions CF-CA-equities.csv   # NSE corporate actions export
uv run tradedesk data import-results board-meetings.csv   # NSE board meetings export
uv run tradedesk data quality --out data/reports/quality.csv
uv run tradedesk data universe --on 2026-09-11
```

Raw candles live in `data/tradedesk.duckdb`; splits and bonuses are applied when read.

Milestones M1–M3 are built and unit-tested; live sign-off waits on INDstocks credentials.
Note: Windows Smart App Control blocks some brand-new wheels (numpy 2.5, pandas 3) — hence the
pins in pyproject.toml. See PLAN.md §14.

Note: this folder is OneDrive-synced. Exclude `.venv/` from sync (or accept a slow first install).
