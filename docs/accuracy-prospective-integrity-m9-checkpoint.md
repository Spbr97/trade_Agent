# M9 checkpoint: prospective evidence integrity and stress

M9 is an independent observer around the frozen M8 cohort. It does not modify M8's
model, features, threshold, top-k, geometry, predictions, outcomes, or activation date.

## Implemented

- [x] Hash-chain activation, prediction and mature-resolution events in an append-only
  JSONL audit. Re-running is idempotent; any changed prior payload fails closed.
- [x] Recompute and verify prediction hashes, model/M7/contract hashes, summary values,
  unique signal identities, source-watchlist hashes and the frozen selection rule.
- [x] Detect a post-activation watchlist containing eligible setup candidates that the
  M8 collector did not record.
- [x] Report observed, evaluated, selected and zero-selected sessions plus selected-session
  coverage. A no-call session remains visible rather than disappearing from the denominator.
- [x] Add deterministic session-cluster and ISO-week-cluster bootstrap lower bounds. These
  remain unavailable until at least 10 sessions and four weeks respectively.
- [x] Reprice every mature selected call with doubled entry/exit slippage under the same
  frozen geometry, quantity and statutory charges.
- [x] Keep `review_ready` false unless M8 passes, integrity is healthy, doubled-slippage
  expectancy is positive and both cluster lower bounds reach 70%.
- [x] Display integrity, audit count, availability, clustered uncertainty and stress
  economics in the existing dashboard.

## Current evidence

The cohort still has zero post-activation calls. Therefore accuracy, clustered uncertainty
and stress results are correctly unavailable. This checkpoint improves the trustworthiness
of future evidence; it does not claim or manufacture an accuracy improvement.

Initial monitor run: 2026-10-03 23:58 IST  
Integrity: `healthy` (no failures)  
Audit events: 1 activation, 0 predictions, 0 resolutions  
Audit head SHA-256: `348d65fe0721a76877487d3545bb745660ba9e9074caf2f4055eae81ad616316`  
End-to-end NSE tracker result: M8 collecting, M9 healthy, review-ready false.
