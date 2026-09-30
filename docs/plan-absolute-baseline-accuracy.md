# Absolute baseline-accuracy improvement plan

Created: 30 September 2026

Status: **governing plan; implementation not started; no accuracy improvement claimed**

## 1. Objective and promise boundary

This is the definitive program for moving the trading agent from its current frozen
same-session baseline toward reliable, selective calls:

- current canonical result: **149/693 strict wins = 21.50%**;
- current Wilson 95% lower bound: **18.60%**;
- current mean result after modeled costs: **-0.27471R per resolved fill**;
- first credible research target: **50% strict success with positive economics**; and
- final product target: **80% observed strict success with a 70% Wilson lower bound,
  useful session coverage, and positive stressed economics on fresh prospective calls**.

“Absolute” means the process, evidence requirements, ownership, stop rules, and next
actions are explicit. It cannot mean a guaranteed accuracy outcome. No model, agent,
or plan can promise a future win rate. If the evidence does not support a qualifying
call, the correct output remains `NO_TRADE`.

The primary target is accuracy, but accuracy never passes alone. Every promoted result
must simultaneously demonstrate:

```text
strict target-before-stop success
+ positive return after costs
+ useful call and session availability
+ stable chronological and regime behavior
+ realistic execution and portfolio behavior
+ fresh, immutable evidence
```

## 2. The measurement contract cannot move

### 2.1 Track A — direct AEM baseline improvement

Track A keeps the frozen AEM v1 contract unchanged:

- same eligible opportunity population;
- 0.8% target, 0.6% stop, and 90-minute maximum hold;
- identical next-bar/limit fill, chase, gap, ambiguity, cost, and deadline logic; and
- strict success only when the declared target precedes stop/deadline and the trade is
  positive after costs.

A filter, ranker, or model on this population may be called an **AEM baseline
improvement** only if it beats 21.50% under every gate in this plan.

### 2.2 Track B — new quick-profit strategy contracts

MCB, event-driven momentum, sector-leader continuation, or a different target/stop
geometry is a new strategy contract. It must establish:

1. its own unfiltered same-contract baseline;
2. matched random timing and stock-selection controls;
3. selected-call improvement over its own baseline; and
4. comparison with the canonical agent scorecard.

A smaller target may increase target-hit frequency, but it is never relabeled as an
AEM v1 improvement. It can become the new agent champion only through locked and
prospective evidence with positive after-cost economics.

### 2.3 Fixed reporting vocabulary

Every run reports, without exception:

- opportunities, calls issued, fills, unfilled calls, unresolved outcomes;
- strict wins and filled failures/timeouts;
- pooled strict rate and Wilson interval;
- mean/median net R, total net return, profit factor, drawdown, costs and slippage;
- active sessions, zero-call sessions, calls per active session;
- active sessions reaching at least 70% and 80%;
- results by fold, month, regime, sector, symbol, liquidity and time of day;
- longest loss sequence and worst rolling session block;
- matched controls, stress results and concentration; and
- every failed gate, including missing evidence as `NOT_AVAILABLE`, never `PASS`.

With at most three fills per session, 70–80% within a session normally means every
fill in that session won. A zero-call session is availability information, not a
successful session.

## 3. Evidence already consumed

The history through the current 120-session cohort is development evidence. It may
diagnose or kill a new mechanism; it cannot certify a new baseline again.

| Track | Best honest observation | Decision |
|---|---|---|
| AEM v1 | 149/693 = 21.50%, -0.27471R | Canonical failing comparator |
| AEM information filters | Locked 13/100 = 13.00%, -0.46624R | Rejected |
| AEM v2, 24 pipelines | No specification passed; selected economics negative | Rejected |
| Opening Sweep-Reclaim | Selectors abstained; best unfiltered OOS 25.71%, -0.433R | Rejected |
| Same-Slot Micro-Momentum | Adjacent-slot placebo stronger; economic effect tiny | Stopped before model |
| Index-price alignment | Best 72/278 = 25.90%, -0.19418R | Stopped before selector |
| Daily quick-exit search | Best profitable rate 50.13%, every net result negative | Rejected |
| MCB prototype | 26 daily candidates, zero executable triggers in the small diagnostic | Data/population insufficient |

