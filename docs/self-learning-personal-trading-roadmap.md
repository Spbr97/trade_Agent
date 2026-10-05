# Self-learning personal-trading roadmap

Date created: 2026-10-06

Status: in progress; Milestones 1-5 completed on 2026-10-06

Primary objective: produce a small, useful set of personally tradeable calls with
verifiable accuracy, positive after-cost expectancy, explicit risk levels and honest
no-call sessions. Successful, failed, rejected and pending calls must remain visible so
the agent's performance and learning process can be audited.

This plan does not authorize live trading, model promotion, order placement or changes to
position management. NSE, BSE and crypto evidence must remain separate. Unavailable,
pending or invalid outcomes are never passes.

## Non-negotiable design

The system maintains two independent dimensions. Recommendation authority is exactly one
of `qualified_call`, `shadow_call` or `rejected_call`. Outcome lifecycle is exactly one
of `pending_call`, `resolved_call`, `invalid_call` or `never_triggered`. Keeping these
dimensions separate means, for example, that a shadow call can remain pending and later
become resolved without ever being presented as a qualified recommendation.

The system maintains three distinct streams:

1. **Qualified personal calls** passed every evidence, market, liquidity and risk gate.
2. **Shadow and rejected calls** were evaluated but are not recommendations to trade.
3. **Invalid, never-triggered and pending records** remain visible but never train a model
   until they have a valid mature outcome.

Self-learning means that mature successes and failures create versioned challenger models.
It does not mean that the active model silently retrains or changes trading authority.

## Milestone 1 — Evidence categories and outcome contracts

- [x] Define `qualified_call`: passed every accuracy, liquidity, regime and risk gate.
- [x] Define `shadow_call`: evaluated but not recommended for trading.
- [x] Define `rejected_call`: failed one or more declared gates.
- [x] Define `pending_call`: valid prediction whose outcome is not mature.
- [x] Define `invalid_call`: corrupted data, impossible geometry or unavailable instrument.
- [x] Define `never_triggered`: entry conditions did not occur before expiry.
- [x] Separate quick-profit calls from swing calls with distinct versioned contracts.
- [x] Freeze exact entry, stop, target, expiry and tie-breaking rules for each contract.
- [x] Keep NSE, BSE and crypto evidence physically and logically separate.

Acceptance criteria:

- Every record has exactly one evidence class and one outcome lifecycle state.
- Pending, unavailable and invalid records cannot count as successes or failures.
- No pre-existing call or result is deleted.
- Quick-profit and swing accuracy are never pooled. They remain inactive until the
  deterministic resolver implements their frozen rules.

Implementation record: [Milestone 1 evidence contracts](self-learning-m1-evidence-contracts.md)

## Milestone 2 — Immutable prediction ledger

Record every evaluated candidate before its outcome is knowable:

- [x] Unique signal ID and immutable creation timestamp.
- [x] Market, symbol, setup and evidence class.
- [x] Entry range, stop, targets, expiry and intended holding period.
- [x] Versioned contract classification; current calls honestly remain `legacy` until the
  quick-profit/swing resolver is activated.
- [x] Grade, rule score and calibrated model probability.
- [x] Market regime, sector identity and explicit unavailable sector-regime state.
- [x] Relative strength, volume, volatility and liquidity feature context.
- [x] Estimated slippage, fees and tax assumptions.
- [x] Every rejection reason.
- [x] Model, strategy and feature-contract versions.
- [x] Source-data snapshot and contract hashes.

Acceptance criteria:

- The prediction payload cannot be rewritten after an outcome becomes knowable.
- Repeated scans do not duplicate a signal.
- Resolution appends outcome fields without replacing original prediction fields.
- Original confidence and rejection reasons remain reproducible and auditable.

Implementation record: [Milestone 2 immutable prediction ledger](self-learning-m2-immutable-ledger.md)

## Milestone 3 — Deterministic outcome resolver

- [x] Resolve each call as target, stop, timeout, invalidated or never triggered.
- [x] Record which price event occurred first.
- [x] Use an explicit conservative rule when target and stop occur in the same bar.
- [x] Calculate gross R and realized holding time.
- [x] Calculate net R after fees and slippage.
- [x] Calculate reporting-only after-tax R where a frozen tax contract exists (crypto);
  NSE/BSE remain explicitly unavailable rather than using a guessed personal-tax profile.
- [x] Record maximum favourable excursion and maximum adverse excursion.
- [x] Record time to entry and time to resolution.
- [x] Resolve quick-profit and swing contracts independently.
- [x] Stop resolution when required observed candles are missing; immature future windows
  remain pending rather than inferred.
- [x] Prevent unresolved, invalid or corrupted records from entering training.

Scheduled resolution:

- [x] Crypto refreshes every 30 minutes through its registered collection pipeline.
- [x] NSE and BSE finalize through their market-specific collection pipelines after close;
  live-session monitoring remains separate from daily-bar finality.
- [x] Repeated tracker runs finalize a call when its maximum holding period expires.

Acceptance criteria:

