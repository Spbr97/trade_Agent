# Accuracy Milestone 6 protocol: anticipatory entry and quick-profit geometry

Status: preregistered before execution

Date: 2026-10-03

Protocol: `accuracy-geometry-v1`

## Why the plan changes

Milestone 5 rejected more ranking models on the existing outcome: the best
coverage-qualified result was 30.67% with -0.446R expectancy. Repeating classifier
variants on that target is stopped. Milestone 6 changes the testable mechanism while
preserving the clean causal cohort, costs, conservative ordering and evidence gates.

This experiment asks whether entering at the first open after a setup arms, combined
with a smaller target and shorter holding period, can produce materially more reliable
calls than waiting for the saved historical fill. The next-open mode is an anticipatory
proxy, not evidence that a live order existed.

## Frozen grid

- Entry mode: saved historical fill; next exchange-session open after arming.
- Stop: 0.75, 1.00 or 1.25 arming ATR below the tested fill.
- Quick target: 0.25R, 0.50R or 0.75R.
- Maximum hold: 1, 2 or 3 sessions after entry.
- Total: 54 geometries.
- Entry and exit slippage, current NSE charges, position sizing, gap limits and
  stop-before-target ambiguity use the existing tested production primitives.
- Saved-fill entry-day targets still require closing confirmation because the
  intraday fill order is unknown. Next-open entries have known open ordering.

## Evidence discipline

- The already-inspected clean cohort is development-only historical research.
- Split by unique arming session: first 80% development, final 20% locked.
- The locked tail remains unopened unless one development geometry passes all gates.
- Minimum 500 resolved calls, 100 active sessions and 40% session coverage.
- At least 80% observed accuracy and 70% Wilson lower bound.
- At least 70% of active sessions must individually reach 80% success.
- Mean after-cost expectancy must be non-negative.
- No live configuration, setup, dashboard call, or canonical baseline changes merely
  because the experiment runs.

An easy 0.25R target has an 80% gross breakeven win rate before costs. Therefore an
80% hit rate with negative expectancy is explicitly a failure, not an improvement.
