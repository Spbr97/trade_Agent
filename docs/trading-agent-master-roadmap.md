# Trading Agent Master Roadmap

Prepared 18 September 2026 from `TRADING_AGENT_MASTER_PLAN.md` and a read-only audit
of the current repository. The user subsequently authorized implementation. Work is
restricted to the isolated research lab: it does not authorize live execution, agent
resumption, model promotion, or dashboard/management changes.

The research agents remain parked until the user explicitly says **resume agents**.
The current implementation branch is `codex/reliability-80-research`; its starting WIP
checkpoint is `ba71981`. The unrelated untracked Groww work is outside this roadmap.

## Implementation status — 18 September 2026

The first causal MCB vertical slice is implemented under `tradedesk_lab`:

- frozen, hashed `MCB_v1` strategy/feature/label contract;
- prior-close daily qualification and closed-M5-bar intraday trigger features;
- time-slot RVOL using prior sessions only;
- explicit `TRADE`, `WATCH`, and `NO_TRADE` decisions with rejection reasons;
- next-bar-open fills, chased-entry rejection, stop-first ambiguity, gap stops,
  provisional NSE intraday costs, strict net-positive success, and MFE/MAE horizons;
- immutable isolated datasets/manifests, read-only source access, and an audit of the
  app-derived successful-call examples.

Two years of M5 data were then loaded and coverage-verified for the five named app
examples (GOODLUCK, NITINSPIN, SKIPPER, UNIPARTS, and PERNIASPOP). This expanded local
M5 coverage from five to ten symbols; PERNIASPOP has only nine sessions because it is
newly listed. The load used the existing data path and did not change production code.

The final 120-session diagnostic applied the existing ₹5 crore median daily-turnover
tradability floor. It found 26 daily candidates and zero executable MCB triggers. All 26
are retained as `NO_TRADE`; the dominant rejection was no fresh breakout cross. A
GOODLUCK trigger that appeared before applying the liquidity gate was correctly rejected:
its preceding 20-session median turnover was only about ₹2.88 crore. Therefore there is
no success-rate or expectancy estimate yet and nothing is eligible for live use.

The success-only app examples remain hypotheses, not labels or proof of edge. The next
useful step is a broader historically frozen, liquid M5 universe followed by raw
after-cost evaluation; classification or expected-move training remains premature until
enough resolved events exist.

## Executive decision

Do not rebuild the application around the reference directory tree in the master
document. The existing architecture is coherent and already implements much of the
foundation. Build Momentum Compression Breakout (MCB) first as an isolated research
track in `tradedesk_lab`, reuse the production data/indicator/cost/risk primitives,
and promote only the pieces that survive causal, cost-adjusted validation.

The user's desired 70–80% successful-call rate remains a product target, but it must
not be manufactured by optimizing raw win rate or selecting a tiny hindsight subset.
The acceptance stack is:

1. strict success = executable target hit before stop/timeout **and** positive net P&L;
2. positive net expectancy and acceptable drawdown after all costs;
3. observed precision, Wilson uncertainty, call count, and active-session coverage;
4. robustness across time, regime, sector, liquidity, and worse execution;
5. fresh shadow evidence before any production promotion.

If no configuration satisfies all gates, the correct output is `NO TRADE`.

## A. Current repository architecture

| Area | Current implementation | Assessment |
|---|---|---|
| Configuration | `config/*.yaml`, strict Pydantic models | Mature; extend rather than replace |
| Market data | DuckDB `CandleStore`, INDstocks loader, CoinDCX/BSE support | Strong foundation; intraday breadth is limited |
| Data quality | OHLC/gap/duplicate checks, benchmark calendar, corporate-action audit | Present; timestamp availability metadata is incomplete |
| Universe | Monthly liquidity/price universe calculated as of date | Partial survivorship protection; historical constituents/surveillance incomplete |
| Indicators | Daily and intraday causal indicators, VWAP bands, ATR, RSI, ADX, volume features | Reusable; MCB-specific exhaustion/RVOL features missing |
| Daily setup engine | `scan_day` with Base Breakout, Trend Pullback, NR7 | Shared live/backtest path; no explicit MCB version |
| Intraday engine | `scan_bar`, closed-bar slicing, MTF alignment, VWAP Reclaim | Good foundation; MCB trigger/opening-range context missing |
| Regime/context | Daily NIFTY, breadth, VIX, RS, limited sector returns | Good daily basis; intraday NIFTY/sector context incomplete |
| Entry/exit simulation | Gap-aware fills, conservative stop priority, time/max-hold exits | Strong; spread/participation slippage is still simplified |
| Costs | Configured NSE cost model, ledger golden tests, provisional intraday schedule | Strong for delivery; intraday needs a real contract-note golden source |
| Risk | Position sizing, heat, sector cap, max positions/entries, weekly loss, pauses | Strong; daily loss, correlation, drawdown kill switch missing |
| Backtest | Shared scanner, monthly universe, daily/15m entries, portfolio replay | Mature swing framework; not yet an event-driven intraday MCB backtest |
| ML | Logistic, optional LightGBM/XGBoost; calibration; threshold selection | Substantial, but trained on existing swing candidates, not MCB events |
| Validation | Production walk-forward plus lab purge, CPCV, DSR and PBO | Lab is stronger; production calibrator currently uses shuffled inner CV |
| Experiment tracking | Isolated M14–M18 lab registry and immutable artifacts | Reusable; current reliability WIP must be finished first |
| Journal/paper | SQLite journal, paper book, live-vs-paper statistics | Present; rejected candidate outcomes are not unified into one event journal |
| Live monitoring | 15m confirmation, stale-feed pause, replay parity, alerts | Strong alert-only flow; no automatic order placement by design |
| Dashboard | Existing FastAPI dashboard with `/lab` integration | Keep unchanged during research; add only after evidence exists |

