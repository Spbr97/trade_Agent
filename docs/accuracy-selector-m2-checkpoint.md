# Accuracy recovery milestone 2 — high-precision selective policy

Date: 2 October 2026

Status: **framework implemented and tested; selector not yet trained on a new evidence cohort;
no accuracy improvement claimed**

## Outcome

The prediction pipeline now evaluates a separate accuracy-first policy over its purged,
walk-forward out-of-sample development predictions. The existing economic threshold remains
available for research comparison, but it cannot be confused with accuracy qualification.

For every probability threshold from 0.50 through 0.95, the selector measures policies that
retain at most the top one, top two, or top three candidates in each session. Every point
reports:

- candidates and selected calls;
- observed strict success and the 95% Wilson lower bound;
- call coverage, active sessions, zero-call sessions and session coverage;
- the fraction of active sessions meeting the session success target;
- availability of realised economics and mean R; and
- every failed gate.

The development nomination policy requires at least 100 selected calls, 30 active sessions,
40% session coverage, 80% observed strict success, a 70% Wilson lower bound, at least 70% of
active sessions meeting the session target, complete realised economics, and non-negative
expectancy. This is only a research nomination gate. The global 500-total/100-OOS live gate
and later locked/prospective reviews remain mandatory.

If no threshold/top-k point clears every requirement, the stored operating point is `null`
and the selector explicitly abstains. It never falls back to the least-bad threshold.

## Artifacts and dashboard

New model artifacts persist the complete `accuracy_selector_curve` and the optional
`accuracy_operating_point`. The dashboard reads those saved artifacts and displays one of:

- `not trained` — no compatible post-milestone artifact exists;
- `abstain` — a curve exists but no operating point qualified; or
- `shadow nomination` — development evidence nominated a point, which remains shadow-only.

The dashboard never treats a training probability or development nomination as observed
prospective accuracy.

## Next gate

Do not retrain merely to produce a dashboard number from already consumed evidence. The next
step is to freeze the selector protocol and obtain a valid chronological evidence cohort with
complete outcome economics. Only then may the model race run and, if qualified, nominate one
shadow operating point for locked testing.
