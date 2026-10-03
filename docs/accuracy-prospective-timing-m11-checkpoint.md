# Accuracy Milestone 11 — prospective same-stock random timing

Status: **implemented; collecting; no new performance result**

M11 completes the missing timing side of the prospective controls. Every fresh selected M8
call now receives 1,000 frozen same-stock timing offsets over the next 1–20 NSE sessions.
Each unique placebo is resolved with causal ATR and the exact M8 entry, target, stop, hold,
sizing and cost rules. Resolved candle inputs are hash-checked thereafter.

The NSE tracker runs M11 after M8–M10. The dashboard reports paired calls, active sessions,
model versus random-timing expectancy, timing advantage, empirical p-value, source
integrity and the exact control scope. Large assignment/placebo payloads stay outside the
dashboard API.

The initial 4 October 2026 registration contains no post-activation M8 selected calls, so
the status is `collecting_insufficient_evidence`: zero paired calls, zero active sessions,
zero errors and live eligibility false. This is neither a pass nor a failure.

The frozen decision gate is 100 paired calls, 30 active sessions, at least +0.10R advantage
and p <= 0.05. See the
[frozen protocol](accuracy-prospective-timing-m11-protocol.md).
