# Plan: AEM v2 custom algorithm for a credible 50%+ research baseline

Date: 25 September 2026

Working branch: `codex/aem-v2-accuracy-first`

Status: **design only — no strategy implementation or production change is authorized
by this document**

## 1. Objective

Build a new, transparent, accuracy-first intraday algorithm for liquid NSE cash
stocks. The first research milestone is to exceed **50% strict successful calls**
while retaining useful call availability and positive returns after realistic costs.
If that milestone survives independent evidence, later iterations may work toward the
existing 70–80% objective.

The new algorithm is provisionally named **AEM v2 Precision Ladder**. It is a separate
challenger, not an edit to AEM v1 and not a production strategy.

The word *baseline* must remain precise:

- The canonical AEM v1 baseline remains **149 strict wins from 693 resolved fills
  (21.50%)**, with a **18.60% Wilson 95% lower bound** and **−0.27471R mean net R**.
- AEM v2 reaching 50% during training does not replace that baseline.
- AEM v2 may become the new research reference only after locked chronological and
  fresh prospective evidence both pass their registered gates.
- Results from different target/stop/deadline contracts are shown side by side and
  are never presented as a direct improvement without a same-contract comparison.

No algorithm can guarantee a future success percentage. This plan defines how a 50%
claim could be earned and how failures will remain visible.

For decision gates, **50% or greater** is the initial qualification threshold. A
literal claim that the algorithm *beats* 50% requires a point estimate above 50%.
Every report must show the exact fraction and uncertainty rather than rounding a
nearby result into a pass.

## 2. Scope and invariants

The first track is same-session, long-only NSE cash research using one-minute bars.
It prioritizes the two requested behaviors:

1. identify an anticipatory move before it is exhausted; and
2. take a smaller, quicker profit whose value remains positive after costs.

The following are fixed constraints:

- Preserve production scanning, ranking, alerts, eligibility, management, risk and
  order paths.
- Keep AEM v1 artifacts and results immutable.
- Implement AEM v2 only in `tradedesk_lab` until a separate future approval.
- Keep at most three selected calls per session and report top-one, top-two and
  top-three policies separately.
- A weak session may produce `NO TRADE`; zero-call sessions remain visible and do
  not count as successful sessions.
- Deterministic code—not an LLM or agent opinion—establishes candidates, timestamps,
  fills, prices, outcomes and scorecards.
- Missing, stale, incomplete or noncausal input fails closed.
- New dependencies, paid data or more complex models require a demonstrated missing
  capability; complexity by itself is not progress.
- No live orders, production-database writes, network acquisition, safety/preflight
  bypass or automatic promotion is authorized by this plan.
- Historical qualification can only nominate a challenger. Production integration
  requires fresh evidence and explicit user approval.

The previously started index-context acquisition checkpoint is parked. Its isolated
store remains at **0/360 index-sessions**, it made zero broker requests, and AEM v2
must not pretend those inputs are available. AEM v2 may initially use causal
cross-sectional breadth from the frozen stock universe. True benchmark/sector inputs
can be added later as a separately versioned experiment.

## 3. What counts as a successful call

The primary metric is **strict success per resolved selected fill**. A success requires
all of the following:

1. The prediction and complete feature record existed before the earliest eligible
   entry.
2. The declared entry was executable under the registered fill model.
3. The declared target was reached before the stop and exit deadline.
4. Net P&L remained positive after fees, taxes, spread/slippage assumptions and other
   modeled costs.

For a long call, the actual fill must satisfy `stop < fill < target`. A gap or delayed
fill is repriced and rechecked rather than receiving the intended entry price.
Same-bar target/stop ambiguity is resolved conservatively, normally stop first unless
finer causal data establishes the order.

The following never count as strict wins:

- an unfilled candidate;
- a profitable timeout that did not reach the registered target;
- a target reached before an executable fill;
- a gross winner made negative by costs;
- an unresolved or missing-data outcome; or
- a retrospective signal reconstructed after its entry deadline.

Unfilled attempts are reported separately. Filled losses, scratches and timeouts stay
in the strict denominator. Unresolved selected calls block a final assessment.

## 4. Current evidence and why a new algorithm is required

