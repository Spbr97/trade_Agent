# Self-learning M22 — frozen feed-aware momentum-ignition protocol

Protocol frozen: 2026-10-12 IST, before the first production M22 snapshot, plan, or
outcome was collected.

Status: preregistered, forward-only crypto challenger. M22 is a research observer only.
It cannot authorize an alert, personal call, order, sizing decision, model promotion, or
baseline-improvement claim.

Implementation identifiers:

- report version: `momentum-ignition-watch-m22-v1`;
- plan version: `momentum-ignition-plan-m22-v1`;
- contract version: `momentum-ignition-m22-contract-v1`;
- implementation: `src/tradedesk/momentum_ignition_watch_m22.py`; and
- independent evidence root:
  `data/m14_m18/momentum_ignition_watch_m22/crypto/`.

## Purpose and single experimental change

M21's first complete raw-volume diagnostic showed two different facts that its single
coverage ratio could not distinguish. All 339 requested active CoinDCX INR pairs had a
fresh, continuous 96-bar M15 feed, but only 213 pairs had every strictly positive
denominator required by the M21 feature vector. The remaining 126 rows were dormant or
price-static at that decision time: 84 had zero prior-three-bar volume, 40 had zero
ATR14, and two had zero prior-20 true range.

Those counts are input diagnostics, not outcomes. They do not show that M22 is accurate
or profitable. M22 preregisters one change before observing any M22 result: complete
market-data coverage and feature-valid ranking breadth are measured by separate gates.
A genuine zero-volume or zero-range bar may be feed-valid while remaining feature-invalid
and ineligible for a percentile, rank, or plan.

M21 remains frozen under
[`self-learning-m21-crypto-momentum-ignition-protocol.md`](self-learning-m21-crypto-momentum-ignition-protocol.md).
Its snapshots, plans, qualification events, thresholds, and denominator are not edited,
relabelled, or copied into M22.

## Scope, source, and activation manifest

- M22 runs only for crypto. NSE and BSE return `not_applicable` and cannot contribute
  evidence.
- Each snapshot requests the complete currently materialized set of active CoinDCX INR
  pairs. Daily-ranker availability is optional metadata and cannot remove a pair.
- M22 uses raw fractional CoinDCX base-asset volume from the same rate-limited M15 fetch
  shared operationally with M20 and M21. Sharing an input fetch never pools evidence.
- The first collection atomically freezes `activation.json`, including the sorted code
  list, activation count, complete M22 contract, contract hash, and manifest hash.
- The contract pins the SHA-256 of `momentum_ignition_watch.py`. A later change to the
  inherited M21 implementation causes an activation-contract mismatch and fails closed;
  it cannot silently alter an active M22 cohort.

Let `A` be the frozen activation count, `R` the number of unique currently requested
pairs, and `O` the number of requested codes also present in the activation manifest.
New registration is open only when all activation-universe conditions hold:

```text
R >= max(300, ceil(0.90 * A))
R <= floor(1.10 * A)
O / A >= 0.90
```

For an activation count of 339, the allowed requested-count interval is 306 through 372,
and at least 306 activation codes must overlap. Failure is
`activation_universe_drift`, never a reduced denominator or an implied pass.

## Frozen feed-valid contract

For observation time `T`, M22 examines the last 96 completed M15 bars for each requested
pair. The expected final bar opens at `floor_15_minutes(T) - 15 minutes`; the expected
index is that bar and its 95 immediately preceding M15 slots.

A pair is feed-valid only when every condition below holds:

1. the normalized frame contains the five columns `open`, `high`, `low`, `close`, and
   `volume`;
2. its timestamps are timezone-aware, unique, and exactly equal to the expected
   contiguous 96-slot UTC index;
3. all OHLCV values are finite;
4. every OHLC value is strictly positive;
5. `low <= open <= high` and `low <= close <= high` on every bar; and
6. raw fractional volume is non-negative.

Zero volume and a zero high-low range are permitted at this layer because they can be
valid observations of inactivity. Missing bars, a stale or misaligned window, duplicate
timestamps visible to M22, non-finite values, invalid geometry, or negative volume make
the pair feed-invalid.

Let `F` be the number of feed-valid pairs. The frozen feed gate is:

```text
feed_coverage = F / R >= 0.95
```

For 339 requested pairs, 323 are required. Feed-invalid rows remain visible by exact
reason and cannot enter feature calculation or ranking.

## Frozen feature-valid and breadth contract

