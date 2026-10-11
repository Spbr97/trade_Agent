# Self-learning Milestone 19 — frozen pre-takeoff tradeability protocol

Protocol frozen: 2026-10-11, before any M19 model was fitted, any M19 operating point
was selected or any M19 internal-diagnostic score was opened.

Status: preregistered historical experiment. It cannot authorize an alert, personal call,
order, active-model change or baseline-improvement claim.

## Objective

M14–M16 established that the old setup grammar misses most later leaders, while a
full-universe prior-close ranker can find some later leaders but cannot reliably turn them
into executable trades. M17 then sealed next-session M1 paths, and M18-A established that
NSE and crypto have enough causal positive and negative examples for one selection-first
experiment.

M19 tests one end-to-end question: can information available by the prior session close
select a useful number of next-session, target-before-stop trades from the broad-market
pre-takeoff candidate stream with materially higher accuracy and positive after-cost R?

This is deliberately not another setup filter. The upstream M17 population was ranked
from the complete causal liquid universe and includes primary discovery, momentum and
matched-random candidates. Candidate role and upstream ranker score are provenance only;
they are forbidden model inputs.

## Frozen markets and evidence

- NSE and crypto are evaluated independently. Pooling is forbidden.
- BSE is unavailable because M18-A failed its 85% path-coverage gate in every non-purge
  block. Missing paths cannot be converted into losses, wins or synthetic rows.
- Source rows are the exact content-addressed M18-A datasets and frozen chronological
  partitions.
- `development_train` fits models only.
- `calibration` calibrates probabilities and chooses exactly one model/threshold policy.
- `internal_diagnostic` is opened once after the policy is fixed and cannot change it.
- Purge rows never fit, calibrate, select or score a model.
- The internal diagnostic is consumed historical evidence. Even a pass can only nominate
  a prospective observer.

## Frozen execution target

The row target is M18-A `precision_label`: the fixed next-session-open, `+0.75R` target,
`-1R` stop contract must hit its target before stop/timeout and finish positive after all
market-specific costs and slippage. `net_r` is reported separately and is never replaced
by a probability or future-high label.

Invalid, unavailable, unresolvable and non-finite rows never enter a performance
denominator. Their coverage remains visible through M18-A.

## Frozen causal features

Only the 18 `leader-causal-features-v1` values sealed by M18-A may enter a model:

`return_1`, `return_3`, `return_5`, `return_20`, `gap_return`, `range_pct`,
`close_location`, `volume_ratio_20`, `turnover_ratio_20`, `distance_sma_20`,
`distance_sma_50`, `distance_prior_high_20`, `atr_14_pct`, `avg_turnover_20`,
`return_5_rank`, `return_20_rank`, `volume_ratio_20_rank`, and
`distance_prior_high_20_rank`.

Role, ranker score, symbol, session, outcome, path, replay event, entry, target, stop and
future values are forbidden inputs. All feature preprocessing is fitted without the
internal diagnostic.

## Frozen model families

Exactly two deterministic families are compared per market:

1. **Regularized logistic** — standard scaling, L2 penalty, `C=0.1`, balanced class
   weights, maximum 2,000 iterations, seed 19.
2. **Bounded random forest** — 300 trees, maximum depth 4, minimum 20 samples per leaf,
   square-root feature sampling, balanced-subsample class weights, seed 19 and one job.

Each base model fits only `development_train`. Sigmoid calibration then fits only the
calibration block using the already-fitted estimator. No grid search, feature search or
diagnostic refit is allowed.

## Frozen one-call policy selection

For each calibrated model, test only probability thresholds
`0.50, 0.55, 0.60, 0.65, 0.70, 0.75, 0.80, 0.85`. At each threshold, choose at most the
highest-probability row per session. A session without an eligible row is an explicit
no-call session.

A calibration policy is eligible only if it produces at least 8 calls in at least 8 of
the 12 calibration sessions and has positive mean after-cost R. Choose among eligible
policies in this fixed order:

1. higher strict accuracy;
2. higher mean after-cost R;
3. more distinct call sessions;
4. higher probability threshold;
5. regularized logistic on an exact tie.

If no policy is eligible, the market is `development_rejected` and its internal
diagnostic remains unopened.

## Frozen controls and scorecard

Report the selected policy alongside:

- the unfiltered M18-A candidate population;
- forced model top-one in every identical session;
- simple `return_20_rank` top-one in every identical session;
- M17 upstream-primary top-one where present; and
- 2,000 deterministic matched-random one-per-session policies from the same rows.

For every policy report calls, distinct sessions, no-call rate, strict wins, strict
accuracy, 95% Wilson interval, mean/median net R, total net R and maximum losing streak.
Matched-random output also reports accuracy and mean-net-R tail probabilities.

## Frozen historical nomination gates

An opened internal diagnostic is `historical_candidate` only if every gate passes:

- at least 8 selected calls across at least 8 distinct diagnostic sessions;
- strict accuracy at least 60%;
- 95% Wilson lower bound at least 30%;
- mean after-cost net R above zero;
- strict-accuracy advantage of at least 10 percentage points over forced model top-one;
- mean-net-R advantage of at least `+0.10R` over forced model top-one;
- matched-random accuracy and net-R tail probabilities both at most 0.10;
- calibration-to-diagnostic accuracy gap no greater than 20 percentage points; and
- no market mixing, timing, data-binding or denominator-integrity violation.

Failure is `development_rejected`, never a partial pass. A historical candidate does not
change the canonical 21.50% baseline. It may only seal a prospective observer using the
already selected market-specific policy.

## Prospective requirement

A nominated policy must collect at least 60 resolved calls across at least 40 new
sessions. Qualification requires at least 70% strict accuracy, 55% Wilson lower bound,
positive mean after-cost R, both matched-random tail probabilities at most 0.05, maximum
losing streak no greater than six and zero integrity violations. Progress toward 80% is
reported separately. Promotion still requires explicit human approval.

## Stop rule

If neither NSE nor crypto becomes a historical candidate, do not retune these models or
thresholds on the exposed diagnostic. The next attempt must change the information set by
collecting causal M5/M15 pre-break snapshots (range contraction, relative-volume change,
VWAP position and early acceleration) before it spends another locked evaluation block.