The frozen 50-stock AEM v1 dataset contains 1,397 candidate events across 120 sessions.
It produced 815 trade decisions, 693 resolved fills and 149 strict wins. The result is
not close to 50% and loses after costs. It also records 122 unfilled attempts, zero
unresolved outcomes, 118/120 active sessions, 3.39% of active sessions reaching 70%
and 1.69% reaching 80%.

The earlier **37.74%** result came from a smaller, availability-selected ten-stock
population. The five stocks shared with the broader cohort reproduced at 19.23%, so
the higher percentage cannot be treated as the general baseline.

The first registered causal information-quality experiment tested 11 filters. Its
development-selected rule fell from a 14.34% holdout baseline to 13.00%, reduced the
Wilson lower bound and worsened mean net R. It was rejected.

A separate daily mean-reversion search reported a best 50.13% *profitable-trade
rate*, but every tested run had negative after-cost mean R. That percentage used a
different strategy, outcome and holding contract and is not proof of 50% strict AEM
success.

An informal development-only diagnostic performed before this plan combined existing
AEM feature thresholds. Its strongest training conjunction reached **23/51 = 45.1%**,
but only **5/17 = 29.4%** in the following development validation slice, with just 11
active validation sessions. This diagnostic was outcome-inspected, was not
preregistered and is **not evidence**. Its only use is to reject the hypothesis that
more aggressive combinations of the same six features are sufficient.

Therefore AEM v2 must add genuinely new information and reconstruct opportunities
from causal minute bars. It must not be another threshold search on the consumed AEM
v1 holdout.

## 5. AEM v2 Precision Ladder

### 5.1 Opportunity generator

AEM v2 starts from eligible minute-bar stock sessions rather than only filtering AEM
v1 trade decisions. It detects a developing state change early enough to retain room
for the target.

Every candidate must be generated from completed bars and assigned one entry mode:

- **Anticipatory impulse:** rising participation and price acceleration before a
  nearby level is broken, with enough remaining room.
- **Confirmed pullback:** an initial impulse followed by an orderly retracement that
  holds a causal reference such as VWAP or the opening range.
- **Breakout retest:** completed breakout, later retest that holds, then continuation;
  each event must occur in order.

Entry modes are different contracts. Their results cannot be pooled until each has a
versioned definition and the pooled policy is registered before evaluation.

### 5.2 Causal feature groups

The initial feature registry may use only information available at the decision time:

| Feature group | Initial measurements |
|---|---|
| Time and opening structure | Minutes since open, opening-range position, opening gap, time remaining |
| Impulse quality | One/three/five-bar return, acceleration, body/wick quality, range expansion |
| Compression and breakout state | Prior compression, causal level distance, attempted breaks, retest depth |
| VWAP structure | Distance, slope, reclaim/hold state and consecutive closes relative to VWAP |
| Participation | Same-time relative volume, recent volume acceleration and turnover |
| Relative strength | Stock return versus causal cross-sectional breadth and liquid-universe median |
| Remaining room | Distance to causal resistance, ATR extension and achievable move before the deadline |
| Execution quality | Liquidity, price-impact proxy, limit distance, chase, fill probability and cost burden |
| Regime | Causal breadth, dispersion, volatility and trend/range state derived from available stocks |

Raw symbol identity is not an initial predictor. It may be used for grouping, leakage
audits and subgroup reporting. Future index/sector features require their own
point-in-time data and availability proof.

Forbidden inputs include later bars, final daily values, future highs/lows, outcome
fields, exit information, post-decision volume and present-day sector membership
applied retrospectively.

### 5.3 Hard veto layer

Candidates are rejected before scoring when any registered hard constraint fails:

- incomplete, stale, duplicated or misaligned bars;
- insufficient daily history or same-time volume history;
- invalid tick, entry, stop or target geometry;
- expected move too small to clear modeled round-trip costs;
- inadequate liquidity or excessive impact proxy;
- no remaining causal price room;
- move already extended beyond the registered exhaustion limit;
- entry would require chasing beyond its maximum distance;
- decision or fill would occur after its registered deadline; or
- portfolio, position, sector or heat limits would reject the call.

The veto layer cannot inspect outcomes and cannot be relaxed after a losing result.

### 5.4 Precision Ladder score

The custom selector is deliberately interpretable:

1. Transform each registered feature using parameters fitted only on the training
   sessions of the current fold.