M22 applies M21's unchanged causal feature function to each feed-valid frame. Every M21
denominator remains finite and strictly positive. The deterministic primary reason for a
feature-invalid row is the first applicable item in this order:

1. `nonpositive_atr14`;
2. `nonpositive_prior_tr20`;
3. `nonpositive_prior_volume20`;
4. `nonpositive_prior_volume3`;
5. `nonpositive_vwap24_volume`;
6. `nonpositive_previous_vwap24_volume`; or
7. `nonfinite_required_feature`.

Let `V` be the number of feature-valid rows. Both frozen breadth gates must pass:

```text
V >= 200
feature_breadth = V / F >= 0.60
```

With 339 feed-valid pairs, at least 204 feature-valid rows are required. The observed
pre-activation diagnostic of 213 therefore clears this input-breadth boundary, but it is
not an M22 performance result. Feature-invalid rows receive no percentile, score, rank,
or registration. Directionless stablecoin/INR pairs remain visible in diagnostics but
retain M21's prohibition on directional plans.

The activation, feed, and feature gates must all be open before any new M22 plan may be
registered. A failed snapshot records its blockers and candidates as observation-only.
Existing valid plans may still be evaluated from causally later valid bars.

## Inherited M21 signal and economics contract

For the feature-valid cross-section, M22 calls the frozen M21 implementation. It makes no
change to:

- completed-M15 information boundaries or the 32-bar feature definitions;
- breakout-ignition and shakeout-reclaim requirements;
- cross-sectional percentile calculation, momentum-score weights, and deterministic
  ordering;
- the top-12 display-only limit or maximum three new registrations per snapshot;
- one-active-plan, four-bar cooldown, two-plans-per-IST-date, reclaim, expiry, and timeout
  limits;
- trigger, structural stop, later close-plus-volume confirmation, gap handling, and
  conservative stop-first ambiguity handling;
- the target grid `0.75R, 1R, 1.25R, 1.5R, 2R, 2.5R, 3R`;
- the 70% maximum implied break-even rate and actual-entry economics recheck;
- configured CoinDCX fee, GST, TDS, slippage, quantity, capital, and risk assumptions; or
- strict-success, net-R, integrity, first-look, control, and authority rules.

The exact inherited definitions are normative in the frozen M21 protocol. M22's
`momentum-ignition-m22-contract-v1` changes only the coverage classification and adds the
activation and breadth guards above.

## Separate prospective evidence and first look

M22 starts a new cohort at its first production M22 registration after the activation
manifest exists. There is no backfill. M20 history, M21 snapshots and plans, inspected
Sonic/LINK paths, and pre-activation diagnostics cannot enter the M22 denominator.

M22 writes its own `activation.json`, `latest.json`, `snapshots.jsonl`, `plans.jsonl`, and
`qualification.jsonl` beneath its independent root. Snapshot, plan, and qualification
chains use the existing canonical integrity checks. Every snapshot must carry the M22
report namespace and activation hash; every plan must carry the M22 plan namespace, a
recomputable M22 plan identity, and an `armed_at` time no earlier than activation; and
every qualification event is bound to the M22 report namespace and activation hash.
Valid-but-misplaced M20/M21 events and pre-activation M22 plans therefore fail closed
instead of entering the M22 denominator.

There is no interim statistical or futility decision. The one core first look occurs at
the first observation with at least 60 resolved M22 calls across at least 40 distinct IST
decision dates. It inherits every M21 requirement:

- strict success rate at least 70%;
- 95% Wilson lower bound at least 55%;
- positive mean after-cost net R;
- zero resolved rows with missing or non-finite net R;
- maximum chronological losing streak no greater than six; and
- zero timing, hash-chain, market-mixing, duplicate-denominator, or contract-integrity
  violations.

A failed core first look is terminal for this version. A core pass remains
`controls_pending` until independent M22-specific matched-random accuracy and net-R
controls both have upper-tail probability no greater than 0.05. M21 controls cannot be
borrowed. Unavailable evidence or controls never pass.

## Abort and authority boundary

New registration pauses on activation drift, feed coverage below 95%, fewer than 200
feature-valid rows, feature breadth below 60%, malformed or non-completed bars, any
ledger/manifest/contract integrity failure, or non-finite required economics. None of
these states becomes a performance success or failure.

Every M22 artifact must retain `tradeable_calls = 0`, `eligible_for_live = false`,
`baseline_accuracy_improved = false`, and research-only authority. A dashboard may show
M21 and M22 side by side, but it must label watches as research, preserve separate
denominators, and never claim that implementation, feed availability, or an early
percentage proves improved accuracy.
