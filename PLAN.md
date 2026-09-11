# Personal Short-Term Trading Agent for INDmoney
### Momentum trades held from hours to a few days · trading plan + build spec for Claude Code

**Owner:** Siba · **Market:** NSE cash equities · **Broker and data:** INDmoney (INDstocks API) · **Version:** 4 · **Drafted:** September 2026 · **Status:** Plan, nothing built yet

> **Not financial advice.** This is a framework for a personal decision-support tool and the discipline around it, not a recommendation to trade. Regulatory, tax, API and pricing details change often; verify them on official sources before acting.

**What changed in v4:** the main horizon moved from 1-minute scalps to trades held from a few hours to 10 sessions. An evening scan on daily charts plus live trigger monitoring replaces minute-by-minute scanning. Overnight trades are long-only. Risk, backtests and the paper book are now gap-aware, costs use delivery charges, and the prediction layer is redesigned around multi-day outcomes.

---

## 0. Read this first

**What you're building.** A scanner that finds short-term momentum trades across a liquid NSE universe. Every evening it scans daily charts and builds tomorrow's watchlist with an exact trigger, stop, targets and quantity for each setup. During market hours it watches those triggers live, confirms entries on 15-minute charts, and alerts you. It also guards your open positions: stops, targets, trailing stops, time stops, results dates and overnight gaps. You place orders in INDmoney. A paper book tracks every signal, so the agent measures its own results.

**A note on terms.** Trades held for hours to days are usually called swing or short-term momentum trading rather than scalping. This plan says "swing" for clarity; the idea is the same: a quick, well-defined move, then out.

**Why this horizon suits you better than minute scalping:**

| Factor | 1-minute scalps | Hours to a few days |
|---|---|---|
| Costs vs target | Can eat half of a 0.3% target | Roughly 0.3–0.6% round trip vs 4–8% targets |
| Chart reliability | Mostly noise | Daily and hourly patterns and levels mean more |
| Speed | A home Python scanner loses to institutional algos | Seconds don't matter |
| Prediction | Not realistically achievable | A probability filter becomes viable (Section 10) |
| Screen fatigue | Constant | Evening scan plus trigger alerts |

**The new main risk: overnight gaps.** A stop-loss only works while the market is open. If results, news or a global shock land overnight, a stock can open well below your stop, and you're filled at the open, not at your stop. The sizing, portfolio limits and results-date rules below exist mainly for this.

**Long-only overnight.** Indian cash equities don't allow carrying a short position overnight, so multi-day trades here are long-only. Shorts are possible only as same-day trades, or later through futures.

**The odds.** SEBI's own studies found that more than 7 in 10 individual intraday traders in the equity cash segment lost money in FY23, and loss-makers placed more trades on average than profit-makers. In equity F&O, over 91% of individual traders lost money in FY25. A rules-first, tested, limited-risk process is the only defensible way to take part.

---

## 1. The personal trading plan (rules the agent enforces)

### 1.1 Keep money in separate buckets

Your emergency fund and long-term investments stay completely outside this system. Trading capital is a separate, fixed pot: an amount you could lose entirely without it affecting your life or long-term goals. The agent only ever sees the trading pot, configured in `config/risk.yaml`.

### 1.2 Hard risk rules (starting values)

These live in config and are **enforced in code**, not by prompting.

| Rule | Start at | Loosen when |
|---|---|---|
| Max risk per trade (entry to stop) | 0.5% of trading capital; 0.25% for your first 50 live trades | Max 1%, only after 100+ live trades with positive net expectancy |
| Max open positions | 5 | Only after Phase 3 success |
| Max open positions per sector | 2 | Never |
| Portfolio heat (total open risk) | 2.5% of capital | Never above 4% |
| Max position value | 25% of trading capital | Never |
| Gap check | A bad overnight gap for that stock (its 95th-percentile gap-down over 2 years) must not cost more than 1% of capital at the planned quantity; otherwise cut quantity | Never |
| Max new entries per day | 3 | Never |
| Weekly loss limit | 3% of capital → no new entries until next week | Never |
| Consecutive losses | 4 → no new entries for 2 sessions, then review | Never |
| Re-entry after a stop-out | Not in the same stock for 3 sessions | Never |
| Min net reward:risk | 2R to the final target, after costs | Never |
| Results and major events | Never hold a stock through its results date; no new entries the session before RBI policy or the Budget | Only with backtest evidence for a specific setup |
| Entry window | No new entries 09:15–09:30 | Never |
| Stop-loss | Set before entry, entered in INDmoney right after the fill, only ever moved up | Never widened |
| Time stop | Exit if not +1R after 5 sessions; hard max hold 10 sessions | Only via backtest evidence |
| Leverage / MTF | None | After Phase 3, cautiously; leverage magnifies gap losses |
| Averaging down a loser | Not allowed | Never |
| Market regime | Neutral → half size; risk-off → no new swing entries (Section 6.1) | Never |

### 1.3 Instrument progression