Consequences:

- do not tune any rejected threshold, geometry, or holdout;
- do not run a more complex model on a mechanism that failed its stop gate;
- do not treat the visually successful app examples as labels;
- do not present 25.90%, 37.74%, or 50.13% as a new canonical baseline; and
- do not use the same 120 sessions as “independent” evidence after changing code.

## 4. Accuracy qualification ladder

Progress is earned one level at a time. A later level cannot compensate for a failed
earlier level.

### Level 0 — data and outcome integrity

Required:

- complete coordinate accounting with explicit exclusions;
- feature `available_at` no later than decision time;
- immutable predictions and separately appended outcomes;
- deterministic target/stop ordering, actual-fill geometry, costs and deadlines;
- point-in-time universe and membership; and
- reproducible source, code, contract, configuration and artifact hashes.

### Level 1 — mechanism present

Before a model race, a registered causal mechanism must:

- beat its inverse and temporal/matched placebos;
- show the same effect direction in every chronological development fold;
- beat the 95th percentile of at least 256 matched shuffles, with a max-statistic
  shuffle control or Holm-adjusted one-sided empirical p no greater than 0.05 across
  the complete registered mechanism budget;
- show a positive top-versus-bottom or accepted-versus-rejected net-R spread; and
- be large enough to survive modeled costs, not merely statistically nonzero.

### Level 2 — credible improvement over 21.50%

On locked historical OOS evidence:

- at least 100 resolved selected fills;
- at least 30 active sessions and 40% active-session coverage;
- at least **26.50%** observed strict success, a minimum +5 percentage-point lift;
- Wilson lower bound above the canonical 18.60% lower bound;
- positive paired session-block-bootstrap lower bound for the accuracy delta;
- positive mean net R and at least +0.10R versus matched random timing;
- positive worst registered execution-stress mean net R; and
- no single symbol over 20% or sector over 35% of strict wins.

Passing Level 2 permits a research challenger. It does not create the requested 50%
baseline or authorize production.

### Level 3 — 50% research baseline

On a separately locked cohort:

- at least 100 resolved fills and 30 active sessions;
- at least 40% active-session coverage;
- observed strict success at least **50%**;
- Wilson lower bound at least **40%** and above the canonical comparator;
- positive base, 1.25x-cost, 1.5x-cost, doubled-slippage, delayed-entry and adverse
  missed-fill economics;
- positive portfolio-constrained net return and acceptable drawdown; and
- positive fold/month/regime direction without one lucky cluster.

### Level 4 — prospective 70–80% qualification

Freeze the candidate before collection. At the registered review point require:

- at least 100 prospective resolved calls across at least 30 active sessions;
- observed strict success at least **80%**;
- two-sided 95% Wilson lower bound at least **70%**;
- at least 70% of active sessions meeting the session target;
- at least 40% eligible-session coverage and no hidden zero-call sessions;
- positive mean/total net return and positive registered stress economics; and
- session/week-block uncertainty, concentration, calibration and drift gates passed.

This is a promotion review, not automatic live activation. If Level 4 fails, retain the
result and reject or redesign; never lower the gate after seeing it.

## 5. Governing research rules

- [ ] Create one append-only trial ledger covering every historical and future trial.
- [ ] Freeze hypothesis, population, features, outcomes, folds, controls, trial budget,
  seed, ranking and gates before real outcomes are joined.
- [ ] Assign every dataset row an evidence class: consumed development, locked
  historical OOS, prospective shadow, paper, or production.
- [ ] Use session-grouped chronological folds, purge overlapping outcome windows and
  embargo adjacent periods.
- [ ] Fit preprocessing, imputation, feature selection, calibration and thresholds
  inside each training fold.
- [ ] Record zero-selection models as abstentions and failures of availability.
- [ ] Use a maximum of three new signal families per research cycle, twelve mechanism
  variants in total, and six model pipelines per surviving family.
- [ ] Apply family-wise error control across the complete registered mechanism and
  model budgets; an isolated unadjusted p-value cannot nominate a candidate.
- [ ] Expand the trial budget only for a materially new information source and a new
  frozen protocol.
- [ ] Keep all losing calls, exclusions, unresolved outcomes and failed experiments.
- [ ] Never use model probability, model agreement, training accuracy, or dashboard
  availability as evidence of observed accuracy.

