# Plan: Test whether a tighter-exit mean-reversion variant can approach 60% win rate

(The previous plan, the AEM baseline-gates validation harness, is complete, committed and
merged to `main` as of commit `0a8940a`. This is the next plan.)

## Context

The user wants to raise tradedesk's accuracy baseline (currently 21.5% strict success on the
AEM baseline, and every mainstream breakout/pullback/momentum setup already independently
killed on NSE/BSE/crypto) and asked, given that mainstream approaches are exhausted, whether an
"independent algorithm" could realistically get closer to a **60% win rate** - explicitly framed
as "just beyond coin flip," not the project's usual 70-80% bar.

Reframing that matters: 60% only means something paired with R:R. At the project's current 2:1
target:stop, breakeven is 33%, so 60% there would be an extraordinary, implausible edge. At a
target closer to 1:1, breakeven is 50%, so 60% is a modest, plausible ask (+0.2R/trade). That
points at redesigning the **exit**, not inventing a new signal from scratch - and specifically at
the one setup in this project with any measured real edge: `rsi2<10 & above ema50`
(`src/tradedesk/research_tracker.py:147-154`), +0.077R vs random timing (t=7.04), which currently
uses the same 2:1/10-session exit as every killed breakout setup and has never had its own exit
geometry searched. Two siblings with weaker but still-positive measured edge exist too:
`rsi2<5 & above ema200` (+0.064R, t=5.28) and `below ema10 & above ema50` (+0.035R, t=4.72),
`research_tracker.py:166-178`.

Critically, none of this needs a new signal-detection algorithm. `scripts/entry_search.py`
already has an `optimize` command (lines 310-443) built for exactly this: an honest,
chronologically-split (train/test) geometry search over stop_atr x target_r x max_hold for one
named rule, against real NSE daily history and real round-trip costs - it has just never been
run for these rules. And `tradedesk_lab/harness/gauntlet.py` (random-entry benchmark -> in-sample
-> walk-forward -> sensitivity -> Monte Carlo -> regime split -> kill criteria) is the exact
reusable rigor this project already built and precedent-tested on `crypto_momentum_continuation`
(`tradedesk_lab/harness/run_crypto_momentum.py`) - the same pattern applies to a daily
mean-reversion rule with almost no new code, since `prediction/labeling.py::triple_barrier`
(gap-aware, stop-wins-ties, tested) already does exactly the barrier resolution needed.

## Approach

**Step 1 - free, no new code: run the existing geometry search.**
`uv run --no-sync python -m scripts.entry_search optimize --rule "rsi2<10 & above ema50"
--risk-pct 0.005` (repeat for `"rsi2<5 & above ema200"` and `"below ema10 & above ema50"`, and
compare risk-pct 0.0025/0.005/0.01 since cost-in-R is sizing-dependent). This searches
stop_atr in [1.0,1.5,2.0,3.0] x target_r in [0.5,0.75,1.0,1.5,2.0,3.0] x hold in [2,3,5,10],
picks the best TRAIN-half net-R cell (n>=300), and reports it ONCE on the locked TEST half
(win_rate, gross, net) - the same discipline `train.py`'s final-test-frac reservation uses.
Report the honest result: which rule/geometry combination (if any) clears close to 60% test
win rate with positive net R, and which don't. This step alone may already answer the question -
if nothing clears it, that's the honest deliverable, exactly like every other finding in this
project, and step 2 either doesn't run or runs anyway to double-confirm via a different method.

**Step 2 - if step 1 finds a candidate geometry: validate it through the harness.**
New file `tradedesk_lab/harness/run_daily_mean_reversion.py`, modeled directly on
`run_crypto_momentum.py`'s structure:
- Load NSE daily history via `CandleStore` + `engine/indicators.py::daily_features` for the full
  universe (same source `entry_search.py` already reads).
