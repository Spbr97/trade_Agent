# AEM isolated M1 collection checkpoint — September 24, 2026

Branch: `codex/nse-anticipatory-validation`. This is data acquisition only, not a
strategy promotion, new trading signal, or demonstrated improvement in accuracy.
Production code, dashboard, management code, configuration, scheduled tasks and
broker credentials are unchanged. No order endpoints are used.

## Implemented

`python -m tradedesk_lab aem-collect` consumes the frozen preperiod universe plan
`1145cab2055949899159d8589d4f04f4`, not a newly selected outcome-dependent shortlist.

- Verifies the manifest, selection and coverage fingerprints, derives the request
  list from the frozen windows, and pins the complete plan artifact for resumption.
- Queries existing production M1 rows through one read-only transaction. It never
  copies live database files and never opens the production database writable.
- Writes insert-only candles and provenance to a dedicated lab DuckDB. Duplicate
  observations must agree exactly; conflicts reject the whole incoming batch.
- Validates raw timestamps, volumes and OHLC values before the production parser
  can coerce them. Requires exact minute alignment and valid finite prices.
- Excludes out-of-window data, non-calendar sessions and the 15:30 marker. A complete
  regular session requires every one of the 375 opens from 09:15 through 15:29 IST.
  Empty or partial HTTP-200 responses remain incomplete; no bars are fabricated.
- Rechecks actual stored coverage when resuming. A successful request alone never
  marks a window complete. Source holes outside the frozen request plan block work.
- Uses cached tokens only: no generation credentials, refresh, invalidation or
  keyring writes. Authentication failures stop collection without retries.
- Routes requests through the existing broker rate limiter, with additional
  serialized one-second pacing, five-code/seven-day bounds and explicit run budgets.
- Uses one collector lock across plans. Checks active processes and scheduled jobs
  before client creation and each request; protected weekday hours are 09:00–16:00
  IST, with a two-minute opening/scheduled-job guard. No service is stopped.
- Atomically saves request intent and progress. Interrupted pending attempts are
  reported as unknown consumption, not folded into an apparently exact usage total.
  Stage and checkpoint aliases/hard links are rejected rather than overwritten.

Local coordination cannot observe other machines or reconstruct account-wide API
usage. It is a conservative point-in-time check, not a broker-wide distributed lock.
Special exchange sessions require explicit calendar review; completeness is not
silently relaxed to accommodate them.

## Actual execution

The offline seed retained **255,000 valid M1 bars / 680 complete stock-sessions**.
The collector first blocked the imminent and then running 16:15 BSE after-close job,
making zero requests during those checks. It proceeded only after the job cleared.

Two bounded live runs then completed, five historical requests each:

- First run: `6b7c5ae99f084287a10a3bea91628b69`.
- Verified resume: `bc308647f00b4b0f84dfd62c273b9777`.
- Saved batch indices: 0 through 9, next cursor 10; no batch replay was required.
- **91,875 new bars** collected, ten recorded requests, zero unconfirmed attempts.
- Final staging: **346,875 bars / 925 of 7,000 complete stock-sessions**.
- **6,075 stock-sessions remain incomplete**, with zero invalid staged rows.
- 261 of the original 271 request batches remain unattempted; partial responses or
  future source discrepancies could require additional work. No runtime is promised.

Artifacts live under:

```text
data/m14_m18/aem_history/1145cab2055949899159d8589d4f04f4/
  candles.duckdb
  state.json
  runs/<run-id>/report.json
```

The final report explicitly remains `eligible_for_live=false` and
`evaluation_ready=false`. The staged February 24–September 18 M1 span is not yet a
replacement for the old full-history source. A subsequent evaluation must validate
its D1/benchmark join and preserve the earlier M1 history required by indicator and
sparse-slot RVOL calculations, or explicitly declare a changed history contract.
This development cohort still has current-metadata survivorship/revision limitations
and is not representative of every NSE liquidity band.

## Verification and commands

- Final runnable lab regression suite: **398 passed, 1 skipped**. The skip is a
  Windows symlink-privilege test; hard-link/source protection tests pass.
- Full-suite collection was blocked in five pre-existing SciPy-dependent modules by
  Windows Application Control (`scipy.integrate._dop`). Those five modules were
  excluded from the runnable-suite count. No dependencies or security settings were
  changed to work around it.
- Scoped Ruff passed for all nine changed/new Python files; Git whitespace check passed.
- No tracked production diff against `96f8b6b`; unrelated Groww files remain excluded.
- Test records: `data/m14_m18/validation_20260924_history_available.xml` (runnable
  suite), `validation_20260924_history.xml` (full-suite collection failures), and
  `validation_20260924_history_orchestrator.xml` (19 orchestration regressions).

```powershell
# Offline seed/coverage audit; default also makes zero broker requests.
.\.venv\Scripts\python.exe -m tradedesk_lab aem-collect --plan-id 1145cab2055949899159d8589d4f04f4 --max-requests 0

# Resume a small, guarded historical-data batch. Allowed range: 1..100 per run.
.\.venv\Scripts\python.exe -m tradedesk_lab aem-collect --plan-id 1145cab2055949899159d8589d4f04f4 --max-requests 5
```

A blocked report exits nonzero. If a process crashes, the collector lock remains;
verify that its recorded owner has exited before manually recovering that exact lock.
Do not remove a live owner's lock or reset state to hide an interrupted request.

Next: finish the frozen collection, audit remaining gaps and data vintage, build the
isolated evaluation join, then re-run AEM and its causal matched-random comparison
with portfolio/execution/cost checks. The prior 37.7% strict-win development result
is unchanged; the desired 70–80% session success rate has not been demonstrated.
No merge, push, dashboard change or unattended collector was created here.
