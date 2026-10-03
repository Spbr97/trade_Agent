# Accuracy Milestone 10 — prospective matched-random selection protocol

Protocol: `accuracy-prospective-control-v1`

M10 measures whether the frozen M8 model ranks better candidates than random selection.
It cannot change M8 predictions, thresholds, geometry, outcomes, availability, or authority.

## Registration

- Registered only after the M8 activation contract was frozen.
- Fixed seed: `20261004`.
- Fixed null count: 1,000 cohorts.
- Each arming session is registered before its M8 score deadline. Late sessions are retained
  as `excluded_late` and never enter evidence.
- The M8 activation, candidate population, selection, prediction hashes, and every random
  assignment are hash-checked on every run.

## Matched selection control

For every prospective M8 session, each null cohort randomly selects without replacement
from that session's exact `trend_pullback` candidate population. Every cohort receives the
same number of candidates as the model selected in that session. The assignments are
frozen before the next-session entry can occur.

Every candidate—not only the selected calls—is later resolved independently with M8's exact
contract: next-session open plus configured slippage, 1 ATR stop, 0.5R target, three-session
maximum hold, identical position sizing, and identical after-cost R. Outcomes of candidates
also selected by M8 must match M8 field-for-field within numerical tolerance.

## Frozen gate

The control can pass only after all of these are true:

- at least 100 paired resolved model calls;
- at least 30 mature sessions;
- model mean after-cost R exceeds the mean random-selection R by at least +0.10R; and
- one-sided empirical randomization p-value is at most 0.05, using a finite-sample
  correction across the 1,000 frozen cohorts.

Accuracy, Wilson bound, session reliability, expectancy, stress, and availability remain
mandatory in M8/M9. Passing M10 cannot compensate for failing them.

## Scope limitation

M10 is a **same-session random-candidate-selection** control. It does not randomize entry
timing and therefore does not satisfy the broader same-stock random-timing gate. The
dashboard and artifact state this limitation explicitly. M10 is research-only and has no
alert, grade, sizing, management, broker, or order authority.
