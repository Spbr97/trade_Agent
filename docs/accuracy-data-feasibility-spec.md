# Accuracy program Checkpoint 1 data-feasibility specification

Protocol: `accuracy-program-v1`

Status: frozen input specification; acquisition and outcome analysis have not begun

Canonical comparator: AEM v1, 149/693 strict wins (21.5007%), Wilson lower bound
18.6035%, mean net R -0.27471

## Purpose and boundary

Checkpoint 1 determines whether sufficiently complete, timestamp-causal data exist to
test genuinely new information. It does not build a signal, inspect a new outcome,
train a model, change the baseline, modify the dashboard, or alter production behavior.
Every unavailable field is reported as unavailable; it is never inferred as passing.

## Frozen cohorts

- Cohort A: 50 point-in-time liquid NSE cash `EQ` stocks for integrity and engine work.
- Cohort B: 200 point-in-time liquid NSE cash `EQ` stocks, stratified by registered
  liquidity and sector buckets.
- The cohort membership date must precede the evaluated session. Today's membership,
  delisted-stock deletion, and later liquidity rankings are prohibited substitutes.
- Expansion beyond Cohort B is deferred until its coverage and execution gates pass.

Checkpoint 1 must publish the exact symbols, membership effective intervals, selection
inputs, tie-breaking rule, exclusions, source versions, and file hashes. It must retain
new listings, suspensions, surveillance restrictions, circuits, insufficient history,
gaps, illiquidity, and unsupported symbols as explicit excluded coordinates.

## Required layers and causal timestamps

| Layer | Minimum fields | Availability timestamp |
|---|---|---|
| Stock prices | M1/M5/M15/D1 OHLCV, adjustment state, gaps | exchange bar close |
| Market context | NIFTY, BANK NIFTY, available sector-index intraday bars | bar close |
| Symbol master | listing/status, sector/industry, index membership intervals | effective/publication time |
| Cross-section | candidate-excluded breadth, rank and peer aggregates | latest completed peer bars |
| Announcements | exchange identifier, category, dissemination timestamp | official dissemination time |
| Corporate actions | action, ex/effective date, adjustment provenance | official publication time |
| Execution | charges, spread/depth or conservative proxy, participation | decision time |
| Provenance | source, retrieval time, version/hash, freshness | capture time |

Announcement text or category may be used only after official dissemination. A later
vendor timestamp, hindsight label, current sector, or adjusted bar without adjustment
provenance fails the layer. Peer statistics must exclude the candidate itself.

## Session partitions

- D0 is all previously opened evidence through 18 September 2026 and is permanently
  `consumed_historical_development`.
- D1 is newly acquired older or wider history and may be used for development only.
- D2 is a later, unopened chronological block selected and hashed before D1 research;
  it may be opened once for locked historical OOS evaluation.
- D3 consists only of predictions persisted before entry after a final candidate
  freeze; it is prospective shadow or paper evidence.

The manifest must use whole trading sessions. Overlapping outcome windows are purged
and adjacent periods embargoed when later folds are defined. Reconstructed old bars
never become prospective evidence.

## Coverage manifest

One row is required for every cohort, symbol, session, and layer coordinate, including
failures. Each row records expected/observed bars or documents, earliest causal
availability, first/last timestamp, freshness, adjustment state, source version/hash,
gap classification, exclusion reason, and whether the coordinate is eligible.
Coverage must be reported by layer, symbol, session, sector and liquidity bucket—not
only as one aggregate percentage.

The manifest and its schema are frozen before signal construction. Feature artifacts
must contain no outcome, target-hit, stop-hit, MFE, MAE, future-return, or resolved
status fields.

## Bounded acquisition

- Freeze request count, date windows and symbol batches before each acquisition run.
- Preserve every response hash, retry, gap and unconfirmed request attempt.
- Resume only from the immutable request ledger; do not silently redownload or replace.
- An enlarged budget requires a new versioned manifest with a written data reason.
- Credentials, broker sessions, orders, alerts and production schedules are out of
  scope. Public/licensed access and rate limits must be documented before collection.

## Pass/fail gates

Checkpoint 1 passes only if all of the following are demonstrated:

1. Cohorts A and B are reproducible point-in-time populations with no silent
   survivorship.
2. All required layer coordinates have a source/version and an explicit coverage or
   exclusion result.
3. Announcement availability, sector membership intervals, price adjustments and
   execution-cost timestamps are verified on sampled golden cases.
4. No future data or candidate-inclusive peer statistic survives mutation tests.
5. D2 boundaries and hashes are locked without opening its outcomes.
6. Coverage is adequate for at least one preregistered signal family under its own
   minimum sample and session rules.

Any unknown timestamp, unexplained gap, silent substitution, current-membership leak,
missing source version, or outcome column is a failure—not a pass. If no family is
feasible, the program stops and reports the missing information rather than searching
the existing OHLCV data again.

## Required Checkpoint 1 outputs

- versioned schema and point-in-time cohort files;
- immutable layer/source manifest and acquisition request ledger;
- complete coordinate coverage table and exclusion ledger;
- golden timestamp, adjustment, membership and execution tests;
- future-bar and candidate-exclusion mutation tests;
- unopened D2 boundary/hash declaration; and
- a signed pass/stop report naming which signal families, if any, may enter
  Checkpoint 2.

Nothing in this specification claims a predictive improvement. The canonical baseline
remains 21.5007% until later locked evidence meets the frozen qualification gates.
