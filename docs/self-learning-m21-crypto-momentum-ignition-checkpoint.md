# M21 Crypto Momentum-Ignition Challenger — implementation checkpoint

Checkpoint date: 2026-10-12 IST
Authority: research observation only; no call, order, sizing, promotion, or baseline
change

## What changed

M21 adds a crypto-only completed-M15 challenger alongside the unchanged M20 compression
benchmark. It scans the full active CoinDCX INR universe for either a controlled
momentum ignition or a prompt VWAP reclaim after an immutable pre-trigger structure
failure. It ranks the complete valid cross-section, may register at most three eligible
plans per snapshot, and displays at most twelve rows without using that display limit as
an evidence filter.

The challenger uses its own hash-chained snapshots, plans, and qualification events under
`data/m14_m18/momentum_ignition_watch/crypto/`. M20 evidence is not rewritten or pooled.
The dashboard comparison is descriptive only.

The first coverage-only production snapshot registered no plan because shared CoinDCX
candles rounded fractional base volume and left only 195/339 valid feature rows. Before
the first plan, the collector was corrected to give M21 raw fractional CoinDCX volume
while deriving M20's unchanged rounded candle view from the same fetched rows. This is a
data-fidelity correction, not a gate or threshold change.

## Why this is an economics change, not an accuracy claim

The M20 fixed `+0.75R` target frequently required an implausibly high target-hit rate
after configured CoinDCX fees, GST, TDS, and slippage. M21 tests the fixed target grid
`0.75R, 1R, 1.25R, 1.5R, 2R, 2.5R, 3R` and freezes the smallest target with positive
target net R, negative stop net R, and an implied break-even rate no greater than 70%.
It rechecks that gate at the actual later confirmation-close fill without moving the
frozen target.

This can reject uneconomic plans; it does not prove profitable or more accurate calls.
Sonic and LINK only motivated the hypothesis and are excluded as prospective evidence.

## Evidence status at protocol freeze

- M21 production resolved calls: **0**
- M21 distinct IST decision dates: **0**
- M21 strict accuracy, Wilson bound, and mean net R: **unavailable**
- tradeable calls: **0**
- baseline improvement: **NO**
- live authority: **NO**

The one frozen core first look is 60 resolved calls across 40 IST dates. A pass requires
at least 70% strict accuracy, a 55% Wilson lower bound, positive after-cost mean net R,
complete net-R evidence, maximum losing streak no greater than six, and no integrity
violation. A core pass remains `controls_pending` until independent M21-specific controls
finish. Details are frozen in
[`self-learning-m21-crypto-momentum-ignition-protocol.md`](self-learning-m21-crypto-momentum-ignition-protocol.md).

## Next evidence checkpoint

Begin forward collection without backfill. Report coverage pauses and every plan state,
but do not retune from provisional outcomes. The next empirical decision is the single
preregistered 60-call/40-date first look, not a visually appealing early percentage.
