# Crypto accuracy C1 — prospective same-coin timing control

Status: **implemented and activated; collecting; no accuracy improvement claimed**

Crypto now receives the same class of timing scrutiny as NSE through a separate market-
specific implementation. Activation froze all 54 existing live-tagged crypto calls as
prior evidence. None can be backfilled into the new control.

Future crypto calls are registered after the established tracker saves them and before the
control's first possible entry. Each setup receives 1,000 deterministic same-coin timing
cohorts over offsets 1–20. The model/control comparison uses only fully closed crypto daily
candles, causal ATR, the call's frozen geometry, crypto slippage and CoinDCX fee/GST/TDS.
Evidence remains separate per setup.

The dashboard reports activation, future registered calls, independently monitored setups,
qualified setups and source integrity. The API omits the large assignment and timing
payloads.

Initial state: zero post-activation calls, zero monitored setups, zero qualified setups and
zero integrity errors. `collecting_insufficient_evidence` is not a pass. The checkpoint
cannot improve the historical baseline by itself; it creates honest forward evidence for
future crypto improvements.

See the [frozen protocol](crypto-accuracy-timing-c1-protocol.md).