- Re-running the resolver produces the same result from the same candles.
- One signal cannot receive conflicting terminal outcomes.
- Missing data produces an explicit unavailable state, never an inferred result.

Implementation record: [Milestone 3 deterministic resolver](self-learning-m3-deterministic-resolver.md)

## Milestone 4 — Failure-attribution engine

Assign one or more evidence-backed categories to each mature failed call:

- [x] False breakout from verified path excursion.
- [x] Late or overextended entry from actual-fill versus ATR distance.
- [x] Market-regime reversal availability check; explicitly unavailable until an
  outcome-time regime snapshot exists, rather than inferred from the result.
- [x] Sector weakness as a labelled diagnostic association.
- [x] Relative-strength deterioration availability check; explicitly unavailable until
  an outcome-time relative-strength snapshot exists.
- [x] Insufficient volume confirmation as a labelled diagnostic association.
- [x] Excessive volatility as a labelled diagnostic association.
- [x] Poor liquidity or excessive slippage.
- [x] Gap through stop from the verified first event.
- [x] Stop too tight for the observed path.
- [x] Target too ambitious for the observed path.
- [x] Holding period too short or too long.
- [x] Model overconfidence.
- [x] Setup-specific weakness from aggregate mature evidence.
- [x] Data-quality failure, reported separately and excluded from performance learning.

For every category calculate:

- [x] Frequency and recent trend.
- [x] Accuracy and Wilson interval.
- [x] Mean gross, net and after-tax R.
- [x] Breakdown by setup, market, sector and regime.
- [x] Breakdown by confidence band.
- [x] Recurrence across independent sessions.

Acceptance criteria:

- Failure categories use prediction-time information plus the realized price path.
- Explanations do not alter the original prediction.
- The dashboard distinguishes verified causes from diagnostic associations.

Implementation record: [Milestone 4 failure attribution](self-learning-m4-failure-attribution.md)

## Milestone 5 — Learning-dataset builder

Include:

- [x] Mature successful calls.
- [x] Mature failed calls.
- [x] Mature rejected and shadow calls as counterfactual evidence.
- [x] Original sealed prediction-time features only.
- [x] Outcome, net R, resolution time and failure categories.
- [x] Market, sector and regime context.

Exclude:

- [x] Pending calls.
- [x] Invalid calls.
- [x] Rows containing future information.
- [x] Duplicate predictions.
- [x] Backfilled rows from prospective evidence.
- [x] Rows with missing or mismatched contract hashes.

Acceptance criteria:

- Dataset generation is deterministic and versioned.
- Development, locked-test and prospective rows are explicitly labelled.
- NSE, BSE and crypto cannot be pooled accidentally.
- The same locked outcome is never reused as fresh evidence by a later challenger.

Implementation record: [Milestone 5 learning dataset](self-learning-m5-learning-dataset.md)

## Milestone 6 — Scheduled self-learning workflow

After every completed session:

- [ ] Resolve mature calls.
- [ ] Refresh strict accuracy, calibration and expectancy.
- [ ] Update failure attribution.
- [ ] Detect performance, confidence and data drift.
- [ ] Leave the active model unchanged.

Weekly, when enough new mature outcomes exist:

- [ ] Freeze a new challenger dataset snapshot.
- [ ] Retrain the transparent baseline.
- [ ] Train only preregistered challenger models.
- [ ] Compare challengers with the frozen active baseline.
- [ ] Record negative and inconclusive experiments.
- [ ] Reject improvements confined to training data.

Monthly, or after a meaningful evidence increment:

- [ ] Run locked chronological evaluation.
- [ ] Test regime, sector and session consistency.
- [ ] Run random-selection and random-timing controls.
- [ ] Decide whether one challenger deserves a prospective cohort.

Acceptance criteria:

- Learning produces versioned challengers, never silent active-model mutation.
- Failed experiments remain in the registry and are not unknowingly repeated.
- No challenger receives trade authority automatically.

## Milestone 7 — Precision selector

Candidate generation can remain broad. A second-stage selector must reject aggressively
using only information available at decision time:

- [ ] Market regime.
- [ ] Sector regime.
- [ ] Relative strength.
- [ ] Volume confirmation.
- [ ] Liquidity and expected slippage.
- [ ] Volatility range.
- [ ] Distance from support, resistance and recent highs.
- [ ] Gap risk.
- [ ] Expected remaining movement after costs.
- [ ] Calibrated model probability.
- [ ] Similar historical failure patterns.

Evaluate operating points separately:

- [ ] Top one call per session.
- [ ] Top three calls per session.
- [ ] Top five calls per session.
- [ ] Fixed calibrated-confidence thresholds.
- [ ] Explicit no-call sessions.

Acceptance criteria:

- The selector can only remove or downgrade calls.
- The selector cannot promote an already rejected call.
- Accuracy improvement persists on locked data and after costs.
- Coverage and no-call frequency are reported alongside accuracy.

## Milestone 8 — Quick-profit and swing experiments

Test exit contracts on identical frozen entry cohorts:

