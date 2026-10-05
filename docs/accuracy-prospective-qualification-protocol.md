# NSE prospective qualification protocol

Protocol: `accuracy-prospective-qualification-v1`

Frozen: 4 October 2026

This read-only protocol makes one atomic decision from the already frozen M8-M11 NSE
evidence streams. It does not retrain, rescore, backfill, alter a prediction, or create a
new strategy. The candidate remains the M7/M8 trend-pullback selector with next-session
open entry, 1 ATR stop, 0.5R target, three-session hold, probability threshold 0.55, and
at most two calls per arming session.

## Evidence components

1. **M8 accuracy:** 100 resolved calls, 30 active sessions, at least 80% strict success,
   at least 70% Wilson lower bound, at least 70% successful-session rate, and positive
   mean after-cost R.
2. **M9 integrity and stress:** frozen model/source integrity, no absent whole watchlist for
   any post-activation session present in stored benchmark bars, no watchlist generated
   after M8's frozen next-entry deadline, positive doubled-slippage expectancy over at least
   100 resolved calls, and session- and week-cluster 95% lower bounds of at least 70%. A
   timely present zero-candidate watchlist remains a valid zero-call day.
3. **M10 selection control:** at least 100 paired calls and 30 sessions, at least +0.10R
   over 1,000 same-session matched-random stock selections, and one-sided empirical
   p <= 0.05.
4. **M11 timing control:** at least 100 completely paired calls and 30 sessions, at least
   +0.10R over 1,000 same-stock random future-session timings, and p <= 0.05.

## Identity and denominator parity

All four protocol versions and the M8 activation fingerprint must match. M8 and M9 must
report the same frozen summary. M10 selected-call outcomes and M11 source hashes must pass
their integrity checks. At review, M8, M10 and M11 must have identical resolved-call and
active-session counts, and identical model accuracy and expectancy. A missing key is
false, not a pass.

## Decision states

- `not_available`: one or more evidence artifacts are absent;
- `collecting_insufficient_evidence`: artifacts are present but any sample/session gate
  has not matured;
- `degraded`: candidate identity, integrity, or final denominator parity is broken;
- `prospective_rejected`: all components matured but at least one performance/control
  gate failed; or
- `human_review_authorized`: every component and parity gate passed.

`human_review_authorized` is not automatic promotion. Every state, including that one,
keeps `baseline_improved=false` and `eligible_for_live=false`. The canonical 21.50% AEM
baseline changes only through a separate explicitly approved promotion decision. BSE and
crypto evidence are excluded from this protocol and cannot be pooled into its gates.
