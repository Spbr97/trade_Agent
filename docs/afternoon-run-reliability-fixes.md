# Afternoon-run reliability fixes

Date: 2026-10-05

This checkpoint responds to the 16:00 NSE/crypto and 16:15 BSE collection receipts. It
changes evidence reliability only. It does not relax an accuracy gate, promote a model,
make a call tradeable, or alter order/position management.

## Model artifact compatibility

- Every newly saved `ModelBundle` records its Python and scikit-learn runtime versions in
  both the serialized bundle and its readable JSON sidecar.
- Loading treats scikit-learn's incompatible-version warning as an error. An incompatible
  estimator is never returned to prediction code.
- Latest-model discovery converts that error into an explicit `ML scoring disabled`
  warning and returns no ML opinion. The rule scan continues because this layer is
  advisory and shadow-only; it does not emit unverified probabilities.
- Replacement models must be trained by the installed runtime under the existing purged
  walk-forward and locked-final-test protocol. Training never enables the model.

Runtime-matched replacements were trained with Python 3.12.14 and scikit-learn 1.5.2:

| Setup | Artifact | Walk-forward evidence | Locked final evidence | Decision |
| --- | --- | --- | --- | --- |
| base breakout | `20261005-204740-base_breakout` | n=858, Brier 0.1416, AUC 0.5608; no qualifying positive threshold | n=350, Brier 0.1441, AUC 0.5653, all-call expectancy -0.582R; zero above threshold | abstain; shadow only |
| NR7 breakout | `20261005-205136-nr7_breakout` | n=14,583, Brier 0.1992, AUC 0.5614; best tested above-threshold expectancy -0.308R at n=29 | n=5,191, Brier 0.1858, AUC 0.5695, all-call expectancy -0.609R; zero above threshold | abstain; shadow only |
| trend pullback | `20261005-205433-trend_pullback` | n=2,576, Brier 0.2002, AUC 0.5507; no qualifying positive threshold | n=1,085, Brier 0.2100, AUC 0.5196; no qualified above-threshold calls | abstain; shadow only |

The real `scan --date 2026-10-05` loaded these exact artifacts without a version
warning. It remained `RISK_OFF`, produced 69 rejected candidates and zero A/B/C
watchlist entries, and saved the watchlist. This is a runtime repair, not evidence of
improved accuracy.

## Frozen NSE forward cohort

The 16:00 failure was not an outcome-contract change. Commit `48845ae` reformatted only
`tradedesk_lab/forward.py` and `tradedesk_lab/portable.py`, but the forward activation
correctly binds exact file hashes. The formatting-only edits were reversed byte-for-byte,
restoring the activated hashes without changing logic or resetting evidence.

The exact scheduled NSE pipeline then completed end to end as run
`20261005T170019-da391c9824`. It retained the existing cohort and now contains 153
prospective scores, with zero resolved prospective calls, so prospective accuracy remains
unavailable. Dashboard collection health reports integrity and freshness passed with no
failed stage, while `baseline_improved=false` and `eligible_for_live=false` remain intact.

## Invalid and stale instruments

- The shared invalid-instrument registry now recognizes explicit CoinDCX invalid-pair
  responses (`Invalid pair` and `BFF-SO-004`) as well as INDstocks `Invalid scrip` errors.
- Codes are quarantined only after three explicit provider identity rejections and are
  retried after 30 days. Timeouts and ordinary no-candle responses never enter this
  quarantine.
- The equity loader no longer retries an explicitly rejected code again in the same run;
  that previously doubled wasted requests. NSE/BSE codes merely behind the newest session
  remain visible as stale and are not falsely labelled delisted.
- The crypto loader now refreshes CoinDCX's active instrument master before selecting
  default targets. Stored history is retained, but inactive pairs such as `WAXL/INR` are
  no longer repeatedly requested just because their old instrument row remains stored.
- Explicit user-supplied codes remain honored.

The real crypto loader then refreshed 339 active pairs, fetched 339 daily bars with zero
errors, and did not request the removed `WAXL/INR` pair. The invalid-pair registry remained
empty; this successful load is freshness evidence only, not prediction-performance
evidence.

The post-fix NSE scheduled-path rerun loaded 2,668 codes, recorded 45 explicit provider
rejections, and showed 31 codes meeting the three-failure quarantine. Those rejected codes
were not immediately requested a second time; only seven non-error stale codes entered the
straggler retry. Ordinary stale codes remain visible rather than being mislabeled invalid.

## Authority

These are operational repairs. NSE, BSE, and crypto evidence remain separate. Collector
completion is not a performance pass; unavailable is never a pass; baseline and live
authority do not change.

## Verification

- 126 targeted tests passed across prediction, invalid-instrument handling, forward
  evidence, CoinDCX, and candle storage.
- The complete 637-test repository run reached 100% with 635 passing, one expected skip,
  and one unrelated date-sensitive failure in
  `test_everything_recently_tested_means_an_empty_batch`. That unchanged test injects an
  October 19 planning time while its registry writes the real October 5 late-evening
  timestamp, leaving slightly less than its asserted 14-day retest interval. It reproduces
  alone, and neither `replacement_search.py` nor that test differs from `main`.
- Ruff passed on every changed non-frozen Python file. The two activated forward-contract
  files are intentionally not reformatted; their exact activated hashes match.
- `git diff --check` passed for this checkpoint's files.
