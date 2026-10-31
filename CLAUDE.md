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
ever alerting (500 resolved trades / 100 OOS / +0.05R after-cost expectancy /
beat-random-by-0.10R; accuracy is descriptive, and unknown evidence fails
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

**Automation (since 2026-10-18 on an Oracle Always Free VM, not the laptop)**: the pipeline
runs on `ubuntu@140.245.20.98` (Ubuntu 24.04 ARM, repo at `/opt/tradedesk`, user `tradedesk`)
as 35 systemd timers rendered from `deploy/schedule.json` (exported from the old Windows tasks)
plus an always-on dashboard and Cloudflare quick tunnel - see docs/cloud-deploy.md. Deploys are
`git archive HEAD` copied over and `sudo bash deploy/linux/install.sh`, which also restarts the
dashboard (a running app keeps old code otherwise) and re-records the lab's base manifest for
the deployed code. The laptop's `tradedesk-*` Windows tasks are DISABLED as a cold standby:
never run both - the hash-chained ledgers must not fork. The server has no keychain:
secrets live in root-only `/etc/tradedesk/env` (ntfy topic, keyring-file password) and an
encrypted keyring file (`tradedesk.server_keyring`, INDstocks login); `tradedesk-cli` on the
server runs the CLI with that environment. Windows-sealed backslash paths in evidence resolve
via symlinks from `deploy/linux/link_windows_paths.py` - never edit modules that hash their own
source into evidence (`digest(Path(__file__))`) to fix paths. Alerts reach the phone through ntfy
(`task_health.alert`); `scripts/send_digest.py`, hooked as ExecStopPost on the jobs it reports
on, sends close-job, end-of-day, intraday-check, multi-day-call, earnings-drift and weekly
digests; drift calls go out whenever that day's price stages loaded, even if a later
research-only stage fails the after-close job, and review items go out as one push per run. Owner's decision 2026-10-24: about 5 pushes a weekday, not 30 - drift calls, review items, trade alerts and every failure/warning push at once; job successes are not pushed; each market's day (end-of-day, intraday study, movers, multi-day calls: count, live record, top 5) comes as one evening summary at 21:15 (`digests.evening_summary`, sent even if that job fails), crypto's intraday study and movers as one morning push; the full multi-day list with stop/target is on the dashboard's Research tab.
Every multi-day call push carries each rule's live record (avg rupees per Rs 10,000 call after
charges) beside the random picks. Owner's rule (2026-10-21): phone figures count everything
cut at the trade - all charges, STT, crypto's 1% TDS (0.56% of a Rs 10,000 delivery round trip
with the DP charge, not the study's 0.39%) - and never deduct the income tax settled at the
year-end filing. Research pass bars that were pre-registered after tax stay as they were. A paper monthly
BTC/ETH plan (src/tradedesk/crypto_monthly.py, Rs 5,000 each, never sold) runs beside the
normal crypto calls, recorded and pushed once a month from tradedesk-crypto-research-tracker.
Six files deploy with CRLF bytes (.gitattributes) because sealed research cohorts hashed them so. Oracle cost safety: a `free-tier-only` quota policy, a SGD budget alert, and
`deploy/oracle/billing_guard.py` every 5 min on both the server and the laptop (kill switch at
SGD 0.30) - the owner cannot afford any Oracle charge. Each machine keeps its own accepted
resource list: accept a new resource (`--accept`) on BOTH, or the other keeps pushing it
(2026-10-24: the laptop re-pushed the default "Tenant Admin Policy" accepted only on the server). Memory safety: every batch job runs in
`tradedesk-jobs.slice` (one shared cap at 85% of RAM, so a runaway job is stopped alone while the
dashboard, tunnel and billing guard keep room), a 4 GB swap file on the boot volume cushions peaks,
and `deploy/linux/memory_guard.py` (every minute) pushes low/critical memory, any out-of-memory
kill and the all-clear. Nightly backups of data/ are pulled to the
laptop (`deploy/linux/backup.py`, `deploy/laptop/pull_backups.py`); the laptop also watches
that the server is alive (`deploy/laptop/server_watch.py`). Dashboard (2026-10-18 layout): Today
(NSE/BSE, opens with earnings-drift calls), Crypto, Scoreboard, Markets, Review, Research; the
research lab is mounted at `/lab` under the same origin/port.