| Stage | Instruments | Unlock condition |
|---|---|---|
| 1 | Liquid cash equities: long swings, same-day trades | Start here |
| 2 | Stock or index futures (allow overnight shorts and hedges) | Stage 1 positive net expectancy over 100+ live trades; lot sizes are large and leverage magnifies gaps |
| 3 | Options | Only if Stage 2 works *and* you've built separate IV/theta logic |

### 1.4 Phased roadmap

| Phase | Typical duration | Exit criteria |
|---|---|---|
| 0 · Define setups | 2 weeks | 3 setups written as unambiguous rules |
| 1 · Data + backtest (M1–M5) | 4–6 weeks | Each setup backtested on all available daily history with gap-aware fills, full costs and portfolio limits; at least one positive out of sample |
| 2 · Live scanner, paper only (M6–M10) | 8–12 weeks | Runs every session reliably; 60+ paper trades; results reasonably close to backtest |
| 3 · Live at 0.25% risk | 4–6 months | 50+ live trades; rules followed on 95%+ of them; live not materially worse than paper |
| 4 · Scale gradually | Ongoing | Move to 0.5% only after 100 live trades with positive net expectancy |
| 5 · Prediction layer (Section 10) | Built after M5; runs in shadow mode during Phase 2 | Beats the plain rule-based score on a purged out-of-sample test *and* on 100 forward paper signals before it influences anything |

**Kill switch:** if net expectancy is negative after 50 live trades and worse than the paper book on the same signals, stop live trading and return to Phase 1.

### 1.5 The numbers that decide everything

Always **net of all charges and slippage**, tracked per setup, regime and holding period:

| Metric | Meaning |
|---|---|
| Expectancy (R) | (win% × avg win in R) − (loss% × avg loss in R). Must be > 0 after costs |
| Profit factor | Gross profit ÷ gross loss |
| Max drawdown | Largest peak-to-trough fall in equity |
| Gap damage | Share of losses larger than the planned 1R, and their total cost |
| Rule adherence | % of trades that followed every rule |
| Live vs paper gap | Your live results minus the paper book on the same signals |

### 1.6 Costs for multi-day trades

Delivery trades are charged differently from intraday. STT is 0.1% on *both* the buy and the sell (intraday pays 0.025% on the sell only), stamp duty on the buy is higher than for intraday, and a DP charge of ₹18.5 + GST applies per stock on each day you sell from your demat. Brokerage depends on plan and order channel (third-party trackers list up to ₹20 per order; the INDstocks API page lists ₹10), so take the real figure from your contract notes. A trade bought and sold on the same day is charged as intraday.

Rough totals: about 0.3% round trip on a ₹1,00,000 delivery trade. On small positions the fixed parts (brokerage, DP charge, GST on them) matter more: on a ₹17,000 position total charges come to roughly 0.6%, most of it fixed. That is still small next to a 4–8% swing target, but it's why net R:R is always computed after costs. Keep the schedule in `config/risk.yaml` and verify it against contract notes in M1.

---

## 2. Trading modes and daily timeline

| Mode | Setup found on | Entry timing | Typical hold | Exits |
|---|---|---|---|---|
| **Swing momentum** (primary) | Daily chart, evening scan | 15-minute confirmation in the next 1–3 sessions | 1–10 sessions | Partial at 2R, trail the rest, time stop |
| **Same-day momentum** (secondary) | A watchlist stock moving strongly, 15-minute chart | 15-minute | Hours | 1.5–2R; exit by 15:15; shorts allowed |

What the agent does, and what you do:

| Time (IST) | Agent | You |
|---|---|---|
| 08:30 | Starts, generates API token, refreshes instruments, runs health check | Confirm dashboard is green |
| 08:45–09:10 | Morning brief: overnight announcements and results for watchlist and holdings | Read it (5 min) |
| 09:15–09:30 | Gap check: marks setups that opened too far past their trigger as "chased, skip"; alerts on holdings that opened below their stop | Act on gap alerts |
| 09:30–15:00 | Live trigger monitoring; confirms on 15-minute closes and volume; position alerts | Place orders when alerted |
| 15:00–15:20 | Close check: flags positions below a closing-basis stop and same-day trades still open | Decide exits before the close |
| 15:45–16:30 | Pulls daily candles, updates trailing stops, runs the evening scan, renders charts, gets Claude's chart reads, sends tomorrow's watchlist | Review watchlist (15 min) |
| Weekend | Weekly review, results calendar and universe refresh | 30 min |

---

## 3. Design principles

1. **Code computes, Claude reasons.** Every price, indicator, pattern, level, size and cost comes from deterministic, tested Python. Claude reads charts and writes judgement (Section 9), but never produces a signal, price or quantity.
2. **Scan in the evening, watch in the day, human executes.** Version 1 never places orders.
3. **Live equals backtest.** The live scanner and the backtester run the *same* indicator, pattern and setup code on the *same* candle source.
4. **Gap-aware everywhere.** Backtests, the paper book and sizing all assume that if price opens past a level, you're filled at the open.
5. **Fail closed.** Stale data, disconnections, API errors or a risk lockout pause signals and tell you why.
6. **Every signal is measured.** The paper book simulates every triggered signal; setups whose rolling results turn negative are benched automatically.

