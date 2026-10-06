# Self-learning Milestone 9 checkpoint — validation hardening

Completed: 2026-10-06

Every contract-specific challenger now uses a chronological development/locked-tail split.
Before fitting, development rows are removed when their recorded exit date reaches or
crosses the first locked-test session. Rows without a verifiable exit date are also removed.
If fewer than five development rows remain, the experiment is blocked rather than relaxing
the purge.

The one-use locked signal IDs are registered durably. Later challengers exclude them before
choosing a new tail, so an inspected result cannot be presented again as fresh evidence.

## Stored validation views

The frozen baseline and challenger each report prediction accuracy, strict outcome rate,
Wilson lower bound, Brier score and fixed confidence calibration buckets. Challenger
consistency is reported by:

- month;
- sector;
- market regime;
- liquidity band;
- individual session.

The experiment also retains its deterministic 1,000-repeat matched random-selection
control. Matched random timing remains unavailable and is a hard blocker, because it needs
a separately frozen symbol universe, candle cohort, entry geometry and cost contract.

## Evidence ladder

The report labels evidence without upgrading authority:

- below 50 resolved calls: collecting below the diagnostic floor;
- 50–99 calls, or fewer than 30 sessions: diagnostic only;
- 100–249 calls across at least 30 sessions: first review;
- 250–499 calls, or fewer than 100 locked out-of-sample calls: stability review;
- at least 500 calls and 100 locked out-of-sample calls: production sample threshold reached.

Reaching a sample threshold is not a performance pass. Final gate status remains false
until accuracy, Wilson uncertainty, net and reporting-only after-tax expectancy, matched
random timing, session consistency and a registered prospective cohort all pass together.
No threshold is relaxed when evidence is thin.

## Current evidence

There are still zero mature sealed outcomes in the live NSE, BSE and crypto learning
datasets. This checkpoint prevents optimistic validation errors; it does not claim a higher
baseline percentage.
