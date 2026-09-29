# OSR Milestone 1: causal event and outcome engine

Date: 29 September 2026

Branch: `codex/osr-m1-causal-engine`

Status: **implemented and tested; no real dataset or selector run; no accuracy result;
no production or dashboard change**

## Outcome

The research-only OSR engine now reconstructs the two hypotheses frozen in Milestone 0
and resolves their later M1 fills conservatively. This is necessary infrastructure,
not evidence that OSR improves the 21.50% canonical AEM v1 baseline.

`tradedesk_lab/osr_events.py` implements:

- `gap_down_reclaim`: a 0.30-2.50% gap below the prior completed daily close, an early
  lower extreme, then a completed bullish bar crossing above both the session open and
  the causal VWAP.
- `opening_low_sweep_reclaim`: the first 15 completed M1 bars freeze the opening-range
  low; a later completed bar must sweep at least 0.10% below it, and a still-later
  completed bullish bar must close back above it.
- One first opportunity per mode per symbol-session, preventing repeated recrosses from
  inflating the population.
- Prediction after the reclaim bar closes, a two-minute later entry window, a 0.10%
  maximum chase, actual NSE cost-model charges, slippage, fixed deadlines, and explicit
  unfilled/unresolved outcomes.
- Adverse stop-first treatment when an M1 bar touches target and stop, adverse gap-stop
  fills, target caps on favorable gaps, and a strict win only when the target is reached
  with positive net P&L after costs.

## Causal and failure verification

Twelve focused test functions establish that:

- bars at or after the decision timestamp cannot change the opportunity;
- the 15-minute opening range, later sweep, and still-later reclaim occur in causal order;
- invalid prior daily context and incomplete M1 sequences fail closed;
- chase, missing entry bars, incomplete outcome windows, expired entries, and contract
  mismatches never become wins;
- target/stop ambiguity becomes a loss;
- a target touch with negative after-cost P&L is not strict success; and
- changes after the frozen exit window cannot rewrite a resolved outcome.

Milestone 2 later added a necessary-condition prefilter to avoid repeatedly validating
flat minutes during full-session reconstruction. The definitive detector is unchanged;
a parameterized regression proves the optimized session result exactly matches exhaustive
minute-by-minute detection for both modes and a no-signal session.

Machine-readable evidence is in
[`docs/evidence/osr-engine.json`](evidence/osr-engine.json). The evidence fingerprints
the OSR strategy contract, event contract, implementation, and its tests.

## Safety boundary

Only `tradedesk_lab`, `tests_lab`, and documentation are changed. No production scanner,
setup, dashboard, manager, risk, broker, alert, or order module imports OSR. The engine
cannot issue a live call or apply a trade.

## Next checkpoint

Milestone 2 must implement exactly the 30 frozen causal features, prove future-bar
invariance and candidate-excluded cross-sectional calculations, then assemble the real
audited development population while retaining every exclusion. Only after that can the
bounded 12-specification walk-forward race produce an OSR development result.
