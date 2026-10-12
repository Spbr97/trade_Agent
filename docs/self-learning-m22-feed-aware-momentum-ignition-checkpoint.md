# M22 Feed-Aware Momentum-Ignition Challenger — implementation checkpoint

Checkpoint date: 2026-10-12 IST
Authority: prospective research observation only; no call, order, sizing, promotion, or
baseline change

## What changed

M22 adds a separately versioned crypto-only challenger in
`src/tradedesk/momentum_ignition_watch_m22.py`. Its report version is
`momentum-ignition-watch-m22-v1`, its plan version is
`momentum-ignition-plan-m22-v1`, its frozen contract is
`momentum-ignition-m22-contract-v1`, and its independent artifacts live under
`data/m14_m18/momentum_ignition_watch_m22/crypto/`.

M22 does not revise M21. It inherits M21's feature calculations, signal gates, ranking,
top-three registration cap, trigger and outcome handling, target grid, after-cost
economics, evidence rules, and first-look qualification. The M21 implementation hash is
pinned in M22's activation contract, so an inherited-code change fails closed rather
than silently changing the experiment. The separate M22 plan namespace changes identity,
not signal behavior, and prevents an M21 plan from entering M22 evidence.

M22's only experimental change is to separate:

- exact 96-completed-bar feed coverage;
- the number and share of rows whose M21 features are computable; and
- activation-universe continuity.

The existing crypto collector supplies M20, M21, and M22 from one rate-limited raw
CoinDCX fetch, preserving fractional volume for the two momentum challengers. Each
program still writes to its own evidence root and denominator.
M22 additionally verifies every historical snapshot, plan, and qualification event
against its M22 namespace and activation boundary before using it.

## Why a new protocol was required

The non-outcome diagnostic that motivated M22 contained all 339 requested pairs with 96
fresh, continuous M15 bars. Only 213 rows were feature-valid. Of the other 126, 84 had
zero prior-three-bar volume, 40 had zero ATR14, and two had zero prior-20 true range.

Thus the raw feed was complete while 37.17% of listed pairs were temporarily too dormant
for M21's required ratios. Reinterpreting M21's existing 85% feature-coverage rule in
place would rewrite a frozen experiment. M21 therefore remains unchanged and continues
under its original root. M22 begins a clean prospective cohort with a new contract and no
backfill.

## Frozen M22 input gates

New M22 registrations require all of the following:

- requested universe count from 90% through 110% of the frozen activation count, with an
  absolute minimum of 300 and at least 90% activation-code overlap;
- at least 95% of requested pairs passing the exact contiguous 96-bar feed contract;
- at least 200 feature-valid rows; and
- feature-valid rows comprising at least 60% of feed-valid pairs.

For an activation universe of 339 pairs, this means 306–372 requested pairs, at least 306
activation-code overlaps, at least 323 feed-valid pairs, and—when all 339 feeds are
valid—at least 204 feature-valid rows. Zero-volume and zero-range observations remain
feed-valid but feature-invalid; they cannot receive a rank or plan.

Full definitions and failure states are frozen in
[`self-learning-m22-feed-aware-momentum-ignition-protocol.md`](self-learning-m22-feed-aware-momentum-ignition-protocol.md).

## Evidence status at protocol freeze

- production M22 snapshots: **0**
- production M22 registrations: **0**
- resolved M22 calls: **0**
- distinct M22 IST decision dates: **0**
- M22 strict accuracy, Wilson bound, and mean net R: **unavailable**
- tradeable calls: **0**
- baseline improvement: **NO**
- live authority: **NO**

The 339/339 feed-valid and 213/339 feature-valid counts are pre-activation input
diagnostics only. They are not registered calls, outcomes, training rows, or proof that
M22 improves M21.

## Next evidence checkpoint

Run the first production collector after this protocol and activation contract are
durable. Verify the manifest and separate hash chains, report feed and feature gates, and
allow only future M22 registrations. Do not backfill earlier candidates or tune any M21
signal/economics threshold from provisional M22 results.

The first empirical qualification decision remains the single inherited 60-resolved-call,
40-IST-date look. Until that gate and M22-specific controls mature, the only accurate
status is `collecting` or the applicable fail-closed input state.

## First post-freeze production verification

The first contract-valid production collection completed at
`2026-10-12T22:32:52.002663+05:30` after commit `e074618` was durable on `origin/main`.
The dashboard loader verified the manifest and independent snapshot/plan chains. Exact
readiness state:

- requested pairs: **339**;
- activation overlap: **339/339**, manifest gate **PASS**;
- feed-valid pairs: **339/339 (100%)**, feed gate **PASS**;
- feature-valid pairs: **214/339 (63.13%)**, feature gate **PASS**;
- feature-invalid pairs: **125** — 94 zero prior-three-bar volume, 28 zero ATR14,
  and three zero prior-20 true range;
- feed-invalid pairs: **0**;
- registration candidates: **1**; and
- newly registered plans: **1**.

The registered plan is an OSMO/INR breakout-ignition research watch. It was armed at the
same observation time with trigger `4.603599`, structural stop `3.992004`, adaptive target
`5.06229525`, target multiple `0.75R`, estimated target net `0.619715R`, and estimated
break-even win rate `63.99%`. It is `pending_trigger`, not a tradeable call: a causally
later completed M15 close-plus-volume confirmation is still required.

Resolved M22 calls remain **0**; strict accuracy, Wilson lower bound, and mean net R remain
**unavailable**; baseline improvement and live eligibility remain **false**. The one active
watch therefore proves that the feed-aware gate can expose a qualifying setup without
weakening M21's signal/economics rules, but it does not prove accuracy or profitability.

One automatic pre-release snapshot had been written before the M22 plan namespace was
finalized. It contained zero plans and zero outcomes, failed the final activation-contract
check, and was moved intact to
`data/m14_m18/momentum_ignition_watch_m22_pre_release_invalid_20261012_184726/`.
It is excluded from the production M22 root and denominator rather than rewritten or
backfilled.