2. Convert feature ranges into smoothed evidence contributions. Sparse bins shrink
   toward the training base rate rather than receiving extreme scores.
3. Combine the registered contributions into an additive evidence score. Only a
   small preregistered set of economically meaningful interactions is allowed.
4. Apply reliability shrinkage for sparse regimes and correlated same-session
   candidates.
5. Calibrate the score chronologically using out-of-fold predictions only.
6. Require both an absolute qualification threshold and a per-session rank. A rank
   cannot turn an unqualified candidate into a call.
7. Deduplicate symbols and apply existing portfolio limits, then select top one, two
   or three according to the policy being reported.

The selector abstains when no candidate crosses its frozen threshold. The threshold
is chosen with an explicit call-volume floor, so selecting one lucky call cannot win
the experiment.

A regularized logistic model is the transparent control. Existing boosted-tree
families may be evaluated on exactly the same folds and features, but the Precision
Ladder remains the primary custom algorithm. A tree, ensemble or temporal model is
retained only if it supplies measured incremental performance on later data.

The first scope remains the frozen, preperiod-selected 50-stock pilot. It cannot
support a whole-NSE reliability claim. Expansion must use point-in-time universe
eligibility, listing history and preregistered sector/liquidity strata, and must report
unsupported, illiquid and unavailable stocks plus survivorship/vintage limitations.

## 6. Quick-profit contracts

Changing exit geometry can raise the hit rate while destroying expectancy. Every
geometry is therefore a separate strategy contract and must pass both accuracy and
after-cost gates.

Before outcomes are inspected, register a small grid—no more than six geometries—of
target, stop and maximum-hold combinations. The starting hypotheses should emphasize
a positive gross reward-to-risk ratio, for example:

- approximately 0.35% target, 0.30% stop and 20–30 minute maximum hold;
- approximately 0.45% target, 0.35% stop and 30–45 minute maximum hold; and
- approximately 0.60% target, 0.45% stop and 45–60 minute maximum hold.

Exact tick-rounded values and any volatility scaling must be frozen after a cost and
data-resolution audit, before the experiment. These examples are not permission to
tune exits until 50% appears.

Production currently expects `min_net_rr=2.0`, which is structurally incompatible
with the current AEM quick-profit geometry. Research must report that exception and
calculate net reward-to-risk after costs. Eventual promotion cannot silently bypass
the production gate: it requires an explicit, separately reviewed reconciliation of
accuracy, payoff ratio and risk policy.

For each geometry report strict target success, any net-profitable timeout rate,
average win, average loss, mean/median net R, expectancy, drawdown and cost share.
Targets cannot be shrunk, stops widened or deadlines extended after evaluation.

## 7. Data and evaluation protocol

### 7.1 Evidence classes

All 120 sessions in the existing frozen dataset, including its former final 40-session
holdout, are now **consumed development data**. They may support feature engineering,
debugging and cross-fitted feasibility estimates, but cannot certify AEM v2.

Evidence is labeled as one of:

1. **Development diagnostic:** reused or outcome-inspected history.
2. **Locked later historical evaluation:** dates and data not inspected during AEM v2
   design, opened once under a frozen protocol.
3. **Prospective shadow evidence:** immutable predictions saved before entries, with
   outcomes appended later.

Only classes 2 and 3 can support a new baseline claim. Prospective evidence remains
required before any production discussion.

### 7.2 Chronological fitting

- Split by complete trading sessions, never random rows.
- Use expanding or rolling nested walk-forward folds.
- Purge observations whose outcome windows overlap a validation boundary and apply a
  registered embargo.
- Fit transformations, missing-value policy, feature bins, weights, calibration and
  selection thresholds inside each training fold.
- Generate one out-of-fold prediction per development event; never score a training
  row using a model fitted on that row.
- Group uncertainty by session and week in addition to the ordinary Wilson interval.
- Keep a ledger of every complete pipeline specification and cap the initial race at
  24 specifications, including geometry/model combinations.
- Do not repeatedly open a locked period or stop at the first favorable checkpoint.

### 7.3 Required controls

Compare AEM v2 with:

- unchanged AEM v1 on its original contract;
- an unfiltered opportunity-generator baseline using the same AEM v2 geometry;
- matched random timing under the same candidate population, fills and costs;
- the regularized logistic control; and
- any tree challenger retained from the bounded model race.

