# Accuracy-first checkbox roadmap

Updated: 30 September 2026. The governing forward plan is the
[absolute baseline-accuracy improvement plan](plan-absolute-baseline-accuracy.md).

Primary objective: maintain and improve the reliability of executable NSE calls,
working toward 70–80% successful calls within sessions. Prioritize anticipatory
entries and smaller, quicker profits. Call volume is flexible, but useful availability
must be demonstrated alongside accuracy and returns after costs.

Governing rule: every milestone and experiment must work toward a more accurate,
better-generalizing prediction baseline. It must identify the frozen comparison
dataset and report deltas in strict accuracy, Wilson lower bound, session consistency,
call availability and after-cost net R. Engineering completion alone is not predictive
improvement. Fewer calls cannot hide losses, unresolved outcomes or worse economics.

`[x]` means the stated work has supporting evidence. `[ ]` means work or evidence is
still required. Completing an engineering task does not complete an accuracy goal.
This checklist is the historical execution ledger. The absolute plan now governs new
work and updates the execution sequence in the earlier
[master roadmap](trading-agent-master-roadmap.md) and
[reliability plan](reliability-70-80-plan.md); their historical status sections remain
dated records. Proposed future production changes in those documents remain deferred.

## Current position

- [x] Implement Checkpoint 1's fail-closed data manifest and complete the feasibility
  audit. It stopped because point-in-time membership, Cohort B, announcement history
  and execution evidence are unavailable. No signal family or Checkpoint 2 work is
  authorized; the baseline remains 21.50%.
- [x] Complete absolute-plan Checkpoint 0: freeze the two scoreboards, Levels 0–4,
  evidence classes, trial budgets and hash-chained trial ledger. Ten prior research
  tracks accounting for 916 trials are consumed development evidence; this is
  governance, not an accuracy improvement. The canonical baseline remains 21.50%.
- [x] Preserve production call, risk and management behavior; conduct strategy work
  in the separate lab and branch. The dashboard addition is read-only and exposes
  research evidence without changing call eligibility.
- [x] Implement causal AEM anticipatory-entry and quick-profit research, with a
  separate MCB breakout research track.
- [x] Establish the corrected AEM development baseline: **40/106 strict wins
  (37.74%)**, **−0.13497R per filled trade**. No accuracy improvement is established.
- [x] Reproduce the conditional random-policy comparison: AEM beats that comparator
  by **+0.18038R per attempt**, while both still lose after modeled costs.
- [x] Screen the 2,664 locally stored NSE cash EQ stocks and freeze a 50-stock pilot
  using liquidity observed before the evaluation period.
- [x] Complete the resumable M1 collection: **2,624,979 valid bars**, **6,999/7,000
  complete stock-sessions**, 272 requests recorded. The only exception is VEDL on
  30 April 2026, whose reproducible 21-bar opening gap is retained and rejected.
- [x] Complete the bounded three-index context acquisition attempt: **134,987/135,000
  bars (99.990%)** and **359/360 complete index-sessions** in 27 requests. The single
  Nifty Financial gap on 7 July 2026 from 15:17-15:29 repeated identically and is
  preserved; this is data readiness, not an accuracy improvement.
- [x] Complete the causal context integrity join: **1,397/1,397 frozen decisions**
  joined to completed price bars with zero exclusions and zero outcome columns. All
  decisions have 1/3/5-minute returns; 755 have 15-minute returns. Only 16 have
  complete all-index VWAP, so VWAP is rejected from the next mechanism test.
- [x] Replay the unchanged AEM contract on the frozen 50-stock cohort: **149/693
  strict wins (21.50%)**, **−0.27471R per resolved fill**. The baseline fails.
- [x] Run the broader matched-random gate: observed timing is **−0.03949R per
  attempt worse** than 500 matched timing-null cohorts. The gate fails.
- [x] Run all registered execution stresses. The worst case is doubled slippage at
  **18.90% strict success** and **−0.44255R per fill**. The gate fails.
- [x] Run the portfolio-constrained replay: **40/191 strict wins (20.94%)**,
  **−0.27686R per fill** and **−₹7,407.78**. The gate fails.
