# Crypto accuracy C2 — anticipatory mechanism protocol

Protocol: `crypto-accuracy-mechanisms-v1`

Status: **frozen and implemented; evidence collecting; research-only**

C2 tests whether a small, preregistered set of causal crypto mechanisms can select
more reliable next-open calls than matched controls. It consumes C1's point-in-time
all-pair dataset and its three frozen quick-profit labels. It does not change the live
scanner, calls, dashboard authority, sizing, management or orders.

## Evidence boundary and causal population

- Only a C1 dataset that passes source and artifact verification may be consumed. The
  C1 dataset ID, source hash, universe-event hash, configuration hash and label contract
  are pinned in C2 evidence.
- A decision row must be C1 `point_in_time_eligible`. A current universe cannot be
  projected backward, and stored candles do not prove historical membership.
- A registered coin's older candles may supply its own causal lookback when every candle
  was closed by the decision time. Those candles cannot add that coin—or any other
  coin—to a historical cross-section whose membership was not observed at that time.
- Lookbacks must be contiguous and valid. Missing or invalid source bars produce an
  explicit unavailable feature; they are never filled, skipped or shortened.
- Every cross-section requires at least 200 feature-valid registered coins and at least
  90% coverage of the active registered nonexcluded population for that session. If
  either floor fails, the whole mechanism/session coordinate is unavailable.
- All prices, volume, ranks, BTC context and universe state must have been available by
  the decision close. Outcomes, exits, labels, MFE, MAE and future candles are forbidden
  from the feature artifact.
- Cross-sectional ties use the frozen deterministic coin-ID ordering. Missing features
  never receive a best, worst or neutral rank.

Crypto evidence remains independent of NSE and BSE. C1 historical/development rows,
forward tracker calls and later prospective predictions also remain separate evidence
classes.

## Four fixed mechanisms

The four mechanisms below are the complete C2 search budget. Percentile selections are
made independently within each eligible session.

### 1. Cross-sectional momentum

- Feature: seven-session close return, `close[t] / close[t-7] - 1`.
- Mechanism selection: highest 20% of valid registered coins.
- Inverse: lowest 20% from the identical session population.

### 2. Pullback continuation

- Long component: cross-sectional percentile rank of the 14-session close return.
- Pullback component: inverse cross-sectional percentile rank of the three-session close
  return.
- Score: the arithmetic mean of those two percentile ranks.
- Eligibility: the raw 14-session return must be positive and the raw three-session
  return must be non-positive.
- Mechanism selection: highest 20% of eligible scores. The inverse is the lowest 20%
  from that identical qualifying session pool.

### 3. Liquidity/volatility compression

- Liquidity: median 20-session INR turnover, where daily turnover is close multiplied by
  volume. Only the highest-turnover half of the valid session population is eligible.
- True range is computed causally from the current high/low and prior close.
- Compression: mean true range over the latest five sessions divided by mean true range
  over the preceding 15 sessions. All 20 true-range measurements—and the causal prior
  close they require—must come from one contiguous valid run.
- Mechanism selection: lowest 20% of compression ratios inside the liquid half.
- Inverse: highest 20% of ratios inside that same liquid population.

### 4. BTC-relative strength

- Feature: the coin's seven-session close return minus the contemporaneous seven-session
  `CDX_BTCINR` return.
- Mechanism selection: highest 20% of valid non-BTC registered coins.
- Inverse: lowest 20% from the identical non-BTC population.
- BTC is context, not a candidate. A missing or invalid causal BTC lookback makes this
  mechanism unavailable for that session.

No threshold, lookback or selection fraction may be changed after inspecting C2
outcomes. A materially different definition requires a new protocol version and counts
as a new trial family, not a repair of this result.

## Twelve frozen trials

Each mechanism is evaluated independently on each of C1's three immutable geometries:

| Geometry | Stop | Target | Maximum hold |
|---|---:|---:|---:|
| `quick_075atr_050r_3d` | 0.75 ATR | 0.50R | 3 sessions |
| `quick_100atr_075r_5d` | 1.00 ATR | 0.75R | 5 sessions |
| `quick_125atr_100r_7d` | 1.25 ATR | 1.00R | 7 sessions |

The registered budget is therefore exactly **four mechanisms × three geometries = 12
trials**. These are twelve trials, not 36 mechanism variants. C2 may not add another
geometry, mechanism, model or threshold to this run. Results are reported per trial and
are never pooled to manufacture a larger sample or a better percentage.

## Frozen controls

Every trial receives all four controls under the identical C1 geometry, costs, sizing
and outcome rules:

1. the registered inverse mechanism;
2. 1,000 within-session shuffles that preserve the session population and selected
   count;
3. 1,000 random-coin cohorts drawn from the same eligible session population with the
   same selected count; and
4. 1,000 same-coin random-timing cohorts using frozen alternative-session assignments
   inside the development window and no outcome-dependent resampling.

The fixed random seed is `20261007`. Assignments are deterministic and pinned before
their outcomes are joined. Missing assigned bars, inactive membership, invalid ATR,
incomplete outcome windows and other source failures are explicit exclusions; timing
controls cannot jump silently to the next available candle.

One family-wise max-statistic correction is applied across all 12 registered trials.
An isolated uncorrected success cannot nominate a mechanism. Control evidence remains
paired by trial; controls, mechanisms and geometries are not pooled.

## Maturity and the no-peeking rule

C2 freezes the first 30 eligible point-in-time decision sessions as its development
window. It publishes coverage and collection progress while C1 grows, but suppresses every
accuracy, Wilson, expectancy, advantage and p-value result until **all 12 trials** and
their required paired controls are mature. Partial results cannot be used to stop early,
choose a geometry or alter a mechanism.

Before maturity, every unavailable metric is represented as unavailable with a null
value. It is never displayed or interpreted as zero, failure, success or pass. A
right-censored label is pending evidence, not a losing call.

## Frozen pass gate

A trial passes only if every condition below is true:

- at least 100 resolved selected calls;
- at least 30 active selected sessions;
- observed success is at least 80%;
- the two-sided 95% Wilson lower bound is at least 70%;
- mean after-cost net R is positive;
- mean after-cost net R exceeds **each** inverse, shuffled, random-coin and random-timing
  control by at least +0.10R;
- both accuracy and mean net R exceed each corresponding control;
- the family-wise max-statistic-corrected one-sided empirical p-value is at most 0.05;
  and
- accuracy and net-R control deltas have the favorable direction in each of three
  chronological folds.

One failed or unavailable gate means that trial does not pass. C2 passes only when at
least one of the 12 frozen trials passes every gate after the single complete evaluation.
If none passes, the result is rejected and these definitions are stopped rather than
retuned.

## Status and authority

The only terminal/evidence states are:

- `collecting_c1_point_in_time_history` while the 30-session window is incomplete;
- `collecting_c1_resolved_labels` while that window's geometry outcomes are immature;
- `mechanism_race_rejected` after a complete evaluation with no qualifying trial; or
- `mechanism_race_passed_research_only` after a complete evaluation with at least one
  qualifier.

Even `mechanism_race_passed_research_only` sets `baseline_improved=false` and
`eligible_for_live=false`. It authorizes only C3's bounded precision-selector research.
It does not change a published baseline, issue a call or grant alert, risk, management,
broker or order authority.
