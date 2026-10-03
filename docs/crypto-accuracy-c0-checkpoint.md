# Crypto accuracy Checkpoint C0 — independent baseline

Status: **implemented; no accuracy improvement claimed**
Protocol: `crypto-accuracy-program-v1`

C0 creates a crypto-only, source-separated scoreboard. It does not alter the scanner or
any existing call. The 4 October 2026 snapshot contains:

| Evidence | Calls | Resolved | Wins | Observed accuracy | Use |
|---|---:|---:|---:|---:|---|
| Forward agent runs | 54 | 47 | 5 | 10.64% | prospective monitoring baseline |
| Historical backfill | 461 | 461 | 68 | 14.75% | development only |

These rows are deliberately not pooled. The forward baseline covers calls evaluated by the
running agent, including rejected and shadow calls, matching the dashboard convention. The
historical rows can support development but cannot establish prospective performance.

The runtime artifact is `data/m14_m18/crypto_accuracy_program/state.json`. It pins the
crypto call-log and universe-report hashes, reports per-setup metrics, and keeps live
eligibility false. Gross R is descriptive only at this checkpoint; after-cost and
reporting-only after-tax economics become mandatory in C1.

No accuracy improvement is established by C0. Its purpose is to make every later crypto
claim measurable against an honest, independent starting point.
