# Self-learning Milestone 5 — deterministic learning dataset

Completed: 2026-10-06

## Outcome

`self-learning-dataset-v1` builds deterministic, market-specific datasets exclusively from
sealed predictions with valid mature outcomes. It does not train or promote a model.

Eligible rows include valid mature successes and failures from qualified, rejected and
shadow calls. Rejected/shadow rows are explicitly labelled `counterfactual`; they are not
silently presented as recommendations.

## Hard exclusions

Every excluded row receives a counted reason:

- missing or duplicate signal ID;
- NSE/BSE/crypto market mismatch;
- legacy or otherwise unsealed prediction;
- prediction, source-snapshot, contract or mirror-field integrity failure;
- pending, invalid, never-triggered or unusable outcome; and
- backfill in a prospective dataset.

This means the current historical logs do not become training evidence merely because they
can be displayed. They predate the immutable ledger and remain `legacy_unsealed`. The
prospective learning dataset will begin accumulating only from newly sealed calls.

## Dataset content

Each accepted row records market, symbol, setup, sector, regime, contract, evidence class,
cohort, sealed prediction hash, original prediction-time numeric features, valid outcome,
gross/net/after-tax R, holding time and failure attributions.

Features are flattened only from the sealed prediction payload. Exit price, outcome,
label, future candles and outcome-time diagnostics cannot enter the feature vector.

Rows are sorted deterministically, and the dataset ID hashes the version, market, purpose,
prediction hashes and mature outcomes. Input ordering cannot change the result.

## Cohorts and reuse control

Every dataset is explicitly one of `development`, `locked_test` or `prospective`.
An append-only evidence-use registry records dataset hash, market, purpose and signal IDs.
Evidence registered as locked test cannot later appear in another locked or development
dataset, preventing inspected locked outcomes from being recycled as fresh evidence.

## Dashboard

Learning > Predictions now shows, per market:

- sealed rows eligible for prospective learning;
- source records checked;
- total excluded rows and exact reasons; and
- dataset contract version.

Zero eligible rows is an honest collection state, not a pass and not a training failure.

## Verification gate

Tests cover deterministic hashes/order, prediction-only features, mature success/failure,
rejected/shadow counterfactual evidence, invalid/pending/never-triggered exclusion,
backfill exclusion, market isolation, duplicate handling, locked-outcome reuse refusal and
dashboard exposure.

The next implementation stage is the full performance/self-learning dashboard checkpoint,
followed by the scheduled challenger-training workflow. The active model remains frozen.