- [x] Run the first bounded causal information-quality selector. The development
  winner failed on the later 40-session holdout: **13/100 strict wins (13.00%)**,
  **7.76% Wilson lower bound** and **−0.46624R/fill**. It was rejected.
- [x] Freeze the custom AEM v2 Precision Ladder Milestone-0 protocol: three entry
  modes, 34 causal decision-time features, three quick-profit geometries and the
  50%/100-fill/30-session gates. This is a protocol, not an accuracy improvement.
- [x] Implement the AEM v2 Milestone-1 causal event/outcome engine for all three modes
  and quick-profit geometries. Conservative gap/chase, ambiguity, deadline, costs and
  missing-data tests pass. No selector has run and no accuracy improvement is claimed.
- [x] Complete the AEM v2 Milestone-2 feature and integrity layer: 34 decision-time
  features, cross-sectional breadth/relative-strength, and a universe integrity audit
  against the real frozen 50-stock cohort - **5,999/6,000 symbol-sessions included
  (99.983%)**, the one exclusion being the known VEDL 2026-04-30 incomplete-M1
  exception. No selector has run and no accuracy improvement is claimed.
- [x] Implement the AEM v2 Milestone-3 Precision Ladder selector mechanics: evidence
  contributions with sparse-regime shrinkage, a hard veto layer, fold-local
  out-of-fold calibration, absolute thresholding, per-session ranking, symbol
  deduplication and a logistic control. Proven leak-free by a reproducible
  independent-refit test. Not run against real data; no accuracy improvement claimed.
- [x] Build the AEM v2 Milestone-4 development-dataset assembly (opportunities ×
  features × per-geometry outcomes over the frozen included universe), fixing a real
  tz-backend bug this surfaced in the Milestone-2 feature module along the way. Real run
  complete 27 September: 5,999 symbol-sessions, 66,668 opportunities, 177,351 resolved
  rows.
- [x] Build the AEM v2 Milestone-4 pipeline race (Stage 2): 24 registered
  specifications (4 entry-mode groupings x 3 geometries x 2 models), a nested
  chronological walk-forward evaluation reusing `validation.walk_forward` and
  Milestone 3's own OOF calibration, and the frozen development gates. Real run complete
  27 September, after finding and fixing two hard-veto bugs the first real attempt
  surfaced (99.997% of rows vetoed - see the pipeline-race checkpoint). Corrected result:
  **no registered specification clears the development gate on real data.** AEM v1
  remains the canonical baseline (21.50% strict success).
- [x] Build the AEM v2 Milestone-4 mandatory execution-stress replay
  (`aem_v2_stress_gates.py`): re-resolves a nominated `(spec_id, top_k)`'s real,
  selected fills through the real Milestone-1 outcome engine under cost inflation
  (1.25x/1.5x), doubled slippage, a one-minute execution delay and an adversarial
  best-10%-never-fills case, reusing AEM v1's own stress precedent
  (`aem_staged_validation.py`) rather than a new methodology. Verified against a real,
  reconstructed opportunity fixture. No specification was ever nominated (the real
  pipeline race qualified none), so this has nothing real to evaluate - the honest
  completion of this item, not a gap.
- [ ] Demonstrate positive portfolio returns after realistic costs and execution. Not
  reached - no AEM v2 candidate cleared the development gate to test this against.
- [ ] Demonstrate the requested accuracy, session consistency and call availability
  on fresh evidence. Not reached, for the same reason.