The trial ledger is not optional. Backtest selection can overfit even when a holdout is
used; the program therefore preserves all trials and uses CPCV/PBO and deflated metrics
as supplementary diagnostics, while reserving fresh chronological evidence as the
actual promotion proof.

## 6. Data program: new information before new complexity

The OHLCV-only search has repeatedly failed. The next program must add information
that was absent or incomplete rather than generating more indicators from the same
bars.

### 6.1 Evidence partitions

| Partition | Use | Promotion authority |
|---|---|---|
| D0: existing 120 sessions through 18 Sep 2026 | Mechanism discovery and failure analysis only | None |
| D1: newly acquired older/wider history | Development and nested walk-forward | None |
| D2: later sessions never opened during D1 work | One locked historical OOS test | Level 2/3 only |
| D3: predictions saved after final freeze | Prospective shadow/paper evaluation | Level 4 |

Reconstructed old bars can increase development sample size but do not become
prospective evidence.

### 6.2 Universe expansion

The scanner continues to inspect all locally known NSE cash `EQ` stocks, but calls are
limited to historically eligible cohorts:

1. Cohort A: top 50 point-in-time liquid stocks for engine and integrity work.
2. Cohort B: top 200 across registered liquidity and sector buckets.
3. Cohort C: 500 or more liquid stocks after coverage and execution audits pass.
4. Whole NSE: screen every supported stock; issue calls only for explicit eligibility.

Every cohort uses membership and liquidity known before its evaluation period. New
listings, suspensions, surveillance, circuits, insufficient history, gaps, illiquidity,
and unsupported symbols receive visible exclusion reasons.

### 6.3 Mandatory data layers

- [ ] Stock M1/M5/M15/D1 OHLCV with exact timestamps, adjustment state and gap ledger.
- [ ] NIFTY, BANK NIFTY and available sector-index intraday history.
- [ ] Point-in-time symbol master, listing status, sector/industry and index membership.
- [ ] Causal universe breadth and candidate-excluded peer aggregates.
- [ ] Corporate actions and official announcement dissemination timestamps.
- [ ] Actual or broker-verified intraday charge schedule and contract-note golden tests.
- [ ] Quote/spread/depth or a documented conservative proxy by price, liquidity and
  time of day.
- [ ] Prediction-time data freshness and source-version metadata.

### 6.4 Prioritized new information sources

1. **Point-in-time sector and cross-sectional context** — stock-versus-sector strength,
   sector breadth, sector/index alignment and leader/laggard state.
2. **Timestamped corporate information** — announcement type and dissemination time,
   followed only by post-publication price/volume response; no hindsight text labels.
3. **Execution microstructure** — spread, depth, turnover, volume participation,
   opening-auction behavior and realized slippage.
4. **Derivatives risk context** — futures basis, India VIX and options measures only if
   timestamped historical availability is auditable.

If a source cannot be obtained with reliable historical timestamps, it is excluded
rather than approximated with later data.

## 7. Signal-family program

### Family 1 — broader Momentum Compression Breakout

This is first because it most closely matches the supplied successful-call examples
and remains incompletely tested, not because its edge is assumed.

- Reuse and extend the existing `mcb_contract`, detector, features, labels and dataset.
- Build point-in-time daily/60m trend, prior momentum, compression, volume contraction,
  breakout proximity, trend age, remaining range and liquidity eligibility.
- Compare direct break, close confirmation and later retest as three separate frozen
  modes; events must occur in causal order.
- Evaluate the complete negative-event population, including WATCH, invalidated,
  rejected, unfilled and late candidates.
- Require the raw mechanism/top-quality bucket to beat matched stock/time controls
  before any tree model.

Stop Family 1 if a broader population still yields inadequate triggers, no controlled
mechanism, or negative cost-adjusted top-bucket economics.

### Family 2 — event-conditioned post-disclosure momentum

This adds information not present in the failed price-only tracks.

- Use official dissemination time, subject category and post-publication reaction.
- Predict continuation only after the information is public and after an executable
  confirmation; never reconstruct a pre-announcement call.