---

## 4. System architecture

```
                INDstocks API
 daily + intraday candles · price WebSocket · order feed
                       │
┌──────────── tradedesk (Python) ─────────────────┐
│ EVENING   Daily candles → Regime → Indicators   │
│           → Patterns → Setups → Filters         │
│           → Scoring → Prediction (optional)     │
│           → Watchlist + charts                  │
│                                                 │
│ MARKET    Price stream → Trigger monitor        │
│ HOURS     → 15m confirmation → Alerts           │
│           Position watch: stops, targets, gaps  │
│                                                 │
│ ALWAYS    Risk manager · Paper book · Journal   │
└─────────────────────────────────────────────────┘
       │                               │
 Claude API: evening chart       MCP server for ad-hoc
 reads, weekly review            questions in Claude Code
```

---

## 5. Market data pipeline (INDstocks)

**First, give Claude Code the real API reference.** INDstocks publishes its full documentation as a single Markdown file for coding agents ("Export for LLMs" on the docs site). Save it as `docs/indstocks-api.md` and point to it from `CLAUDE.md`, so endpoints, fields and limits are never guessed.

### 5.1 Documented limits that shape the design

As published in the INDstocks docs in September 2026; re-check before M2.

| Item | Limit |
|---|---|
| Historical candles | Up to 5 instruments per call; max window per call is 7 days for 1–30 minute candles, 15 days for 1–4 hour candles, 1 year for daily/weekly/monthly; page backwards for more |
| Candle timestamps | Candle *open* time in epoch seconds (IST); requests use epoch milliseconds; intraday buckets anchored to 09:15 |
| Data and quote APIs | 5 requests per second, 100,000 per day |
| Full quotes | Up to 1,000 instruments per call, including day volume, circuit limits and market depth |
| WebSocket | Up to 3 connections; up to 3,000 instruments per connection; `ltp` and `quote` modes; separate order-updates feed |
| Token generation | TOTP-based, 1 per minute; repeated failures trigger a lockout |
| Live API orders | Require a whitelisted static IP (not needed for data-only scanning) |

### 5.2 Data flow

| When | What | API load |
|---|---|---|
| Monthly | Rebuild the universe: ~200–500 stocks (e.g., Nifty 500) filtered by average daily turnover, plus Nifty 50, Bank Nifty, sector indices and India VIX | Negligible |
| Evening | Daily candles for the whole universe | 500 stocks = 100 calls ≈ 20 seconds |
| Evening | Hourly and 15-minute candles for the top candidates, to check intraday structure | A few dozen calls |
| Market hours | WebSocket price stream for the whole universe (triggers, position watch, gap checks) | 1 connection |
| Market hours | 15-minute candles for armed and held stocks, fetched as each 15-minute bar closes | ~6 calls per bar for 30 stocks |
| Every few minutes | One full-quote call for volume, circuit limits and spread | 1 call |

**Measure in M2:** whether the daily candle's close matches NSE's official closing price, and how soon after 15:30 it's final. Run the evening scan only on verified closes.

### 5.3 Reliability requirements

| Concern | Requirement |
|---|---|
| Disconnects | WebSocket auto-reconnect with backoff; ignore heartbeats; after reconnecting, re-check every trigger and stop against the day's high/low via REST before resuming alerts |
| Staleness | No price update for 2+ minutes during market hours → pause alerts and send a "DATA STALE" alert |
| Clock | Use exchange timestamps for candle logic; verify system clock sync at startup |
| Token | Generate at startup via TOTP; keep the TOTP secret in the OS keychain (`keyring`), never in plain files |
| Machine | Disable sleep during market hours; run under a process supervisor that restarts on crash and alerts you |

### 5.4 History and data quality (for backtests)

| Topic | Requirement |
|---|---|
| Depth | All available daily history (the docs describe multiple years), plus 2 years of hourly and 15-minute candles. Roughly 15,000 calls in total for 500 stocks, done in one evening |
| Corporate actions | Check whether candles are adjusted for splits and bonuses. If not, adjust using NSE corporate-action data first, or a 1:1 bonus will look like a 50% crash |
| Survivorship bias | Backtesting only today's index members ignores stocks that later collapsed or got delisted. Build the universe per date from liquidity rules on the data you have, and treat backtest results as optimistic |
| Results calendar | Import NSE's board-meeting / financial-results calendar weekly; it drives the results blackout |

---

## 6. Scanning engine

### 6.1 Market regime

Computed each evening and refreshed intraday. Thresholds are starting points for backtesting.

| Regime | Rule of thumb | Effect |
|---|---|---|
| `risk_on` | Nifty above a rising 50-day EMA, more than 50% of the universe above its 50-day EMA, India VIX calm | Full size |
| `neutral` | Mixed signals | Half size, A-grade setups only |
| `risk_off` | Nifty below its 50-day EMA with breadth under 40%, or VIX spiking | No new swing entries |

