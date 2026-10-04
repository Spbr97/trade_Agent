# BSE B1 protocol: transported quick-profit accuracy test

Status: **preregistered before the first BSE quick-profit replay**

Date: 4 October 2026

Protocol: `bse-accuracy-quick-profit-v1`

## Question

Can the existing BSE forward research rules produce substantially more reliable strict
target-before-stop outcomes when they use one smaller, faster exit geometry that was fixed
outside the BSE evidence?

This is a BSE-only experiment. It does not pool NSE or crypto rows, does not change a live
setup and cannot promote itself.

## Why this is the next bounded test

The current BSE evidence has two limitations:

- the legacy setup backfill is the old five-stock cohort, not evidence for the current
  full BSE universe; and
- the honest forward research cohort has only 11 sessions and uses a 3 ATR stop, 2R target
  and ten-session deadline. Its strict target rate is 0-6.15% by rule, even where timeout
  exits make gross expectancy look less poor.

The BSE store itself is not the limiting factor: it contains daily history for about 2,472
BSE codes from 2016 through 1 October 2026. The bounded test therefore changes the outcome
geometry, not the model count.

## Frozen geometry and provenance

Exactly one geometry is allowed:

- decision population: existing rows in `data/reports/research_calls_bse.jsonl`;
- entry: first BSE session open after the arming close, with configured entry slippage;
- stop: 1.0 arming ATR below the slipped entry;
- target: +0.5R;
- deadline: three sessions after entry;
- ambiguous OHLC bar: stop wins;
- economics: configured equity costs, delivery/intraday treatment, gap-aware position sizing
  and exit slippage.

This geometry comes from NSE M6 run `20261003-192811`, where it was the strongest complete
development geometry. NSE outcomes do not count as BSE evidence. Reusing the already-fixed
geometry avoids a new 54-cell search on BSE and the false discovery risk that would create.

## Frozen BSE rules and control

Four already-forward-collected rules are evaluated independently:

1. `rsi2_dip_ema50`
2. `rsi2_dip_vol`
3. `rsi2_deep_ema200`
4. `pullback_ema10_above_ema50`

Each rule is compared on common arming sessions with the existing `random_eligible` calls,
which use the same eligible BSE universe and daily cap. The statistic is the mean of paired
session-level after-cost R differences. A one-sided sign-flip null is corrected across the
four registered rules with Holm's procedure. An absent matched control or p-value is a failed
gate, never a pass.

## Evidence separation

- Rows armed before **4 October 2026** are `development_transport` diagnostics only. They
  may show whether the transported mechanism is plausible, but can never qualify it.
- Only rows armed on or after 4 October 2026 accumulate prospective evidence.
- Missing bars, incomplete outcome windows, invalid OHLC/ATR and untradeable position sizes
  remain counted by status. They are not silently converted to losses or removed from the
  audit, and they never pass a gate.
- BSE outcomes, costs and controls remain separate from NSE and crypto.

## Prospective gates per rule

All gates are required:

- at least 100 resolved calls;
- at least 30 active BSE sessions and 30 matched-control sessions;
- at least 40% session coverage;
- at least 80% strict target-before-stop success;
- at least 70% Wilson lower bound;
- at least 70% of active sessions individually reaching 80% success;
- positive mean return after configured costs;
- at least +0.10R over matched random control; and
- Holm-adjusted one-sided paired-session p-value at most 0.05.

Before sample gates mature the only valid verdict is `collecting`, even if a provisional
percentage is high. Passing later means `qualified_research_only`, not live eligibility.
The state always carries `live=false`, `baseline_improved=false` and
`promotion_allowed=false`; an independent review and a separately frozen promotion cohort
would be required to change those claims.

## Dashboard-ready state

The artifact schema is `bse-accuracy-quick-profit-state-v1`. The compact top-level fields
are `market`, `status`, `live`, `baseline_improved`, `promotion_allowed`, `geometry`,
`data_source`, `readiness`, `development_transport` and `prospective`. A later dashboard
endpoint can serve this file directly without recomputing or mixing evidence.