Evidence: [collection checkpoint](aem-history-checkpoint.md),
[corrected baseline](nse-aem-validation-checkpoint.md),
[random comparison](aem-random-policy-checkpoint.md), and
[canonical scorecard](aem-scorecard-checkpoint.md), and
[baseline gates](aem-baseline-gates-checkpoint.md). The first bounded challenger is
recorded in the [daily mean-reversion exit-search checkpoint](daily-mean-reversion-exit-search-checkpoint.md),
and the first causal selector is recorded in the
[information-quality checkpoint](aem-information-quality-checkpoint.md).
Market/sector input readiness is recorded separately in the
[context readiness checkpoint](aem-market-sector-context-checkpoint.md); it has not
changed the baseline.
The custom-algorithm protocol is recorded in the
[AEM v2 Milestone-0 checkpoint](aem-v2-protocol-checkpoint.md); it contains no model
or performance result. The research-only mechanics are recorded in the
[AEM v2 Milestone-1 checkpoint](aem-v2-engine-checkpoint.md); they also contain no
model or performance result. The causal feature registry (the first half of
Milestone 2) is recorded in the
[AEM v2 Milestone-2 feature checkpoint](aem-v2-features-checkpoint.md); the universe
integrity audit and frozen development dataset that complete Milestone 2 are recorded
in the
[AEM v2 Milestone-2 universe-audit checkpoint](aem-v2-universe-audit-checkpoint.md).
Neither contains a model or performance result. The Precision Ladder selector
mechanics are recorded in the
[AEM v2 Milestone-3 checkpoint](aem-v2-precision-ladder-checkpoint.md), which also
carries forward the full list of known gaps across Milestones 3-4; it contains no
model or performance result either. The Milestone-4 development-dataset assembly (code,
tests, and the tz-bug fix it surfaced) is recorded in the
[AEM v2 Milestone-4 dataset checkpoint](aem-v2-development-dataset-checkpoint.md); its
real run completed 27 September (177,351 resolved rows). The Milestone-4 pipeline race
(Stage 2) is recorded in the
[AEM v2 Milestone-4 pipeline-race checkpoint](aem-v2-pipeline-race-checkpoint.md), whose
"Real run, 27 September 2026" section is the first place in this entire AEM v2 effort
with an actual accuracy result: two hard-veto bugs found and fixed, then a real,
non-degenerate run showing no registered specification clears the development gate - AEM
v1 remains the canonical baseline. The mandatory execution-stress replay is recorded in
the
[AEM v2 Milestone-4 stress-gates checkpoint](aem-v2-stress-gates-checkpoint.md); it is
verified against a real, reconstructed opportunity fixture but has nothing to evaluate,
since no specification was nominated.

## 1. Freeze how accuracy will be measured

- [x] Define strict success for the current AEM contract: an executable fill followed
  by the declared target before the stop/deadline, with positive net P&L after costs.
- [x] Freeze the comparison scorecard and research protocol before the next model
  race: same-session AEM is primary; swing and MCB results remain separately labeled.
- [x] Report issued calls, actual fills, wins, filled losses/timeouts, unfilled
  attempts, unresolved outcomes and zero-call days. Positive time exits remain a
  separate metric; unresolved fills prevent a clean final accuracy assessment.
- [x] Publish both pooled strict accuracy and each session's wins/fills, plus the
  fraction of active sessions reaching 70% and 80%, losing streaks and worst periods.
  Show active-session coverage over every eligible calendar session.
- [x] Freeze useful-availability and session-consistency acceptance criteria before
  prospective testing. Compare qualifying top-one, top-two and top-three policies
  within existing limits; report the full accuracy-versus-availability tradeoff.

Acceptance: every percentage has a fixed denominator, period, outcome contract and
uncertainty estimate. A zero-call day is visible and is not a successful session.
Overall 80% accuracy cannot substitute for the user's per-session objective. With
at most three entries per day, a day with one to three fills reaches 70% only if all
of its fills win. Any session below target remains a recorded miss. A minimum result
in every future session cannot be guaranteed by a model or by this roadmap.

## 2. Complete and verify the data foundation — next work

- [x] Finish the frozen collection; all planned request batches are exhausted and
  only one of 7,000 stock-sessions remains incomplete.
- [x] Verify exact regular-session slots; investigate missing history, special
  sessions and conflicting data. Record every exclusion and its effect on coverage.
- [x] Build the isolated daily/index/minute-data join. Preserve earlier M1 history
  needed by indicators and relative volume, or explicitly register a changed history
  contract and re-establish its baseline.
- [x] Freeze the three-index, 120-session context acquisition plan and implement a
  resumable isolated collector with preflight, request caps and fail-closed storage.
- [x] Complete the context integrity join. All **1,397/1,397 decisions** joined with
  no exclusions; the repeated 15:17-15:29 source gap affects zero decisions because
  the frozen decision window ends at 11:00. Price context is ready; VWAP is not.