### 6.2 Relative strength and sectors

RS score: a weighted mix of each stock's 1-, 3- and 6-month returns relative to Nifty, ranked as a percentile across the universe. Sector strength: each sector index relative to Nifty. Swing longs favour stocks in the top quartile of RS within the stronger half of sectors.

### 6.3 Indicators

| Family | Indicators |
|---|---|
| Trend | EMA 10/20/50/200 (daily), EMA 20/50 (hourly), ADX (14) |
| Levels | Swing highs/lows, 52-week high/low, base high/low, previous week high/low, anchored VWAP from the breakout day |
| Momentum | RSI (14), RSI (2), MACD, 5- and 20-day rate of change |
| Volatility | ATR (14) and ATR %, Bollinger Band width, NR7 and inside-day flags, range-contraction ratio |
| Volume | Volume vs 50-day average, 20-day up/down volume ratio, volume dry-up inside a base, OBV |

Validate with **golden-file tests** against TradingView values.

### 6.4 Pattern library: what the chart is drawing

Swing points (zigzag pivots) on daily and hourly charts feed code-detected patterns. Each detector returns geometry (pivots, breakout level, base height, measured-move target) so it's drawn on the alert chart and unit-tested.

| Pattern | Used as |
|---|---|
| Tight base / consolidation (5–25 sessions, contracting range, volume drying up) | Breakout setup |
| Bull flag or pennant after a strong multi-day move | Continuation setup |
| Pullback to a rising 20-day EMA on lower volume | Pullback setup |
| NR7 or inside day near highs | Volatility-expansion trigger |
| Higher low or double bottom at support | Confirmation |
| Overhead supply (prior swing highs, 52-week high) | Room-to-target check; veto if too close |
| Gap and hold (gap up on volume that holds above the gap) | Continuation setup (later) |

### 6.5 Swing setups (start with three)

Long side. All thresholds are starting guesses; the backtest decides.

| Setup | Arms at the evening scan when | Trigger (next 1–3 sessions) | Stop | Exits |
|---|---|---|---|---|
| **Base breakout** | Price above a rising 50-day EMA, RS top 25%, a 5–25 session tight base with volume drying up, close within 3% of the base high | 15-minute close above the base high on above-normal volume | Below the base low; skip if that's more than 2× ATR away | Sell half at 2R and move the stop to breakeven; trail the rest under the 10-day EMA on a closing basis |
| **Trend pullback** | 20 > 50 > 200-day EMAs, all rising; RS top 25%; a 2–5 session pullback to the 20-day EMA on lower volume | 15-minute close above the previous day's high | Below the pullback low | Partial at 2R or the recent swing high, trail under the 10-day EMA |
| **NR7 / inside-day breakout** | Uptrend as above, plus an NR7 or inside day within 5% of recent highs | 15-minute close above that day's high | Below that day's low | Partial at 2R, trail at 2× ATR below the highest close |
| *Later:* gap-and-hold continuation, RSI(2) oversold pullback above the 200-day EMA, same-day shorts in weak sectors | | | | |

Every setup also carries the time stop and max hold from 1.2, and must show at least 2R net of costs to its final target before overhead supply.

### 6.6 Entry timing rules

| Rule | Why |
|---|---|
| No entries 09:15–09:30 | The opening minutes are the most erratic |
| Trigger needs a 15-minute *close* beyond the level, not just a touch, with volume above that time's usual average | Filters false breakouts |
| If the stock opens more than 1× ATR past the trigger, mark it "chased, skip" | The stop is now too far away and the R:R is broken |
| Triggers after 15:00 wait for the daily close to confirm, then fill at next session's price | Avoids end-of-day fakeouts (configurable) |
| Setup expires after 3 sessions without a trigger | Stale setups lose their edge |
| Setup is invalidated if the stock closes below the stop level first, or results are announced | The premise is gone |

### 6.7 Filters

Reject a candidate if average daily turnover is too low, ATR % is outside a sensible band (e.g., 1.5–5%), the stock is on a surveillance (ASM/GSM) list, price is near a circuit limit, results fall inside the max holding window, the sector cap or portfolio heat would be exceeded, or the regime doesn't allow new entries.

### 6.8 Scoring and grading

Score 0–100 from: trend quality, RS rank, sector strength, pattern quality (tightness, volume dry-up), trigger volume, room to overhead supply, net R:R after costs, regime, and the setup's own track record (rolling paper-book expectancy, with backtest results as the starting prior). If the prediction layer is enabled, its probability adjusts the score (Section 10.5).

| Grade | Score | Behaviour |
|---|---|---|
| A | 80+ | On the watchlist; triggers alert with sound, desktop and Telegram |
| B | 65–79 | On the watchlist; triggers show on the dashboard |
| C | Below 65 | Logged silently, still paper-traded, so you learn whether B/C grades deserve promotion |

