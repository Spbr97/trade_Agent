# Self-learning Milestone 18-A — frozen selection-readiness protocol

Protocol frozen: 2026-10-11T01:13:11+05:30, before any M18 after-cost label was
generated, any M18 dataset statistic was opened or any M18 selector was trained.

Status: preregistered dataset/readiness protocol. M18-A cannot select a model, register a
forward observer, change an active model, authorize a signal or establish improved
baseline accuracy.

## Objective

M17 showed that changing intraday entry/exit geometry alone did not produce a reliable,
positive-after-cost contract. M18 changes selection instead: build a market-specific,
causal dataset that can train a later high-precision abstaining selector to distinguish
the subset of fixed-contract candidates that resolve successfully and profitably.

M18-A asks only whether the existing sealed M17 candidate paths contain enough valid,
chronologically distributed positive and negative examples to begin M18-B development.
It does not search features, models, thresholds, targets, stops or holding periods.

## Frozen source population

- NSE, BSE and crypto remain separate. Pooling is forbidden.
- Use the exact M17 acquisition manifest already sealed for each market. No new candidate,
  replacement instrument or availability-based fallback may enter M18-A.
- Deduplicate by `(market, decision session, scrip code)` while preserving every sealed
  M17 role as provenance.
- Join only the 18 causal `leader-causal-features-v1` values frozen for the same decision
  session and instrument in M17's content-addressed causal source.
- Exclude roles, ranker score and M17 result fields from model features. They remain audit
  metadata only.
- Load the exact M17 path contract, including the committed crypto M1-authoritative
  amendment. An invalid/unavailable path receives null labels and stays in coverage.
- M17 validation and diagnostic evidence is consumed historical development evidence.
  Repartitioning it for M18 cannot make it fresh or independent proof.

## Frozen execution label

Use exactly one contract for every market and candidate:

- entry: next observed M1 open plus configured buy slippage;
- stop: the unchanged M17 `-1R` risk distance;
- target: `+0.75R`;
- timeout: final M1 close in the sealed next-session path;
- costs: the existing market-specific M17 fee, tax and slippage model;
- ambiguous same-minute stop/target ordering: stop first.

This is M17's predefined direct execution anchor, not a new exit race. M18-A does not
compare it with another geometry.

For a valid resolved path persist:

- `strict_success`: target or gap-target before stop/timeout;
- `net_r`: configured after-cost R multiple;
- `positive_after_cost`: `net_r > 0`;
- `precision_label`: `strict_success AND positive_after_cost`.

Invalid, unavailable, unresolvable or non-finite rows keep all four labels null and never
enter a model-performance denominator.

## Frozen chronological partition

Sort the 72 distinct M17 decision sessions ascending and require exactly:

1. first 42 sessions: `development_train`;
2. next 3 sessions: `purge_1`;
3. next 12 sessions: `calibration`;
4. next 3 sessions: `purge_2`;
5. final 12 sessions: `internal_diagnostic`.

Purge rows are retained in the artifact with null model-use status and cannot train,
calibrate or evaluate. The internal diagnostic is a consumed development diagnostic. It
cannot qualify M18 or improve the baseline; fresh prospective evidence is mandatory.

## Frozen feature contract

The only model-eligible columns are:

`return_1`, `return_3`, `return_5`, `return_20`, `gap_return`, `range_pct`,
`close_location`, `volume_ratio_20`, `turnover_ratio_20`, `distance_sma_20`,
`distance_sma_50`, `distance_prior_high_20`, `atr_14_pct`, `avg_turnover_20`,
`return_5_rank`, `return_20_rank`, `volume_ratio_20_rank` and
`distance_prior_high_20_rank`.

Every value must be finite. Feature imputation, scaling, selection and model fitting belong
to M18-B and are forbidden in M18-A.

## Frozen readiness gates

Each non-purge block must independently have at least 85% valid resolved-path coverage.
In addition:

| Block | Minimum sessions with resolved rows | Minimum resolved rows | Minimum positive labels | Minimum negative labels |
| --- | ---: | ---: | ---: | ---: |
| Development train | 36/42 | 300 | 40 | 40 |
| Calibration | 10/12 | 80 | 12 | 12 |
| Internal diagnostic | 10/12 | 80 | 12 | 12 |

All 18 feature columns must be present and finite for every model-eligible resolved row.
Missing evidence is `not_ready`, never a pass. A market is `ready_for_development` only if
every gate passes. Readiness authorizes M18-B research for that market only.

## M18-B boundary fixed now

If a market is ready, M18-B may compare a finite preregistered set of calibrated,
market-specific selectors using development train only, select abstention on calibration
only and open the internal diagnostic exactly once. It must compare with the unfiltered
anchor, momentum and matched-random controls and require useful call volume plus positive
after-cost R. Model families, hyperparameters, threshold grid and promotion gates must be
frozen in a separate M18-B protocol before any training result is opened.

Even an M18-B historical pass may register only a fresh prospective observer. The final
target remains at least 70% strict accuracy with progress toward 80%, positive after-cost
expectancy and uncertainty/control gates on new evidence.

## Required artifacts and dashboard

- Base-protocol, M17-manifest, causal-source and path-contract hashes.
- Content-addressed row-level M18 dataset with roles, split, features, nullable labels,
  replay event and explicit exclusion reason.
- Market-specific readiness summary with every gate and exact blocker.
- Append-only registry event.
- Read-only dashboard panel showing coverage, label balance, readiness and authority
  `NONE — RESEARCH ONLY`.
