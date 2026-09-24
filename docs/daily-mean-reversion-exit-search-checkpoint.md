# Daily mean-reversion exit-search checkpoint

Date: 25 September 2026

Branch: `codex/daily-mean-reversion-exit-search`

## Question and frozen method

This checkpoint tested whether tighter or differently shaped exits could move the
project's three previously positive-gross-edge daily mean-reversion rules toward a
60% profitable-trade rate while retaining positive expectancy after modeled NSE
costs. It used the existing `scripts.entry_search optimize` path without changing its
rules or execution logic.

For every rule and risk sizing, the search evaluated 96 combinations of 1.0–3.0×ATR
stops, 0.5–3.0R targets and 2–10-session maximum holds. Geometry selection used only
the chronological training half before 1 March 2025. Exactly one selected geometry
was then scored on the locked test half from that date through 13 September 2026.
The same 900-code cap, next-session-open entry, gap-aware/stop-first barrier and real
NSE delivery cost model were used throughout.

## Locked-test results

| Rule | Risk/trade | Selected geometry | Test n | Profitable trades | Mean net R |
|---|---:|---|---:|---:|---:|
| RSI(2)<10 and above EMA50 | 0.25% | 3×ATR / 2R / 10 | 4,383 | 49.67% | −0.08705R |
| RSI(2)<10 and above EMA50 | 0.50% | 3×ATR / 2R / 10 | 4,383 | 49.67% | −0.04491R |
| RSI(2)<10 and above EMA50 | 1.00% | 3×ATR / 2R / 10 | 4,383 | 49.67% | −0.01547R |
| RSI(2)<5 and above EMA200 | 0.25% | 3×ATR / 1.5R / 10 | 3,140 | 50.13% | −0.09486R |
| RSI(2)<5 and above EMA200 | 0.50% | 3×ATR / 1.5R / 10 | 3,140 | 50.13% | −0.05374R |
| RSI(2)<5 and above EMA200 | 1.00% | 3×ATR / 1.5R / 10 | 3,140 | 50.13% | −0.02432R |
| Below EMA10 and above EMA50 | 0.25% | 3×ATR / 2R / 10 | 19,837 | 46.79% | −0.11749R |
| Below EMA10 and above EMA50 | 0.50% | 3×ATR / 2R / 10 | 19,837 | 46.79% | −0.07504R |
| Below EMA10 and above EMA50 | 1.00% | 3×ATR / 2R / 10 | 19,837 | 46.79% | −0.04535R |

“Profitable trades” is the existing optimizer's fraction with gross R above zero. It
is not target-hit accuracy and is labeled separately to avoid inflating the project's
strict-success baseline.

## Decision

The best locked-test profitable rate was 50.13%, 9.87 percentage points below the
60% research target. Every locked-test result was negative after costs. The best
economics were still −0.01547R per trade at 1% risk; increasing size only diluted
fixed charges and did not create predictive edge.

Therefore the plan stops after Step 1:

- No daily mean-reversion gauntlet runner is created.
- No rule is added to `research_tracker.py::CANDIDATES`.
- No live setup, eligibility threshold, management rule or order path changes.
- The visually attractive training rows around or above 60% are not promoted after
  seeing the locked test. Doing so would be test leakage.

The complete machine-readable evidence, including the nine source CSV hashes, is in
`docs/evidence/daily-mean-reversion-exit-search.json`. The research dashboard exposes
the same nine locked-test outcomes and the stop decision.

## Accuracy-baseline impact

This is a rejected challenger, not an AEM baseline replacement. The canonical AEM
metrics remain unchanged: 21.50% strict success, 18.60% Wilson lower bound,
−0.27471R/fill, 98.33% active-session coverage, 3.39% of active sessions reaching
70%, and 1.69% reaching 80%. No accuracy improvement is claimed.

## Next checkpoint

Do not continue exit searching on this locked test period. Return to the preregistered
failure taxonomy and information-quality hypotheses—market/sector alignment, remaining
room, exhaustion, liquidity and causal entry quality—then evaluate challengers on a
new chronological cohort under the canonical scorecard.
