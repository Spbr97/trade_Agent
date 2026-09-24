# NSE / anticipatory quick-profit research — 23 September 2026

Follow-up: [24 September random-policy comparison and preperiod universe plan](aem-random-policy-checkpoint.md).

Branch: `codex/nse-anticipatory-validation`, based on integrated checkpoint `705895b`.
The user explicitly resumed agents. This work stays in `tradedesk_lab`, `tests_lab`
and research documentation. Production source/configuration, dashboard, management,
alerts and strategy eligibility are unchanged. No orders, network data downloads,
automatic model promotion or unattended research watcher were started. Unrelated
Groww files remain untouched and excluded.

## Implemented

- `nse-screen`: consider all locally stored NSE cash EQ stock records using AEM/MCB
  daily qualification, independent of intraday data availability. Preserve every
  candidate and rejection; rank a configurable shortlist by median turnover, not a
  claimed success probability. Audit M1/M5 completeness and estimate bounded backfill
  requests without downloading. This is not proof the local master contains every
  exchange listing, and current metadata is not historical membership.
- AEM data: common benchmark evaluation dates, prior-session daily freshness, no M5
  prerequisite, explicit completed-session cutoff and full observed M1 history for
  stable same-time-slot relative volume. Sparse observations may reach farther back
  than twenty exchange sessions; full-history loading has a memory cost. Fingerprint
  all consumed candle frames, benchmark dates, rules and code dependencies.
- AEM integrity: fixed one-minute intervals; causal closed-bar signals; limit fills
  capped at the limit; no pre-fill high credited as a win; stop-first intrabar
  ambiguity; gap-aware exits; incomplete horizons remain unresolved. Intrabar timing
  is reported as observation bounds, not falsely precise execution timestamps.
  Corrupt sessions are audited and excluded without erasing earlier valid sessions.
- Reports: strict target success versus any positive-net exit, Wilson uncertainty,
  per-symbol/pattern breakdowns, every evaluation date, zero-call days, no-fills and
  unresolved outcomes. A zero-event day does not prove complete market-data coverage.
- Locked-source commands persist a separate blocked report and exit nonzero. They
  do not stop the live service, copy a live database, or replace the latest dataset.

The strategy remains `AEM_v1` with unchanged thresholds (0.8% target, 0.6% stop,
90-minute maximum holding time). Feature and label versions are now v2. Legacy
datasets are preserved; the lab's latest pointer references the newly built dataset.

## Verified development result

### Broad daily screen

Command: `python -m tradedesk_lab nse-screen --shortlist-size 50 --backfill-sessions 120`.
Artifact: `data/m14_m18/nse_universe/runs/c0bd762cad564712bc2b08d7f373fc83/report.json`.

At the stored 23 September close, the screen considered 2,793 instrument records,
including **2,664 NSE cash EQ stocks**. It retained 242 daily research candidates:
241 qualified for AEM and 12 for MCB, with overlap. All candidates are preserved;
only the top 50 by liquidity receive the initial detailed M1/M5 coverage plan.
These are daily WATCH candidates, not executable calls or predicted winners.
None of the top 50 currently has a complete stored M1 or M5 session in the requested
window. The current-close shortlist is not a point-in-time historical universe for
earlier dates; any backfilled retrospective result would still be development evidence.

The 120-session coverage plan spans 1 April–23 September and estimates 2,600 missing
request windows across the 50-stock shortlist's M1/M5 history, before retries/auth
overhead. It is a data-acquisition plan, not permission to bypass provider limits or
evidence of trading performance. No download was performed by this screen.

### Corrected AEM diagnostic

Command: `python -m tradedesk_lab aem-prepare --sessions 120 --as-of 2026-09-18`.
Artifact: `data/m14_m18/aem/datasets/d90767ee45224f1fa5d8f50911f0892d/manifest.json`.

The corrected run covers ten M1-available stocks and 120 common benchmark sessions:
209 candidate sessions, 141 trade decisions, 106 resolved fills, 35 unfilled limits,
and 68 daily candidates without a trade. It is not a whole-NSE performance test.

- Strict target wins: 40/106 = **37.74%**; diagnostic Wilson 95% interval 29.09–47.24%.
- Any positive-net result: 48/106 = 45.28%, including profitable time exits.
- Mean net return: **−0.13497R** per resolved trade; mean observed holding time 36.80 min.
- 68/120 sessions had resolved trades; 52 had no resolved fill. Of the 68 active
  sessions, 16 (23.53%) reached at least 70%, also at least 80%. Small daily counts
  make these percentages discrete; this does not establish future session reliability.
- Data audit: two incomplete observed sessions, two corrupt sessions/six corrupt
  bars across consumed history, one stale prior daily close, and eight insufficient
  daily-warmup rejections.

The prior v1 diagnostic (`7dfe378e13264d24b70b888c6a5593f8`) reported 107 fills,
37.38% strict success and −0.16413R. These are different execution/data contracts,
not a controlled model improvement. The history was repeatedly inspected and tuned;
neither result is untouched out-of-sample or prospective evidence. Independent event
returns do not simulate concurrent portfolio, sector or heat limits. Fees/slippage
remain provisional. **The 70–80% target is not met; live eligibility stays false.**

## Verification

- `python -m pytest tests_lab -o addopts='' -q`: **174 passed**.
- Final test artifact: `data/m14_m18/validation_20260923_final.xml`.
- Scoped Ruff lint passed across all changed research modules and tests.
- Git diff against `705895b` shows no tracked production source/configuration changes.
- No merge or push to main is part of this checkpoint.

## Next validation steps

1. Review the whole-local-NSE screening and missing-history report. Freeze a broader
   development universe before choosing what history to load; do not select stocks
   using later winning outcomes. Audit vintage/membership limitations explicitly.
2. Construct an AEM-matched random-entry benchmark with identical market/limit fills,
   costs and entry-relative holding limits. The existing generic harness's fixed
   session-window null is not equivalent and has not certified AEM.
3. Apply portfolio limits and execution/cost stress before interpreting economic
   performance. Stop or redesign rules if no after-cost advantage is reproducible.
4. Only then compare pre-registered causal context/ranking features and simple models
   using chronological, purged validation. More model complexity is not evidence.
5. Freeze a prospective shadow contract and collect genuinely later predictions
   before their possible entries. No automatic production promotion.