## B. Current trading flow

```text
INDstocks / stored candles
        ↓
DuckDB + data-quality checks + benchmark calendar
        ↓
Monthly liquid universe + daily feature preparation
        ↓
NIFTY/VIX/breadth regime + relative strength + results blackout
        ↓
scan_day (Base Breakout / Trend Pullback / NR7)
        ↓
scoring + eligibility + costs/R:R + sizing filters
        ↓
evening watchlist
        ↓
live 15m confirmation / gap and chased checks
        ↓
alert-only decision + journal / paper simulation
        ↓
gap-aware exits + full costs + portfolio limits
        ↓
dashboard, performance reports, review queue and shadow ML logs
```

The research lab is a parallel, non-promoting path for cleaner outcomes, model
comparisons, CPCV/stress diagnostics, prospective evidence, and intraday prototypes.

## C. Existing components that match the master specification

- Deterministic setup detection before ML.
- Shared live/backtest daily scanner and shared entry rules.
- Causal feature slicing and explicit leakage tests.
- NIFTY regime, VIX, breadth, relative strength, and some sector context.
- Monthly as-of-date liquidity universe and benchmark trading calendar.
- Gap-aware fills, stop-first same-bar ambiguity, time stops, and maximum holds.
- India-specific costs, slippage, position sizing, portfolio heat and sector caps.
- Logistic baseline, optional LightGBM/XGBoost, calibration and shadow-only inference.
- Walk-forward validation, final test separation, and stronger CPCV/DSR/PBO lab tools.
- Paper book, live journal, signal lifecycle, structured alerts, review queue and drift checks.
- Fail-closed behavior for stale live data and missing required VIX.
- Integrated dashboard and a read-only research lab view.

These should be reused. Reimplementing them in a new package tree would increase
parity risk and violate the repository's shared-engine design.

## D. Missing or partial components

| Requirement | Status | Primary gap | Planned treatment |
|---|---|---|---|
| Explicit MCB detector | Partial | Base Breakout resembles MCB but has no versioned MCB contract | Build isolated MCB v1 detector from existing patterns |
| Historical MCB reconstruction | Missing | No full point-in-time intraday event population | Replay every eligible session and log positives/negatives/rejections |
| Full intraday universe | Partial | Deep 1/5/15m data exists only for a small set | Begin with a frozen liquid pilot universe; expand only after data audit |
| Opening range | Missing | No formal 5/10/15/30-minute experiment | Add causal features and pre-register variants |
| Time-normalized RVOL | Missing | Current volume ratios are not slot-normalized | Build same-time-of-day rolling baselines |
| Remaining ATR/range | Missing | Weak late/exhausted setups cannot be rejected explicitly | Add causal distance-travelled and remaining-range features |
| Trend age/exhaustion | Partial | Some distance/momentum features exist, not a dedicated layer | Add a small pre-registered feature set and ablations |
| Intraday NIFTY/sector context | Partial | Daily context exists; intraday VWAP/breadth alignment does not | Snapshot index/sector features at signal availability time |
| MFE/MAE labels | Missing | No 15/30/60/EOD event outcomes | Add deterministic labeler with lower-timeframe ordering rules |
| Expected-move models | Missing | Only success classification exists | Add simple baselines, then quantile/tree regression if justified |
| Unified trade gate | Partial | Filters/eligibility exist but not MCB probability + move + economics | Build typed TRADE/WATCH/NO_TRADE research decision |
| Rejected-event journal | Partial | Rejections exist in separate logs | Store all candidates with immutable snapshots and later outcomes |
| Dynamic slippage | Missing | Fixed slippage only | Model spread/liquidity/time/participation after data supports it |
| Daily loss/correlation kill switches | Partial | Weekly and sector limits exist | Add only during production-promotion phase |
| Paper broker interface | Partial | Paper simulator exists, not a broker-compatible adapter | Defer until MCB is validated |
| Automatic execution | Intentionally absent | No M12 authorization | Remain alert/shadow-only |

