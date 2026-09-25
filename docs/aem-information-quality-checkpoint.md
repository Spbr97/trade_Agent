# AEM information-quality accuracy checkpoint

Date: 25 September 2026

Branch: `codex/aem-information-quality-checkpoint`

Dataset: `ec539cf66bea4c18ba994506f85a52d6`

Experiment: `31dcf458fb974a2c9ac5b5c2faa8515a`

## Frozen experiment

This checkpoint tested 11 bounded, causal selectors using only information recorded
when each AEM decision was made: remaining resistance room, same-time relative volume,
VWAP position and slope, daily extension, impulse body quality, and one combined
quality rule. Instrument identity and outcome fields were forbidden.

The first 80 sessions, 25 March–23 July 2026, selected one specification by Wilson
lower bound, then mean net R, then active-session coverage. A selector required at
least 150 resolved development fills, 30 active sessions and no unresolved decisions.
The final 40 sessions, 24 July–18 September 2026, were opened once for evaluation.

The later partition is a chronological holdout, but it belongs to a baseline already
inspected in earlier work. It is development evidence, not fresh prospective evidence.

## Failure taxonomy

The development partition contained 335 resolved strict failures. The registered
primary classification assigned 89 to weak impulse body, 82 to weak same-time volume,
69 to an extended daily move, 66 to insufficient remaining room, 16 to the lower half
of development liquidity, two to cost drag, and 11 to no registered category.

These categories explain observable context; they do not establish causation. Intraday
benchmark alignment, sector alignment and historical point-in-time sector membership
were unavailable in the frozen event artifact and remain explicit data gaps.

## Selected challenger

The development winner retained calls with a non-negative impulse body ratio. On
development it produced 56/199 strict wins (28.14%), a 22.35% Wilson lower bound,
−0.19965R per resolved fill and 90.0% active-session coverage. Economics were already
negative, so later promotion still required every frozen predictive and economic gate.

## Locked chronological holdout

| Metric | Holdout baseline | Challenger | Delta |
|---|---:|---:|---:|
| Calls issued | 282 | 104 | −178 |
| Resolved fills | 244 | 100 | −144 |
| Strict wins | 35 | 13 | −22 |
| Strict success | 14.34% | 13.00% | −1.34 pp |
| Wilson 95% lower bound | 10.50% | 7.76% | −2.74 pp |
| Mean net R | −0.36875R | −0.46624R | −0.09749R |
| Active sessions | 39/40 | 38/40 | −2.50 pp coverage |
| Active sessions reaching 70% | 0.00% | 5.26% | +5.26 pp |
| Active sessions reaching 80% | 0.00% | 5.26% | +5.26 pp |

The small session-threshold increase does not offset worse pooled accuracy, uncertainty
or economics. With one to a few fills per day, it is also highly discrete.

## Decision

The challenger fails. It met the 100-fill, 20-active-session, no-unresolved and 50%
availability gates, but failed all three predictive/economic gates: at least +5
percentage points strict accuracy, improved Wilson lower bound, and positive mean net R.

- No selector is registered or promoted.
- No live setup, eligibility, management, risk or order behavior changes.
- The 70–80% objective remains unachieved.
- The holdout will not be reused to tune these selectors.

Machine-readable evidence is in
`docs/evidence/aem-information-quality-experiment.json`.

## Next checkpoint

Do not search more thresholds on this holdout. Add genuinely causal intraday benchmark
and sector context with point-in-time membership, freeze a new trial budget, and score
it on later unconsumed sessions. If no later history is available, begin prospective
shadow collection instead of presenting reused history as fresh evidence.
