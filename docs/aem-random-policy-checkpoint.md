# AEM random-policy comparison — 24 September 2026

Continuation of `18ffe83` on `codex/nse-anticipatory-validation`. Work remains confined
to research modules, tests and documentation. No production source/configuration,
dashboard, management, alerts, broker credentials or live eligibility were changed.
No broker calls, price-history downloads or source-database writes were performed.

## Result: better than this random comparator, still unprofitable

The fixed-seed run used saved dataset `d90767ee45224f1fa5d8f50911f0892d`, whose
120-session development period ends on 18 September. All 141 observed trade attempts
replayed exactly, including 35 unfilled orders. The full event fingerprint, source
candles/calendar and original code/configuration dependencies were verified first.
The CSV reproduces the original dataset fingerprint without loading executable pickle.

Final-code report:
`data/m14_m18/aem_benchmark/runs/6e92823c840045598215cf3b32b544b6/report.json`.
The first run, `a04c2b421d7b423eb9ff98c81e0d6c8c`, is retained. Repeating the same
seed after code formatting produced identical grid/cohort artifact hashes and
numerical results; no thresholds, cohort definition or sampling settings were tuned.

| Diagnostic | AEM | Random-policy cohorts |
|---|---:|---:|
| Attempted opportunities per cohort | 141 | 141 |
| Mean net R per attempted opportunity | -0.10147 | -0.28184 |
| AEM advantage over mean comparator | +0.18038R | — |
| Strict target success among AEM fills | 40/106 = 37.74% | Not the primary comparison |
| AEM mean net R per filled trade | -0.13497 | Not the primary comparison |

There were 500 comparison cohorts. Only three had mean net R at least as high as
AEM; the plus-one corrected descriptive upper-tail fraction is 4/501 = 0.007984.
The central 95% interval of cohort means is -0.42010R to -0.13824R per attempt.
This fraction is **not a confirmatory p-value** and does not prove a deployable edge.
Both AEM and the comparator lose after modeled costs. The desired 70–80% success rate
is not achieved; there is no live promotion or model activation.

## What was compared

- Keep the same 141 selected stock-days across 82 sessions, including unfilled
  attempts. The 38 evaluation sessions without a recorded trade are not sampled.
- Enumerate every one-minute decision time from 09:20 through 11:00 inclusive:
  101 times per attempt, or 14,241 counterfactual paths.
- At each time, recompute price, VWAP and entry pattern from closed bars only.
  Bypass intraday signal qualification to create the random attempt. Do not copy
  a later observed trigger's price or entry style into an earlier random decision.
- Reuse the unchanged `label_trade`: identical market/VWAP-limit routing, limit
  caps, chase/stop/target handling, sizing, fees and entry-relative holding deadline.
- Random bars with no recognized pattern use the labeler's market-entry branch.
  Thus this tests the combined intraday timing/gating/entry-style policy, not pure timing.
- Sample uniformly with seed `20260923`, using one shared minute per date per cohort
  to retain some cross-stock co-timing. Canonical ordering makes input order irrelevant.
- Keep known no-execution outcomes at zero R per attempt. Any unknown outcome in
  the entire grid blocks the comparison, even when no random draw selected that path.
- Grid outcomes: 13,379 resolved, 821 unfilled limits and 41 chased entries; no
  unresolved paths. Each artifact records population, source and code hashes.

The comparator conditions on already-selected stock-days; it cannot validate daily
stock selection, rejected opportunities, whole-NSE accuracy or future availability.
The historical window was repeatedly inspected during development, so statistical
significance cannot be recovered merely by introducing a new random baseline.
Independent event R does not model portfolio capital, sector, heat or daily-entry
limits. Common-time sampling does not reproduce every kind of market dependence.

## Broader data plan: selected before the evaluation period

Plan artifact:
`data/m14_m18/aem_universe/runs/1145cab2055949899159d8589d4f04f4/report.json`.

The March 24 freeze precedes the March 25–September 18 evaluation. Among 2,664 locally
stored NSE cash EQ records, 979 met preperiod data-quality/history/liquidity criteria.
All candidates and rejections are preserved. A top-50 liquidity shortlist uses only
prior median turnover, not AEM/MCB qualification, app examples, intraday availability
or later outcomes. Current metadata and revised history still carry survivorship and
vintage limitations; top-liquidity stocks are not representative of every NSE stock.

The M1-only plan includes 20 warmup sessions, beginning February 24: 140 sessions and
2,625,000 expected regular-session bars for 50 stocks. Existing coverage reduces the
plan to 1,355 single-code windows or **271 bounded five-code batches**, before retries
and authentication overhead. The full-span upper bound is 1,500 single-code requests.
This is a saved request plan, not a completed download or a runtime estimate.

Future acquisition must use explicit requested windows in a separate lab staging
store. The existing incremental loader resumes after the latest timestamp and does
not repair earlier holes. Require exact 375-slot M1 session checks, not the legacy
60%-complete threshold. Coordinate a single rate-limited data client, reuse token
caching, and do not interrupt live services or overwrite production candles.

## Commands and verification

```powershell
.\.venv\Scripts\python.exe -m tradedesk_lab aem-benchmark --dataset-id d90767ee45224f1fa5d8f50911f0892d --cohorts 500 --seed 20260923
.\.venv\Scripts\python.exe -m tradedesk_lab aem-universe-plan --dataset-id d90767ee45224f1fa5d8f50911f0892d --shortlist-size 50
```

- Complete isolated research suite: **245 passed**.
- Scoped Ruff lint passed; final comparison code fingerprints match the working files.
- Test record: `data/m14_m18/validation_20260924_benchmark.xml`.
- Regression coverage includes causal random decisions, label parity, denominator
  preservation, malformed/altered input blocking, every frozen time slot, deterministic
  sampling, preperiod-only selection and future-price/data-availability invariance.
- Protected tracked production paths have no diff against `18ffe83`.
- No merge or push is part of this checkpoint; unrelated Groww work remains excluded.

Next: implement bounded, resumable acquisition into isolated staging, validate the
broader preperiod universe, then apply portfolio and execution/cost stress. The
conditional relative advantage is worth testing further, not grounds to relax gates
or claim the original success-rate target has been reached.