## E. Technical debt and risk findings

### Highest priority

1. Existing production setups have not shown a positive edge and remain correctly
   blocked by eligibility. They cannot be used as evidence that MCB will work.
2. Existing exported research labels included invalid target geometry; the paused
   `net-target-v2` work is intended to fix this but is not merge-ready.
3. Historical triggered candidates are conditional on having triggered. They cannot
   measure full scanner availability or rejected/untriggered opportunities.
4. Production model calibration uses shuffled stratified inner folds. Although outer
   evaluation is chronological, all time-series preprocessing/calibration should be
   chronological and label-window purged.
5. The currently inspected historical windows are not untouched final evidence.
   A new strategy needs a frozen experiment contract and later prospective shadow data.

### Execution/data realism

- Daily bars cannot prove whether a target happened after an unknown intraday fill.
- Intraday fees are provisional until backed by a real contract note.
- Fixed slippage does not capture spread, liquidity, time of day, or participation.
- The current universe retains listing/survivorship and revision caveats.
- Sector candle coverage is limited to provider-supported indices.
- Event/calendar availability timestamps are not uniformly stored.
- Historical intraday breadth is insufficient for broad sector/regime claims.

### Product/evaluation risk

- Optimizing directly for 80% can collapse to almost no calls or overfit one symbol/regime.
- Session-level accuracy is discrete and unstable at low call counts.
- Candidate-level average return is not a risk-constrained portfolio return.
- Multiple correlated same-sector calls cannot count as independent evidence.
- Dashboard polish should follow evidence; it must not substitute for validation.

## F. Recommended implementation order

### Phase 0 — Finish and audit the paused reliability foundation

Scope: `tradedesk_lab` only. Do not resume automatically.

- Resolve the SciPy import/test environment without weakening system security.
- Run the complete lab suite and fix the new clean-dataset tests.
- Finish forward-integrity and immutable-outcome validation.
- Confirm `net-target-v2`: executable entry, valid geometry, conservative ordering,
  costs, net P&L, quantity, immutable feature/model/config hashes.
- Preserve legacy datasets and reports; never silently relabel them.

Gate: all lab tests/lint pass; no production files changed; old artifacts are intact.

### Phase 1 — Freeze the MCB v1 research contract

- Define the setup, prediction time, entry rule, invalidation, target/stop pairs,
  time exits, label horizons, ambiguity rule, cost version and allowed feature list.
- Define `bar_start`, `bar_end`, `available_at`, `signal_timestamp`, and earliest fill.
- Pre-register a small grid: compression duration, opening range, direct/retest entry,
  and no more than a few target/stop variants.
- Define success and reporting before looking at results.

Gate: a versioned contract reviewed before the first full historical run.

### Phase 2 — Data feasibility and frozen pilot universe

- Audit 1m/5m/15m/60m coverage, gaps, timezone, duplicates, zero volume and sessions.
- Freeze a liquid pilot universe based on data available at each historical date.
- Include NIFTY and supported sector benchmarks on all required timeframes.
- Store dataset source, adjustment state, checksums, close time and availability time.
- If broad history is unavailable, do not extrapolate from five symbols; describe the
  result as a prototype and expand data before model training.

Gate: reproducible data manifest; no critical gap/quality failure; sufficient symbols,
years, sectors and market regimes for the claimed scope.

### Phase 3 — Deterministic MCB detector and negative-event capture

- Reuse `daily_features`, `find_base`, trend/RS logic and `scan_bar` closed-bar safety.
- Add explicit prior momentum, compression, volume contraction, breakout proximity,
  trend maturity, remaining range, liquidity and time-of-day observations.
- Emit `WATCH`, `ARMED`, `TRIGGERED`, `REJECTED` and `INVALIDATED` transitions.
- Record every candidate, including false, late, opposed, illiquid, exhausted and
  untriggered cases. No ML in this phase.
- Compare raw MCB with matched random entries on the same symbol/session universe.