- Start with deterministic event categories and abnormal price/volume response.
- Add text embeddings or language models only after the timestamped deterministic
  baseline passes and every input document is versioned.

Stop if post-event returns do not beat matched same-symbol/non-event controls after
costs or if historical announcement completeness cannot be demonstrated.

### Family 3 — point-in-time sector-leader continuation

This is not another AEM filter. Opportunities originate from the cross-section:

- rank candidate-excluded stock returns within historical sector/liquidity cohorts;
- require sector breadth and sector-versus-market confirmation;
- enter only on a separate pullback/re-acceleration or opening confirmation; and
- test inverse leader/laggard and shuffled-sector controls.

Stop if point-in-time membership is unavailable, the effect disappears against
candidate-excluded placebos, or the gross edge is smaller than costs.

### Microstructure is a gate, not a fourth pattern

Spread/depth/participation information should reject untradeable calls and improve
fill realism. It must not be credited with predictive edge merely because it removes
small illiquid losers. Report predictive lift and execution-quality lift separately.

## 8. Model and learning path

Models rank an already defined causal event; they do not search raw bars for an
unbounded strategy.

### Stage 1 — transparent baselines

- empirical success/net-R buckets with shrinkage;
- a frozen additive quality score;
- regularized logistic regression; and
- an unfiltered and matched-random comparator.

### Stage 2 — one nonlinear challenger

Use one constrained tree family—histogram gradient boosting, LightGBM, XGBoost, or
CatBoost according to the compatible runtime—not all of them as an unbounded search.
Limit depth/leaves and tune only inside earlier-only inner folds.

### Stage 3 — expected move and downside

Predict separately:

- strict target-before-stop probability;
- MFE and MAE at 15, 30, 60 minutes and deadline;
- net return quantiles; and
- fill/chase probability.

The final gate requires a calibrated success estimate **and** enough lower-bound
expected move to clear costs and downside. A high probability with insufficient
remaining move is `NO_TRADE`.

### Stage 4 — selective policy

Publish the complete risk-versus-coverage curve for top-one, top-two and top-three
per-session policies. Select the operating point inside training data subject to the
sample, coverage and economic floors; do not assume a predicted probability of 0.50
is universally correct. Calibration is assessed separately from ranking.

### Deferred models

No neural network, reinforcement learner, large ensemble or language-model trade
decision is authorized until a simpler model passes Level 2 and at least 10,000
resolved causal events support the added degrees of freedom. A complex model must
beat the linear/tree baseline on a later locked cohort, not merely fit development
data better.

## 9. Evaluation and control matrix

Every surviving signal/model is evaluated against:

| Control | Required comparison |
|---|---|
| Unfiltered signal | Does selection add value to its own event generator? |
| Existing AEM v1 | Does agent-level accuracy/economics exceed the canonical scorecard? |
| Random time | Same stock/session/call count, randomized eligible entry time |
| Random stock | Same session/liquidity/sector bucket, randomized eligible stock |
| Inverse mechanism | Opposite registered directional state |
| Temporal placebo | Adjacent or shifted predecision state |
| Shuffled labels/ranks | Same grouped population and selection count |
| Simple model | Does complexity add locked OOS value? |
| Execution stresses | Does edge survive costs, slippage, delay and missed fills? |
| Portfolio replay | Does event-level edge survive capital/risk/concentration limits? |

Uncertainty uses Wilson intervals for proportions, paired session/week block bootstrap
for deltas and economics, and explicit regime/symbol concentration. PBO and deflated
performance statistics disclose research-selection risk but never substitute for D3
prospective evidence.

## 10. Implementation checkpoints

### Checkpoint 0 — accuracy-program contract

- [x] Freeze the two scoreboards, qualification ladder and evidence classes in code.
- [x] Create the append-only trial ledger and trial-budget enforcement.
- [x] Mark all existing periods/runs as consumed development evidence.
- [x] Add artifact identity and “missing evidence never passes” tests.

Completed 30 September 2026 on `accuracy-program-v1`: governance-only audit passed,
with ten prior research tracks and 916 registered trials marked as consumed
development evidence. No new outcomes were opened, no model was trained, and the
canonical baseline remains 21.50%.

Gate: protocol/code/tests committed before new outcome analysis.

### Checkpoint 1 — data feasibility and point-in-time manifest

