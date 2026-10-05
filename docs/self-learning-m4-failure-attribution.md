# Self-learning Milestone 4 — failure attribution

Completed: 2026-10-06

## Outcome

Every valid mature failed call can now receive deterministic, evidence-labelled failure
attributions. The engine separates:

- `verified_path`: directly observed entry/exit/path events;
- `verified_calibration_miss`: a failed call issued with at least 70% probability;
- `diagnostic_association`: a reproducible association that is not causal proof; and
- `insufficient_diagnostics`: the record lacks enough frozen/path data for a narrower
  explanation.

Invalid or unavailable rows receive a `data_quality_failure` system diagnostic but remain
outside valid resolved-call counts, accuracy, attribution frequencies, and learning.

## Implemented attributions

- false breakout;
- late or overextended entry;
- sector weakness;
- insufficient volume confirmation;
- excessive volatility;
- poor liquidity or excessive slippage;
- gap through stop;
- stop too tight for the observed path;
- target too ambitious;
- holding period too short or too long;
- model overconfidence; and
- setup-specific weakness from aggregate mature evidence.

Market-regime reversal and relative-strength deterioration require outcome-time market
snapshots that the current ledger does not yet capture. The report lists these as
unavailable diagnostics. It does not infer either one from a losing outcome.

## Aggregation

For each observed category the report includes frequency, share of failures, recent versus
previous-window trend, independent-session recurrence, evidence levels, mean gross/net/
after-tax R, and setup/market/sector/regime/confidence breakdowns.

Because these categories are assigned after a failure is known, their category-specific
strict accuracy is zero by definition and its Wilson interval is descriptive, not a
predictive estimate. Setup performance is reported separately across successes and
failures with a genuine strict accuracy and Wilson interval.

## Dashboard and persistence

- Newly resolved tracker rows persist their attributions outside the immutable prediction
  seal.
- Historical rows are attributed dynamically for display without rewriting their files.
- Learning > Predictions now shows resolved counts, valid failures, invalid rows excluded,
  the most frequent pattern, recurrence, evidence level and gross/net R by category.
- `/api/failure-attribution?market=nse|bse|crypto` keeps markets separate and uses live-only
  evidence for crypto/BSE, matching the existing performance policy.

## Verification gate

Tests cover verified versus associative labels, invalid exclusion, recurrence, Wilson
statistics, R summaries, setup performance, success/pending exclusion, resolver
integration, legacy fallback and the dashboard endpoint.

The next milestone is the deterministic learning-dataset builder. It will include valid
mature successes and failures (including counterfactual rejected/shadow evidence), exclude
invalid/pending/never-triggered rows, and enforce sealed prediction-time features only.
