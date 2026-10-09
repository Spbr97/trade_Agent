# Self-learning Milestone 15 — frozen leader-separability protocol

Protocol frozen: 2026-10-09, before any M15 model was fitted or any validation/test result
was opened.

Status: preregistered research protocol. It does not authorize a signal, active-model
change, personal call, order, or management action.

## Objective

Test whether the causal decision-close features created by M14 contain repeatable
information about the next three sessions' strongest liquid instruments. The experiment
must distinguish three questions:

1. Can a model rank hindsight opportunity leaders better than simple momentum and random
   selection?
2. Does the result survive a chronological locked block that model selection never sees?
3. Can a selected instrument satisfy a fully executable next-session quick-profit contract
   after slippage and market-specific costs?

A “yes” to the first question alone is not an accuracy improvement. Future-high labels are
diagnostic. Only the executable replay addresses trade outcomes, and historical replay is
still development evidence rather than prospective qualification.

## Frozen data contract

- Markets: NSE, BSE and crypto evaluated independently; pooling is forbidden.
- Source: `leader-causal-features-v1` / `three-session-max-high-top5-v1` extraction code.
- Window: latest 180 mature sessions available when the experiment is first registered.
- Universe and leader thresholds: exactly M14's frozen market-specific contract.
- Inputs: the 18 M14 causal features only.
- Target: M14 `opportunity_label` only.
- Missing values: median imputation fitted on the training block only.
- Test outcomes may be opened exactly once per market/source-dataset digest and are then
  permanently marked consumed in the experiment registry.

## Frozen chronological split

For exactly 180 ordered mature sessions:

- training: first 102 sessions;
- purge: next 3 sessions;
- validation: next 36 sessions;
- purge: next 3 sessions;
- locked test: final 36 sessions.

The three-session purges match the opportunity horizon so no label path crosses from a
fitted/selected block into the next evaluation block. A market with fewer than 180 mature
sessions is unavailable—not a pass.

## Frozen contenders

No hyperparameter search is allowed.

1. **Regularized logistic**
   - standardization fitted on training only;
   - L2 penalty, `C=0.1`;
   - balanced class weights;
   - maximum 2,000 iterations;
   - deterministic seed 15.

2. **Bounded random forest**
   - 200 trees;
   - maximum depth 4;
   - minimum 25 samples per leaf;
   - square-root feature sampling;
   - balanced-subsample class weights;
   - deterministic seed 15.

The winner is selected using validation only, in this order:

1. top-one opportunity precision;
2. top-three opportunity precision;
3. lower row-level Brier score;
4. logistic wins an exact tie because it is simpler.

The locked test cannot change the winning model, features, or policies.

## Frozen selection policies and controls

Primary policy: select the highest-scored instrument in every test session (`top_1`).

Secondary policy: select the three highest-scored instruments in every test session
(`top_3`).

Controls:

- simple momentum: same policies ranked only by `return_20_rank`;
- matched random selection: 2,000 deterministic repetitions, sampling one or three from
  every identical session universe without replacement;
- existing-setup coverage: descriptive only and only where a tracker-observed session
  exists; it cannot substitute for a missing model selection.

Report opportunity precision, leader recall, lift over same-session prevalence, Brier
score, ROC-AUC when both classes exist, calls per session, and no-call frequency. The
primary policy always emits one research selection per evaluable session, so its frozen
no-call frequency is zero; this is a stress test of ranking, not live recommendation
authority.

## Frozen executable quick-profit replay

The replay uses only selected locked-test instruments and actual later daily bars:

- enter at the next session open plus the configured buy-side slippage;
- risk distance is the larger of 0.75 decision-close ATR and a floor of 2% of entry for
  NSE/BSE or 4% for crypto;
- stop is one risk distance below entry;
- target is `+0.75R` above entry;
- maximum holding period is three sessions;
- a gap through stop or target exits at that session's open;
- when stop and target occur in the same bar, stop wins;
- unresolved positions exit at the third session close;
- sell-side slippage is applied to every exit;
- position risk budget is ₹250 from ₹100,000 research capital; equities use whole shares
  and crypto uses its configured fractional step;
- every replay is charged through the existing market-specific cost model using the
  conservative delivery contract.

Strict success means target before stop/timeout. Timeout gains are not successes. Report
strict accuracy, mean net R, median net R, maximum losing streak, target/stop/timeout counts,
and the same 2,000 matched-random replay distribution.

## Frozen research-candidate gates

M15 may be labelled `development_candidate` only if the locked test clears every gate:

- 36 primary selections across 36 distinct locked sessions;
- top-one opportunity precision at least 20%;
- top-one lift over random prevalence at least 3.0x;
- top-one precision at least 10 percentage points above momentum top-one;
- matched-random opportunity tail probability at most 0.05;
- top-three opportunity precision at least 15% and lift at least 2.0x;
- executable top-one strict accuracy at least 50%;
- executable top-one mean net R above zero;
- executable mean-net-R advantage over matched random at least +0.10R;
- matched-random executable tail probability at most 0.05; and
- validation and locked-test top-one precision differ by no more than 20 percentage points.

Failure of any gate means `rejected_no_separability`. Missing/corrupt evidence means
`invalid_or_unavailable`, never a pass. Even a `development_candidate` remains research
only: baseline improvement is not proven, the active model is unchanged, and a separately
registered prospective cohort is mandatory before any personal-call review.

## Required artifacts

- Market-specific compressed causal dataset and SHA-256 digest.
- Frozen split manifest and protocol digest.
- Validation scorecard for both fixed contenders.
- One locked-test scorecard for the validation-selected winner.
- Momentum and matched-random controls.
- Executable replay ledger for model and random selections.
- Saved research-model bundle and digest.
- Append-only experiment registry preventing silent locked-test reuse.
- Fail-closed dashboard panel with no live authority.
