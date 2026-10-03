# Accuracy Milestone 4 — executable cohort, trained race and full crypto universe

Completed: 3 October 2026 (IST)

## Outcome

This checkpoint removes the Milestone-3 training blockers instead of weakening them.
The historical source was rebuilt through the existing causal clean-data contract, with
after-cost outcomes reconstructed for every retained row. The transparent rule score,
regularised logistic regression and histogram gradient boosting candidates were then
evaluated on identical purged chronological folds.

The race **trained and evaluated all three candidates**. No candidate cleared all frozen
accuracy, confidence, availability and economics gates, so the correct result is
`abstain`. The 4,848-row locked tail remains unopened.

## Audited cohort

- Source opportunities: 30,038
- Retained complete rows: 22,756
- Explicitly excluded: 7,282
- Sessions: 750, from 1 September 2023 through 10 September 2026
- Causal decision-time features: 33 (`accuracy-causal-arming-v1`)
- Complete after-cost economics: 22,756/22,756 (100%)
- Complete transparent scores: 22,756/22,756 (100%)
- Label base rate: 23.48%
- Mean candidate after-cost result: -0.555R
- Cohort fingerprint: `f786d7cc40479a116b102c9f55855ec9ed31d5ad74b3cb81d1eb38ad1bb66f04`

Primary exclusions were 2,946 outside the ATR band, 1,750 unsizeable, 1,075 with
insufficient/stale warm-up history, 661 below the turnover floor, 501 with an invalid
entry-validity interval and 333 with a target at or below the saved fill. No missing
feature or missing-economics row was admitted.

## Development race result

All candidates received 13,305 out-of-fold development predictions.

| Candidate | Best sampled point | Selected | Success | Wilson lower bound | Coverage | Mean R | Decision |
|---|---:|---:|---:|---:|---:|---:|---|
| Transparent rule score | p≥0.70, top 3 | 1,198 | 20.78% | 18.58% | 92.29% | -0.602R | Fail |
| Logistic regression | p≥0.55, top 3 | 23 | 47.83% | 29.24% | 2.92% | -0.056R | Fail |
| Histogram gradient boosting | p≥0.50, top 1 | 47 | 21.28% | 11.99% | 9.79% | -0.738R | Fail |

The logistic result is directionally better than the cohort base rate but is not a new
baseline: it has only 23 calls, negative expectancy, inadequate session coverage and a
29.24% Wilson lower bound. Claiming 47.83% as a durable accuracy result would be
statistically misleading.

## Crypto universe expansion

The hard-coded ten-coin tracker was replaced by dynamic discovery from CoinDCX's public
active-INR instrument master. The run on 3 October found **339 active INR pairs**—one more
than the old 338-pair snapshot—and scanned all 339, including tradeable benchmark BTC. All
339 had the latest
fully closed crypto session with zero fetch errors. The optimized verification run needed
zero duplicate candle refreshes after the scheduled loader, found one setup candidate and
allowed zero tradeable calls under the existing evidence gates.

Monitoring breadth and trade permission remain separate. Stablecoin/peg exclusions,
liquidity, stale-data, setup, cost and accuracy gates still reject weak opportunities.

## Decision

Milestone 4 passes its implementation objective: the race can train on complete evidence,
and crypto monitoring now covers the full current active INR universe. It does **not** pass
the accuracy objective, so no model is promoted and the canonical accuracy claim is not
raised.

The next experiment should target the logistic model's high-precision/low-coverage region:
improve causal information and calibration, then require at least 100 selected calls, 30
active sessions, 40% session coverage, non-negative after-cost expectancy, 80% observed
success and a 70% Wilson lower bound before the locked tail can open.
