# Self-learning M23 — equity opening-impulse continuation protocol

Date frozen: 2026-10-13

Status: prospective research protocol. No live authority and no baseline improvement are
claimed.

## Purpose

M20 looks for compression immediately below resistance. That is intentionally unsuitable
for a stock whose opening move has already cleared resistance. M23 tests a separate,
causal question: after an unusually strong and liquid opening impulse holds rather than
fully reversing, can a later completed-bar break of the session high capture a second leg
with positive, repeatable after-cost expectancy?

GALLANTT and NIACL on 12 October 2026 motivated this question. They are inspected examples,
not training rows, prospective wins or evidence. M23 evidence begins only after the frozen
activation artifact is created.

## Frozen identity and isolation

- report: `equity-impulse-continuation-m23-v1`;
- plan: `equity-impulse-continuation-plan-m23-v1`;
- contract: `equity-impulse-continuation-m23-contract-v1`;
- markets: NSE and BSE, evaluated independently;
- artifacts: `data/m14_m18/equity_impulse_continuation_m23/<market>/`; and
- authority: `research_watch_only`.

M20 plans, outcomes and denominators are not copied into M23. NSE and BSE evidence is never
pooled. The protocol and implementation hashes are pinned at activation and a later mismatch
fails closed.

## Causal information set

Every decision uses completed 15-minute candles only. The first 09:15 candle is never an
entry. A stock becomes structurally eligible only after at least four completed candles in
the current session and at least 32 earlier candles for reference. A missing 09:15 candle
invalidates the snapshot rather than relabelling a later candle as the opening bar.

The opening impulse must satisfy all of the following:

- first-candle close is at least 2% and no more than 15% above the previous session close;
- first-candle volume is at least three times the median positive volume of the preceding
  32 completed 15-minute candles;
- first-candle close location is at least 70% of its range;
- the post-opening session low remains above the previous close;
- the current close is no more than 6% below the session high;
- the current close is at least 98.5% of session VWAP; and
- the frozen stop is below both the decision close and trigger.

The trigger is 0.10% above the highest completed session high observed at the decision.
The stop is the lower of the post-opening session low and 98.5% of session VWAP. The target
is a fixed +0.75R from the trigger. Candidate order is deterministic, at most the top
three new plans may be registered per snapshot, and one symbol may register only one plan
per session.

## Outcome contract

- A later completed candle must reach the immutable trigger; a watch is not a trade.
- Entry is gap-aware and includes configured adverse slippage.
- A same-candle trigger and stop is invalid, never a win or loss.
- Stop is evaluated before target after entry.
- Pending plans expire at the end of their decision session.
- Triggered plans that remain open are closed at the final completed session candle.
- Costs use the configured market cost model and `strict_success` requires a target with
  positive net R.
- Invalid, expired and never-triggered plans never enter the accuracy denominator.

The equity collector must therefore run once at 15:31 IST so the 15:15–15:30 candle can
resolve late-session targets and stops. New-plan registration remains closed after 15:00.

## Qualification

Implementation and a visually promising example are not evidence. NSE and BSE each need:

- at least 60 resolved triggered plans;
- at least 40 distinct resolved sessions;
- at least +0.05R mean after-cost net expectancy;
- a positive two-sided normal 95% lower confidence bound on after-cost expectancy;
- profit factor of at least 1.20;
- at least +0.10R versus matched random timing;
- maximum losing streak no greater than six; and
- zero integrity violations.

Strict accuracy and its Wilson interval remain visible diagnostics, but neither is a pass
or fail condition. The legacy 80% production gate was retired on 13 October 2026. Passing
M23 would permit human review only; it cannot place orders or alter the active model
automatically.
