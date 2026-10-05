# BSE B1 checkpoint: transported quick-profit accuracy test

Date: 4 October 2026

Current protocol: `bse-accuracy-quick-profit-v2`

Historical committed development snapshot: `bse-accuracy-quick-profit-v1`

State schema: `bse-accuracy-quick-profit-state-v1`

Result: **COLLECTING — live false, baseline improved false**

## What was implemented

B1 adds a BSE-only evaluator for the single next-open / 1.0 ATR stop / 0.5R target /
three-session geometry preregistered in the
[B1 protocol](bse-accuracy-quick-profit-b1-protocol.md). It reuses the tested conservative
M6 price-path resolver, configured BSE/equity costs, slippage and position sizing. It does
not change a setup, watchlist, alert, scheduler, management component or dashboard.

The result is written to
[`docs/evidence/bse-accuracy-quick-profit-b1.json`](evidence/bse-accuracy-quick-profit-b1.json).
Its top-level fields are intentionally suitable for a later read-only dashboard endpoint.

## Baseline audit before B1

The BSE evidence cannot honestly inherit the NSE baseline:

- the setup backfill is the old five-stock population: `base_breakout` 21/221 (9.50%),
  `nr7_breakout` 292/1,327 (22.00%) and `trend_pullback` 37/212 (17.45%);
- only one live-source BSE setup call is resolved, and it lost; and
- the existing BSE forward research log covers 11 arming sessions. Under its original
  2R / 3 ATR / ten-session outcome, strict target rates were 4.00% for random control,
  6.15% for pullback-EMA10, 4.92% for RSI2-EMA50 and 0% for the other two rules.

The data store is broad enough for continued work: 4,502,955 daily BSE bars for 2,472 codes,
from 14 September 2016 through 1 October 2026. Evidence maturity, not raw history breadth,
is the immediate blocker for a prospective claim.

## Pre-activation transport diagnostic

Rows armed before 4 October remain development-only. They were re-resolved once under the
frozen quick-profit geometry:

| Rule | Resolved | Strict wins | Accuracy | Wilson lower | Net expectancy | vs random | Holm p |
|---|---:|---:|---:|---:|---:|---:|---:|
| `random_eligible` | 120 | 65 | 54.17% | — | -0.376R | control | — |
| `rsi2_dip_ema50` | 96 | 59 | 61.46% | 51.46% | -0.202R | +0.160R | 0.233 |
| `rsi2_dip_vol` | 31 | 20 | 64.52% | 46.95% | -0.126R | +0.155R | 0.409 |
| `rsi2_deep_ema200` | 87 | 52 | 59.77% | 49.26% | -0.236R | +0.180R | 0.409 |
| `pullback_ema10_above_ema50` | 119 | 69 | 57.98% | 49.00% | -0.300R | +0.086R | 0.409 |

The smaller target materially raises the raw hit percentage compared with the old 2R
target, but it does **not** establish an accuracy baseline improvement:

- no rule reaches 80% accuracy or a 70% Wilson lower bound;
- every rule remains negative after configured costs;
- no candidate survives the four-rule familywise significance gate; and
- only seven or eight resolved sessions exist, depending on the rule.

These rows therefore remain transport diagnostics even if later code or thresholds change.
They cannot be promoted or relabeled as prospective.

## Prospective readiness

The activation date is 4 October 2026. At this checkpoint:

- prospective source sessions: **0 / 30 required**;
- rules with at least 100 resolved calls, 30 active sessions and 30 matched-control
  sessions: **0 / 4**;
- qualified rules: **0**; and
- state: **`collecting`**.

An unavailable metric is never shown as a pass. Every prospective rule lists the missing
sample gates and the evidence gates separately. `baseline_improved`, `live` and
`promotion_allowed` remain false regardless of the development percentages.

## Decision and next BSE step

Keep the existing BSE research tracker collecting the same five frozen series each exchange
session. Re-run this evaluator after new outcomes mature; do not retune the geometry during
the cohort. At 30 prospective sessions, either a rule clears every accuracy, economics,
session-consistency and matched-control gate, or B1 is stopped. A negative B1 result should
lead to a newly preregistered BSE mechanism, not a wider search over this cohort.

Dashboard work was intentionally deferred to the parent integration checkpoint. The JSON
state is ready to expose read-only without recomputation or evidence pooling.

R1 subsequently added a B1 hook to the BSE research tracker, moved its changing prospective
state to `data/m14_m18/bse_accuracy_quick_profit/state.json`, and added a hash-verified
terminal first-look lock. R2 later found that the unattended order was wrong and that a
module-path failure could skip B1 while the base research process still appeared successful.
R2 fixes the import path, runs load → scan → signal tracker → research/B1 in one strict
pipeline, propagates derived failure to Task Scheduler, excludes late/missing/duplicate
registration, and makes same-day sampling deterministic across processes and reruns. The
committed file in `docs/evidence/` remains the original v1 development/activation snapshot;
the 64.52% / -0.126R best development diagnostic is still not a baseline. See the
[R1 evidence-refresh checkpoint](accuracy-evidence-refresh-r1-checkpoint.md) and
[R2 collector-integrity checkpoint](accuracy-collector-integrity-r2-checkpoint.md).
