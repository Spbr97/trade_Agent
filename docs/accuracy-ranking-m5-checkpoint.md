# Accuracy Milestone 5: coverage-aware ranking experiment

Date: 2026-10-03

Run: `20261003-182349`

Protocol: `accuracy-ranking-v3`

Result: **FAIL / abstain — no promotion**

## Purpose

Milestone 4 showed a 47.83% logistic pocket, but it contained only 23 calls and
covered 14 of 480 out-of-sample sessions. Milestone 5 tests whether broader
thresholds, class balancing, setup-specific models, and regularised tree models
can preserve useful accuracy at a deployable call frequency. It does not change
the outcome definition, causal features, locked tail, or promotion gates.

## Frozen experiment

- Cohort: 22,756 rows, 750 sessions, 33 causal features.
- Data integrity: 100% rule-score and after-cost economics coverage.
- Development evaluation: four chronological walk-forward folds, 13,305
  identical out-of-sample rows per candidate, with a 10-session embargo.
- Candidates: transparent rule score, logistic, balanced logistic,
  setup-specific logistic, histogram gradient boosting, balanced extra trees,
  and regularised XGBoost.
- Probability thresholds: 0.15 through 0.95 in 0.05 steps; top 1/2/3 calls.
- Coverage report: at least 100 calls, 30 active sessions, and 40% of the 480
  development sessions. This is a reporting floor, not a relaxed promotion gate.
- Promotion remains frozen at 80% observed accuracy, 70% Wilson lower bound,
  70% successful-session rate, non-negative after-cost expectancy, and the
  existing sample/availability requirements.
- Locked tail: 4,848 rows; never opened because no development nominee existed.

## Result

The strongest coverage-qualified result was balanced logistic at threshold 0.60,
top 3:

- 146 wins from 476 calls: **30.67% observed accuracy**.
- 26.70% Wilson lower bound.
- 217/480 active sessions: 45.21% session coverage.
- 29/217 active sessions met the success target: 13.36%.
- -0.446R mean after-cost expectancy.

This is 7.19 percentage points above the clean cohort's 23.48% raw prevalence,
but it is not a viable baseline: accuracy confidence is low, session reliability
is low, and expectancy is materially negative. The least sparse adequately
sampled peak was also balanced logistic, 78/241 or 32.37%, but it covered only
22.71% of sessions and produced -0.385R.

The 47.83% figure remains visible only as a diagnostic: it is 11/23 calls,
29.24% Wilson lower bound, 2.92% session coverage, and -0.056R. It is not a
baseline and is not eligible for deployment.

## Decision

No candidate is nominated, no model is promoted, and the canonical AEM v1
baseline remains 21.50%. The result rejects “more classifiers on the same target
and geometry” as the next path. Further accuracy work should change the causal
information or the target/exit geometry under a preregistered experiment, rather
than mine more thresholds from this development cohort.

Machine-readable evidence is in
[`docs/evidence/accuracy-ranking-m5.json`](evidence/accuracy-ranking-m5.json).