- [ ] Inventory obtainable dates/symbols for all mandatory layers.
- [ ] Freeze Cohorts A/B, sessions, membership source, gaps and request budget.
- [ ] Validate announcement timestamps, sector membership and execution data.
- [ ] Publish coverage by symbol/session/layer before building a signal.

Gate: no silent survivorship, timestamp, adjustment, gap or source-version failure.

### Checkpoint 2 — causal event populations

- [ ] Build the broader MCB population first.
- [ ] Build event-conditioned and sector-leader populations only when their data gates
  pass.
- [ ] Preserve every eligible/rejected coordinate and feature-only artifacts before
  joining outcomes.
- [ ] Prove future-bar and candidate-inclusion invariance with mutation tests.

Gate: complete population accounting and no outcomes in feature artifacts.

### Checkpoint 3 — mechanism race

- [ ] Run no more than twelve frozen mechanism variants across the three families.
- [ ] Apply inverse, random-stock/time, temporal and shuffled controls.
- [ ] Stop each family immediately on its registered failure rule.
- [ ] Authorize models for at most two surviving families.

Gate: Level 1 passed; otherwise stop and collect genuinely new information.

### Checkpoint 4 — bounded model and expected-move race

- [ ] Compare additive score, logistic and one tree challenger.
- [ ] Add MFE/MAE/net-return quantiles and fill-quality estimates.
- [ ] Fit/calibrate/select thresholds fold-locally and publish risk-coverage curves.
- [ ] Nominate at most one candidate per family.

Gate: positive nested-walk-forward economics and stable predictive lift.

### Checkpoint 5 — locked historical OOS

- [ ] Freeze candidate code, data, features, model, thresholds and costs.
- [ ] Open D2 once.
- [ ] Run Level 2 and Level 3 gates, stresses and portfolio replay.
- [ ] Reject without retuning if any mandatory gate fails.

Gate: one candidate at most may enter prospective shadow collection.

### Checkpoint 6 — prospective shadow and paper evidence

- [ ] Save predictions before the earliest entry and append outcomes after maturity.
- [ ] Reconcile actual availability, missed fills, latency, spread and slippage.
- [ ] Review only at preregistered sample/session checkpoints.
- [ ] Apply Level 4 without changing the frozen candidate.

Gate: fresh 100-call/30-active-session evidence meets every accuracy, availability,
economic, stress and uncertainty requirement.

### Checkpoint 7 — NSE expansion and independent reproduction

- [ ] Repeat the locked candidate over Cohorts B/C without changing it.
- [ ] Report supported, excluded and unsupported stocks across the whole NSE universe.
- [ ] Require sector/liquidity/regime stability and independent scorecard reproduction.
- [ ] Reject extrapolation from the 50-stock pilot if broader cohorts fail.

Gate: robust broader-universe evidence, not merely more candidate rows.

### Checkpoint 8 — read-only dashboard and promotion review

- [ ] Add dashboard evidence only after a real frozen candidate exists.
- [ ] Show evidence class, denominator, Wilson bound, session coverage, net R, trial
  count, drift, missingness and every failed gate.
- [ ] Preserve all existing dashboard and management behavior.
- [ ] Keep execution alert/manual-approval only unless separately authorized.

No production scanner, call, risk, management, broker, alert or order code changes in
Checkpoints 0–7.

## 11. Files and test ownership

Reuse existing modules instead of starting another framework. Expected research-only
additions/extensions are:

```text
tradedesk_lab/accuracy_program_contract.py
tradedesk_lab/accuracy_trial_ledger.py
tradedesk_lab/accuracy_data_manifest.py
tradedesk_lab/mcb_contract.py          # extend, do not duplicate
tradedesk_lab/mcb_detector.py          # extend, do not duplicate
tradedesk_lab/mcb_dataset.py           # extend, do not duplicate
tradedesk_lab/mcb_features.py          # extend, do not duplicate
tradedesk_lab/mcb_labels.py            # extend, do not duplicate
tradedesk_lab/event_context.py
tradedesk_lab/point_in_time_sector.py
tradedesk_lab/execution_quality.py
tradedesk_lab/expected_move.py
tradedesk_lab/accuracy_race.py
tradedesk_lab/prospective_accuracy.py
```

