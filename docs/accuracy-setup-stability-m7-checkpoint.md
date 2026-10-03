# Accuracy Milestone 7: setup-specific stability and locked validation

Date: 2026-10-03

Run: `20261003-194846`

Protocol: `accuracy-setup-stability-v1`

Result: **HISTORICAL LOCKED PASS — prospective shadow still required**

## Frozen candidate

The primary candidate was fixed before this run from the Milestone-6 diagnostic:

- `trend_pullback` only;
- enter at the next exchange-session open after arming;
- 1.0 ATR stop;
- 0.5R target;
- maximum three-session hold; and
- one standardized L2 logistic selector using the existing 33 causal features.

The secondary `base_breakout` hypothesis was evaluated independently but could not
displace a qualifying primary.

## Mechanism stability

The unselected trend-pullback geometry contained 1,811 development calls:

- 1,437 wins: 79.35% accuracy.
- 77.42% Wilson lower bound.
- +0.033R after-cost expectancy.
- Four embargoed chronological fold accuracies: 79.25%, 73.14%, 79.87%, 81.30%.
- Three of four folds had non-negative expectancy; the second was -0.055R.

This passed the preregistered mechanism-stability gate and authorized the single
selector. It did not itself pass the final 80% promotion standard.

The secondary base-breakout geometry failed stability because only two of four folds
had non-negative expectancy. Its selector was therefore never trained.

## Development walk-forward selector

The frozen selector's maximum-coverage qualified operating point was threshold 0.55,
top two calls per active setup session:

- 526/647 wins: **81.30% accuracy**.
- 78.11% Wilson lower bound.
- 378/378 active OOS sessions represented.
- 269/378 sessions met the 80% target: **71.16%**.
- **+0.056R** after-cost expectancy.

This cleared every development gate and authorized one locked-tail evaluation.

## Locked historical tail

The threshold and top-k were not changed. The locked result was:

- 186/223 wins: **83.41% accuracy**.
- 77.97% Wilson lower bound.
- 123/126 active sessions: 97.62% coverage.
- 91/123 active sessions met the 80% target: **73.98%**.
- **+0.083R** after-cost expectancy.

Every frozen locked gate passed. This is the first locked historical pass in the
accuracy program.

## Evidence boundary and decision

The source population is still a previously triggered and previously inspected
historical candidate set with selection and survivorship limitations. The locked tail
was untouched by Milestones 4–6 and was opened once under the Milestone-7 protocol,
but it is not prospective live evidence.

Therefore:

- the candidate is eligible for prospective shadow tracking;
- no live setup, alert eligibility or trade management is changed;
- the canonical live baseline remains unchanged until fresh shadow calls mature; and
- the dashboard labels this as “historical pass — prospective shadow pending.”

Machine-readable evidence is in
[`docs/evidence/accuracy-setup-stability-m7.json`](evidence/accuracy-setup-stability-m7.json).
