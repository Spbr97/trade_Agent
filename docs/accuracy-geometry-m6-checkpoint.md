# Accuracy Milestone 6: anticipatory quick-profit geometry

Date: 2026-10-03

Run: `20261003-192811`

Protocol: `accuracy-geometry-v1`

Result: **FAIL / abstain — useful mechanism evidence, no promotion**

## Why this experiment replaced the old plan

Milestone 5 showed that adding classifiers to the same target was not producing a
deployable edge. Milestone 6 therefore changed the mechanism: enter at the first
exchange-session open after a setup arms and seek a smaller profit within one to
three sessions. The frozen 54-cell grid is documented in the
[Milestone-6 protocol](accuracy-geometry-m6-protocol.md).

## Cohort and controls

- 17,908 development rows over 600 arming sessions.
- The same clean, already-inspected historical cohort; this is not fresh evidence.
- 54 combinations of two entry modes, three ATR stops, three quick targets and
  three holding periods.
- Current sizing, gap limits, slippage, NSE charges and conservative OHLC ordering.
- Minimum promotion gates remained 80% accuracy, 70% Wilson lower bound, 70%
  successful-session rate, adequate samples/coverage and non-negative expectancy.
- The 4,848-row chronological tail remained locked because no complete development
  candidate passed.

## Main result

The strongest complete geometry was next-session-open entry, 1.0 ATR stop, 0.5R
target and three-session maximum hold:

- 12,764 wins from 17,264 resolved calls: **73.93% accuracy**.
- 73.27% Wilson lower bound.
- 600/600 active development sessions.
- Only 231/600 sessions reached 80% accuracy: **38.50%**.
- **-0.076R** mean after-cost expectancy.

The matching saved-fill geometry produced 8,719/17,264 wins (**50.50%**) with
-0.469R. The anticipatory entry materially improved the historical mechanism, but
the all-call result still failed accuracy, session reliability and economic gates.

## Setup diagnostics and next plan change

No setup diagnostic simultaneously reached 80% accuracy and non-negative
expectancy. The closest economically valid diagnostic was `trend_pullback` under the
best overall geometry:

- 1,437/1,811 wins: **79.35% accuracy**.
- 77.42% Wilson lower bound.
- **+0.033R** mean after-cost expectancy.

`base_breakout` reached 80.33% under that geometry but remained slightly negative
at -0.0003R. A different 1.25 ATR stop produced 79.59% and +0.0187R on 676
base-breakout calls. These are development diagnostics selected after inspecting the
grid, not promotion evidence.

The next plan is therefore no longer a broad model or broad geometry race. It is a
preregistered setup-specific chronological validation of anticipatory
`trend_pullback`, with `base_breakout` as a secondary hypothesis. It must demonstrate
stability across internal development folds before any locked-tail evaluation. NR7
is not carried forward because it dominated the population while remaining only
72.98% accurate and -0.093R under the best geometry.

## Decision

No nominee, no locked-tail access, no live configuration change and no canonical
baseline change. The 73.93% all-call and 79.35% setup diagnostic are promising
development measurements, not achieved live-call reliability.

Machine-readable evidence is in
[`docs/evidence/accuracy-geometry-m6.json`](evidence/accuracy-geometry-m6.json).