- [x] Preregister a narrower context hypothesis that cannot leak current membership:
  use all three index series uniformly for every candidate and do not join stocks to
  present-day sector/index membership. Point-in-time membership remains future work.
- [ ] Verify feature availability times, adjustments, benchmark dates, trading
  status and data-source versions. Preserve source and experiment fingerprints.
- [ ] Restore the five SciPy-dependent test modules in an isolated compatible
  research environment while preserving system security and production dependencies.
  Current runnable verification is **930 passed**, with two pre-existing skips
  and one deselected tuning test.
  Five modules remain collection-blocked by SciPy `_dop`; one tuning test is blocked
  by scikit-learn `_sgd_fast`. Windows Application Control was not weakened.

Acceptance: a reproducible, auditable evaluation dataset with no unexplained gaps in
consumed signal/label windows. Exceptions cannot silently change the frozen cohort
or remove losing opportunities. Acquisition progress is measured separately from
strategy performance.

## 3. Establish a broader, executable baseline

- [x] Replay the unchanged AEM contract on the frozen 50-stock cohort. Keep the
  current 0.8% target, 0.6% stop and 90-minute maximum hold fixed for this comparison.
- [x] Re-run matched random timing policies with the same candles, fills, deadlines
  and costs. The timing gate fails; a stock-selection control is still required.
- [x] Replay simultaneous calls under existing position, entry, sector, sizing and
  portfolio-heat limits. Report executable portfolio results alongside event results.
- [x] Apply the registered modeled-cost, doubled-slippage, one-bar-delay and
  missed-fill stresses. Suitable broker evidence and explicit gap-exit verification
  remain required before any production claim.
- [x] Classify observable errors: weak impulse, exhausted move, insufficient room,
  weak same-time volume, liquidity and cost drag. Intraday market/sector context is
  explicitly unavailable and remains a data task. Retain every failure.
- [x] Test the preregistered three-rule daily mean-reversion exit grid across three
  risk sizings. The best locked-test profitable-trade rate was **50.13%**, every
  after-cost result was negative, and the challenger was rejected without changing
  the canonical baseline or registering a strategy.

Acceptance: a complete baseline scorecard and a documented explanation of where
errors arise. The 50-stock pilot cannot establish reliability across all NSE stocks.

## 4. Improve the information used to select calls

- [x] Register and execute 11 causal selector specifications, below the prior maximum
  of 24. Log every development result and open one chronological holdout once.
- [ ] Test market/sector alignment, relative strength, same-time volume, opening
  structure, volatility, trend age and remaining room for the target using only
  information available when the signal would have been issued.
- [x] Test the available causal subset—same-time volume, remaining room, extension,
  VWAP context and impulse-body quality. None earned promotion; benchmark/sector
  alignment still requires new point-in-time inputs.
- [ ] Freeze and evaluate market/sector context on later unconsumed sessions or a
  prospective shadow cohort after acquisition and integrity checks are complete.
- [ ] Test anticipatory impulse versus confirmed pullback/retest entry, distance
  from VWAP and resistance, and execution quality. Version each changed entry policy.
- [ ] Test quick-profit exit alternatives as separate contracts with identical
  cost/risk reporting. Judge average win/loss, expectancy and drawdown with accuracy.
- [x] Complete the first bounded quick-profit exit search. None of its nine
  training-selected runs reached the 60% research target or positive locked-test
  expectancy, so the plan correctly stopped before gauntlet or candidate registration.
- [x] Run one-component-at-a-time comparisons and a single registered bundle. The
  selected impulse-body filter did not survive the later period and was rejected.
  App success examples remain hypotheses.

Acceptance: reproducible improvements over the frozen baseline with credible
after-cost economics. If the proposed information does not help, revise the
hypothesis before increasing model complexity. Do not widen losses, relax risk
limits or retrospectively alter outcomes to raise the success percentage.

## 5. Compare learning paths against simple baselines

- [ ] Start with a transparent rule score and regularized logistic model.
- [ ] Compare existing XGBoost/CatBoost and tree challengers on the same folds,
  population and execution assumptions; retain only measured incremental benefit.
- [ ] Calibrate probabilities chronologically and test a second-stage selector
  trained on predictions generated outside its training folds.