- Apply the winning rule + `entry_search.py`'s next-open-entry convention, resolve each trade
  with `prediction/labeling.py::triple_barrier` at the winning stop_atr/target_r/max_hold
  (reused as-is, not reimplemented).
- Cost/net-R via `markets/costs.py::EquityCostModel` (reused, matches `entry_search.py optimize`'s
  own cost call) at a policy-compliant risk-pct (0.5%, matching CLAUDE.md's stated breakeven
  reference).
- Convert to `HarnessTrade` objects, call `run_gauntlet()` with `KillCriteria` set to
  `min_net_expectancy_r=0.0`, a sample-size floor appropriate to however much history clears the
  rule (likely >=300, matching the crypto precedent), a drawdown cap, and a losing-streak cap -
  each stage of the existing gauntlet (`backtest/null_baseline.py`, `tradedesk_lab/validation.py`,
  `harness/sensitivity.py`, `harness/monte_carlo.py`, `harness/regime_split.py`) runs unmodified.
- Also add a `tradedesk_lab/harness/rules/daily_mean_reversion_<rule>.md` Phase-0 doc, matching
  the `crypto_momentum_continuation.md` precedent (rule definition, prior evidence, kill
  criteria, stated approximations).
- Write the report to `data/m14_m18/harness/daily_mean_reversion/report.json`, same pattern as
  the crypto run.

**Step 3 - report honestly, and only then decide on registration.**
If the gauntlet's stages (especially the random-entry benchmark, which is the one every prior
setup failed) pass: report the real numbers and recommend adding it to
`research_tracker.py::CANDIDATES` (trivial - append one `Candidate` with the found
stop_atr/target_r/max_hold, `research_tracker.py:36-38`) for genuine forward, out-of-sample
tracking - explicitly NOT live-eligible, same as every other candidate, still gated by
`engine/scoring.py::eligibility()` before anything could ever alert. If it fails at any stage
(most likely candidate: still loses to the random-entry benchmark, since a better exit doesn't
change whether the ENTRY has real timing edge beyond what eligibility+geometry already captures) -
report exactly where it failed with real numbers, the same way the AEM baseline-gates checkpoint
did. No step writes to `config/setups.yaml`, no step touches the live eligibility gate, and
nothing here can ever auto-promote per this project's existing sandbox/review_queue discipline.

## Files

- No changes to `scripts/entry_search.py` (step 1 just runs it).
- New: `tradedesk_lab/harness/run_daily_mean_reversion.py`,
  `tradedesk_lab/harness/rules/daily_mean_reversion_<winning_rule>.md`.
- Possible (only if step 3's evidence supports it, and only after showing the user the real
  numbers first): one new `Candidate` entry appended to
  `src/tradedesk/research_tracker.py:137-179`.
- No changes to `tradedesk_lab/harness/gauntlet.py`, `spec.py`, `sensitivity.py`,
  `monte_carlo.py`, `regime_split.py`, `trade.py`, `backtest/null_baseline.py`,
  `tradedesk_lab/validation.py`, `prediction/labeling.py`, or `markets/costs.py` - all reused
  as-is, consistent with this project's repeated "reuse tested primitives, don't re-derive them"
  discipline.

## Verification

- Step 1's `entry_search.py optimize` output IS the verification for that step - printed
  train/test tables, no test suite needed (it's an analysis script, like `backtest`/`train`).
- Step 2: `uv run --no-sync python -m tradedesk_lab.harness.run_daily_mean_reversion` produces a
  real report.json; `uv run --no-sync ruff check tradedesk_lab/harness/run_daily_mean_reversion.py`
  clean.
- If a `Candidate` is added to `research_tracker.py`, run the existing
  `tests/*research_tracker*` suite (if present) or at minimum `uv run --no-sync python -m
  scripts.research_tracker report` to confirm it's picked up without breaking the existing four
  candidates' stats.
- Final response to the user reports the actual numbers from whichever step the evidence
  stopped at - not "it ran."
