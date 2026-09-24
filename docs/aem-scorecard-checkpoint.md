# Canonical AEM accuracy scorecard checkpoint

Date: 24 September 2026

Branch: `codex/canonical-accuracy-scorecard`

Baseline dataset: `ec539cf66bea4c18ba994506f85a52d6`

## Implemented

The framework and dashboard now consume one versioned scorecard vocabulary instead
of independently calculating performance labels. The scorecard contains exactly 12
core metrics:

1. Calls issued
2. Filled calls
3. Unfilled calls
4. Unresolved calls
5. Strict wins
6. Strict success rate
7. Wilson 95% lower bound
8. Mean net R after costs
9. Zero-call sessions
10. Active-session coverage
11. Active sessions reaching 70%
12. Active sessions reaching 80%

It also contains four promotion gates: matched-random advantage, registered stress
economics, portfolio-constrained replay and prospective evidence. A gate without a
matching artifact is `PENDING` with no observed value. Missing evidence can never
become a pass. Mismatched dataset or protocol identifiers are rejected.

Every new staged dataset embeds the canonical scorecard in its manifest. The
dashboard reconstructs the same scorecard for older compatible manifests, so the
existing frozen dataset did not need to be rewritten.

## Accuracy-first governing rule

Every later milestone must name its frozen baseline and report deltas in strict
accuracy, Wilson lower bound, after-cost net R, active-session coverage and the
fractions of sessions reaching 70% and 80%. Engineering completion, a smaller call
count or additional model complexity is not evidence of predictive improvement.
Losses, unresolved calls and zero-call sessions remain visible.

## Why the baseline changed

The prior 37.74% diagnostic used ten stocks selected by existing M1 availability.
Only five overlap the frozen 50-stock cohort. The overlap reproduces exactly in both
pipelines at 5/26 wins (19.23%), including identical per-symbol fills and net R. The
five old-only stocks supplied 35/80 wins (43.75%), while the broader cohort outside
the overlap supplied 144/667 wins (21.59%). The evidence points to small-population
and availability-selection effects, not a reconstruction regression on the overlap.

## Verification

- 30 focused scorecard, report, artifact and dashboard tests passed.
- The full runnable repository suite passed **906 tests**, with two skips and the
  same Windows Application Control-blocked tuning test explicitly deselected.
- Ruff and Git whitespace checks passed.
- The real endpoint returned 12 core metrics, four pending promotion gates and
  `all_promotion_gates_pass=false`.
- The dashboard was visually verified with metric denominators and explicit
  `PENDING / Not available` gate states.

This milestone changes reporting and framework evidence only. It does not improve
the measured 21.50% baseline and does not enable live calls.
