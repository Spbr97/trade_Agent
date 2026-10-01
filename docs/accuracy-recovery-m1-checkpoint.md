# Accuracy recovery milestone 1 — research/live separation

Date: 2 October 2026

Status: **implemented and tested; no accuracy improvement claimed**

## Outcome

Passing the research gauntlet no longer grants a detector live-call rights. New detectors
approved through self-review are installed as `research_only_markets` and remain available
to the market trackers for forward shadow evidence. The live scanner excludes them.

The accuracy-first live eligibility gate is restored and strengthened:

- at least 500 resolved observations;
- at least 100 out-of-sample observations;
- at least 80% observed success;
- a two-sided 95% Wilson lower bound of at least 70%;
- non-negative after-cost expectancy; and
- at least +0.10R versus matched random timing.

Every existing self-review-generated detector is now shadow-only on the market where it was
validated. This includes `r_adx_thrust_tsmom_up_trend_trail`, whose 43.75% historical hit
rate is useful research evidence but does not qualify as an accuracy-first live strategy.

The dashboard now reads the live policy from configuration and shows the observed-rate
target, Wilson floor, evidence requirements, and installed shadow-only detectors.

## Integrity repair

The checkpoint also repaired a duplicate self-review installation of
`r_tsmom252_rsi_momentum_trend_trail` that had inserted the same enum member, import,
registry entry, and YAML block twice and prevented the application from importing.
Self-review now refuses to install a detector whose module already exists.

## What this does not claim

This milestone prevents lower-accuracy research from being presented as live-qualified. It
does not raise the canonical 21.50% AEM baseline, does not prove 50% accuracy, and does not
prove the requested 70–80% prospective accuracy. The next milestone must build and evaluate
the high-precision selector on fresh chronological evidence while preserving abstentions and
zero-call sessions.
