# M23 checkpoint — profitability-first opening-impulse continuation

Date: 2026-10-13

## Outcome

The active 80% accuracy / 70% Wilson promotion gate is retired. Accuracy and its interval
remain visible diagnostics, but current eligibility is decided by adequate forward/OOS
evidence, at least +0.05R mean after-cost expectancy and at least +0.10R advantage over
matched random timing. New prospective promotion results additionally require a positive
95% lower bound on expectancy and profit factor of at least 1.20.

This policy change does not approve any existing setup. Their negative or unavailable
expectancy and random-timing evidence still block them.

## New experiment

M23 is a separate NSE/BSE opening-impulse continuation experiment. It exists because M20
correctly rejects a stock that has already broken resistance, but that stock may still form
a causal same-session second-leg entry.

- uses the full daily-liquid equity universe and the collector's existing completed-M15
  fetch;
- requires a strong, high-volume 09:15 opening candle and a subsequent hold above the
  previous close and session VWAP;
- freezes a trigger above the observed session high, a structural stop and +0.75R target;
- registers no more than three plans per snapshot and one plan per symbol/session;
- resolves target, stop or final-bar timeout after modeled costs;
- excludes invalid, ambiguous, expired and never-triggered plans from performance; and
- writes independent NSE and BSE activation, snapshot and plan hash chains.

The inspected GALLANTT and NIACL moves are examples only. They are not M23 wins, training
rows or evidence.

## Dashboard and operation

The Learning dashboard now shows M23 candidates, collector health, active and resolved
plans, mean net R, the expectancy lower bound, profit factor, drawdown and descriptive
accuracy. Crypto is explicitly not applicable. The equity collector receives a final
15:31 IST run so the completed 15:15–15:30 candle can resolve late outcomes.

## Evidence still required

No profitability improvement is claimed at implementation. NSE and BSE independently need
60 resolved triggered plans over 40 sessions, the frozen economic thresholds, complete
matched-random timing evidence and intact hash chains before human review. M23 never places
orders and cannot promote itself.