When contracts differ, publish cross-contract results but use only same-contract
comparisons to attribute improvement to selection quality.

## 8. The 50% acceptance gates

### 8.1 Development qualification

A pipeline may be frozen for later evaluation only when cross-fitted development
evidence satisfies all of these:

- at least **50% strict success**;
- at least **100 resolved selected fills**;
- at least **30 active sessions**;
- no unresolved selected calls;
- at least **40% active-session coverage** over eligible evaluation sessions;
- positive mean net R after base costs;
- positive mean net R under every registered mandatory stress;
- a Wilson 95% lower bound above the comparable baseline and preferably at least 40%;
- better mean net R than matched random timing; and
- no single symbol, week or narrow regime accounts for most of the result.

This gate only permits freezing a candidate. It does not establish a 50% baseline.

### 8.2 Locked evaluation gate

The frozen candidate must then satisfy the same accuracy, sample, availability and
economic gates on later unconsumed data. It must also show:

- stability across nearby thresholds and geometry values;
- reported top-one, top-two and top-three trade-offs;
- performance by entry mode, symbol, liquidity, volatility and regime;
- conservative one-bar-delay, missed-fill and doubled-slippage results; and
- no material deterioration hidden by pooled results.

Failure rejects the candidate. Thresholds cannot be adjusted using the locked result.

### 8.3 First prospective 50% review

The first prospective research review requires at least:

- **100 resolved prospective selected fills**;
- **30 active sessions**;
- **50% observed strict success**;
- **40% Wilson 95% lower bound**;
- positive base-cost and stressed mean net R;
- at least **+0.10R** mean-net-R advantage over the matched timing control;
- no unresolved selected calls; and
- the frozen availability and concentration gates.

Passing this review may establish AEM v2 as a **50% research baseline**. It does not
make the strategy live. The existing eventual eligibility requirement of 500 resolved
trades, 100 out-of-sample trades, 80% measured success and all other risk/economic
gates remains unchanged unless the user separately approves a different production
policy.

## 9. Accuracy and availability reporting

Every experiment and dashboard view must show:

- issued candidates, selected calls, fills, unfilled attempts and unresolved calls;
- strict wins and losses with the exact denominator;
- pooled success rate and Wilson interval;
- per-session wins/fills and the percentage of active sessions reaching 50%, 70% and
  80%;
- eligible sessions, active sessions, zero-call sessions and active-session coverage;
- longest losing streak and worst session/week/month;
- gross and net R, P&L, average win/loss, expectancy and drawdown;
- profit factor, maximum favorable/adverse excursion and losing streaks;
- modeled fees, slippage, latency and missed-fill stress results;
- calibration and the complete accuracy-versus-call-volume curve;
- symbol, entry-mode, liquidity, volatility and regime breakdowns; and
- trial count, data/model/feature/contract hashes and evidence class.

With one to three fills per session, session percentages are discrete. For example,
two wins from three fills is 66.7%, not a session reaching 70%. Pooled 50% cannot be
described as 50% in every session.

## 10. Failure and kill rules

Stop and record the experiment when any of the following occurs:

- no pipeline reaches 50% while meeting the minimum sample and availability gates;
- accuracy rises only because call volume collapses below the registered floor;
- mean net R is nonpositive after base costs or any mandatory stress;
- the selected result reverses on a later chronological fold;
- nearby parameters fail sharply, indicating a narrow backtest spike;
- the advantage depends mainly on one symbol, short period or one unavailable regime;
- ambiguous bars, missing data or look-ahead affect the conclusion;
- the trial budget is exhausted; or
- a locked or prospective gate fails.

A failed result leaves AEM v1 as the canonical reference and makes no live change.
A new trial budget requires a documented new source of information, entry hypothesis
or execution hypothesis—not another search over the same consumed answers.

## 11. Implementation roadmap

### Milestone 0 — freeze this protocol

- [ ] Review and approve the 50% definition, sample floor and availability floor.
- [ ] Freeze the initial feature registry, entry modes, geometry grid and trial budget.
- [ ] Create machine-readable contract and protocol fingerprints.

### Milestone 1 — causal event and outcome engine

- [ ] Build AEM v2 opportunity reconstruction directly from isolated M1 data.
- [ ] Implement anticipatory impulse, confirmed pullback and breakout-retest event
  ordering with completed bars only.
