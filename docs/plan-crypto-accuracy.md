# Crypto accuracy improvement program

Status: **C0 implemented; C1 prospective timing foundation activated; research-only**

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
- [ ] Materialize all supported active INR pairs with delistings and missingness explicit.
- [ ] Extend the closed-candle and mutation guarantees to the full materialized all-pair
  research dataset.
- [ ] Recompute outcomes for registered quick-profit geometries with gap and same-bar
  ambiguity rules frozen.
- [ ] Attach fee/slippage economics and reporting-only VDA after-tax economics per trade.

## C2 — anticipatory mechanism and quick-profit geometry race

- [ ] Test a bounded set of crypto-specific mechanisms: cross-sectional momentum,
  pullback continuation, liquidity/volatility compression and BTC-relative strength.
- [ ] Pair every mechanism with inverse, shuffled, random-coin and random-timing controls.
- [ ] Search only preregistered stop/target/holding geometries that can support higher
  accuracy while remaining positive after costs.
- [ ] Stop rejected families instead of repeatedly retuning them.

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
[C1 timing checkpoint](crypto-accuracy-timing-c1-checkpoint.md), and
[C1 timing protocol](crypto-accuracy-timing-c1-protocol.md).