**What may alert (2026-10-18)**: `profit_gate.py` judges every setup per market daily on LIVE
calls after costs (time-uniform confidence sequence, NSE+BSE pooled): TRADEABLE (lower bound >
+0.05R, 50+ calls) alerts at full size; PROVISIONAL (500+ historical gauntlet trades at +0.20R or
better on that market, NSE/BSE) alerts at a quarter size and is stopped automatically below
break-even or after 12 straight losses; `build_watchlist` takes these verdicts (score floor,
regime, filters and risk limits still apply; a missing or stale status allows nothing).
Owner-approved 2026-10-28: the momentum rule setups (`r_*` except mean-reversion triggers) are one bet, so
on one evening their alertable calls share ONE full position's risk (`evening_scan.cap_momentum_class`,
best score first); and the self-review's rolling monitor flags a setup when its 20-day window loses money
after costs (mean net R < 0, each call once), no longer when its hit rate is under 30%.
`earnings_drift.py` is the one strategy that passed a pre-registered bar after costs
(tradedesk_lab/edge_hunt.py: liquid NSE stocks jumping 5%+ vs the market on 2x volume after
results beat the market over 20 sessions, 2016-2026 - by +1.6% BEFORE costs, about +0.7% after
the real 0.564% round trip of a Rs 10,000 call; +1.8% net a call; better above NIFTY's 200-day
average, +2.2%, than below, +0.8% - tradedesk_lab/earnings_drift_timing.py): calls after each NSE close, Rs
10,000 each, sold at +10% or the 20th close (from 2026-10-19, tradedesk_lab/takeoff_capture.py: ~32% more
rupees a year on Rs 1 lakh), no stop, stops issuing if live results trail the market. The owner's real
fills are read after each close (read-only order book, src/tradedesk/owner_trades.py) and matched
to the calls; each call says how many of the ten Rs 10,000 slots are in use. From 2026-10-24
(owner) a call whose reaction closed on a market down day (equal-weight liquid market -1% or
worse that session) is marked "DOWN DAY - weaker" and real money should skip it: in history
(live rule, exploratory split) such calls made +0.84% a call but only +0.23% over the market
(256 calls, 45% of years ahead) against +1.25% and +0.94% on other days (1,349, 91%). They are
still issued and kept on paper; calls from before the mark get one appended `annotated` event,
and the scorecard reports the two groups apart.
Tested and failed on 2026-10-18 (do not re-test without new data): the intraday playbook (ORB,
VWAP pullback, breakout-retest, first-hour momentum - tradedesk_lab/intraday_playbook.py), daily
setups' entries held 5 sessions (daily_twin_test.py), NIFTY 50 trend rules and sector rotation
(edge_hunt.py). Also failed on 2026-10-19 (the owner's algo plan): ORB with a volume filter and
VWAP band + RSI reversion on 1-minute history (tradedesk_lab/intraday_plan_test.py), dual-momentum
rotation and Donchian-55 with a 3xATR trail (tradedesk_lab/multiday_rotation.py).
Crypto multi-day alternatives also fail after 30% tax (tradedesk_lab/crypto_multiday_alternatives.py, 2026-10-20): BTC/ETH above their 200-day average and a BTC+ETH trend pair grow money but less than holding BTC and not better than random timing; top-5 coin momentum loses 66%. Liquidity-sweep fades also fail (tradedesk_lab/sweep_and_reversion.py); its multi-day dip buy
(RSI2<5 above EMA200) passes on paper but is erased by the real 0.56% round trip at Rs 10,000 a
trade - and as a 10-slot Rs 25,000 book (Rs 2.5 lakh assumed, 0.36% round trip,
tradedesk_lab/dip_book.py, 2026-10-24) it still fails: ~10%/yr, beats all 200 matched random
books, but no better than holding the market (11.6%/yr) and a 45% fall (bar 25%); charges run
~16%/yr of the capital at ~440 round trips a year. Down-day leaders fail too (2026-10-24,
tradedesk_lab/down_day_leaders.py): stocks closing up 1%+ on volume while the market fell 1%+
then trail the market by ~1.6% over 10-20 sessions in every year 2017-2026 (t -4 to -7).
That is why drift calls made on a market down day are marked weaker (see earnings_drift above).
Buying a results-day pop as it happens fails too (2026-10-28, tradedesk_lab/results_reaction.py, 15-minute
bars 2025-10 to 2026-10, 1,656 stocks, 8,927 results events): a results stock up 2% or 4% over the market on
3x usual volume loses 0.7-0.8% by the close (t -5.5 to -6.2) and 1.5-2.1% over 5-20 sessions, worse than
no-news pops - the pop tends to fade, which is why drift waits for the jump to hold into the close. The
mirror trade (short the pop, cover at 15:15) was suggested by that data, so it is judged only forward from
2026-10-29 (`run_fade_forward`, nightly 20:30, paper; pass: 150+ trades, 20+ days, t >= 2.5, both halves).
New listings fail too (2026-10-28, tradedesk_lab/new_listings.py, NSE listings 2017-2026 still listed today):
buying every debut, a 5%+ debut, or the first close above the listing-period high, held 20 or 60 sessions
after real costs - none reaches t >= 3 over entry weeks. Closest: listing-period high held 60 sessions,
+5.7% a call, +3.3% over the market, t 1.7, both halves positive, ~25 calls a year - a weak lead that
survivorship flatters, too rare to prove forward in under several years. Medians are about zero or below.
Special-situation events fail too (2026-10-31, tradedesk_lab/event_studies.py, NSE filings 2021-2026, lock-ins from listings 2017 on): buying the next open after the first rights-issue, buyback or open-offer filing, 30 sessions after a rights filing, or after a 30/90/180-day IPO lock-in expiry, held 20 or 60 sessions - none of 14 variants passes (bar: 60+ calls, t >= 3 over entry weeks, both halves). Rights-issue filings are followed by underperformance (-4.6% vs market over 20 sessions, t -5.5) and buyback filings too (-2.5%, t -3.7) - unusable long-only; avoiding them in other calls would be a new hypothesis needing its own test. Closest: lock-in day 30, +1.3% vs market, t 1.7.
Insider buying fails too (2026-10-31, tradedesk_lab/insider_buying.py, NSE SEBI-PIT disclosures 2021-01 to 2026-04, 14,604 open-market buys by promoters/directors/KMP, bought the next open after NSE published them): 4 rules (any Rs 10 lakh+, Rs 1 crore+, two insiders within 30 days, half a day's turnover) x liquid/small stocks x 20/60 sessions - none of 16 passes; the stocks rose but no more than the market (excess t -0.9 to +1.75). Closest: two insiders in Rs 1-5 crore stocks over 60 sessions, +2.8% vs market, t 1.75, all in 2021-23. NSE's PIT API returns nothing from May 2026.
Index changes fail too (2026-10-31, tradedesk_lab/index_changes.py, 249 equity press releases parsed from 1,191 NSE Indices PDFs 2016-2026, 3,178 add/drop events): index funds must buy an added stock and sell a dropped one at a known close, but by the time the release is public the move has happened - all 9 variants (large/mid/broad tier x bought the next open and sold at the pre-effective close / held 20 sessions from the effective date / dropped stocks held 20 sessions) fail. Closest: a Nifty 500 or Smallcap add held to the funds' buying close, +1.07% over the market, t 1.68, both halves positive, 580 calls - but -2.0% a call in real money after costs, so it only beats a market that fell harder. Nifty 50 / Next 50 adds are negative over the market both ways (-0.28% to -1.74%): the stock NSE promotes has already run.
Drift timing (2026-10-28, tradedesk_lab/drift_timing.py): buying the reaction day's close instead of the next
open adds nothing (+0.01% a call, t 0.8). Dating results by filing time - judging intraday-filed results on
the same day's close and calling a session sooner - made +1.6% more a call on 28 paired events (t 1.4): under
its bar (30 pairs, t 2.0); re-run once the Oct-Nov 2026 season's calls mature (about mid-December 2026).
Drift by market state (2026-10-28, tradedesk_lab/drift_market_split.py, live exit, 1,602 calls): reactions on a
market down day while NIFTY is below its 200-day average lost -1.5% a call (-2.1% vs market, t -2.2, 1 of 8
years positive, 87 calls) - real money should skip them; with NIFTY above its 200-day, down-day calls were the
best group (+2.2% a call, 8/10 years). So from 2026-10-28 (owner left the call to the agent) the live mark is
"FALLING-MARKET DOWN DAY - skip with real money" (`earnings_drift.skip_real_money`: down day AND NIFTY below
its 200-day); down-day calls in a rising market are normal calls. The scorecard reports those skip-marked calls apart from the calls to take (`by_real_money`). The raw `down_day` flag and the paper
record's down-day split are unchanged. The Rs 10,000 delivery round trip is 0.56%, not 0.39%.
Option income (9:20 straddle, Friday iron condor) cannot be backtested - INDstocks' expired-contract
candles return nothing yet - so `tradedesk_lab/option_income.py` paper-trades both FORWARD on real
Nifty quotes (scripts/option_income_watch.py, weekdays 09:17-15:20, saves every minute's chain);
judge after 60 straddle sessions / 12 condor weeks. Paper only, one lot; F&O brokerage provisional. From 2026-10-31 the summary also reports each straddle rule's risk (worst open loss of a day, the both-legs-stopped day in average winning days, stop fill overshoot; older trades replayed from the saved minute chains), because a high win rate hides it.

**Scope limits, by design, not oversight**: paper trading and the live risk manager remain
NSE-only (crypto/BSE have no paper book to grade against). Crypto/BSE calls are
research/paper-only. The review-queue's self-analysis loop (`rolling_failure_monitor.py` ->
`self_review/orchestrate.py` -> `config_tuning.py`/`replacement_search.py`/`detector_authoring.py`
-> the harness gauntlet -> one human approval -> `apply()`) was NSE-only through 2026-09-26;
from 2026-09-27 it covers all three markets - crypto first (explicit user decision: the
loop's own validation path never depended on a paper book, only on the harness gauntlet's
backtest-based rigor, already market-generic), then BSE the same day on the same reasoning
(it runs the identical daily engine as NSE/crypto, so the earlier crypto exception applied
equally to it). Scheduled daily for all three (`tradedesk-nse-self-review` 16:25, after
`tradedesk-after-close`; `tradedesk-bse-self-review` 16:40; `tradedesk-crypto-self-review` twice,
00:20 and 13:45, matching its 24/7 data).
**Retirement is per-market and never leaves a market with nothing being worked on**
(2026-09-29): `config/setups.yaml` serves every market, so an approved retirement adds the
market to that setup's `retired_markets` (never flips `enabled`, which would also switch it
off on the other markets); the setup keeps scanning there but `build_watchlist` rejects it
("retired by self-review on <market>"), so it never alerts yet is still logged
(`TrackedSignal.shadow`) and graded. Every self-review run with a flagged or retired setup
also runs `self_review/replacement_search.py`: improvement variants of those setups (their
own pattern + new trend/indicator filters and exits) and a grammar of new methods
(`tradedesk_lab/candidates/rules.py`: breakouts, momentum/MA/MACD crosses, squeezes, volume
thrusts, mean reversion), continuing from where the last run stopped and re-testing old
results on fresh data after 14 days. A candidate becomes a NEW_DETECTOR proposal only if it
clears the full gauntlet, +0.10R over random, a Bonferroni-adjusted random-benchmark p-value
and beats the retired setups' own forward result - the bar is never lowered to produce a
winner. A passing candidate already installed on that market, or whose entries a live rule
setup already takes more than half the time (`replacement_search.MAX_LIVE_OVERLAP`, measured on
the same history), is not proposed (2026-10-20: a Donchian-55 proposal was 100% inside the live
Donchian-20); 2026-10-24: improvement variants of rule setups skipped this check and sent six
proposals 97-100% inside live setups - they are now measured too, and count as one idea per base setup). 2026-10-25: the gauntlet's random stage was biased against random entries (drawn inside each real trade's window, force-closed at its exit, fixed target) and the lab ranked stocks by today's liquidity. The search now judges candidates on `tradedesk_lab/harness/fair_null.py` (random entries on any day the stock was in the point-in-time top 100 of 300, same exit, 20,000 cohorts, Bonferroni floor p 0.0001) and re-tests earlier passes first. Under it none of the ten installed `r_*` rules beats random by +0.10R (margins -0.28R to +0.05R; only NSE r_tsmom60_rsi_momentum is close, and adds +0.25R over random on its own filter days, p 0.00005): their history edge was mostly holding strong stocks with a trailing exit in a rising market. Owner's decision: they keep scanning and calling (replace, never blank), and a pass that fails the retest keeps its quarter-size permission until `profit_gate.GRANDFATHER_UNTIL` (2026-11-08), flagged on the scoreboard, for the owner to decide. The search judges candidates on sane bars only (`detector_authoring.sane_bars`): impossible bars are dropped and crypto history starts in 2021, because CoinDCX's 2018-2020 daily history is about 10% corrupt. The Review tab's "Replacement research" panel shows the plan and leaderboard.
**Replace, never retire into a blank** (owner's rule, 2026-10-18): the loop no longer raises retire-only items. A sustained failure stays on its market's roster (and stays a research target) until a candidate beats it; that candidate is one NEW_DETECTOR proposal whose `replaces` lists the setups it beat - only the original three or setups the profit gate has judged NO EDGE/STOPPED on live rupees (`orchestrate.failing_on_money`, 2026-10-21: the target-hit-rate monitor flags every trailing-exit trend setup, so swaps were set to retire live, gate-cleared setups), and `apply()` adds it and moves those to `retired_markets` in a single commit. The 2026-09-30 retire-only changes were restored the same day.
Full spec and milestone list: PLAN.md.

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
- Alerts (M8): alerts/router.py routes by the entry's grade (A -> desktop+Telegram+ntfy phone push+dashboard, B -> dashboard, C -> log only; config/alerts.yaml). Telegram sends are queued and drained by AlertRouter.worker so the monitor never blocks. Dashboard = FastAPI + SSE on 127.0.0.1 only; DashboardState.publish() pushes every change. "Took it"/"Skip" -> live/session.py::apply_decision moves TRIGGERED -> TAKEN/SKIPPED. httpx's ASGITransport buffers responses: test SSE against a real uvicorn server on a free port.
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
- 47 files are BYTE-SEALED into `docs/evidence/*.json`: a sealed cohort records the sha256 of the exact bytes of the modules AND the tests that produced it, so reformatting one - a line break, an import re-sort - fails its digest assertion. The seal is NOT visible from the file: it can sit in another module's evidence, so grepping for `digest(Path(__file__))` misses most of them (that is how `tests_lab/test_osr_selector_race.py`, sealed as `osr-selector-race.json: tests_sha256`, was broken and reverted on 2026-10-31). Run `uv run python scripts/sealed_files.py <path>` BEFORE editing anything under `tradedesk_lab/` or `tests_lab/`; exit 1 means leave it alone, and a run with no argument reports any sealed file already modified. `tradedesk_lab/__main__.py` is sealed too, which is why every new lab study owns its own `main()` and is run as `python -m tradedesk_lab.<study>` instead of growing the shared CLI.
- `ruff check .` is therefore clean EXCEPT four errors in `tradedesk_lab/osr_selectors.py`, `osr_selection_population.py` and `osr_selector_race.py`. They are deliberate; any other ruff error is new.
- Windows Smart App Control is ON on this machine: it blocks unsigned DLLs in brand-new wheels. Pins in pyproject.toml exist for that reason; if a new package fails with "Application Control policy has blocked this file", pin an older, widely-distributed version. mypy itself is currently blocked here for the same reason - ruff + pytest are the reliable local checks.
- Prediction layer (M11) lives in prediction/: labeling.py (triple barrier: T1 -> 1, stop or gap-through-stop -> 0, vertical barrier max_hold -> 0), features.py (feature_version-tagged, all known at the arming close), train.py (purged walk-forward BY DATE with ml.yaml embargo_sessions, a genuinely locked final test set carved out before any fold, calibrated logistic/xgboost race, per-setup breakdown, compare_strategies() for the rules-vs-rules+ML economic comparison), predict.py (apply_probability can only LOWER a grade or halve qty, gated by ml.yaml enabled+not shadow; refuses to score a bundle whose feature_version doesn't match), calibration.py (drift_check pauses the layer when a bucket with >= 30 resolved signals drifts > 0.15). `tradedesk train` refuses --no-shadow; switching on is a config edit, never a CLI flag.
- Markets (M13) share one `Market` bundle (markets/market.py: name, code_prefix, CostModel, session/universe rules, benchmark, qty_step, min_notional, vix_required) so engine/backtest/scan code takes a `Market` instead of assuming NSE. `EquityCostModel` wraps risk/costs.py verbatim (NSE/BSE); `CryptoCostModel` computes CoinDCX's real charges directly (0.2% maker/taker, 1% Section 194S TDS on the sell leg only, 18% GST on the fee not the TDS, no STT/stamp/DP/SEBI). `qty_step`/`min_notional` support fractional crypto sizing (NSE stays whole-unit, qty_step=1.0). `Portfolio.sector_of` defaults to an empty dict, so a market with no sector mapping (crypto) simply never triggers the sector cap - no special-casing needed.
- CoinDCX quirks (broker/coindcx/): candles endpoint silently ignores startTime/endTime unless BOTH are passed together; `CoinDcxClient.candles_history()` takes tradedesk scrip codes, needing `register_pairs()` first or every call 422s; some 2019-2020 history has a synthetic duplicate-calendar-day phantom bar, deduped by `backtest/runner.py::_dedupe_by_calendar_day` (no-op for NSE). Crypto's INR-stablecoin pairs (USDTINR/USDCINR) are excluded from its universe (`Market.universe_rules.exclude_codes`) - their near-zero volatility otherwise pollutes breakout scans since they're also the most liquid pairs.
- A missing VIX reading fails the regime closed (RISK_OFF, size 0) only when `Market.vix_required` is True (NSE); crypto/BSE have no VIX and stay False so `vix=None` degrades quietly instead of blocking.
- Intraday infrastructure (mtf.py::align/last_closed_bar, intraday_regime.py::classify_intraday_regime, intraday_engine.py::scan_bar, intraday_signals.py, backtest/null_baseline.py::run_null_baseline) is real, tested against live NSE data, and NOT wired into any live session, CLI command or the eligibility gate. `null_baseline.py` is the reusable matched-random-timing gate any new intraday setup must clear before being trusted - built after VWAP Reclaim (setups/intraday/vwap_reclaim.py) was tested and found to NOT beat random timing (p=0.44), consistent with every rule-based setup tested in this project so far.
- `tradedesk_lab/` (M14-M18) is an isolated research sandbox: `verify-base`/`base_manifest.json` hash-checks the pre-existing repo to prove no production path was touched; its own SQLite Registry and `data/m14_m18/` output dir; CPCV/PBO/DSR statistical rigor for re-checks of the mined 2023-2026 window; `forward.py` scores new candidates prospectively against a frozen, hash-verified model from a fixed activation date - the only place in this sandbox generating genuinely new (not re-mined) evidence. `harness/gauntlet.py::run_gauntlet` reuses `backtest/null_baseline.py`, `validation.py::walk_forward`, and `markets/costs.py` rather than re-deriving them, stops at the first failing stage, and checks `spec.py::KillCriteria` (net expectancy, sample size, drawdown, losing streak) against the realised trade sequence, never a reshuffle. Anything here that clears the same eligibility bar as production becomes a `review_queue.py` item, never an automatic promotion. Served at `/lab` under the main dashboard's port via `app.mount()`; its own `fetch()` calls are prefixed at runtime (`API_BASE`) so they resolve correctly whether served standalone or mounted.
- `tradedesk-*` scheduled tasks that need repo-root imports from a console-script entry point (e.g. the dashboard importing `tradedesk_lab`) must explicitly extend `sys.path` - unlike `python -m`/pytest, a console script does not add the repo root automatically.
