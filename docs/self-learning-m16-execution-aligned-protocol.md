# Self-learning Milestone 16 — frozen execution-aligned selector protocol

Protocol frozen: 2026-10-09, before any M16 model was fitted, any M16 threshold was
selected, or any M16 prospective prediction was created.

Status: preregistered research protocol. It cannot authorize a signal, alert, order,
active-model change or management action.

## Objective

M15 showed that causal close-time features can rank instruments that later print strong
highs, but that label did not translate into target-before-stop accuracy or positive net R.
M16 changes the learning target rather than retuning M15 against its exposed test.

The experiment asks whether a two-stage market-specific selector can:

1. identify a small causal pool with short-horizon upside potential;
2. distinguish tradeable paths from paths that stop or time out;
3. abstain when no candidate has enough estimated tradeability; and
4. reach at least 70% strict success on a fresh forward cohort after costs.

The 80% rate is a reported stretch level, not a threshold that may be manufactured by
silently reducing the sample after outcomes are known.

## Evidence separation

- NSE, BSE and crypto are trained, registered and evaluated independently. Pooling is
  forbidden.
- The latest 180 mature historical sessions are development evidence only. They overlap
  M15's consumed window and can never prove improvement.
- M15's final 36 sessions are named `consumed_diagnostic`, never `locked_test`.
- Only predictions persisted after M16 registration and before their next market bar may
  enter the prospective denominator.
- Missing, corrupt, late-written or contract-mismatched evidence is invalid—not a win and
  never a pass.

## Frozen historical data and split

- Inputs: the 18 `leader-causal-features-v1` values available at the decision close.
- Stage-one target: M14's `three-session-max-high-top5-v1` opportunity label.
- Stage-two targets: the M15 executable replay's strict target-before-stop outcome and
  after-cost net R.
- Replay: next-session open plus buy slippage; risk distance is the larger of 0.75
  decision-close ATR or 2% of entry for equities / 4% for crypto; stop `-1R`; target
  `+0.75R`; maximum three sessions; gap-aware; stop wins an ambiguous bar; third-close
  timeout; sell slippage; existing market-specific delivery costs; ₹250 risk from
  ₹100,000 research capital; whole equity and configured fractional crypto quantity.
- Invalid historical replay paths count as failures with `0R`; they are not silently
  discarded.

For exactly 180 ordered mature sessions:

- training: first 102 sessions;
- purge: next 3 sessions;
- validation and policy selection: next 36 sessions;
- purge: next 3 sessions;
- consumed diagnostic: final 36 sessions.

## Frozen two-stage algorithm

Missing feature values are median-imputed using the fitting block only.

Stage one is the fixed M15 bounded opportunity forest:

- 200 trees;
- maximum depth 4;
- minimum 25 rows per leaf;
- square-root feature sampling;
- balanced-subsample class weights;
- deterministic seed 16 for the new fit.

Within each session, stage one supplies an opportunity probability and its percentile
rank. Its candidate pool contains the highest-scored
`min(20, max(3, ceil(1% * liquid universe rows)))` instruments.

Stage two has two fixed components fitted on the training block:

- tradeability classifier: bounded random forest, 300 trees, maximum depth 5, minimum 25
  rows per leaf, square-root feature sampling, balanced-subsample class weights, seed 17;
- after-cost net-R regressor: bounded random forest, 300 trees, maximum depth 5, minimum
  25 rows per leaf, square-root feature sampling, seed 18.

Both consume the 18 causal features plus stage-one opportunity probability and within-
session opportunity percentile. No future price, label, entry price or replay field is an
input.

## Frozen no-call policy selection

The selector considers only the stage-one candidate pool and requires predicted net R to
be above zero. It tests exactly these classifier thresholds on validation:

`0.50, 0.55, 0.60, 0.65, 0.70, 0.75, 0.80`.

For each threshold, at most the highest-probability candidate is selected per session.
Sessions with no eligible candidate are explicit no-call sessions. A threshold is eligible
only with at least 12 selections across 12 validation sessions.

The frozen winner order is:

1. higher strict accuracy;
2. higher mean after-cost net R;
3. more selected calls;
4. higher probability threshold.

The consumed diagnostic block cannot change the model, candidate-pool rule, threshold or
winner order.

## Historical development controls and registration gate

Report the selected policy against:

- forced stage-one top-one on identical sessions;
- simple 20-session momentum top-one;
- 2,000 deterministic matched-random selections from the same session universes.

A prospective observation cohort is registered only when every development sanity gate
passes:

- at least 12 validation selections;
- validation strict accuracy at least 55%;
- validation mean net R above zero;
- validation accuracy at least 10 percentage points above forced stage-one top-one;
- validation mean-net-R advantage at least `+0.10R` over forced stage-one top-one;
- at least 12 consumed-diagnostic selections;
- consumed-diagnostic strict accuracy at least 50%;
- consumed-diagnostic mean net R above zero;
- consumed-diagnostic matched-random net-R tail probability at most 0.10;
- validation/diagnostic strict-accuracy gap at most 20 percentage points.

Passing these gates does not prove improvement. It only says that freezing the selector
for new observations is worth the collection time. Failure registers
`development_rejected` and creates no prospective predictions.

## Fresh prospective cohort

After a development pass, final stage-one and stage-two models are refitted on all 180
mature sessions. Their bundle, data digest, protocol digest and selected policy are sealed.
The cohort begins strictly after the latest closed session known at registration.

Each prediction must be hash-chained and append-only and must include market, decision
session, instrument identity, causal-feature digest, model digest, scores, threshold and
contract version. Resolution occurs only after three later market sessions exist and uses
the frozen replay unchanged.

Progress checkpoints:

- early descriptive read: 20 resolved selections across at least 15 sessions;
- qualification read: 60 resolved selections across at least 40 sessions.

The qualification cohort passes only if every gate holds:

- at least 60 resolved selections and 40 distinct sessions;
- strict accuracy at least 70%;
- 95% Wilson lower bound for strict accuracy at least 55%;
- mean after-cost net R above zero;
- accuracy and net-R matched-random tail probabilities both at most 0.05;
- maximum losing streak no greater than six;
- no prediction-integrity, model-binding, market-mixing or timing violation.

Report progress toward 80% separately. Never shorten, backfill, relabel or cherry-pick the
cohort to reach either percentage.

Even a prospective pass authorizes human review only. Promotion remains an explicit,
separate decision through the existing promotion controls.

## Required artifacts and dashboard

- Content-addressed execution-aligned development dataset.
- Frozen split, training diagnostics, threshold grid and development controls.
- Hash-bound final model bundle when the registration gate passes.
- Immutable market-specific registration record.
- Hash-chained prediction and resolution ledgers.
- Prospective scorecard with Wilson interval, random controls and exact blockers.
- Fail-closed Learning-dashboard panel that separates development from prospective
  evidence and always displays live authority as `NONE`.