Gate: causal synthetic tests; deterministic replay; positive after-cost signal versus
matched random is required before proceeding to complex models.

### Phase 4 — Setup-event dataset and labels

- Persist immutable point-in-time feature snapshots.
- Calculate target-before-stop, strict net success, gross/net returns, fill quality,
  MFE/MAE at 15/30/60 minutes and EOD, and rejection outcomes.
- Use lower-resolution reconstruction where available and conservative ambiguity otherwise.
- Separate same-session MCB from swing outcomes; never pool their labels.
- Version feature, label, universe, cost and detector contracts.

Gate: feature truncation tests; label goldens; event counts reconcile with detector logs;
no unresolved or invalid rows enter model training.

### Phase 5 — Raw strategy evaluation

- Report expectancy, profit factor, drawdown, false-breakout rate, acceptance rate,
  time-of-day, sector, regime, liquidity, price and holding-time breakdowns.
- Stress base/1.25x/1.5x costs, 2x slippage, one-bar delay, missed fills and data gaps.
- Run direct-entry versus retest and remaining-range/exhaustion ablations.
- Track every attempted variant to control research degrees of freedom.

Gate: positive net expectancy, adequate sample size, stability across periods, and no
single-symbol/sector dependence. If it fails, stop or redesign MCB—do not add ML.

### Phase 6 — Baselines and calibrated classification

- Baselines: heuristic MCB score, logistic/ridge, Random Forest and Extra Trees.
- Add LightGBM only after baselines and only in the isolated environment; CatBoost and
  existing XGBoost remain challengers, not automatic improvements.
- Use nested chronological, purged and embargoed validation. Fit imputation,
  transformations and calibration inside each training fold.
- Evaluate Brier, ECE/calibration curves, PR-AUC, precision/recall and economic selection.
- Use an untouched diagnostic period once; never tune from it.

Gate: a complex model must materially and stably beat the heuristic/linear baseline.

### Phase 7 — Expected move, MFE and MAE

- Begin with unconditional and bucketed empirical baselines.
- Compare simple regularized regression, tree regression and quantile prediction.
- Predict horizons separately; do not mix EOD and 15-minute behavior.
- Check interval coverage and residuals, not just point RMSE.
- Test whether this layer rejects low-remaining-range/weak-Uniparts-style events.

Gate: measurable OOS improvement in selection economics or rejection quality.

### Phase 8 — Unified decision gate

Create a typed, versioned research decision with:

```text
setup quality
+ calibrated success probability
+ expected move/MFE/MAE and uncertainty
+ remaining range and time left
+ NIFTY/sector context
+ costs, spread/slippage and net R:R
+ liquidity and portfolio risk
→ TRADE / WATCH / NO_TRADE with reason codes
```

The 70–80% objective is tested here. Report observed strict precision, Wilson lower
bound, total calls, calls/session, active-session coverage, net expectancy and drawdown.
Suggested prospective qualification target: at least 100 resolved calls over at least
30 active sessions, observed precision at least 80%, Wilson lower bound at least 70%,
positive mean/total net return, and positive stress-case expectancy. This is a target,
not a guarantee; zero calls cannot pass.

Gate: all economic, reliability, coverage and risk requirements pass simultaneously.

### Phase 9 — Historical portfolio replay and governance

- Apply max positions, daily entries, sector/correlation concentration, heat, daily and
  weekly loss limits, cooldowns and liquidity capacity in event time.
- Run ablations: MCB only; +market; +sector; +classifier; +expected move; full gate.
- Record experiment count, DSR/PBO diagnostics, deterministic seed and complete manifest.
- Nominate a challenger only; no live promotion from historical results.

Gate: stable risk-constrained portfolio outcomes and a signed frozen shadow contract.

### Phase 10 — Shadow and paper evidence

- Generate real-time MCB decisions without orders.
- Save every prediction before the earliest eligible entry and append outcomes later.
- Compare backtest assumptions with actual missed fills, latency and slippage.
- Monitor calibration, feature/prediction drift, call availability and data health.
- Keep the existing production strategy and dashboard behavior unchanged initially.

Gate: a fresh prospective cohort independently meets the qualification criteria.

### Phase 11 — Production promotion, only after explicit approval

- Port the proven detector into the shared engine and add parity/replay tests.
- Add dashboard views without replacing current management or dashboard behavior.
- Run challenger in shadow; require review before champion replacement.
- Remain alert/manual-approval only. Broker order placement is a separate M12 decision
  and is not authorized by this roadmap.

## G. Exact existing files likely to modify after research passes

No production file should be modified during Phases 0–8 unless separately approved.
Promotion would most likely touch:

