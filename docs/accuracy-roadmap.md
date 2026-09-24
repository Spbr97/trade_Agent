# Accuracy-first checkbox roadmap

Updated: 24 September 2026. Working branch: `codex/nse-anticipatory-validation`.

Primary objective: maintain and improve the reliability of executable NSE calls,
working toward 70–80% successful calls within sessions. Prioritize anticipatory
entries and smaller, quicker profits. Call volume is flexible, but useful availability
must be demonstrated alongside accuracy and returns after costs.

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
- [ ] Demonstrate positive portfolio returns after realistic costs and execution.
- [ ] Demonstrate the requested accuracy, session consistency and call availability
  on fresh evidence.

Evidence: [collection checkpoint](aem-history-checkpoint.md),
[corrected baseline](nse-aem-validation-checkpoint.md),
[random comparison](aem-random-policy-checkpoint.md).

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
- [ ] Verify feature availability times, adjustments, benchmark dates, trading
  status and data-source versions. Preserve source and experiment fingerprints.
- [ ] Restore the five SciPy-dependent test modules in an isolated compatible
  research environment while preserving system security and production dependencies.
  Current runnable verification is **903 passed, two skipped and one deselected**.
  Five modules remain collection-blocked by SciPy `_dop`; one tuning test is blocked
  by scikit-learn `_sgd_fast`. Windows Application Control was not weakened.

Acceptance: a reproducible, auditable evaluation dataset with no unexplained gaps in
consumed signal/label windows. Exceptions cannot silently change the frozen cohort
or remove losing opportunities. Acquisition progress is measured separately from
strategy performance.

## 3. Establish a broader, executable baseline

- [x] Replay the unchanged AEM contract on the frozen 50-stock cohort. Keep the
  current 0.8% target, 0.6% stop and 90-minute maximum hold fixed for this comparison.
- [ ] Re-run matched random policies with the same candles, fills, deadlines and
  costs. Add a control that evaluates stock selection as well as conditional timing.
- [ ] Replay simultaneous calls under existing position, entry, sector, sizing and
  portfolio-heat limits. Report executable portfolio results alongside event results.
- [ ] Verify intraday costs against suitable broker evidence and stress spreads,
  doubled slippage, one-bar delay, missed fills and gap exits at registered settings.
- [ ] Classify errors: weak follow-through, late entry, exhausted move, market/sector
  opposition, inadequate liquidity, bad fill or data failure. Retain every failure.

Acceptance: a complete baseline scorecard and a documented explanation of where
errors arise. The 50-stock pilot cannot establish reliability across all NSE stocks.

## 4. Improve the information used to select calls

- [ ] Register a bounded hypothesis list and trial budget; start from the prior
  plan's maximum of 24 pipeline specifications per track and log all nested trials.
- [ ] Test market/sector alignment, relative strength, same-time volume, opening
  structure, volatility, trend age and remaining room for the target using only
  information available when the signal would have been issued.
- [ ] Test anticipatory impulse versus confirmed pullback/retest entry, distance
  from VWAP and resistance, and execution quality. Version each changed entry policy.
- [ ] Test quick-profit exit alternatives as separate contracts with identical
  cost/risk reporting. Judge average win/loss, expectancy and drawdown with accuracy.
- [ ] Run one-component-at-a-time comparisons and retain only improvements that
  survive later periods and multiple stocks. App success examples remain hypotheses.

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
- [ ] Register the first bounded accuracy-improvement experiments from its failure analysis.

For each completed item, attach its artifact/run identifier, date, sample size and
pass/fail result. Update this checklist at checkpoints. The 70–80% objective remains
unachieved until the relevant evidence and session criteria pass.
