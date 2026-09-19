# tradedesk

Short-term momentum scanner across **NSE, BSE, and crypto (CoinDCX)**.
Evening scan on daily charts, live trigger monitoring, holds of hours to 10 sessions.

**It alerts; a human places every order.** Order placement is not implemented and is
deliberately gated behind a milestone that hasn't been started. Full spec: [PLAN.md](PLAN.md).

NSE and BSE trade through the INDstocks (INDmoney) API; crypto reads CoinDCX's public
endpoints (no credentials needed). Crypto and BSE are research/paper-only — their own
call logs (`data/reports/crypto_signal_tracking.jsonl`, `.../bse_signal_tracking.jsonl`)
track every signal and grade it later, since neither has a paper book like NSE's. **Crypto's
own backtest verdict is honestly negative** (-0.515R expectancy on 22 large caps, 2018-2026 —
CoinDCX's 1% TDS on every sell leg is structural, not a tuning problem); it stays live purely
to keep collecting evidence, not because it's expected to work.

## Status

| Milestone | State |
|---|---|
| M1 cost model | Verified to the paisa against 5 real delivery contract notes. Intraday is provisional (no real intraday trade exists yet) — hand-computed from the published rate card. |
| M2 broker client | **Signed off live.** Full-universe 10y load: 4.65M bars, 0 errors. Daily close matches NSE's official close exactly. |
| M3 data layer | **Signed off live.** Quality report reviewed across 2,639 codes; universe-by-date clean. |
| M4 indicators | Passing against an independent hand-derived reference (TradingView's CSV export is paywalled). |
| M5–M11 | Built and unit-tested: setups, scan, backtester, live monitor, alerts, journal, paper book, Claude advisor, prediction layer. |
| M13 crypto + BSE | Live full-universe monitoring on all three markets. |
| M11 prediction layer | Shadow-only (never places or sizes a trade). Purged walk-forward validation, a locked final-test set, per-model hyperparameter tuning, per-setup breakdown, sector-return features, and a closed calibration-drift loop (`tradedesk ml check-drift`) as of 2026-09-13. Honest current finding: OOS ROC-AUC ~0.55, no threshold shows a real edge yet (`has_edge=False`) — it's a rigorous pipeline, not (yet) a profitable one. A follow-up mean-reversion feature search found a real gross edge (+0.067R, statistically significant) that does not survive real costs at policy-compliant sizing; a model trained on it scored a coin-flip OOS AUC (0.4996) — reported honestly as a negative result, not shipped. |
| Setup eligibility gate | **Every setup currently shows NO TRADE.** As of 2026-09-13 no signal alerts until its setup has *proven* itself — ≥500 resolved trades, ≥100 out-of-sample, ≥80% win rate, and (the strongest check) beats a matched random-entry timing baseline by a real margin. None of the three live NSE setups clear it yet; measured against random timing they are actually *worse* than picking an entry at random. This is deliberate, not a bug — see "Signal eligibility" below. |
| Intraday research (unreleased) | A from-scratch intraday/scalping engine (session VWAP, multi-timeframe alignment, a 10-state regime taxonomy, and a null-timing validation gate) was built and proven against 2 years of real 1–60 minute data for 5 liquid NSE names. The one setup tried (VWAP Reclaim) failed the same random-timing test above (p=0.44) and is built, tested, and wired into nothing live. Infrastructure only — no intraday alerting exists. |
| Research lab / strategy validation harness | `tradedesk_lab/` (M14–M18) is an isolated sandbox — hash-verified to never touch production — for statistical-rigor re-checks, prospective forward scoring, and crypto intraday research. `tradedesk_lab/harness/` is a reusable gauntlet (random-entry benchmark → in-sample → walk-forward → parameter sensitivity → Monte Carlo → regime split → kill criteria) any new rule set can be run through before it's trusted. First real run: `MomentumContinuation` on crypto H1 data, correctly killed at the random-entry-benchmark stage (p=0.986, worse than random timing). Results render at `/lab`'s Harness tab, mounted under the main dashboard's port. |

Details: [docs/signoff-m2-m3.md](docs/signoff-m2-m3.md), [docs/signoff-crypto-phase4.md](docs/signoff-crypto-phase4.md).

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
uv run tradedesk data load                  # incremental daily candles (--market crypto|bse for the others)
uv run tradedesk scan --charts              # evening watchlist + PNGs
uv run tradedesk live                       # 09:15-15:35: triggers, position watch, dashboard
uv run tradedesk paper update               # after the close (NSE only - no paper book for crypto/BSE)
uv run tradedesk journal stats
uv run tradedesk backtest --setup base_breakout --from 2023-09-01
uv run tradedesk train --from 2023-09-01    # prediction layer, shadow only
uv run tradedesk ml check-drift             # calibration drift -> review queue, never auto-acts
uv run tradedesk dashboard                  # http://127.0.0.1:8765 - Calls/Report/Crypto/BSE/Lookup/Review
```

`uv run tradedesk --help` lists the rest (replay, alerts, review, mcp).

### Unattended automation

~15 Windows Scheduled Tasks (`Get-ScheduledTask -TaskName tradedesk-*`) run this without you:
data loads, live sessions, after-close scans and `ml check-drift` on each market, per-market
signal trackers, research trackers, EOD learning, an options snapshot, and an always-on
dashboard. See `CLAUDE.md` for the full list and the `--no-sync` scheduling gotcha. To run
any check manually right now instead of waiting for its schedule:

```powershell
Start-ScheduledTask -TaskName tradedesk-after-close       # NSE
Start-ScheduledTask -TaskName tradedesk-bse-tracker        # BSE
Start-ScheduledTask -TaskName tradedesk-crypto-tracker     # crypto
```

## Signal eligibility

A signal only alerts once its setup has evidence, not just a decent-looking score. The
threshold (`config/setups.yaml`'s `eligibility:` block) is: ≥500 resolved trades, ≥100 held
out of sample, ≥80% win rate, positive expectancy, and — the check that actually matters —
its real expectancy must beat a *matched random-entry timing* baseline by a real margin. That
last one is the one nothing else catches: a setup can post an ordinary-looking win rate while
being genuinely worse than picking an entry at random on the same stock and day, which is
exactly what happened to all three live NSE setups when measured. Until a setup clears every
bar, `tradedesk scan` reports `NO TRADE` with the specific unmet reasons — that is the correct,
intended output, not a broken scan. Lowering any threshold is a visible, commented config edit,
never silent.

## Notes that bite

- **The INDstocks feed already serves split/bonus-adjusted history.** The store therefore does
  *not* re-apply corporate-action factors — doing so double-adjusts and silently corrupts prices.
  See `CandleStore(apply_corporate_actions=...)`.
- A candle window narrower than ~6 days ending at "now" silently drops the newest bar — an
  undocumented API quirk. `candles_history()` pads around it.
- Windows Smart App Control blocks some brand-new wheels (numpy 2.5, pandas 3); hence the pins in
  `pyproject.toml`. It also blocks `mypy` itself on this machine — `ruff check` + `pytest` are
  the reliable local checks; don't rely on the `mypy` line above actually running.
- This folder is OneDrive-synced — exclude `.venv/` from sync, or accept a slow first install.

## Scope

Personal project, one user, one account. Not investment advice, not a product, no warranty.
Backtests carry survivorship bias (the universe is built from the candles the API still serves)
and every reported result says so.
