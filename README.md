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

Current milestone: **M2** — INDstocks client (auth, instruments, candles, quotes, WebSocket).
Live verification pending credentials. See PLAN.md §14.

Note: this folder is OneDrive-synced. Exclude `.venv/` from sync (or accept a slow first install).
