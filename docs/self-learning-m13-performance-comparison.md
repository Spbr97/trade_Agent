# Self-learning Milestone 13 checkpoint — exact-cohort performance comparison

Status: implemented and fail-closed; no market has enough evidence to prove an
accuracy improvement.

This checkpoint completes the missing performance-comparison panels in the Learning
dashboard. It does not change a scanner, model, signal, outcome contract, eligibility
gate, personal-call authority or broker path.

## Questions answered

For each market, the dashboard now answers four distinct questions without pooling them:

1. What was the strict accuracy and after-cost expectancy of every sealed, mature,
   prospective row in one exact frozen cohort before the prediction-time qualification
   filter?
2. What were the same metrics after that existing filter retained only calls classified
   as `qualified_call` before their outcomes were known?
3. How did each exact contract, strategy, feature and model version perform?
4. On the latest immutable chronological test, how did the frozen challenger compare with
   its frozen baseline, and how did the single preregistered primary selector perform?

The first comparison is descriptive. A higher after-filter percentage is never treated as
promotion evidence, because the independent locked, random-control, prospective,
uncertainty and economic gates must still pass.

## Evidence boundaries

- NSE, BSE and Crypto are always reported independently.
- Contract version **and digest** must match.
- Strategy, feature, model and model-kind versions must match.
- Only ledger-v1, integrity-valid, mature, resolved, live prospective rows enter.
- Pending, invalid, never-triggered, legacy-unsealed and backfilled rows remain visible in
  their existing evidence/exclusion panels but do not enter performance learning.
- Missing accuracy or expectancy is displayed as `not available`, never as zero and never
  as a pass.
- Multiple frozen cohorts are shown as separate table rows; their percentages and R values
  are not pooled into a headline.
- The challenger panel reads the exact saved locked-test artifact. It does not refit or
  reconstruct a challenger on a dashboard request.
- The active model remains unchanged and promotion remains unauthorized.

## Dashboard additions

The Learning view now includes:

- before-filter strict accuracy and sample size;
- after-filter strict accuracy and sample size;
- accuracy delta and retained-call fraction;
- before/after mean after-cost R;
- a table of exact contract/strategy/feature/model cohorts;
- frozen-baseline classifier accuracy and Brier score;
- current frozen-challenger classifier accuracy and Brier score;
- primary-selector strict accuracy, selected sample and net R; and
- an explicit `Improvement proven` indicator that fails closed.

The supporting endpoint is
`/api/self-learning/performance-comparison?market=<nse|bse|crypto>`.

## Current repository evidence — 9 October 2026

| Market | Eligible mature sealed rows | Exact cohorts | Cohorts with before and after evidence | Frozen challenger | Improvement proven |
|---|---:|---:|---:|---|---|
| NSE | 0 | 0 | 0 | not available | no |
| BSE | 0 | 0 | 0 | not available | no |
| Crypto | 1 | 1 | 0 | not available | no |

Crypto's one row is a shadow/counterfactual call, so its before-filter result exists but
there is no after-filter qualified sample. The dashboard therefore says
`after filter not available`; it does not convert the missing denominator into a
percentage.

## Interpretation

This checkpoint improves measurement and makes filtering effects auditable. It does not
improve the measured baseline by itself. The next accuracy-changing event must come from
new sealed mature evidence under the frozen contracts. Until then, the honest result for
all three markets remains: **accuracy improvement not proven**.
