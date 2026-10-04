# Crypto accuracy C2 — anticipatory mechanism checkpoint

Protocol: `crypto-accuracy-mechanisms-v1`

Status: **implementation complete; evidence collecting; 0 of 12 trials evaluated; no
accuracy improvement claimed**

C2 now has a fixed, executable research contract for testing whether causal all-pair
crypto context can improve the reliability of C1's quick-profit labels. The engineering
checkpoint is complete, but the predictive checkpoint is not: the real point-in-time C1
history has not yet produced mature labels for a fair mechanism/control comparison.

## What is implemented

- Four fixed mechanisms: seven-session cross-sectional momentum, 14/3-session pullback
  continuation, liquid five-versus-prior-15-session true-range compression, and
  seven-session BTC-relative strength.
- Deterministic top/bottom selections over point-in-time registered cross-sections, with
  a minimum 200 valid coins and 90% active-universe coverage per session.
- The unchanged three C1 quick-profit geometries, producing exactly 12 registered trials.
- Inverse, 1,000 within-session shuffle, 1,000 random-coin and 1,000 same-coin
  random-timing controls with seed `20261007`.
- A family-wise max-statistic correction across the complete 12-trial budget.
- Hard accuracy, Wilson-confidence, sample, session, after-cost expectancy, control,
  corrected-significance and three-fold stability gates.
- A first-30-session development window and fail-closed reporting: incomplete evidence
  stays unavailable and cannot appear as zero or pass; partial performance is suppressed
  until every trial and paired control is mature.

The feature boundary allows an older closed candle to supply a registered coin's own
causal lookback. It never treats that candle as proof of historical cross-sectional
membership. This preserves useful history without recreating the survivorship error C1
was designed to prevent.

## Current real evidence

| Measure | Current result |
|---|---:|
| Registered mechanisms | 4 |
| Frozen C1 geometries | 3 |
| Registered trials | 12 |
| Evaluated trials | 0 |
| Passing trials | 0 |
| C1 minimum point-in-time sessions per required pair | 1 of 30 |
| Required pairs at the C1 session gate | 0 of 337 |
| Mature C1 geometry labels | 0 |
| Accuracy / Wilson lower bound | Not available |
| Mean net R / control advantage | Not available |
| Baseline improved | false |
| Eligible for live use | false |

Zero evaluated trials is a collection state, not a failed experiment. Likewise, the
accuracy and expectancy fields are unavailable rather than 0%. The evaluator cannot open
partial results merely because one mechanism or shorter geometry matures first.

## Frozen decision rule

After all 12 trials and their controls mature, each trial is evaluated once. A qualifying
trial needs at least 100 resolved calls over at least 30 active sessions, at least 80%
observed success, a 70% Wilson lower bound, positive after-cost net R, at least +0.10R
over every control, superior accuracy and net R versus every control, a corrected
one-sided empirical p-value no greater than 0.05, and favorable accuracy/net-R direction
in all three chronological folds.

No qualifying trial means `mechanism_race_rejected` and stops this mechanism version. A
qualifier means `mechanism_race_passed_research_only` and permits only C3's bounded
selector comparison. Neither outcome
changes the live agent automatically, and even a C2 pass cannot set baseline improvement
or live eligibility true.

See the [frozen C2 protocol](crypto-accuracy-mechanisms-c2-protocol.md), the
[C1 dataset checkpoint](crypto-accuracy-dataset-c1-checkpoint.md), and the
[crypto accuracy plan](plan-crypto-accuracy.md).

R1 now refreshes C1 and then C2 automatically after the daily crypto tracker. C2 is never
called when C1 source integrity is missing or false, and a derived refresh failure cannot
undo the recorded point-in-time universe observation. See the
[R1 evidence-refresh checkpoint](accuracy-evidence-refresh-r1-checkpoint.md).