**Auto-bench:** a setup whose rolling 30-trade paper expectancy drops below zero stops alerting until you review it.

### 6.9 Signal lifecycle

Implemented as an explicit state machine with transition tests.

| State | Meaning | You see |
|---|---|---|
| ARMED | Found at the evening scan; trigger, stop and targets set; valid 3 sessions | Evening watchlist |
| TRIGGERED | Confirmed on a 15-minute close | Urgent alert: trade card + chart |
| CHASED | Opened more than 1× ATR past the trigger | Morning note |
| EXPIRED / INVALIDATED | No trigger in time, or the premise broke | Evening update |
| TAKEN / SKIPPED | From your INDmoney fills, or a Telegram tap | Dashboard |
| OPEN | Held position: trailing stop updates each evening, time-stop countdown, results watch | Evening update; alerts near stop, at T1, on gaps |
| CLOSED | Exited at target, stop, trail, time stop, gap, or manually | Summary |

---

## 7. Watchlist, alerts and dashboard

**Evening watchlist** (Telegram and dashboard, around 16:30): the regime, the top A and B setups each with its chart, trigger, stop, targets, quantity, net R:R, next results date and Claude's chart read, plus every open position with its updated trailing stop and days held.

**Channels:** desktop notification with sound while you're at your desk; a private Telegram bot with the chart image and "Took it / Skip" buttons when you're away; a local dashboard.

**Trade card** (illustrative numbers, placeholder symbol, ₹20/order brokerage assumed):

```
TRIGGERED · XYZ · Base breakout · LONG · A (86)
Entry   842.00  15m close above 838.50 base high, volume 2.1x
Stop    818.00  below base low · 24.00 per share (2.9%)
T1      890.00  2R · sell half, stop to breakeven
T2      914.00  3R, just under the 916 swing high · then trail
Time    exit if not +1R by day 5 · max hold 10 sessions
Qty     20 → risk ₹480 (0.48% of ₹1,00,000) · position ₹16,840
Costs   ~₹110 round trip → net R:R 1.4 at T1, 2.3 at T2
Events  results in 18 sessions ✓ · regime risk_on
Record  last 30 paper trades on this setup: 43% win, +0.34R net
Heat    open risk 1.4% → 1.9% of 2.5% cap · sector 1 of 2
```

Notice that ₹110 of costs turns a 2R T1 into 1.4R net on a small position; about ₹70 of it is fixed per trade. That's why the 2R net rule applies to the final target and why tighter-structure setups (smaller stop, larger quantity) score higher.

**Dashboard** (FastAPI + server-sent events, one HTML page, bound to localhost): health lights (feed, token, data freshness), regime, tonight's or today's watchlist, triggers, open positions (days held, trailing stop, P&L), portfolio heat, sector exposure, and live vs paper P&L.

---

## 8. Risk manager, journal and paper book

**Your fills.** Subscribe to the INDstocks order-updates WebSocket. If orders you place in the INDmoney app appear on that feed (verify in M9), trades are journaled and every limit is computed from real fills automatically. If they don't, fall back to the Telegram "Took it" button plus a quick fill entry on the dashboard.

**Limits enforced before any alert:** portfolio heat, max positions, sector cap, max new entries per day, weekly loss limit, consecutive-loss pause, re-entry cooldown, results blackout, gap check, and the regime size multiplier.

**Position watch.**

| Event | Alert |
|---|---|
| Stock opens below its stop | "Gapped below stop at open" with the gap size and your pre-set rule (e.g., exit in the first 15 minutes) |
| Price within 0.5× ATR of the stop | Warning |
| T1 reached | "Sell half, move stop to breakeven" |
| 15:15 and price below a closing-basis stop | "Exit before the close" |
| Evening | Updated trailing stops to enter in INDmoney, time-stop countdown |
| Results or major announcement for a held stock | Immediate alert |

**Paper book.** Every TRIGGERED signal is simulated, whether you take it or not: entry at the confirming 15-minute close plus slippage; partial exits and trailing exactly as the rules say; if a session opens below the stop, the exit is at that open price, not the stop; if a bar touches both stop and target, assume the stop; full delivery costs including DP charges. This is your forward test and feeds both scoring and auto-bench.

**Journal tables (SQLite):** `signals`, `alerts`, `positions`, `stop_updates`, `trades`, `paper_trades`, `daily_state`, `weekly_state`, `data_health`, `tags` (rule breaks, FOMO entries, revenge trades).

---

## 9. Where Claude fits

With an evening scan, latency no longer matters, so Claude can do what you originally asked for: actually read the charts.

