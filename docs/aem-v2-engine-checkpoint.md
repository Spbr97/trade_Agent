# AEM v2 Milestone 1: causal event and outcome engine

Date: 26 September 2026

Branch: `codex/aem-v2-accuracy-first`

Status: **implemented and tested; not evaluated for accuracy; no live change**

## Outcome

Milestone 1 adds the deterministic research-only mechanics needed to reconstruct AEM
v2 opportunities and measure their outcomes without look-ahead. It does not yet
implement the feature registry, Precision Ladder selector, model training, ranking or
an accuracy experiment.

The canonical AEM v1 baseline therefore remains **149/693 = 21.50% strict success**,
with an **18.60% Wilson lower bound** and **−0.27471R mean net R**. Milestone 1 makes
no claim that these values improved.

## Implemented contracts

- Three separately labeled completed-bar sequences: anticipatory impulse, confirmed
  pullback and breakout/retest.
- A decision timestamp equal to the signal bar's close. Entry evaluation begins only
  on bars at or after that timestamp.
- Three-minute entry validity, 0.15% maximum chase, ₹0.05 adverse tick rounding and
  gap-aware actual fill prices.
- The three frozen quick-profit geometries from Milestone 0.
- Target, stop and maximum-hold clocks derived from the actual fill rather than the
  intended entry.
- Adverse stop-first treatment when one M1 bar contains both barriers. An intrabar
  trigger-bar low that could be pre-fill is never turned into favorable evidence; the
  possible adverse path is recorded explicitly.
- NSE intraday fees and entry/exit slippage applied to the actual fill and exit.
- Strict success only when the registered target is hit and net P&L remains positive.
  A profitable timeout remains separately identified and is not a strict win.
- Missing, duplicated, misaligned, invalid or incomplete M1 sequences fail closed.
  Filled calls with incomplete outcome windows remain unresolved and cannot pass a
  later evidence gate.
- Signals after 11:00 IST are rejected.

## Verification

Focused tests cover all three event orderings, future-bar isolation, gap/chase
handling, same-bar ambiguity, missing entry and outcome bars, post-deadline signals,
entry expiry, contract mismatches and target touches made negative by costs.

The complete runnable repository suite is **930 passed and two pre-existing skips**
across 932 collected tests. The same five SciPy-dependent modules and one known
scikit-learn tuning test remain excluded under Windows Application Control; no system
security control was weakened. One initial full run encountered a transient Hypothesis
slow-input health check in an unchanged cost property test; the exact seeded test then
passed, and the clean complete rerun passed.

Machine-readable evidence is stored in
[`docs/evidence/aem-v2-engine.json`](evidence/aem-v2-engine.json). It pins the event
contract, implementation and test fingerprints.

## Safety boundary

The engine exists only in `tradedesk_lab`. Production scanning, ranking, alerts,
management, risk, dashboard and order code do not import it. The research dashboard
only reads the checkpoint evidence and explicitly displays that accuracy has not been
evaluated and the baseline has not improved.

## Next checkpoint

Milestone 2 implements the causal feature registry and availability timestamps,
audits missingness/liquidity/tradability/source versions, and freezes the development
dataset while preserving every excluded event and reason. Selection and model fitting
remain later milestones.
