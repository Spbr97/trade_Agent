# Crypto accuracy C1 — all-pair dataset and label protocol

Protocol: `crypto-accuracy-dataset-v1`

Status: **implementation frozen and first real materialization verified; collecting;
research-only**

This protocol creates the point-in-time crypto population required before testing an
anticipatory mechanism. It is independent of NSE and BSE, does not upgrade the live agent,
and does not convert historical or implementation evidence into an accuracy result.

## Exact universe history

- Every observation contains the exact sorted IDs of all currently supported active
  CoinDCX INR pairs, not only a count or a fixed sample.
- Observations are append-only, strictly chronological and hash-chained. Each event also
  records explicitly known inactive IDs plus additions and removals since the prior event.
- The first observation activates membership history. Membership before that timestamp is
  `unknown_not_inferred`; stored candles never manufacture historical active membership.
- A pair/session is point-in-time eligible only when the most recent observation available
  by that daily bar's close says the pair was active.
- Current and previously observed pairs remain represented so missing history, stale
  trailing data, invalid OHLCV, zero volume and delisting/removal transitions stay visible.

The current universe report must carry the same exact active-code set as the latest
append-only observation. A count-only report or a disagreement fails closed.

## Closed source and missingness contract

- Only daily bars whose full 24-hour interval ended by the aware `as_of` cutoff enter the
  frozen source. The forming bar and future bars are excluded.
- OHLC prices must be finite and positive; high must contain open, low and close; low must
  contain open, high and close; volume cannot be negative.
- Coverage is reported for every observed pair. No history, internal absent daily sessions,
  invalid OHLCV and stale trailing sessions are explicit artifacts or counters, never
  silently filled.
- CoinDCX's known duplicate IST-day filler rows are removed by keeping the later bar, the
  same rule used by the established backtester. Every discarded row remains in a hashed
  audit artifact and per-pair counter, while only the canonical daily source feeds ATR,
  labels and the source hash.
- The canonical closed-candle source is deterministically hashed. Changing a forming,
  future or discarded filler bar cannot change that hash; changing any retained frozen
  source bar fails later verification.

## Frozen quick-profit geometries

All geometries use causal Wilder ATR(14) known at the decision close. ATR must have 14
valid, consecutive calendar sessions and resets after a gap or invalid row. The C1 registry
is:

| Geometry | Stop distance | Target | Maximum hold |
|---|---:|---:|---:|
| `quick_075atr_050r_3d` | 0.75 ATR | 0.50R | 3 daily sessions |
| `quick_100atr_075r_5d` | 1.00 ATR | 0.75R | 5 daily sessions |
| `quick_125atr_100r_7d` | 1.25 ATR | 1.00R | 7 daily sessions |

These are preregistered label geometries, not three discovered strategies. C2 may compare
mechanisms on them but may not rewrite them after seeing results.

## Entry, exit and label rules

- Entry is the next calendar daily open after the decision bar closes, with configured buy
  slippage. A missing next bar is an explicit exclusion.
- Position size uses a fixed Rs 10,000 notional and permits fractional coin quantity.
- An adverse open through the stop exits at that open with sell slippage.
- A favorable open through the target is known to occur before later intrabar prices and is
  conservatively capped at the target, with sell slippage.
- When one OHLC bar touches both levels without a decisive opening gap, the stop wins.
- A three-session hold evaluates the entry session plus the next two sessions (and
  equivalently for five and seven); it never consumes an extra bar. A trade still open at
  its frozen horizon exits at that final bar's close. Missing or invalid bars anywhere in
  the outcome window exclude the label rather than shorten it.
- Gap exits record the bar's open timestamp; intrabar outcomes record the bar close because
  daily OHLC cannot establish an earlier exact time.
- Success means the target was reached and modeled after-cost P&L remained positive.

The label artifact preserves decision and entry timestamps, eligibility/status, exclusion
reason, entry/stop/target, outcome/exit, binary label and all economic R fields.

## Economics

Every resolved row reports three separate quantities:

- `gross_r`: price outcome relative to initial risk after the frozen fill rules;
- `net_r`: gross result after CoinDCX maker/taker fees, GST on those fees, configured
  slippage and sell-side Section 194S TDS; and
- `after_tax_r`: a reporting-only Indian VDA view that credits TDS as prepayment and applies
  30% tax to positive gross gains without offsetting losses.

The after-tax field is a conservative research report, not tax advice and not a live
eligibility calculation. Gross, after-cost and after-tax values must never be substituted
for one another.

## Frozen artifacts and authority

Each run atomically publishes a compact manifest plus Parquet artifacts for canonical
closed daily rows, discarded duplicate rows, explicit missing sessions and labels. A
partial interrupted build cannot become the latest run. The manifest pins the universe
event, source-candle hash, geometry/label contract, crypto configuration and artifact
hashes. Verification checks the append-only universe chain, every artifact and the exact
canonical frozen candles. A missing, stale or failed tracker universe-event hash blocks
materialization.

`eligible_for_live` remains false for the manifest and every label. This checkpoint does
not train or select a model, measure an accuracy improvement, alter calls or grant alert,
management, sizing, broker or order authority.

The first accepted freeze is `2026-10-03-095a9bcdc03d-f991d060-394b1f39`. It contains 339
observed-active pairs, 355,278 raw closed rows and 354,402 canonical daily rows after
removing 876 duplicate IST-day fillers; source verification passed. All pairs remain
visible for coverage, while the two configured stablecoin exclusions are explicitly
ineligible for labels.

Three exact universe observations now cover one point-in-time session. The minimum is one
session per required pair, 0 of 337 required pairs meet the 30-session gate, and none of the
1,011 geometry rows is resolved: 1,008 lack the next daily entry and three lack a contiguous
ATR lookback. These are excluded/collecting, not failed, passed or an accuracy result.

The next checkpoint is C2: test the preregistered anticipatory mechanism families and
quick-profit geometries against inverse, shuffled, random-coin and random-timing controls,
using only eligible C1 rows. C2 cannot report a result until enough post-activation C1
sessions exist and its evidence gates can be evaluated.
