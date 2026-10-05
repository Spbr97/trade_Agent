# Self-learning Milestone 2 — immutable prediction ledger

Completed: 2026-10-06

## Outcome

Every newly logged NSE, BSE, or crypto candidate now carries a canonical
`prediction-ledger-v1` payload sealed with SHA-256 before its outcome is known. The
operational JSONL remains a materialized tracker view, but prediction-time facts inside a
sealed row cannot be rewritten without detection.

Historical rows remain explicitly `legacy-unsealed-v0`. They are retained and visible,
but are not retroactively claimed to have guarantees that did not exist when they were
created.

## Sealed prediction content

The immutable payload records:

- signal ID, creation time, source, market, instrument, sector and setup;
- evidence class and frozen outcome-contract identity/hash;
- entry range, stop, both targets, entry validity, chase rule and intended holding time;
- original grade, rule score, calibrated probability, alertability and every rejection;
- market regime, sector availability, relative strength, ATR, turnover, score components,
  signal geometry and the exact numeric feature row visible at decision time;
- quantity, risk, position value, size caps, estimated round-trip costs and net R:R;
- configured slippage plus the complete fee/tax schedule used for that market;
- strategy, feature-contract, model and model-kind versions; and
- a separately hashed source snapshot.

The current scanner has no sector-regime classifier. The payload records sector identity
and percentile when available and stores sector regime as explicit `null`; it never fills
that field later using future information.

## Integrity rules

- Entry, stop, targets, confidence, grade, evidence class, rejection reasons, market,
  contract or version changes fail closed.
- Editing the nested payload, source snapshot, or contract definition fails its hash.
- Outcome, label, exit price, R multiple and resolution time remain outside the prediction
  seal and may be appended by the resolver.
- Repeated scans do not duplicate an existing signal.
- Duplicate signal IDs already present in a ledger fail closed instead of being collapsed.
- The complete collection is validated before saving.
- Saving writes a temporary file and atomically replaces the previous JSONL only after
  every row passes validation, so one bad row cannot truncate the last good ledger.
- Market-specific filenames and stored market values must continue to agree.

## Dashboard

Call tables and the Learning > Predictions panel now identify rows as `sealed` or
`legacy`. The call-summary API reports sealed and legacy-unsealed counts separately.
Model version is shown for sealed predictions; `none` is displayed when no current model
scored the candidate.

## Verification gate

Automated coverage verifies:

- full payload creation from a real synthetic-market watchlist;
- contract, source-snapshot and prediction hashes;
- model/feature version propagation from the actual scorer;
- outcome append without prediction-hash change;
- refusal of top-level prediction mutation and nested payload tampering;
- preservation of the last good file after failed validation;
- duplicate detection, idempotent repeat logging and legacy compatibility; and
- dashboard ledger counts and display fields.

The next milestone is the deterministic outcome resolver. It must activate quick-profit
and swing contracts only when it can enforce their exact rules and calculate after-cost
results reproducibly.