| Role | How | When |
|---|---|---|
| **Builder** | Claude Code implements the milestones in this plan | Development |
| **Evening chart analyst** | For the top 5–10 setups, the Claude API receives the daily and hourly chart images plus the setup JSON and writes a three-line read: pattern quality, where overhead supply sits, what would make it fail. Mode in `config/claude.yaml`: `off`, `notify` (note only) or `veto` (can downgrade or remove, never add or upgrade) | 16:00–16:30 |
| **Trigger note** (optional) | A one-line note on the 15-minute chart at trigger time, sent after the alert, never delaying it | Market hours |
| **Weekly coach** | Reads the journal, paper book and rule-break tags; writes a short review with one thing to change next week | Weekend |
| **Ad-hoc analyst** | An MCP server exposes live state and tools (`get_watchlist`, `get_position`, `analyze`, `journal_stats`, `backtest`) so you can ask Claude Code "why is XYZ on the list?" or run `/review week` | Anytime |

Keep model names in config: a stronger model (e.g., `claude-sonnet-5`) for evening chart reads and the weekly review, and a fast, low-cost one (e.g., `claude-haiku-4-5-20251001`) for trigger notes. API usage is billed separately from a Claude subscription, so set a spend limit. A schema test enforces that Claude's output can never change a price, level or quantity.

---

## 10. Prediction layer: meta-labeling for multi-day trades

### 10.1 What "prediction" honestly means here