- [ ] Test expected remaining move and adverse-move/quantile estimates if they help
  reject trades whose achievable profit is too small for costs and downside.
- [ ] Assess regime specialists, ensembles or temporal neural models only when
  sample size and independent validation justify them. Defer unnecessary complexity
  with a recorded reason. Additional agents can audit evidence and reproduce results.

Acceptance: improved selected-call accuracy, useful availability and net expectancy
on later data. A predicted 80% probability, agreement among models or a more complex
architecture does not satisfy the observed-accuracy requirement.

## 6. Validate generalization and expand NSE coverage

- [ ] Use nested chronological walk-forward splits, purge overlapping outcomes and
  apply an embargo. Fit preprocessing, calibration and thresholds within each fold.
- [ ] Keep all previously inspected history marked as development data. Maintain
  a trial ledger and use unseen evaluation periods only under a frozen protocol.
- [ ] Compare by date, symbol, sector, liquidity and market regime; use session/week
  grouping for uncertainty because same-day calls can share the same risk.
- [ ] Expand the pilot into preregistered sector and liquidity groups. Audit exchange
  listing coverage, new listings, historical membership and unavailable data.
- [ ] Assess the full supported tradable NSE universe through explicit eligibility
  filters. Report unsupported/illiquid stocks and rejected opportunities; eligibility
  to be screened does not imply eligibility for a call.
- [ ] Reproduce the candidate's scorecard independently, including losing periods,
  portfolio constraints, sensitivity to nearby parameters and stressed costs.

Acceptance: robust improvement over the baseline within the stated tested scope.
Current metadata and revised history retain survivorship/vintage limitations.
Sparse subgroups remain unqualified until their evidence is adequate.

## 7. Collect fresh paper evidence and assess the accuracy target

- [ ] Freeze one candidate's data, features, model, thresholds and execution contract
  before its prospective observation period; register evaluation checkpoints.
- [ ] Record paper predictions before eligible entries, then append mature outcomes
  without changing predictions. Verify restart, duplicate and scoring-deadline rules.
- [ ] Reach the proposed first research review point: at least **100 resolved
  prospective selected calls over at least 30 active sessions**; extend observation
  when actual call volume or outcome maturity requires it.
- [ ] At that checkpoint, test **at least 80% observed strict success**, a **95%
  Wilson lower bound of at least 70%**, and session/week-cluster uncertainty. Require
  positive net expectancy under the registered execution/cost stress cases.
- [ ] Pass the predeclared session-consistency and availability criteria as well as
  pooled accuracy. Report every below-target and zero-call session.
- [ ] Satisfy the unchanged existing eligibility minima: **500 resolved trades,
  100 OOS trades, 80% measured win rate, positive expectancy and at least +0.10R versus
  matched random**. Reconcile that gate's inputs with the strict success contract.
  Report historical, OOS and prospective counts separately.

Acceptance: accuracy, session behavior, availability, economics and risk all pass
together. These sample counts are minimum review points, not automatic certification;
insufficient independent evidence means further observation. Registered review dates
or a valid sequential protocol prevent repeatedly testing until a favorable result.
Failures return to a documented hypothesis and a fresh evaluation cohort.

## 8. Maintain accuracy as conditions change

- [ ] Define rolling strict accuracy, calibration, net expectancy, fill quality,
  availability and data-health checks, with predeclared warning/pause thresholds.
- [ ] Keep the evaluated model fixed for its cohort. Train challengers only on
  resolved past data and give each version new future evaluation evidence.
- [ ] Investigate deteriorating sessions and publish the causes; require challengers
  to improve on the incumbent under the same evaluation rules.
- [ ] Demonstrate that stale feeds, drift or invalid inputs stop affected research
  calls, and that recovery and model rollback work without rewriting old records.
- [ ] Prepare an evidence package for any later production decision. Preserve the
  current base code, dashboard and management system; integration remains a separate
  user-approved change after qualification. Broker order placement stays outside scope.

Acceptance: measured performance remains within the registered operating criteria,
with an auditable history of every model and call. Improvements earn promotion through
fresh evidence; missed targets remain visible.

## Next three deliverables