- [ ] Implement conservative fills, costs, target/stop/deadline ordering and ambiguity
  handling for every registered quick-profit contract.
- [ ] Add failing-case tests for gaps, chase, same-bar ambiguity, missing bars,
  post-deadline signals and cost-negative target hits.

### Milestone 2 — feature and integrity layer

- [ ] Implement the causal feature registry and feature-availability timestamps.
- [ ] Add cross-sectional breadth/relative-strength inputs without using parked index
  data or current sector membership.
- [ ] Audit missingness, corporate actions, liquidity, tradability and source versions.
- [ ] Freeze the development dataset and preserve every excluded event with a reason.

### Milestone 3 — Precision Ladder selector

- [ ] Implement smoothed evidence contributions, sparse-regime shrinkage and hard
  vetoes.
- [ ] Implement fold-local calibration, absolute thresholding, per-session ranking and
  symbol deduplication.
- [ ] Add logistic control and optional existing tree challengers on identical folds.
- [ ] Prove outcome columns and future bars cannot enter prediction code.

### Milestone 4 — bounded development experiment

- [ ] Register no more than 24 complete pipeline specifications.
- [ ] Run nested chronological walk-forward evaluation on consumed development data.
- [ ] Publish the accuracy/availability/economics curve and every failed trial.
- [ ] Freeze at most one candidate only if every development gate passes.

### Milestone 5 — independent evaluation

- [ ] Acquire or wait for later unconsumed sessions without inspecting outcomes.
- [ ] Score the frozen candidate once and run matched-random, stress and portfolio
  controls.
- [ ] Independently reproduce the scorecard and issue a freeze-or-reject decision.

### Milestone 6 — dashboard and prospective shadow evidence

- [ ] Add a read-only AEM v2 panel to the research dashboard showing evidence class,
  accuracy, Wilson bound, availability, economics, trial count and gate failures.
- [ ] Save immutable prediction-time records before eligible entries.
- [ ] Append mature outcomes separately and conduct the first 100-call/30-session
  prospective review without changing the frozen model.

### Milestone 7 — broader NSE qualification

- [ ] Expand from the 50-stock pilot through preregistered liquidity and sector
  cohorts using point-in-time listing and eligibility data.
- [ ] Re-run data-integrity, matched-control, stress, portfolio and concentration
  gates without changing the frozen algorithm for each cohort.
- [ ] Report unsupported, illiquid and unavailable NSE stocks rather than silently
  excluding them or claiming whole-market coverage.
- [ ] Require independent broad-universe and prospective evidence before describing
  AEM v2 as reliable across supported NSE cash stocks.

Each milestone receives its own reviewed commit. Strategy work remains on a `codex/`
branch, tests must pass, and unrelated user files remain untouched. Merging research
infrastructure or dashboard evidence does not authorize production behavior.

## 12. Proposed file boundaries

Implementation should remain modular and research-only:

- `tradedesk_lab/aem_v2_contract.py` — immutable entry/exit and evidence contracts;
- `tradedesk_lab/aem_v2_events.py` — causal opportunity reconstruction;
- `tradedesk_lab/aem_v2_features.py` — point-in-time feature computation;
- `tradedesk_lab/aem_v2_precision_ladder.py` — custom scoring and abstention;
- `tradedesk_lab/aem_v2_experiment.py` — chronological race and artifact generation;
- `tests_lab/test_aem_v2_*.py` — contract, leakage, execution and evaluation tests; and
- `docs/evidence/aem-v2-*.json` — immutable machine-readable checkpoints.

Do not import these modules into production scanning, signal management or order code.

## 13. Definition of done

The plan is not complete because a model trains, a dashboard shows 50%, or one subset
looks promising. The 50% milestone is complete only when:

1. the data, feature, strategy, model, threshold and cost contracts are frozen;
2. an independently scored cohort has at least 100 resolved fills and 30 active
   sessions;
3. strict observed success is at least 50% with a Wilson lower bound of at least 40%;
4. availability meets the frozen floor and zero-call sessions are disclosed;
5. base and stressed after-cost expectancy are positive;
6. matched controls, portfolio limits and concentration checks pass;
7. the result is reproducible from immutable artifacts; and
8. no production behavior changed to obtain the result.

Until every condition passes, the dashboard and reports must say **50% target not yet
achieved**.
