# Crypto accuracy improvement program

Status: **C0–C2 engineering implemented; C1 history and C2 evidence collecting; no
accuracy improvement established; research-only**

Market: **CoinDCX INR crypto pairs only**
Evidence rule: **crypto is never pooled with NSE or BSE; historical backfill is never
pooled with forward agent runs**

The objective is the same accuracy-first objective used elsewhere: work toward at least
80% observed success with a 70% Wilson lower bound, without manufacturing accuracy by
shrinking targets until expectancy becomes negative. A candidate must also retain positive
after-cost expectancy and beat matched controls by at least +0.10R. Indian VDA tax is
reported separately because its no-loss-offset treatment can reverse the practical result.

## C0 — freeze the independent baseline and protocol

- [x] Separate forward agent-evaluated calls from historical backfill.
- [x] Publish accuracy, Wilson lower bound, sample, sessions and gross expectancy.
- [x] Pin source hashes and assert that no NSE/BSE evidence is present.
- [x] Display the checkpoint independently on the crypto dashboard.
- [x] Keep live, alert, sizing, management and order authority false.

## C1 — point-in-time dataset and label integrity

- [x] Freeze all existing live crypto calls and exclude them from the new prospective
  same-coin timing control.
- [x] Register later calls before the first controllable entry; keep every setup's evidence
  independent and apply 1,000 frozen timing cohorts.
- [x] Use only fully closed candles for outcomes and hash resolved source windows.
- [x] Apply crypto slippage, fee, GST and TDS to paired net-R timing comparisons.
- [x] Materialize all supported active INR pairs with delistings and missingness explicit.
- [x] Extend the closed-candle and mutation guarantees to the full materialized all-pair
  research dataset.
- [x] Recompute outcomes for registered quick-profit geometries with gap and same-bar
  ambiguity rules frozen.
- [x] Attach fee/slippage economics and reporting-only VDA after-tax economics per trade.

C1 engineering is complete and its latest corrected all-pair freeze passed source
verification: 339 pairs, 355,617 raw closed rows, 354,741 canonical daily rows after
removing 876 duplicate IST-day fillers, 7,058 explicit absent sessions and 673 invalid
OHLCV rows. Exact observations now cover two point-in-time sessions per required pair;
0 of 337 required pairs meet the 30-session gate. Of 2,022 geometry rows, 1,014 are
excluded and 1,008 pending; none is resolved. Accuracy and expectancy remain
unavailable—not zero and not a pass. Completing C1 does not improve the baseline; it
creates the auditable input needed to test C2.

## C2 — anticipatory mechanism and quick-profit geometry race

- [x] Freeze four crypto-specific mechanisms: seven-session cross-sectional momentum,
  14/3-session pullback continuation, liquid 5-versus-prior-15-session true-range
  compression and seven-session BTC-relative strength.
- [x] Bind each mechanism to the three immutable C1 geometries: exactly 12 trials, with no
  extra threshold, mechanism, model or geometry available after outcomes are inspected.
- [x] Pair every trial with inverse, 1,000 within-session shuffle, 1,000 random-coin and
  1,000 same-coin random-timing controls under seed `20261007`.
- [x] Freeze the 200-coin/90%-coverage cross-section floors, family-wise max-statistic
  correction, three-fold stability requirement and full accuracy/economic gate.
- [x] Suppress partial results and represent immature accuracy, expectancy and control
  evidence as unavailable—never zero or pass.
- [ ] Evaluate all 12 trials once every trial/control is mature; reject and stop this
  version if none passes, or authorize only C3 research if one qualifies.
- [x] Refresh C1 and then C2 automatically after the established crypto tracker cadence; skip C2
  on any C1 integrity failure and keep tracker collection durable on refresh failure.
- [x] Run collection through one strict ordered crypto pipeline and publish hash-bound,
  freshness-limited collector health; operational completion is never a performance pass.
- [x] Latch the first complete C2 decision in an atomically published immutable envelope
  verified against the exact run, artifacts, C1 sources and complete 12-trial denominator.
- [x] Run the independent R3 quick-profit geometry recovery over the historical tracker,
  with backfill-only selection, purged chronological folds and a separate live
  retrospective cohort. All 64 registered trials were rejected; baseline and live
  authority remain false.

C2 implementation is complete, but **0 of 12 trials are evaluated** because current C1
evidence still has only two point-in-time sessions per required pair and no mature geometry
labels. No predictive percentage exists yet. A complete C2 trial must have at least 100
resolved calls over 30 active sessions, 80% observed accuracy, a 70% Wilson lower bound,
positive net R, at least +0.10R and superior accuracy/net R versus every control, a
family-wise corrected p-value no greater than 0.05, and favorable direction in all three
chronological folds. Even a pass remains research-only with baseline and live eligibility
false.

C3 remains blocked until C2 produces one verified terminal result. A completed collector
run, a mutable state file or partial trial maturity cannot authorize C3.

## C3 — precision selector

- [ ] Compare a transparent score, calibrated logistic model and one bounded tree model.
- [ ] Fit, calibrate and choose thresholds inside purged chronological folds only.
- [ ] Publish top-1/top-2/top-3 accuracy-versus-availability curves.
- [ ] Nominate at most one frozen crypto candidate.

## C4 — locked historical evaluation

- [ ] Open the untouched crypto tail once, with no post-result retuning.
- [ ] Require observed accuracy, Wilson bound, session consistency, positive after-cost
  expectancy, stress survival and at least +0.10R versus matched controls.
- [ ] Report coin, liquidity, volatility and BTC-regime concentration.

## C5 — prospective crypto shadow

- [ ] Save each prediction before its earliest entry and append outcomes only after maturity.
- [ ] Preserve zero-call sessions and every rejected candidate.
- [ ] Require at least 100 resolved calls over at least 30 active sessions.
- [ ] Keep the candidate shadow-only throughout collection.

## C6 — independent integrity, drift and promotion review

- [ ] Hash-chain prediction and outcome events and verify source/model identities.
- [ ] Bootstrap uncertainty by crypto session/week and stress doubled slippage.
- [ ] Monitor accuracy, calibration, availability, expectancy and universe drift.
- [ ] Permit review—not automatic promotion—only if every frozen gate passes.

Checkpoint count: **7, C0 through C6**. The NSE program continues separately and neither
market can borrow the other's wins, sample size, confidence, random advantage or promotion
status.

Evidence: [C0 baseline](crypto-accuracy-c0-checkpoint.md),
[C1 timing checkpoint](crypto-accuracy-timing-c1-checkpoint.md),
[C1 timing protocol](crypto-accuracy-timing-c1-protocol.md),
[C1 dataset checkpoint](crypto-accuracy-dataset-c1-checkpoint.md),
[C1 dataset protocol](crypto-accuracy-dataset-c1-protocol.md),
[C2 mechanism checkpoint](crypto-accuracy-mechanisms-c2-checkpoint.md), and
[C2 mechanism protocol](crypto-accuracy-mechanisms-c2-protocol.md),
[R3 recovery checkpoint](crypto-accuracy-recovery-r3-checkpoint.md), and
[R3 recovery protocol](crypto-accuracy-recovery-r3-protocol.md).