- [x] Complete and audit the frozen M1 collection.
- [x] Build the reproducible broader AEM dataset and executable baseline scorecard.
- [x] Run matched-random, stress and portfolio gates on that exact frozen baseline.
- [x] Register and execute the first bounded exit-geometry experiment; preserve its
  failed result as evidence rather than promoting a weak or test-selected rule.
- [x] Register the first information-quality experiments from the frozen baseline's
  failure taxonomy and evaluate one selected rule on the later chronological cohort.
- [x] Test the leakage-safe index-price context mechanism before any selector.
  Collection now covers 359/360 index-sessions (134,987/135,000 bars); the sole
  repeated source gap is frozen. The leakage-safe index-only hypothesis is registered
  in the [AEM index-context accuracy plan](plan-aem-index-context-accuracy.md). The
  [causal join integrity checkpoint](aem-index-context-integrity-checkpoint.md) passed
  for all 1,397 decisions. The committed mechanism run tested four rules once. All
  improved pooled raw accuracy, but every rule remained negative after costs and none
  passed the complete fold/shuffle/economic gates. The best raw subset was 72/278
  (25.90%) at -0.19418R. The track stopped before a selector; see the
  [mechanism result checkpoint](aem-index-context-mechanism-result-checkpoint.md).
- [x] Freeze the AEM v2 custom-algorithm contract, feature registry, entry modes,
  geometry grid, trial budget and evidence gates with immutable fingerprints.
- [x] Build Milestone 1: the causal event and outcome engine with conservative fills,
  costs, deadlines and target/stop ordering before training a selector.
- [x] Complete Milestone 2: the causal feature registry, the universe integrity audit
  (5,999/6,000 symbol-sessions included on the real frozen cohort) and the frozen
  development dataset are all done.
- [x] Build Milestone 3: the Precision Ladder selector mechanics (evidence
  contributions, hard vetoes, fold-local calibration, thresholding, ranking,
  deduplication, logistic control) are implemented and proven leak-free. Its real-data
  use is now covered by the completed Milestone-4 pipeline race.
- [x] Complete Milestone 4: the real 5,999-symbol-session development dataset contains
  177,351 resolved opportunity-by-geometry rows, and all 24 preregistered specifications
  were evaluated with nested chronological walk-forward selection. None cleared the
  frozen development gate. Therefore no candidate was nominated and the implemented
  execution-stress replay has no selected fills to evaluate. This is a completed negative
  checkpoint, not an accuracy improvement; AEM v1 remains the 21.50% canonical baseline.
- [x] Register OSR v1 (Opening Sweep-Reclaim), a genuinely different failed-auction
  mean-reversion signal family, before another bounded race. Its two event modes, three
  small-profit geometries, 30 causal feature names, 12-specification budget, accuracy/
  availability/economics gates and evidence classes are frozen in Milestone 0. This is
  a protocol, not a performance result; all previously inspected history remains
  development-only evidence.
- [x] Build OSR Milestone 1's causal event and conservative outcome engine. Both frozen
  failed-auction modes now require completed observations in order; future bars cannot
  change a decision; later fills, chase, gaps, target/stop ambiguity, deadlines, costs,
  and missing data resolve conservatively. Twelve focused test functions pass. This is engine
  evidence only and does not improve the baseline.
- [x] Implement OSR Milestone 2A's 30-feature causal layer and audited development-
  dataset assembly. Future candidate/peer bars, current daily bars, candidate-included
  peer statistics, mismatched prior context, and undersized peer populations fail closed.
  The full engine/feature/synthetic-dataset suite passes 34 cases. This is implementation
  evidence only and does not improve the baseline.
- [x] Run and freeze the real 5,999-symbol-session OSR development population. Run
  `2ece3a469de84a3aad74f8afa9192f59` found 2,697 causal opportunities and 7,303
  resolved geometry rows across 120 sessions. The raw label prevalence is 21.42% and
  raw after-cost mean net R is -0.529; every unfiltered geometry is negative. All 788
  exclusions are retained. This is consumed development evidence, not selector
  accuracy, and it does not improve the 21.50% canonical baseline.
