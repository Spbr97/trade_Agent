# Self-learning Milestone 18-A — selection dataset readiness result

Generated: 2026-10-11 IST

Status: completed historical dataset-readiness checkpoint. This result does not train a
model, open an internal diagnostic, register a prospective observer, improve baseline
accuracy or authorize a live call.

## Frozen question

Can the exact sealed M17 candidate population support a later market-specific,
high-precision abstaining selector experiment when every row uses the same causal feature
contract and the same fixed `next_open / +0.75R` after-cost execution label?

The protocol was committed before M18 paths or labels were opened. All markets used the
same preregistered splits and gates. NSE, BSE and crypto evidence was never pooled.

## Result

| Market | Status | Gates | Train coverage | Calibration coverage | Internal diagnostic coverage | Eligible train / calibration / diagnostic |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| NSE | `ready_for_development` | 18/18 | 96.37% | 93.31% | 97.21% | 956 / 265 / 279 |
| BSE | `not_ready` | 15/18 | 50.75% | 44.68% | 53.00% | 507 / 126 / 150 |
| Crypto | `ready_for_development` | 18/18 | 100.00% | 100.00% | 100.00% | 339 / 185 / 243 |

NSE label balance across the non-purge blocks is 636 positive and 864 negative rows.
Crypto has 252 positive and 515 negative rows. Both markets therefore have enough
coverage, chronological sessions, rows and class balance to preregister M18-B.

BSE has enough resolved sessions, rows and both label classes, but fails the independent
85% path-coverage gate in all three non-purge blocks. Its missing or invalid paths remain
null evidence. They were not converted into wins, losses or performance-learning rows.

## Interpretation

`ready_for_development` means only that a later experiment has enough valid data to train
on the development block, choose abstention on calibration and open the consumed internal
diagnostic once. It is not an accuracy result. In particular, the positive-label fraction
of this unfiltered dataset must not be presented as agent accuracy because M18-B's selector
and call/no-call rule do not exist yet.

The M18-B model families, hyperparameters, probability calibration, threshold grid,
minimum call volume, controls and pass/fail gates must be frozen in a separate protocol
before either ready market is trained. BSE requires a coverage-recovery checkpoint rather
than a lower threshold.

## Authority

- Active model changed: **NO**
- Model trained: **NO**
- Prospective observer registered: **NO**
- Baseline accuracy improved: **NO**
- Eligible for live calls: **NO**
- Authority: **NONE — RESEARCH ONLY**
