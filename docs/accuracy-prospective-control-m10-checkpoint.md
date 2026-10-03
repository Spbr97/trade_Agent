# Accuracy Milestone 10 — prospective matched-random selection control

Status: **implemented; collecting; no new performance result**

M10 is registered against the frozen M8 candidate and now runs after M8/M9 in the NSE
tracking job. For each fresh M8 session it freezes 1,000 same-session, same-population
random selections with the model's exact call count, then resolves every candidate using
the same next-open quick-profit contract.

Integrity checks reject changes to:

- the M8 activation contract;
- the session's full candidate population;
- the model-selected IDs;
- prediction hashes;
- random assignments; or
- independently recomputed selected-call outcomes.

The dashboard reports model and random expectancy, selection advantage, p-value, mature
sessions, resolved calls, and outcome parity. It also says plainly that this is not the
broader random-timing control.

The initial 4 October 2026 run contains zero post-activation M8 sessions and zero resolved
calls, so its status is `collecting_insufficient_evidence`. This is expected, not a pass or
failure. The gate requires 100 paired calls, 30 sessions, at least +0.10R selection
advantage, and p <= 0.05. Live eligibility remains false.

See the [frozen protocol](accuracy-prospective-control-m10-protocol.md).