Each module gets focused synthetic, causality, failure, identity and real-fixture tests
under `tests_lab`. Required cross-cutting tests include:

- feature/result invariance when future bars are mutated;
- target/stop ambiguity, gaps, chase, deadlines and invalid geometry;
- candidate exclusion from every peer statistic;
- point-in-time membership and announcement availability;
- missing/late data fail-closed behavior;
- fold chronology, purge, embargo and fold-local fitting;
- exact trial-budget and seed enforcement;
- zero-call availability failure;
- artifact hashes and rerun determinism; and
- old production/dashboard behavior unchanged.

## 12. Delivery order and realistic duration

Engineering duration depends on data access; evidence duration depends on qualifying
calls and cannot be compressed by more compute.

| Work | Indicative engineering effort | Result |
|---|---:|---|
| Checkpoint 0 | 1–2 working days | Frozen governance and trial ledger |
| Checkpoint 1 | 2–7 days plus acquisition | Point-in-time data feasibility verdict |
| Checkpoint 2 | 4–10 days per viable family | Complete causal event population |
| Checkpoints 3–4 | 4–8 days after data | Mechanism/model pass-or-stop result |
| Checkpoint 5 | 1–3 days | One-time locked historical verdict |
| Checkpoint 6 | Roughly 7–20 trading weeks at 1–3 fills/session | First 100-call prospective review |
| Checkpoints 7–8 | Only after Level 4 | Broader NSE and promotion review |

These are work estimates, not dates by which 50% or 80% will be achieved.

## 13. Definition of done

The baseline-accuracy goal is complete only when one frozen candidate:

1. passes Levels 0–3 on locked evidence;
2. passes Level 4 on prospective calls;
3. remains positive after costs and all registered execution stresses;
4. demonstrates useful active-session and broader-NSE coverage;
5. survives portfolio, concentration, regime and drift checks;
6. is independently reproduced from immutable artifacts; and
7. is explicitly approved for read-only dashboard exposure and any later promotion.

Until then, the canonical baseline remains 21.50%, every higher subset percentage is
diagnostic, and the system remains research/shadow only.

## 14. Immediate next checkpoint

Implement **Checkpoint 0 only** on a new branch:

1. encode `accuracy-program-v1` with the two scoreboards and Levels 0–4;
2. create the append-only trial ledger seeded with every completed AEM, exit-search,
   OSR, SSM and index-context trial;
3. mark all existing evidence as consumed development;
4. add tests proving gates cannot be weakened and unavailable evidence cannot pass;
5. publish a data-feasibility specification for Checkpoint 1; and
6. commit/push/merge the checkpoint before reading outcomes for any new signal.

Do not train a model, retune an old strategy, modify the dashboard, or alter production
behavior in the same checkpoint.

## 15. Sources and evidence anchors

Local evidence:

- `docs/aem-staged-baseline-checkpoint.md`
- `docs/aem-baseline-gates-checkpoint.md`
- `docs/aem-information-quality-checkpoint.md`
- `docs/aem-v2-pipeline-race-checkpoint.md`
- `docs/osr-selector-race-checkpoint.md`
- `docs/ssm-mechanism-checkpoint.md`
- `docs/aem-index-context-mechanism-result-checkpoint.md`
- `docs/daily-mean-reversion-exit-search-checkpoint.md`
- `docs/trading-agent-master-roadmap.md`

Methodology/data references:

- [The Probability of Backtest Overfitting](https://papers.ssrn.com/sol3/Papers.cfm?abstract_id=2326253)
  motivates explicit trial accounting and overfitting diagnostics; it does not prove an
  edge.
- [NSE corporate announcements](https://www.nseindia.com/companies-listing/corporate-filings-announcements)
  expose exchange dissemination timestamps suitable for a point-in-time feasibility
  audit; their presence does not imply a tradable signal.
- [NSE data-sharing inventory](https://nsearchives.nseindia.com/web/sites/default/files/inline-files/Data%20list%20under%20NSE%20Data%20Sharing%20Policy%20for%20Research%20and%20Analysis_20250728.pdf)
  identifies official corporate, historical contract and tick/order data categories;
  availability, licensing and cost must be verified before implementation.
