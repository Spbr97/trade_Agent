# M8 prospective accuracy shadow protocol

Status: activated only after code and contract verification. This is a forward-only,
shadow research cohort and has no alert, grade, live-sizing, management, or order authority.

## Frozen candidate

- Setup: `trend_pullback` across the normal NSE scan universe.
- Model: standardized L2 logistic regression, `C=0.1`, trained once on the M7 development
  partition using the same 33 causal arming-close features.
- Selection: probability at least `0.55`, then at most the top two candidates per arming
  session. Ranking is frozen atomically for the complete session.
- Entry: next NSE session open plus configured buy slippage.
- Risk: stop one arming-date ATR below the simulated fill.
- Profit: full exit at `0.5R`.
- Horizon: entry session plus three later sessions. Stop wins any same-bar ambiguity; gaps
  through the stop fill at the open; configured NSE fees and exit slippage are included.

## No-backfill clock

Activation uses the latest of the activation calendar date, newest existing NSE watchlist,
and newest stored benchmark session as `forward_after`. Including the activation date keeps
a stale local store from making an already-finished but not-yet-downloaded session appear
prospective. A watchlist must be armed after that cutoff, generated after activation, and
scored after its close but before 09:15 IST the next calendar day. Late, old, or modified
sessions cannot enter prospective evidence.

## Decision gate

The cohort remains `collecting_insufficient_evidence` until both 100 selected calls and 30
active sessions are resolved. A prospective pass then requires every condition:

- at least 80% strict after-cost success;
- at least 70% Wilson 95% lower bound;
- at least 70% of active sessions achieve the 80% per-session target;
- positive mean after-cost expectancy in R.

Even a pass remains research evidence. It does not change the canonical baseline or enable
live calls automatically; a separate reviewed promotion decision is required.

## Operations

Activate once with `python scripts/accuracy_prospective_shadow.py activate`. The established
NSE signal-tracker run then calls the idempotent collector after its normal work. Runtime
state and the hash-pinned model live under
`data/m14_m18/accuracy_prospective_shadow/` and are intentionally not committed.
