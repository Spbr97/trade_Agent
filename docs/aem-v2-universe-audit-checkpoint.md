# AEM v2 Milestone 2 (complete): universe integrity audit and frozen development dataset

Date: 26 September 2026

Branch: `codex/aem-v2-accuracy-first`

Status: **Milestone 2 complete - feature registry, universe audit and frozen
development dataset all exist. Not evaluated for accuracy. No live change.**

## Outcome

This closes the two Milestone 2 sub-items left open by
[the feature checkpoint](aem-v2-features-checkpoint.md): the missingness/corporate-
action/liquidity/tradability/source-version audit, and freezing the development
dataset with every excluded (symbol, session) pair and its reason.

This ran against the **real, already-collected frozen 50-stock staged dataset**
(`ec539cf66bea4c18ba994506f85a52d6`) - the same physical M1/daily data AEM v1's baseline,
gates and exit-search checkpoints already use - reusing
`aem_staged_data.read_staged_aem_source` rather than re-collecting anything.

**Interpretation, disclosed**: the plan's wording ("preserve every excluded event with a
reason") is read here as excluded *(symbol, session)* pairs, since missingness,
corporate actions, liquidity and tradability are all session-level concerns, not
opportunity-level ones. Deciding which individual opportunities exist within the
included sessions, and whether each one resolves, is Milestone 4's job.

## Result

Across the frozen 50-stock, 120-session cohort (6,000 symbol-sessions):

| Measure | Result |
|---|---|
| Included symbol-sessions | **5,999 / 6,000 (99.983%)** |
| Excluded symbol-sessions | 1 |
| Exclusion reason | `incomplete_m1_session` |
| Corporate-action suspects flagged | 0 |
| Liquidity failures | 0 |
| Insufficient daily warmup | 0 |

The single exclusion - **NSE_3063 on 2026-04-30** - is the same VEDL 21-bar opening-gap
exception CLAUDE.md and the M1 collection checkpoint already document. An independently
built audit, reading the same frozen data through a different code path, reproduced the
exact known exception rather than finding a new or different one - real corroborating
evidence that the audit logic is doing what it claims.

Zero corporate-action suspects and zero liquidity failures were expected, not
surprising: this cohort was already screened for liquidity when it was selected
(`aem_universe_plan.py`), so the audit mainly re-confirms that screening still holds
under AEM v2's own (identical) liquidity floor rather than discovering new problems.

This changes nothing about accuracy. The canonical AEM v1 baseline remains
**149/693 = 21.50% strict success**, an **18.60% Wilson lower bound** and **−0.27471R
mean net R**.

## What is implemented

`tradedesk_lab/aem_v2_universe_audit.py::run_universe_audit`:

- Loads the exact same frozen staged source AEM v1 uses, verifying the source and
  contract fingerprints match the pinned manifest before auditing anything.
- **Missingness/tradability**: reuses the staging layer's own regular-session
  completeness check (`source["coverage"]`) rather than re-deriving it.
- **Corporate actions**: reuses the already-tested
  `data/corporate_actions.py::detect_unadjusted` heuristic directly against each
  stock's own daily frame.
- **Liquidity**: a trailing 20-session median daily turnover floor, using the exact
  same constant (`aem_v2_precision_ladder.MIN_MEDIAN_TURNOVER_INR`) the Milestone 3
  selector's hard-veto layer already uses, so the audit and the selector can never
  silently disagree about what counts as liquid.
- **Source versions**: the source and contract SHA-256 fingerprints are recorded in the
  frozen report.
- Writes a frozen `report.json` (every excluded pair and its reason, a per-symbol
  summary, and the audit parameters) plus an `included_pairs.csv` - the actual frozen
  development-dataset artifact Milestone 4 will iterate over.
- Wired into the CLI as `tradedesk_lab aem-v2-universe-audit` and into the dashboard's
  AEM v2 panel ("Universe audit (M2)").

## Verification

Seven tests: the exclusion-decision logic itself is tested in isolation (a clean liquid
session is included; a missing, illiquid, warmup-insufficient or corporate-action-
suspect session is excluded with the specific reason) with no dependency on real data,
plus two orchestration tests (via monkeypatched staged-source and manifest loaders)
proving the report aggregates correctly, freezes to disk, and rejects a source that no
longer matches its pinned manifest.

The module was then actually run once against the real frozen dataset (not just
tested against synthetic fixtures) - the 5,999/6,000 result above and its checkpoint
test (`test_milestone_two_universe_audit_evidence_matches_source_and_is_real`) pin that
real output, not a mock.

`uv run --no-sync ruff check tradedesk_lab/aem_v2_universe_audit.py
tests_lab/test_aem_v2_universe_audit.py` is clean, as is the full repository suite.

Machine-readable evidence is stored in
[`docs/evidence/aem-v2-universe-audit.json`](evidence/aem-v2-universe-audit.json), the
actual frozen report from the real run described above (not a synthetic example).

## Safety boundary

The audit module is read-only against the production DuckDB (opened `read_only=True`,
matching every other lab reader) and exists only in `tradedesk_lab`. It is not imported
by production scanning, ranking, alerts, management, risk, dashboard or order code.

## Known gaps carried forward

Unchanged from the [Milestone 3 checkpoint](aem-v2-precision-ladder-checkpoint.md)'s
list, minus the two Milestone 2 items this checkpoint closes:

- Milestone 3: the optional tree-model challenger - not built (explicitly optional).
- Milestone 3: `select_calls`'s `portfolio_filter` hook exists but nothing calls it
  with the real risk manager yet - deferred to Milestone 4.
- Milestone 4 (not started): register the bounded ≤24-specification pipeline race, run
  nested chronological walk-forward evaluation over the 5,999 included symbol-sessions,
  and publish the accuracy/availability/economics curve including every failed trial.
  This is the first point an actual AEM v2 accuracy number can exist.
- Milestones 5-7 (not started): independent locked evaluation, prospective shadow
  evidence, and broader NSE qualification.

## Next checkpoint

Milestone 4: register a bounded set of pipeline specifications (geometry × entry-mode ×
model combinations, ≤24 total) and run the first real nested chronological
walk-forward evaluation over the 5,999 included symbol-sessions - reconstructing
opportunities (Milestone 1), computing features (Milestone 2) and scoring them with the
Precision Ladder and logistic control (Milestone 3), all against real outcomes for the
first time. That is the first checkpoint that can honestly report an accuracy number for
AEM v2.
