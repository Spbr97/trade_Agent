# Crypto accuracy C1 — prospective same-coin timing protocol

Protocol: `crypto-accuracy-timing-v1`

This checkpoint gives crypto its own forward-only timing control. It does not reuse NSE
assignments, outcomes, costs, thresholds or qualification and does not count crypto's
historical backfill.

## Activation and evidence separation

- Activation freezes the IDs of every existing `source=live` crypto call.
- Existing calls and all `source=backfill` rows are permanently excluded.
- Only calls logged after activation with no outcome at registration may enter evidence.
- Each setup is scored independently. Calls from different setup mechanisms are never
  pooled to reach a sample, accuracy, confidence or random-advantage gate.
- Signal definitions and timing assignments are hash-checked on every run.

## Timing contract

- Seed: `20261006`; cohorts: 1,000.
- Model timing: the first complete crypto daily-bar entry whose open occurs after the call
  was registered. This avoids pretending that the scheduled tracker could fill at an open
  that occurred earlier that day.
- Random timing: the same coin at offsets 1–20 later complete daily sessions.
- Geometry: each call freezes its original stop distance in ATR units and its T1 reward/R.
  Every model/control timing recalculates causal Wilder ATR(14) immediately before entry.
- Execution: next daily open plus crypto slippage, original stop-ATR and target-R,
  ten-session maximum hold, stop-wins-ties, gap-aware exits and a fixed Rs 10,000 notional
  permitting fractional coin quantity.
- Economics: CoinDCX fee, GST and the configured 1% sell-side TDS are included in net R.
  Indian VDA after-tax economics remain a separate reporting task and are not silently
  treated as implemented.
- Resolved input candles are hashed and verified on later runs.

The longest comparison needs the 20-session offset, its entry, and ten outcome sessions;
therefore fully paired evidence can take roughly 31 crypto daily sessions to mature.

## Setup-level gate

A setup passes this timing control only with:

- at least 100 completely paired calls;
- at least 30 active signal sessions;
- model-timing mean net R at least +0.10R above same-coin random timing; and
- one-sided empirical p-value no greater than 0.05 with finite-sample correction.

This timing pass is only one control. Crypto still needs random-coin selection, mechanism,
locked historical, accuracy/Wilson, prospective, stress and after-tax reporting evidence.
Live, alert, sizing, management, broker and order authority remain false.
