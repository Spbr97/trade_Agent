# Accuracy-first checkbox roadmap

Updated: 26 September 2026. Working branch: `codex/aem-v2-accuracy-first`.

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
This checklist updates the execution sequence in the earlier
[master roadmap](trading-agent-master-roadmap.md) and
[reliability plan](reliability-70-80-plan.md); their historical status sections remain
dated records. Proposed future production changes in those documents remain deferred.

## Current position

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
- [ ] Demonstrate positive portfolio returns after realistic costs and execution.
- [ ] Demonstrate the requested accuracy, session consistency and call availability
  on fresh evidence.

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
or performance result.

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
- [ ] Collect and verify the 135,000 planned index M1 bars outside the protected
  market window. Current coverage is **0/360 index-sessions**; the attempted run was
  blocked before any request, so this is not an accuracy improvement.
- [ ] Obtain point-in-time historical sector membership, or preregister a narrower
  context hypothesis that cannot leak current membership into historical evaluation.
- [ ] Verify feature availability times, adjustments, benchmark dates, trading
  status and data-source versions. Preserve source and experiment fingerprints.
- [ ] Restore the five SciPy-dependent test modules in an isolated compatible
  research environment while preserving system security and production dependencies.
  Current runnable verification is **921 passed**, with two pre-existing skips
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
- [ ] Add point-in-time intraday benchmark and sector context, then freeze a new
  bounded trial set for later unconsumed sessions or prospective shadow evidence.
  The collection plan and safe collector are complete, but data coverage is 0/360
  index-sessions and historical point-in-time sector membership remains unresolved;
  this acquisition track is currently parked.
- [x] Freeze the AEM v2 custom-algorithm contract, feature registry, entry modes,
  geometry grid, trial budget and evidence gates with immutable fingerprints.
- [ ] Build Milestone 1: the causal event and outcome engine with conservative fills,
  costs, deadlines and target/stop ordering before training a selector.

For each completed item, attach its artifact/run identifier, date, sample size and
pass/fail result. Update this checklist at checkpoints. The 70–80% objective remains
unachieved until the relevant evidence and session criteria pass.
