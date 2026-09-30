# Accuracy program v1 — Checkpoint 0

Completed: 30 September 2026

Branch: `codex/accuracy-program-v1`

Status: **governance contract passed; no accuracy improvement claimed**

Checkpoint 0 is complete. It freezes the two non-interchangeable scoreboards, five
qualification levels, evidence classes, family-wise search budget, and the rule that
missing evidence is never a pass. Level 4 can permit a human promotion review, but no
level automatically authorizes live operation.

The append-only, SHA-256-chained ledger records ten completed research tracks as
`consumed_historical_development`: AEM baseline and controls, the information-quality
selector, AEM v2 pipeline race, daily mean-reversion exit search, OSR, SSM, index
context, initial MCB trigger audit, and the earlier M14–M18 model preview. Together
they account for 916 registered trials and 1,652 control draws. Their evidence cannot
be relabelled locked OOS or prospective evidence.

Validation proves that:

- frozen thresholds and search budgets cannot be weakened in place;
- absent gates are reported `not_available` and fail;
- the wrong evidence class fails even if all numeric metrics pass;
- source edits, ledger edits, reordering and duplicates are rejected;
- trial budgets reject a fourth family, thirteenth mechanism, or seventh model
  pipeline per surviving family; and
- passing a research level never makes a candidate automatically live-eligible.

Focused validation: 22 tests passed and Ruff reported no violations. The audit opened
no new market outcomes, trained no model, nominated no candidate, and changed neither
dashboard nor production behavior.

The canonical AEM comparator is unchanged: 149/693 strict wins = **21.5007%**, Wilson
95% lower bound **18.6035%**, mean net R **-0.27471**. Checkpoint 0 therefore improves
research integrity, not the measured baseline.

Next: Checkpoint 1 must implement the frozen
[data-feasibility specification](accuracy-data-feasibility-spec.md), publish complete
point-in-time coverage for Cohorts A/B and lock D2 without opening its outcomes. A
signal may not be built until those data gates pass.
