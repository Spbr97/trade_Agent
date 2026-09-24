# Frozen AEM baseline-gates checkpoint

Date: 25 September 2026

Branch: `codex/aem-baseline-gates`

Dataset: `ec539cf66bea4c18ba994506f85a52d6`

Validation run: `8d07b3bb048f4313893876b2db2febf9`

## Outcome

The matched-random, registered-stress and portfolio gates were run against the exact
frozen 50-stock staged baseline. All 815 observed TRADE attempts replayed exactly from
their original candles. The counterfactual population contains 82,315 executions
(815 attempts × all 101 registered decision minutes), and the comparison uses 500
same-session timing cohorts.

The result is negative and remains visible:

| Gate | Observed | Requirement | Result |
|---|---:|---:|---|
| Matched-random advantage | −0.03949R / attempt | ≥ +0.10R | FAIL |
| Worst registered stress | −0.44255R / fill | > 0R | FAIL |
| Portfolio-constrained replay | −0.27686R / fill | > 0R | FAIL |
| Prospective evidence | Not available | 100 resolved, 30 active sessions and accuracy thresholds | PENDING |

The observed policy averaged −0.23359R per attempted call, compared with −0.19409R
for the matched timing-null cohorts. The descriptive upper-tail fraction was 0.9182;
this is not a confirmatory p-value. The broader baseline does not reproduce the
favorable conditional timing result from the earlier ten-stock availability-selected
sample.

## Registered execution stresses

| Case | Fills | Strict wins | Strict rate | Mean net R / fill |
|---|---:|---:|---:|---:|
| Base costs | 693 | 149 | 21.50% | −0.27471R |
| Costs ×1.25 | 693 | 149 | 21.50% | −0.31124R |
| Costs ×1.50 | 693 | 149 | 21.50% | −0.34777R |
| Slippage ×2 | 693 | 131 | 18.90% | −0.44255R |
| Same signal, one M1 bar late | 685 | 152 | 22.19% | −0.26837R |
| Adverse missed fills | 623 | 79 | 12.68% | −0.43039R |

The missed-fill sensitivity deterministically removes the best-net-R 10% of resolved
fills. It is deliberately pessimistic and outcome-aware; it is a robustness bound,
not a claim about the probability of a live fill.

## Portfolio replay

The replay selected 191 fills after the existing no-entry window, risk/position sizing,
cash, heat, maximum-open-position, maximum-three-new-entries, weekly-loss,
consecutive-loss-pause, re-entry and sector constraints. Unknown sectors share one
conservative capped bucket.

- Strict wins: 40/191 (20.94%).
- Mean net R: −0.27686R per selected fill.
- Net P&L: −₹7,407.78 from ₹100,000 initial research capital.
- Maximum drawdown: 7.68%.
- Most rejections came from the no-entry window (254), consecutive-loss pause (127),
  daily-entry cap (60), unknown-sector cap (47) and re-entry cooldown (14).

The production `min_net_rr=2.0` gate is structurally incompatible with the frozen
AEM quick-profit target/stop ratio. It is reported as a contract exception and is not
silently used to turn the replay into zero calls. This remains an explicit issue for
the next contract-design experiments.

## Baseline deltas and interpretation

This checkpoint validates the unchanged baseline; it does not change its core
scorecard. Strict accuracy remains 21.50%, Wilson lower bound 18.60%, mean net R
−0.27471R, active-session coverage 98.33%, sessions reaching 70% 3.39%, and sessions
reaching 80% 1.69%. Therefore every accuracy delta versus the frozen baseline is zero.

The three newly measured promotion gates fail. Prospective evidence is still absent
and therefore pending, never passed. No live eligibility or 70–80% reliability claim
is supported.

## Implementation and dashboard

`aem-validate-staged` creates a durable report and only advances its latest pointer
after every validation stage completes. It verifies the frozen event hash, contract,
execution dependencies and source fingerprint. A completed counterfactual grid can be
reused only when its dataset and source hashes match; cohort validation still checks
every event/slot denominator.

The existing research dashboard reads only a completed validation artifact matching
the current staged dataset. It shows the three failed gate values and keeps prospective
evidence pending. Base call, dashboard-management and order-placement behavior remain
unchanged.

Focused validation/dashboard tests, scoped Ruff and Git whitespace checks passed. The
full runnable repository suite passed **910 tests**, with the same pre-existing skips
and explicit exclusions for five SciPy modules and one scikit-learn tuning test blocked
by Windows Application Control. The live local dashboard was visually verified with
all three measured gates marked `FAIL` and prospective evidence marked `PENDING`.

## Next checkpoint

Use the retained failures to preregister a bounded error taxonomy and accuracy-first
candidate-selection hypotheses. Any challenger must beat this frozen scorecard on
later chronological data without hiding losses through abstention and must preserve
positive after-cost/stress economics before prospective collection begins.
