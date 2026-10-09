# Milestone 16 checkpoint — execution-aligned selector

Run completed: 2026-10-09

Status: implementation complete; development rejected for NSE, BSE and crypto. No
prospective cohort was registered, no active model changed, and baseline improvement is
not proven.

Protocol:
[frozen M16 execution-aligned protocol](self-learning-m16-execution-aligned-protocol.md)

## Result

M16 fitted the preregistered opportunity forest, target-before-stop classifier and
after-cost net-R regressor on each market separately. It then applied the frozen candidate
pool, probability grid and positive-predicted-net-R requirement.

| Evidence | NSE | BSE | Crypto |
|---|---:|---:|---:|
| Development dataset rows | 196,095 | 189,496 | 1,976 |
| Validation candidate-pool rows | 432 | 420 | 108 |
| Candidates with probability ≥50% | 431 | 408 | 51 |
| Candidates with predicted net R >0 | 0 | 0 | 2 |
| Candidates clearing both conditions | 0 | 0 | 1 |
| Sessions clearing both conditions | 0/36 | 0/36 | 1/36 |
| Minimum required validation selections | 12 | 12 | 12 |
| Selected threshold | none | none | none |
| Development gates passed | 1/10 | 1/10 | 1/10 |
| Prospective cohort registered | no | no | no |

The experiment IDs are:

- NSE: `c151ed1bc4b0e750249ae40d891a36649d3a860a8b5bc6d4d00964cb748128d8`
- BSE: `814ffa9e55b3a6ed96d78c425bba5cf74a868ba3d3182e9b5c63ca79a2fdaabe`
- Crypto: `e2fcdd3abd301c1a1f4cdbc5124cf0889a8deec3a53b40c9ae3d6853ce823a56`

## Why it stopped

M16 did not fail because the classifier lacked confidence. The equity classifier assigned
at least 50% target-before-stop probability to almost every stage-one candidate. The
independently fitted economic model nevertheless predicted negative after-cost net R for
every NSE and BSE candidate. Its best validation estimates were `-0.115R` NSE and
`-0.148R` BSE. Crypto's best estimate was only `+0.014R`, with one row clearing both frozen
conditions.

The forced stage-one top-one control confirms the economic problem:

| Forced daily top-one | NSE | BSE | Crypto |
|---|---:|---:|---:|
| Strict accuracy | 33.33% (12/36) | 30.56% (11/36) | 33.33% (12/36) |
| Mean net R | -0.583R | -0.666R | -0.188R |
| Maximum losing streak | 7 | 9 | 12 |

This is the same distinction M15 exposed from another direction: detecting an instrument
that may trade higher is not enough when the entry, stop, target, path order and costs make
the executable contract negative. Removing the positive-net-R requirement would create
many nominally confident calls but knowingly admit a negative-expectancy stream. M16
correctly refused to do that.

The one passed gate in every market is the validation/diagnostic accuracy-gap check. With
no eligible policy in either block, that equality is not positive evidence and cannot
override the nine substantive failures.

## What was implemented

- Exact M14 causal latest-session extraction without requiring a future bar.
- Fixed two-stage opportunity/tradeability models and net-R model.
- Frozen no-call threshold selection and development gates.
- Market-specific content-addressed datasets, replays, models and registries.
- Append-only, hash-chained prospective observation and resolution ledgers.
- Strict post-registration timing boundary and duplicate-session protection.
- Matched-random prospective controls and 60-call/40-session qualification gates.
- Non-blocking hooks in NSE, BSE and crypto trackers.
- Fail-closed Learning-dashboard panel with baseline and live authority fixed to `NO` and
  `NONE`.

The prospective machinery remains dormant because the development gate rejected all three
models. This is intentional; rejected research does not consume collection time or appear
as a personal call.

## Next accuracy-changing checkpoint

Do not lower the M16 economic gate or retune these exposed models. The next checkpoint must
change the information and execution contract:

1. Acquire time-ordered intraday path evidence (M1/M5/M15 where available) for the full
   liquid NSE/BSE universes and the monitored crypto universe. Daily high/low bars cannot
   reliably distinguish entry timing or stop/target order.
2. Record spread, gap, liquidity and market-specific cost inputs at each possible entry.
3. Preregister a finite execution-contract race: next open, causal breakout confirmation
   and causal first-retracement entry, each with fixed market-specific stop/target/timeout
   geometries.
4. Select a contract only on purged chronological development evidence using strict
   accuracy, after-cost net R, call count and matched-random controls together.
5. Freeze the winner and require a new prospective cohort before any baseline-improvement
   statement.

Until that intraday execution evidence exists and passes, the agent should continue to
abstain rather than turn high classifier confidence into negative-expectancy calls.
