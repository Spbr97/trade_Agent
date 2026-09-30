# Plan: raise baseline accuracy

Written 30 September 2026. No plan can promise the win rate *will* reach a given number.
This one guarantees that each step either finds a real, measured improvement or rules
one out cleanly, ordered from the cheapest and most likely lever to the most expensive.

## Where the evidence stands

- Daily setups (`base_breakout`/`trend_pullback`/`nr7_breakout`): none beats random entry
  timing on any market. Crypto rolling hit rates are 8-17% (rolling_windows.jsonl,
  2026-09-30).
- Intraday AEM: canonical baseline 21.50% strict success. AEM v2 (24 specifications),
  OSR, SSM and the information-quality selector all failed their gates
  ([accuracy roadmap](accuracy-roadmap.md)).
- Exit search: best win rate 50.13%, every result net-negative after costs.
- The one real edge: `rsi2 < 10 & close > ema50` beats random by about +0.07R before
  costs and fails after them.
- ML layer: ROC-AUC 0.50-0.56 - nothing to filter.

Every test so far asked *when* to enter a stock. Almost none asked *which* stock to
hold, which is where most published, durable edges are.

## Phase 1 - Fix the scoreboard (1 day)

Win rate alone is set by exit geometry, not skill. Break-even win rate =
(1 + costs) / (1 + target in R); a 0.3R target needs about 80% winners just to break
even. From now on "baseline accuracy" means win rate AND positive net R AND beating
random, reported together with the Wilson lower bound. Raising win rate by shrinking
targets does not count.

## Phase 2 - Audit the cost model (2-3 days)

RSI(2) fails by a thin margin, so small cost errors decide its verdict.

- Crypto: check whether the 1% Section 194S TDS on sells is modelled as a loss. It is a
  creditable tax prepayment, not a fee. Separately, India's 30% flat VDA tax with no
  loss set-off makes real after-tax returns worse - model both explicitly.
- NSE: part of the cost is fixed rupees per trade (Rs 2 minimum brokerage; STT and stamp
  rounded to the rupee). Find the position size at which fixed costs stop dominating.
- Gate: re-score RSI(2) under the audited costs. This is an audit, not a way to make
  results look better; if the costs were already right, record that.

## Phase 3 - Sharpen the one real edge (1-2 weeks)

- Meta-labelling on RSI(2): a second model that only decides which RSI(2) signals to
  take, trained with purged walk-forward. This is the right use of the ML layer - raising
  the precision of a signal that already has edge.
- Sizing: measure the risk per trade needed for costs to stop dominating. If it exceeds
  current risk policy, that is a user decision, surfaced rather than changed.
- Gate: full harness gauntlet, +0.10R over matched random, positive net R.

## Phase 4 - Test which-stock-to-hold edges (2-3 weeks)

- Candidates: 6-12 month cross-sectional momentum, relative strength vs Nifty, nearness
  to the 52-week high (NSE's Nifty200 Momentum 30 index is built on this idea), and
  post-earnings drift using the results dates `data import-results` already loads.
- New build: a random-stock-selection null (same dates, randomly chosen stocks) alongside
  the existing random-timing null. Without it a stock-picking edge cannot be measured.
- Horizon: these edges work over weeks, which fits the 10-session holding window.

## Phase 5 - Let the replacement search finish (automatic, about 1 week)

The 140 research-backed daily candidates added 2026-09-29 (commit 53461db) are tested
first, two crypto runs a day. Passes feed Phase 6; failures are logged.

First result (30 September 00:20 run, 145/461 tested): `r_tsmom20_calm_vol_trend_trail`
(Liu & Tsyvinski's 1-month crypto momentum, calm-volatility filter) is the most robust
result this project has recorded - +0.065R/trade net after costs over 1,987 trades, beats
matched random, positive in all three walk-forward test folds (+0.06/+0.12/+0.12R). It
fails only `max_losing_streak`: 18 > 10. At a 39% win rate over ~2,000 trades, a streak of
about 15 is the statistical expectation, so this limit rejects almost any trend-following
rule at large samples. But the streak is also partly real risk: crypto coins fall
together, so same-day losses cluster. Open user decision: whether the streak limit should
scale with sample size and win rate, or be measured per day rather than per trade. The
bar is not changed without that decision. The 52-week-high candidates look strong
overall (+0.08 to +0.16R) but are positive in only the earliest walk-forward fold; that
edge is concentrated in one early period, not robust.

## Phase 6 - Tune exits for win rate, survivors only (1 week)

Once a strategy has proven edge, test smaller targets and time stops to raise win rate
while net R stays positive. This is where a high win rate becomes legitimately reachable.

## Phase 7 - Forward proof (at least 30 sessions)

Freeze the winner and track it on paper prospectively. Nothing goes live before at least
100 resolved calls over at least 30 sessions, with the Wilson lower bound and net R
passing - the existing eligibility gate, unchanged.

## Stop rules

- If Phases 2-4 all fail, the honest conclusion is no tradable edge at this horizon and
  cost level. Record it and stop; do not loosen a gate.
- Do not retry what is already ruled out: filter stacking on the three dead setups, ML
  on setups with no edge, re-tuning rejected families (AEM v2, OSR, SSM).
