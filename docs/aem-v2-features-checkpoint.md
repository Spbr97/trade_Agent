# AEM v2 Milestone 2 (partial): causal feature registry

Date: 26 September 2026

Branch: `codex/aem-v2-accuracy-first`

Status: **feature registry implemented and tested; integrity audit and development
dataset freeze not yet done; not evaluated for accuracy; no live change**

*Update, same day: the integrity audit and development-dataset freeze this checkpoint
describes as pending are now complete - see the
[Milestone 2 universe-audit checkpoint](aem-v2-universe-audit-checkpoint.md). The rest
of this document is left as it was written for this checkpoint's own record.*

## Outcome

Milestone 2 has four planned sub-items in
[the plan](plan-aem-v2-50pct-baseline.md): (1) the causal feature registry with
availability timestamps, (2) cross-sectional breadth/relative-strength inputs, (3) a
missingness/corporate-action/liquidity/tradability/source-version audit across the
staged universe, and (4) freezing the development dataset with every excluded event and
its reason. This checkpoint completes (1) and (2) only. It does **not** complete (3) or
(4), and it implements no selector, no model and no accuracy experiment.

The canonical AEM v1 baseline therefore remains **149/693 = 21.50% strict success**,
with an **18.60% Wilson lower bound** and **−0.27471R mean net R**. This checkpoint makes
no claim that these values improved, and cannot: it produces feature vectors, not
predictions or trade outcomes.

## What is implemented

`tradedesk_lab/aem_v2_features.py::compute_features` computes all 34 features frozen in
the Milestone-0 contract (`tradedesk_lab/aem_v2_contract.py::FEATURES`) at exactly one
opportunity's `decision_at`, reusing Milestone 1's own bar-validation and VWAP helpers
rather than re-deriving them, and reusing `daily_features` (indicators) and
`time_of_day_rvol` (already-tested same-time relative-volume research) rather than
re-implementing daily indicators or a volume baseline from scratch.

- Every feature is computed only from bars closed strictly before `decision_at` -
  candidates in the first six minutes of the decision window (09:20-09:25 IST) cannot
  get a full feature vector yet, since several formulas need at least 11 closed bars;
  this is a disclosed availability constraint, not a bug.
- The same-time relative-volume feature (`tod_rvol`) requires a separate prior-session
  history input that must end strictly before today's open; an overlapping or missing
  history fails closed.
- The daily-derived features (opening gap, resistance distance, extension, 20-day
  turnover) require a daily frame whose last row is strictly before today's session
  date; a same-day or later daily row fails closed.
- Cross-sectional features (relative strength, breadth, dispersion, causal volatility)
  require at least 5 peers with a matching causal bar count at the same decision time,
  and always exclude the candidate's own code from the peer distribution.
- Several formulas (`causal_level_distance`, `failed_break_count`, `retest_depth`,
  `prior_compression`) are disclosed approximations of the plan's prose feature table,
  not a literal transcription - the plan only names feature groups and intent, not exact
  math. Each approximation is documented inline in the module.

## Verification

Seven focused tests cover: every registered feature name is present and finite; future
bars on the candidate's own frame AND on every peer's frame cannot change an
already-computed feature vector; an undersized signal history, an undersized peer
universe, a non-causal daily frame, an overlapping relative-volume history, and a
strategy-contract mismatch all fail closed with a specific reason.

`uv run --no-sync ruff check tradedesk_lab/aem_v2_features.py
tests_lab/test_aem_v2_features.py` is clean. The complete repository suite ran with no
failing or erroring test and one pre-existing skip (unchanged in kind from Milestone 1's
report); this session's shell truncated pytest's own final summary line, so an exact
total test count is not reproduced here - the per-test pass/fail/skip marker stream
itself was captured in full and is failure-free.

Machine-readable evidence is stored in
[`docs/evidence/aem-v2-features.json`](evidence/aem-v2-features.json), pinned to the
feature contract and to the current module/test file hashes so it cannot silently drift
from the code it describes.

## Safety boundary

The feature module exists only in `tradedesk_lab` and is not imported by production
scanning, ranking, alerts, management, risk, dashboard or order code. The research
dashboard's AEM v2 panel now shows a "Feature layer (M2)" row and explicitly states that
integrity auditing and the development-dataset freeze are still pending, and that
accuracy has not been evaluated.

## Next checkpoint

Complete Milestone 2's remaining two sub-items - the missingness/corporate-action/
liquidity/tradability/source-version audit across the full staged 50-stock universe, and
freezing the development dataset while preserving every excluded event and its reason -
before moving to Milestone 3 (the Precision Ladder selector, calibration, thresholding
and abstention). No accuracy claim is possible before Milestone 4's bounded development
experiment actually runs a selector against resolved outcomes.