- [x] Run the frozen 12-specification OSR chronological walk-forward selector race on
  a corrected decision-time population that retains all 8,091 calls, including 720
  later-unfilled and 68 unresolved outcomes. Both selector families issued zero calls
  at the frozen 50% probability threshold; the best unfiltered OOS slice was 25.71%
  strict success (22.16% Wilson lower bound) with -0.433R mean net R. No specification
  cleared the gates, so OSR is rejected without retuning and AEM v1 remains the 21.50%
  canonical baseline. See the
  [OSR Milestone-3 selector-race checkpoint](osr-selector-race-checkpoint.md).
- [x] Register SSM v1 (Same-Slot Micro-Momentum) as the next evidence-backed signal
  family. Unlike AEM/OSR pattern variations, SSM tests published half-hour return
  periodicity documented directly on NSE data and independently in the Journal of
  Finance. Its 30-minute slots, two predictor modes, three quick-profit geometries,
  24 causal features, 12-specification budget, placebos, and unchanged accuracy/
  confidence/availability/economics gates are frozen before measurement. This is a
  protocol, not an accuracy improvement; see the
  [SSM 50% baseline plan](plan-ssm-50pct-baseline.md).
- [x] Implement SSM Milestone 1's causal feature, opportunity, and outcome engine.
  All 24 frozen features are computed one minute before entry from prior matching
  slots and completed pre-decision bars; the candidate is excluded from at least 30
  peers. Conservative next-slot fills, zero-volume entries, gaps, same-bar ambiguity,
  costs, missing windows, and time exits are explicit. Nineteen SSM tests pass across
  the protocol and engine. This remains implementation evidence only; no real SSM
  population or accuracy result exists. See the
  [SSM Milestone-1 engine checkpoint](ssm-engine-checkpoint.md).
- [x] Evaluate SSM Milestone 2 on the frozen real 50-stock population and enforce the
  mechanism stop rule. All 66,000 planned stock/slot decisions are accounted for,
  with 65,989 eligible and 11 explicitly excluded. Lag-1 same-slot continuation was
  negative, and the mean same-slot coefficient was weaker than the next-adjacent-slot
  placebo. SSM is therefore rejected before any selector race; the canonical 21.50%
  baseline does not change. See the
  [SSM Milestone-2 mechanism checkpoint](ssm-mechanism-checkpoint.md).
- [x] Restore the fail-closed 80% observed / 70% Wilson live qualification gate and
  explicitly keep newly installed detectors research-only until they earn evidence.
  This restores governance, not measured accuracy; see the
  [accuracy-recovery Milestone-1 checkpoint](accuracy-recovery-m1-checkpoint.md).
- [x] Implement the high-precision top-1/top-2/top-3 selector curve with accuracy,
  Wilson, session coverage, sample and complete-economics gates. No compatible model
  has qualified; see the
  [accuracy-recovery Milestone-2 checkpoint](accuracy-selector-m2-checkpoint.md).
- [x] Freeze Milestone 3's transparent-score/logistic/tree challenger race and run its
  fail-closed readiness audit. The 29,964-row saved cohort is blocked before fitting:
  13 v4 features are missing, economics coverage is 612/29,964 (2.04%), and rule-score
  coverage is zero. The locked tail remains unopened and the 21.50% canonical baseline
  is unchanged; see the
  [Milestone-3 checkpoint](accuracy-selector-m3-checkpoint.md).
- [x] Complete Milestone 4's executable repair: rebuild 22,756 rows with 33 causal
  features, 100% transparent-score coverage and 100% reconstructed after-cost economics,
  then actually train/evaluate the rule-score, logistic and histogram-gradient-boosting
  candidates on 13,305 identical walk-forward OOS rows. No candidate qualifies; the best
  logistic sampled point is 47.83% on only 23 calls with a 29.24% Wilson lower bound,
  2.92% session coverage and -0.056R expectancy, so the locked 4,848-row tail remains
  unopened. Crypto monitoring is also expanded from ten hard-coded coins to the dynamic
  active CoinDCX INR universe (339 pairs in the verified run). See the
  [Milestone-4 checkpoint](accuracy-training-m4-checkpoint.md).
