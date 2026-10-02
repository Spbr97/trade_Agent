# Accuracy recovery milestone 3 — frozen challenger-race readiness

Date: 3 October 2026

Status: **race protocol implemented and tested; current cohort blocked before fitting; no
accuracy improvement claimed**

## Frozen race

The new `accuracy-race-v1` protocol compares exactly three learning paths on identical
purged chronological folds:

1. the transparent rule score;
2. regularized logistic regression; and
3. histogram gradient-boosted trees from scikit-learn.

The final 20% chronological tail is locked. It is opened only if a development candidate
clears every Milestone-2 accuracy, Wilson-confidence, session-coverage, sample-size and
economics gate. No qualifier means abstention; a readiness failure means no model is fitted.

## Real readiness run

The current `data/reports/ml_dataset_current.csv` cohort was fingerprinted as
`85c0ea02eca3006338838a4fd05e60778fff0e2b93b2d0420d6c7b23f70589a0`.

- 29,964 rows across 751 sessions, 1 September 2023 through 10 September 2026;
- both outcome classes are present;
- 13 current v4 decision-time features are absent;
- realized economics are complete for only 612/29,964 rows (2.04%);
- transparent rule score coverage is 0/29,964; and
- the locked tail remains unopened.

Decision: **BLOCKED BEFORE FITTING**. Training on this artifact would compare different
feature contracts and allow missing economics to masquerade as evidence. No candidate and
no new baseline are reported.

## Next gate

Rebuild one v4 cohort from the causal source data, populate the frozen transparent score,
and produce same-contract after-cost R for every evaluated row. Re-run the unchanged race
against that new fingerprint. Only a development qualifier may open the locked tail and
only a locked pass may become a shadow nominee.
