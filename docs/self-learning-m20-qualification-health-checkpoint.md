# M20 Pre-Takeoff Qualification and Collector-Health Checkpoint

Implemented: 2026-10-11  
Authority: measurement only; no order placement, promotion, or baseline change

## What this closes

The completed-M15 pre-takeoff framework now has one automatic, market-separated evidence
evaluator for NSE, BSE, and crypto. It reads the immutable plan ledger and reports:

- resolved triggered trades and distinct decision sessions;
- strict successes and strict accuracy;
- 95% Wilson lower and upper bounds;
- after-cost mean net R and missing-net-R integrity count;
- maximum chronological losing streak;
- the current frozen gate and progress toward it; and
- a fail-closed qualification state.

NSE and BSE use the frozen M20 early gate at the first available look with at least 20
resolved trades across 10 sessions. Failing accuracy below 50%, non-positive mean net R,
or incomplete net-R evidence writes a terminal hash-chained rejection. Passing continues
collection toward the 60-trade/40-session qualification gate.

All three markets use the 60-trade/40-session core requirements: at least 70% strict
accuracy, at least 55% Wilson lower bound, positive mean net R, complete net-R evidence,
and maximum loss streak no greater than six. A passing NSE/BSE result becomes
`qualified_for_review`, never live authority. Crypto additionally retains the already
frozen M19 matched-random accuracy and net-R tail-probability requirements; a core pass is
therefore `controls_pending` until those independent controls exist. Unavailable controls
cannot silently pass.

Each first valid gate look is appended to `qualification.jsonl` with a canonical hash and
previous-event hash. A terminal rejection or qualification remains terminal even if later
outcomes would change the rolling percentages. This prevents optional stopping.

## Collector health

The dashboard now distinguishes evidence quality from collection health:

- crypto is healthy only when its latest artifact is no more than 45 minutes old;
- NSE/BSE are checked against the frozen 09:31-15:16 weekday schedule;
- weekends and pre-session hours report `waiting_for_market_session` when the preceding
  scheduled run is present, rather than incorrectly reporting stale; and
- missing or overdue evidence is explicitly `not_collected` or `stale`, never a pass.

The dashboard exposes collector state, evidence sessions, Wilson lower bound, maximum loss
streak, gate status, and calls/session progress for the selected market. NSE, BSE, and
crypto remain separate throughout.

## Current boundary

No model is retrained from these outcomes while the cohorts are below their frozen sample
requirements. The first scheduled NSE/BSE market-hours run is due 2026-10-12 at 09:31 IST
and cannot be truthfully verified on 2026-10-11. That future operational observation is
not an unfinished implementation and must not be backfilled.