- [x] Complete Milestone 5's coverage-aware ranking experiment. Seven candidate
  models were evaluated on the same 13,305 chronological OOS rows over thresholds
  from 0.15 to 0.95. Balanced logistic was best at the deployability reporting
  floor, with 146/476 wins (30.67%), a 26.70% Wilson lower bound, 45.21% session
  coverage and -0.446R expectancy. It failed the frozen gates, no nominee was
  created, and the locked 4,848-row tail stayed unopened. The 47.83% result remains
  only a 23-call diagnostic, not a baseline. AEM v1 remains the 21.50% canonical
  baseline; see the
  [Milestone-5 checkpoint](accuracy-ranking-m5-checkpoint.md).
- [x] Pivot Milestone 6 from classifiers to anticipatory entry and quick-profit
  geometry. The frozen 54-cell development race improved the best complete result
  from a 50.50% saved-fill control to 73.93% at next-session open, with a 73.27%
  Wilson lower bound. It still failed with -0.076R expectancy and only 38.50% of
  sessions reaching 80%, so no nominee existed and the 4,848-row tail stayed locked.
  A development-only `trend_pullback` diagnostic reached 79.35% on 1,811 calls with
  +0.033R; it becomes the next preregistered stability hypothesis, not a promoted
  result. The canonical baseline remains 21.50%; see the
  [Milestone-6 checkpoint](accuracy-geometry-m6-checkpoint.md).
- [x] Complete Milestone 7's preregistered setup-specific stability test. The fixed
  anticipatory trend-pullback mechanism passed its four-fold stability gate, and its
  single causal logistic selector qualified on embargoed development OOS evidence at
  526/647 wins (81.30%), a 78.11% Wilson lower bound, 71.16% successful-session rate
  and +0.056R. Its one-time locked historical evaluation also passed: 186/223 wins
  (83.41%), 77.97% Wilson lower bound, 73.98% successful-session rate and +0.083R.
  This is the program's first historical locked pass, but the source population is
  previously inspected research; prospective shadow evidence is still pending and no
  live/canonical baseline changes. See the
  [Milestone-7 checkpoint](accuracy-setup-stability-m7-checkpoint.md).
- [x] Implement Milestone 8's forward-only shadow validator for that exact frozen
  candidate. Activation excludes its own date, every existing watchlist and every
  already-stored NSE benchmark session; later sessions are scored before the next 09:15
  cutoff at p>=0.55 and top two per session, then resolved with the fixed next-open /
  1 ATR stop / 0.5R / three-session after-cost contract. The dashboard reports fresh sample size, accuracy,
  Wilson lower bound, successful-session rate and expectancy while keeping live authority
  false. Implementation is complete; the evidence gate remains pending until at least 100
  resolved calls across 30 sessions. See the
  [Milestone-8 checkpoint](accuracy-prospective-shadow-m8-checkpoint.md) and
  [frozen protocol](accuracy-prospective-shadow-m8-protocol.md).
- [x] Implement Milestone 9's independent evidence-integrity and stress observer without
  changing the frozen M8 cohort. It hash-chains prediction/outcome events, verifies every
  pinned artifact and source watchlist, retains zero-call sessions in availability,
  computes session/week clustered uncertainty when sample sizes permit, and reprices
  mature calls at doubled slippage. The dashboard exposes every result while live
  authority remains false. Current performance is unavailable because no post-activation
  calls exist; see the
  [Milestone-9 checkpoint](accuracy-prospective-integrity-m9-checkpoint.md).

For each completed item, attach its artifact/run identifier, date, sample size and
pass/fail result. Update this checklist at checkpoints. The 70–80% objective remains
unachieved until the relevant evidence and session criteria pass.

## Parallel crypto accuracy program

Crypto now has an independent seven-checkpoint program, C0–C6. C0 freezes the
source-separated forward and historical baselines without pooling either with NSE/BSE or
with each other. Subsequent checkpoints cover point-in-time labels and economics,
anticipatory/quick-profit mechanisms, precision selection, locked evaluation, prospective
shadow evidence, and integrity/drift review. See the
[crypto accuracy plan](plan-crypto-accuracy.md) and
[C0 checkpoint](crypto-accuracy-c0-checkpoint.md). Crypto cannot borrow qualification from
the NSE M1–M10 sequence, and NSE cannot borrow crypto evidence.