This layer does not forecast prices. It estimates **the probability that a setup the rules already found will reach its target before its stop within the holding window.** The setups still decide *what* the trade is; this layer decides *how much to trust it*. The approach is known as meta-labeling (from Marcos López de Prado's *Advances in Financial Machine Learning*).

At a multi-day horizon this is far more realistic than for minute bars: daily features like trend, relative strength, volume behaviour and market regime carry more signal relative to noise and costs, and backtesting a large universe yields thousands of labelled examples. Expect a modest improvement in *selection*, not a crystal ball. If it can't beat the plain rule-based score out of sample, you don't use it.

### 10.2 Labels: triple barrier

For every historical ARMED-then-TRIGGERED signal, look forward from the entry and record which barrier is hit first:

| Barrier | Level | Label |
|---|---|---|
| Upper | +2R (the T1 level) | 1 |
| Lower | The stop, gap-aware (an open below the stop counts as hit) | 0 |
| Vertical | 10 sessions (the max hold) without either | 0 |

Also store the realised R of the full exit plan for each signal, for evaluating trading impact.

### 10.3 Features (all known at the evening scan, or at the trigger bar)

| Group | Features |
|---|---|
| Trend | Distance from the 20/50/200-day EMAs in ATRs, EMA slopes, ADX |
| Relative strength | 1/3/6-month RS percentiles, sector RS, whether the RS line is at a new high |
| Pattern quality | Base length and depth, range-contraction ratio, volume dry-up ratio, number of tests of the base high |
| Volume | Trigger-bar volume vs normal, 20-day up/down volume ratio |
| Volatility | ATR %, stop distance in ATRs, NR7 / inside-day flags |
| Room | Distance to overhead supply in R |
| Market | Regime, breadth, Nifty trend, India VIX level and 5-day change |
| Calendar | Sessions until results, day of week, derivatives expiry week |
| Setup | Setup type, its rolling paper expectancy |

**Hard rule: no feature may use information from after the moment it's recorded.** A leakage test (truncate all data after the signal date; assert features are unchanged) is mandatory. Leakage is the most common reason trading models look brilliant in backtests and fail live.

### 10.4 Model and validation

| Step | Requirement |
|---|---|
| Baseline | Calibrated logistic regression; check that the coefficients make trading sense (e.g., higher RS helps) |
| Upgrade | LightGBM only if it beats the baseline out of sample |
| Splits | Purged walk-forward: train on older data, leave an embargo gap of at least 10 sessions (the max hold, so overlapping trades can't leak), test on a later untouched period, then roll forward |
| Grouping | Many signals fire on the same day and move together; split and evaluate by date, and don't treat them as independent samples |
| Metrics | Brier score and a calibration curve for the probability; for trading, net expectancy of signals above the threshold vs all signals |
| Threshold | Chosen on the validation period only, never on the test period |
| Bias notes | Record survivorship bias and any universe limitations alongside every result |

### 10.5 Using the output

| Rule | Detail |
|---|---|
| Shadow mode first | During Phase 2 the model's probability is logged on every signal but changes nothing |
| Switch-on gate | Beats the plain score on the purged out-of-sample test *and* on 100 forward paper signals |
| Effect on grades | E.g., A-grade requires probability ≥ 0.50; below 0.40 drops a setup to C |
| Effect on size | Can only *reduce* size (e.g., half size below 0.45); never increases it beyond the hard caps in 1.2 |
| Retraining | Monthly; every model version saved with its out-of-sample scores |
| Drift monitor | Among live signals rated around 0.6, roughly 60% should succeed over time. If calibration drifts, pause the layer, not the setups |

### 10.6 Later: cross-sectional ranking (optional)

A second model can estimate each universe stock's probability of outperforming Nifty over the next 5 sessions, used only to order the watchlist. It will largely rediscover momentum, and it must be evaluated with realistic turnover and costs. Build it only if meta-labeling has already proven useful.

---

## 11. Backtesting and forward testing

**Event-driven backtester.** Drive the *live* engine code with a historical clock instead of writing separate backtest logic.

| Rule | Detail |
|---|---|
| Timing | Evening scan runs on each day's verified close; triggers are checked only on later 15-minute bars |
| Gap-aware fills | Open beyond a trigger by more than 1× ATR → "chased", no trade; open below a stop → exit at the open |
| Intrabar ambiguity | If a bar touches both stop and target, assume the stop |
| Portfolio constraints | Max positions, heat, sector caps and daily entry limits applied in time order. Without them, a backtest happily "takes" 40 breakouts on one strong day |
| Costs | Full delivery or intraday schedule, DP charges and slippage on every fill |
| Exits | Partials, trailing, time stops and results blackout exactly as live |
| Walk-forward | Tune on older years; report on the most recent year untouched |
| Parameters | Few, with round values, to limit overfitting |

**Replay harness.** Record market-hours sessions (WebSocket messages and REST responses) and replay them through the trigger monitor and position watch at high speed. The evening scan gets deterministic snapshot tests: the watchlist for any past date must match the backtester's signals for that date.

**Forward test.** Phase 2's paper book, running every session.

**Test ideas that play to an SDET's strengths:** look-ahead and feature-leakage tests; gap-fill tests with synthetic gap-downs through stops; portfolio-constraint tests on a crowded breakout day; state-machine tests for every lifecycle transition; property-based tests for sizing (never exceeds risk, heat or position caps); a parity test proving replay and backtest produce identical triggers; chaos tests for stale data, 429 responses and token expiry.

---

## 12. Tech stack

| Layer | Choice |
|---|---|
| Language / env | Python 3.12, `uv` |
| Async and networking | asyncio, httpx, websockets, aiolimiter |
| Scheduling | APScheduler |
| Data | pandas, NumPy, DuckDB |
| Indicators and patterns | TA-Lib or pandas-ta (check maintenance status), plus custom zigzag and pattern code |
| Machine learning | scikit-learn (logistic regression, calibration), LightGBM, joblib for model versions |
| Charts | mplfinance |
| Alerts | Desktop notifications, python-telegram-bot |
| Dashboard | FastAPI + server-sent events |
| Claude | `anthropic` SDK; `mcp` Python SDK for the MCP server |
| Secrets | `keyring` (OS keychain) |
| Storage | DuckDB (candles, features), SQLite (journal) |
| Quality | pytest, hypothesis, ruff, mypy, structlog |

Python over TypeScript because the technical-analysis, data and machine-learning ecosystem is far deeper, and it doubles as solid Python practice.

---

## 13. Repository layout

```
tradedesk/
├── CLAUDE.md
├── PLAN.md                     ← this document
├── docs/indstocks-api.md       ← INDstocks "Export for LLMs" file
├── config/
│   ├── risk.yaml               capital, limits, heat, cost schedule
│   ├── universe.yaml           liquidity rules, index lists
│   ├── setups.yaml             enabled setups + parameters
│   ├── schedule.yaml           daily timeline
│   ├── alerts.yaml             channels
│   ├── claude.yaml             models, mode (off/notify/veto)
│   └── ml.yaml                 enabled, shadow, thresholds, retrain schedule
├── src/tradedesk/
│   ├── broker/indstocks/       auth, rest, ws, ratelimit, instruments
│   ├── data/                   history_loader, candle_store, corporate_actions,
│   │                           results_calendar, universe, health
│   ├── engine/                 regime, relative_strength, indicators, patterns,
│   │                           filters, scoring, lifecycle, engine
│   ├── setups/                 base, base_breakout, trend_pullback, nr7_breakout
│   ├── scan/                   evening_scan, watchlist_report
│   ├── live/                   trigger_monitor, confirmation, position_watch, gap_check
│   ├── risk/                   costs, sizing, limits, heat
│   ├── paper/                  book
│   ├── journal/                db, stats
│   ├── prediction/             labeling, features, train, predict, calibration
│   ├── alerts/                 desktop, telegram, cards, charts
│   ├── dashboard/              app, static/index.html
│   ├── claude/                 chart_read, trigger_note, weekly_review
│   ├── backtest/               runner, fills, portfolio, reports
│   ├── replay/                 recorder, player
│   ├── mcp_server.py
│   └── cli.py                  tradedesk live | scan | backtest | replay | train
├── .claude/skills/             analyze/, review/, backtest/
└── tests/                      unit/, golden/, lifecycle/, gaps/, leakage/, replay/, backtest/
```

---

## 14. Build milestones for Claude Code

Run each milestone as its own session. Suggested opening prompt: *"Read PLAN.md, CLAUDE.md and docs/indstocks-api.md. Implement Milestone M<n>. Write the tests first, then the code, then show me the test run."*

| # | Milestone | Done when |
|---|---|---|
| M1 | Skeleton, config loading, **cost calculator** (intraday and delivery, DP charges) | Matches your INDmoney contract notes for 5 intraday and 5 delivery trades |
| M2 | INDstocks client: TOTP token, instruments, daily/hourly/15-minute candles, quotes, WebSocket stream, rate limiter | Universe data pulled with no 429s; daily close verified against NSE's official close |
| M3 | History loader, corporate-action adjustment, universe-by-date, results calendar import, data-quality report | Split and bonus days don't appear as crashes; gap and missing-data report reviewed |
| M4 | Indicators, regime, relative strength, pattern library | Golden tests match TradingView; pattern tests pass on hand-picked charts |
| M5 | Three setups + event-driven backtester with gap-aware fills and portfolio constraints | Per-setup report with costs and walk-forward split; look-ahead, gap-fill and crowded-day tests pass |
| M6 | Evening scan pipeline, watchlist report, chart rendering | The watchlist for any past date matches the backtester's signals for that date |
| M7 | Live trigger monitor, 15-minute confirmation, gap check, position watch, recorder and replayer | A replayed session produces the same triggers as the backtester |
| M8 | Alerts (desktop, Telegram) + dashboard | Cards with charts arrive during replay; dashboard updates live |
| M9 | Risk manager (heat, sector caps, weekly limit, results blackout), order-updates feed, journal, paper book, auto-bench | Limits fire in a simulated losing week; paper book matches hand-calculated trades, including a gap-down exit |
| M10 | Claude evening chart reads, trigger notes, weekly review, MCP server + skills | Schema test proves Claude can't change prices or quantities; notes never delay alerts |
| M11 | Prediction layer: labels, features, baseline model, purged walk-forward, shadow mode, calibration monitor | Leakage test passes; out-of-sample report vs the plain score; shadow logging runs live |
| M12 | *Only after Phase 3 succeeds:* one-tap order with stop attached | Static IP whitelisted; broker's algo requirements confirmed; order rate capped in code |

---

## 15. Starter `CLAUDE.md`

```markdown
# tradedesk

Short-term momentum scanner for NSE stocks using the INDstocks (INDmoney) API.
Evening scan on daily charts, live trigger monitoring, trades held hours to 10 sessions.
It alerts; the human places every order.

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
- Live session: uv run tradedesk live
- Evening scan: uv run tradedesk scan --date today
- Backtest: uv run tradedesk backtest --setup base_breakout --from 2023-09-01
- Train model (shadow): uv run tradedesk train --shadow

## Conventions
- pydantic models: Candle, Signal, Position, TradeCard, RiskStatus, RegimeSnapshot, HealthStatus
- One module per setup, implementing the Setup protocol in setups/base.py
- New setups ship disabled until their backtest report and first 30 paper trades are reviewed
```

---

## 16. Compliance, tax and safety (India)

**Algo rules.** SEBI's retail algo framework has been enforced since 1 April 2026. For orders sent through a broker API it requires a static IP registered with the broker, a daily 2FA login and a cap of 10 orders per second, and brokers convert market orders into price-protected orders. INDstocks likewise requires a whitelisted static IP for live API orders. This agent only reads data until M12, but plan for a static IP if you ever automate.

**Keep signals personal.** Sharing or selling buy/sell calls to other people requires SEBI Research Analyst registration. Don't publish the watchlist or alerts as recommendations.

**Tax.** Same-day trades are generally treated as speculative business income. Delivery trades held for a few days are usually short-term capital gains, but very frequent trading can be treated as business income instead, which changes your ITR form and can bring audit thresholds into play. Confirm the treatment for your pattern of trading with a CA before your first live trade.

**Security.** Your access token plus TOTP secret is effectively the keys to your trading account: keychain only, never committed, never pasted into prompts. Run the MCP server locally over stdio, bind the dashboard to localhost, and restrict the Telegram bot to your own chat ID.

---

## 17. Sources to re-check before building

- INDstocks API docs and "Export for LLMs": https://api-docs.indstocks.com/
- INDstocks rate limits: https://api-docs.indstocks.com/conventions/
- INDstocks historical candles: https://api-docs.indstocks.com/historicalData/
- INDstocks WebSockets: https://api-docs.indstocks.com/Websockets/
- INDstocks market quotes: https://api-docs.indstocks.com/MarketQuote/
- INDstocks API trading page (static IP): https://www.indstocks.com/features/api-trading
- INDmoney pricing (STT, DP charges): https://www.indmoney.com/pricing
- SEBI intraday study (July 2024): https://www.sebi.gov.in/media-and-notifications/press-releases/jul-2024/sebi-study-finds-that-7-out-of-10-individual-intraday-traders-in-equity-cash-segment-make-losses_84948.html
- SEBI F&O FY25 findings, as reported by Business Standard (July 2025): https://www.business-standard.com/markets/news/net-losses-of-traders-in-fo-widens-in-fy25-sebi-study-125070701221_1.html
- Retail algo framework summary: https://www.tradejini.com/blogs/what-sebis-new-algo-trading-rules-mean-for-you
- Claude Code docs (skills, MCP, CLAUDE.md): https://code.claude.com/docs