| File | Minimal purpose |
|---|---|
| `src/tradedesk/engine/signals.py` | Add a versioned MCB setup kind/typed metadata |
| `src/tradedesk/engine/intraday_signals.py` | Add MCB intraday event kind if needed |
| `src/tradedesk/engine/indicators.py` | Promote proven remaining-range/RVOL features |
| `src/tradedesk/engine/intraday_engine.py` | Register proven trigger; preserve closed-bar slicing |
| `src/tradedesk/setups/__init__.py` | Register higher-timeframe MCB candidate detector |
| `src/tradedesk/setups/intraday/__init__.py` | Register MCB trigger after validation |
| `src/tradedesk/prediction/features.py` | Add only stable validated features; bump version |
| `src/tradedesk/prediction/labeling.py` | Promote tested intraday/event labels if shared |
| `src/tradedesk/prediction/train.py` | Replace shuffled inner calibration with temporal folds |
| `src/tradedesk/engine/filters.py` | Integrate expected-move/range/spread reason codes |
| `src/tradedesk/risk/limits.py` | Add approved daily-loss/correlation controls |
| `src/tradedesk/journal/db.py` | Persist immutable candidate/prediction/rejection records |
| `src/tradedesk/scan/evening_scan.py` | Add MCB candidate flow through the shared engine |
| `src/tradedesk/live/session.py` | Add shadow/manual MCB consumption after parity proof |
| `src/tradedesk/dashboard/app.py` | Read-only MCB views only after evidence exists |
| `src/tradedesk/dashboard/static/index.html` | Add, do not replace, existing views |
| `config/setups.yaml` | Enable only after eligibility evidence and explicit review |
| `config/ml.yaml` | Challenger policy and thresholds after validation |
| `config/risk.yaml` | Add reviewed controls without relaxing existing caps |

Every production edit must retain existing setups, dashboard, management, risk caps and
alert behavior unless a separate change is explicitly approved.

## H. Exact research files/modules to add

Suggested additions, kept isolated first:

```text
tradedesk_lab/mcb_contract.py
tradedesk_lab/mcb_features.py
tradedesk_lab/mcb_detector.py
tradedesk_lab/mcb_dataset.py
tradedesk_lab/mcb_labels.py
tradedesk_lab/mcb_baselines.py
tradedesk_lab/expected_move.py
tradedesk_lab/mcb_gate.py
tradedesk_lab/mcb_portfolio.py
tradedesk_lab/mcb_report.py

tests_lab/test_mcb_contract.py
tests_lab/test_mcb_features.py
tests_lab/test_mcb_detector.py
tests_lab/test_mcb_dataset.py
tests_lab/test_mcb_labels.py
tests_lab/test_mcb_baselines.py
tests_lab/test_expected_move.py
tests_lab/test_mcb_gate.py
tests_lab/test_mcb_portfolio.py
```

Add `config/strategy_mcb.yaml` only when moving from a frozen research contract toward
production; while researching, store the contract with the experiment artifact so it
cannot silently affect existing scans.

## I. Tests required before each phase is complete

| Phase | Required tests/evidence |
|---|---|
| 0 | Complete lab tests, lint, compile, artifact immutability, old-output preservation |
| 1 | Contract schema, timestamp semantics, stable hash/version, invalid geometry |
| 2 | Gaps/duplicates/OHLC/timezone/zero-volume/staleness, point-in-time universe |
| 3 | Valid MCB/no trend/no compression/no contraction/false/late/exhausted scenarios |
| 4 | Feature truncation invariance, target-stop ordering, MFE/MAE goldens, cost labels |
| 5 | Deterministic replay, matched random, stress and ablation reproducibility |
| 6 | Purged nested temporal splits, fold-local preprocessing/calibration, baseline parity |
| 7 | Horizon alignment, quantile coverage, no target leakage, economic ablation |
| 8 | Full TRADE/WATCH/NO_TRADE truth table, reason codes, threshold/coverage reporting |
| 9 | Portfolio limits, correlation/sector caps, daily loss, delay/missed-fill sensitivity |
| 10 | Prediction-before-entry proof, immutable snapshots, restart/duplicate protection |
| 11 | Live/backtest parity, replay, stale-data fail-close, dashboard/API compatibility |

## Next concrete task

Keep the agents parked. Expand from the ten-symbol diagnostic to a representative,
historically frozen liquid pilot universe, using the existing loader and database
contract. Rebuild the event dataset, then run the raw setup and matched-random evaluation
before authorizing classification or expected-move models. This is the smallest sequence
that can answer the decisive first question: **does MCB itself have a reproducible
after-cost edge?**
