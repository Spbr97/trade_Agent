# OSR Milestone 3: leakage-safe selector race

Date: 29 September 2026

Branch: `codex/osr-m3-selector-race`

Status: **all 12 frozen specifications evaluated; no selector issued a qualifying
out-of-sample call; OSR rejected; no baseline, live, dashboard, or management change**

## Outcome

OSR does not improve the accuracy baseline. Both registered selector families
abstained under the frozen 50% absolute probability threshold in every top-one,
top-two, and top-three reporting policy. No candidate advanced to stress replay.

The strongest unfiltered out-of-sample slice was the gap-down reclaim with the
`reclaim_40_28_35` geometry: 135 strict successes from 525 resolved fills (**25.71%**),
a **22.16%** Wilson 95% lower bound, and **-0.433R** mean net R after modeled costs.
Every unfiltered mode/geometry slice had negative expectancy. This is not a near miss;
lowering the call threshold after seeing the result would violate the frozen protocol.

The canonical AEM v1 result therefore remains **149/693 = 21.50% strict success**,
**18.60% Wilson lower bound**, and **-0.27471R** mean net R. This checkpoint does not
claim that the baseline is profitable or accurate enough—it only records that OSR did
not beat the registered gates.

## Accuracy-critical population correction

The Milestone-2 `events.csv` contained resolved fills only. Using it directly for
training would condition the selector on future knowledge: whether a decision-time
call eventually filled and resolved. Before fitting either selector, this checkpoint
created a leakage-safe selection population that restores every later outcome:

- 8,091 total calls from 2,697 opportunities across 120 sessions and 49 stocks;
- 7,303 resolved, 720 unfilled, and 68 unresolved calls;
- all unfilled and unresolved calls retained as non-successes;
- exactly 30 finite decision-time features on every row;
- zero duplicate call identities and zero missing/non-finite feature cells; and
- source event, exclusion, contract, implementation, population, and report hashes
  frozen in machine-readable evidence.

Where one geometry resolved and another did not, the same opportunity's feature vector
was reused. Features were reconstructed from frozen source candles only for 256
opportunities absent from the resolved table, under the same source fingerprint,
universe audit, strategy contract, and causality guards.

The leakage-safe all-call prevalence is 1,564/8,091 (**19.33%**). The familiar
1,564/7,303 (**21.42%**) figure is only the resolved-fill rate and is not an honest
denominator for a decision-time call selector.

## Frozen race

Exactly 12 specifications were evaluated:

- two entry modes: gap-down reclaim and opening-low sweep reclaim;
- three preregistered quick-profit geometries;
- two deliberately different selector families: a transparent shrunk additive model
  and a regularized depth-one boosted-tree model; and
- top-one, top-two, and top-three per-session policies reported from each fitted
  specification without counting those views as extra trials.

Each selector used nested chronological walk-forward evaluation. Outer tests were
purged with a ten-session embargo; inner calibration used earlier-only folds with a
two-session embargo. Preprocessing, model fitting, Platt calibration, absolute
thresholding, ranking, and symbol deduplication were fold-local. Two of three outer
folds were scoreable for each specification; the earliest fold failed closed because
it lacked the frozen minimum selector-training population.

The absolute call threshold remained 0.50. Neither selector family produced a
calibrated out-of-sample probability at or above that level, so every top-k policy
selected zero calls. Consequently all policies failed the minimum calls, active
sessions, coverage, observed accuracy, Wilson confidence, economics, matched-random,
and fold-stability gates. Zero calls are reported as abstention—not as a pass or a
successful session.

## Implementation and verification

`tradedesk_lab/osr_selection_population.py` builds and freezes the complete
decision-time population. `tradedesk_lab/osr_selectors.py` contains both fixed selector
families and earlier-only calibration. `tradedesk_lab/osr_selector_race.py` registers
the bounded race, performs nested chronological evaluation, reports availability and
session consistency, constructs 200 matched-random cohorts with identical per-session
call counts, and checks every frozen gate.

The final full OSR suite passes 54 tests. It covers causality,
conservative outcomes, population completeness, model determinism, fold chronology,
threshold-before-ranking behavior, symbol deduplication, matched-random call counts,
all gate failures, exact trial-budget enforcement, and the rule that a development
race can never change the canonical baseline automatically.

Machine-readable evidence is in
[`docs/evidence/osr-selector-race.json`](evidence/osr-selector-race.json). Selection
population run `8bee2b7ede9b4d1bb28c6958d5ad646d` and race run
`d4ec1201a3394548bd4299d8f17647e4` are both fingerprinted there.

## Safety boundary and stop decision

Only the isolated `tradedesk_lab` research package, its tests, and research
documentation changed. Production scanning, call generation, risk, management,
broker, alerts, and dashboard code remain untouched. Because no candidate qualified,
there is no honest performance object to expose in the dashboard and no stress replay
to run.

OSR stops here. The accuracy-first next step is a preregistered, genuinely different
causal signal hypothesis—not post-result OSR threshold or geometry tuning on the same
consumed history.