- [ ] 0.5R quick target.
- [ ] 0.75R quick target.
- [ ] 1R quick target.
- [ ] Partial profit followed by a breakeven stop.
- [ ] 1.5R–2R swing target.
- [ ] Trend-trailing exit.
- [ ] One-, three-, five- and ten-session expiry.

Measure:

- [ ] Strict accuracy and Wilson lower bound.
- [ ] Gross, net and reporting-only after-tax expectancy.
- [ ] Resolution speed.
- [ ] Maximum losing streak.
- [ ] Calls per session and no-call frequency.
- [ ] Performance by market, setup and regime.

Acceptance criteria:

- Exit alternatives share the same frozen entries.
- A smaller target is not accepted merely because it increases hit rate.
- Net expectancy must remain positive after realistic costs.

## Milestone 9 — Challenger validation

- [ ] Use chronological walk-forward folds.
- [ ] Purge overlapping outcomes.
- [ ] Keep every inspected period permanently marked as development.
- [ ] Reserve an untouched locked final test.
- [ ] Compare with the frozen baseline.
- [ ] Compare with matched random selection.
- [ ] Compare with matched random timing.
- [ ] Test probability calibration by confidence bucket.
- [ ] Test performance by month, sector, liquidity and regime.
- [ ] Publish negative and inconclusive results.

Evidence ladder:

- [ ] 50 resolved calls: diagnostic only.
- [ ] 100 resolved prospective calls across at least 30 sessions: first review.
- [ ] 250 resolved calls: stability review.
- [ ] 500 resolved calls with at least 100 out-of-sample: production evidence threshold.

Final evidence gates:

- [ ] At least 80% observed strict success.
- [ ] At least 70% 95%-Wilson lower bound.
- [ ] Positive after-cost expectancy.
- [ ] Positive reporting-only after-tax expectancy.
- [ ] At least +0.10R improvement over matched random controls.
- [ ] Acceptable session consistency and useful call availability.

No gate may be relaxed because the target is difficult or because the sample is small.

## Milestone 10 — Personal-trading and performance dashboard

### Qualified personal calls

Show only calls that cleared every gate:

- [ ] Symbol and market.
- [ ] Entry range, stop and expiry.
- [ ] Quick-profit and swing targets where applicable.
- [ ] Position-risk suggestion.
- [ ] Calibrated confidence.
- [ ] Evidence sample size, strict accuracy and Wilson bound.
- [ ] Reasons the call qualified.

### Full agent performance

Keep every evaluated call visible:

- [ ] Successful calls.
- [ ] Failed calls.
- [ ] Rejected and shadow calls.
- [ ] Pending calls.
- [ ] Never-triggered and invalid calls.
- [ ] Confidence versus actual outcome.
- [ ] Failure-category breakdown.
- [ ] Accuracy before and after filtering.
- [ ] Net expectancy before and after filtering.
- [ ] Model and strategy version comparison.

### Self-learning status

Show:

- [ ] New mature evidence since the last training run.
- [ ] Most frequent failure patterns.
- [ ] Current challenger and frozen baseline.
- [ ] Experiments that failed.
- [ ] Development-only improvements.
- [ ] Prospective evidence progress.
- [ ] Exact reasons promotion remains blocked.
- [ ] The next checkpoint and minimum remaining evidence.

The dashboard must be able to state `NO QUALIFIED PERSONAL CALL TODAY` while still showing
all failed, rejected and pending research calls.

## Milestone 11 — Promotion and rollback control

- [ ] Freeze candidate data, features, thresholds and execution contract.
- [ ] Register its prospective cohort before any outcome becomes known.
- [ ] Collect the required evidence without backfilling the cohort.
- [ ] Independently reproduce the scorecard.
- [ ] Produce a side-by-side comparison with the frozen baseline.
- [ ] Require explicit approval before promotion.
- [ ] Preserve the previous version for rollback.
- [ ] Continue failure and calibration monitoring after promotion.
- [ ] Automatically pause affected scoring on stale data, drift or contract mismatch.

The agent may generate and validate challengers automatically. It may not silently change
personal-trading authority.

## Implementation order

1. [x] Evidence categories and frozen outcome contracts.
2. [x] Immutable prediction ledger.
3. [x] Deterministic outcome resolver.
4. [x] Failure-attribution engine.
5. [x] Learning-dataset builder.
6. [ ] Full performance and self-learning dashboard panels.
7. [ ] Scheduled challenger-training workflow.
8. [ ] Quick-profit versus swing experiments.
9. [ ] Precision selector and no-call policy.
10. [ ] Locked historical validation.
11. [ ] Prospective shadow cohort.
12. [ ] Personal-trading qualification panel.
13. [ ] Explicit promotion and rollback review.

## Completion definition

This roadmap is complete only when:

- failed calls remain visible and measurably influence versioned challenger training;
- the dashboard separates personal calls from research without hiding either;
- one frozen market-specific candidate passes every historical, random-control,
  prospective, accuracy, uncertainty and economic gate;
- an independent reproduction agrees with its scorecard; and
- the user explicitly approves any change in trading authority.
