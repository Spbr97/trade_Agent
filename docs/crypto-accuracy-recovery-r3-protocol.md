# Crypto accuracy recovery R3 protocol

## Objective

Test whether smaller, quicker profit targets improve the crypto signal engine's out-of-sample accuracy and expectancy. This is a research-only experiment. It cannot change the reported baseline, authorize live calls, or make a strategy eligible for deployment.

## Frozen experiment

- Evaluate each crypto setup independently.
- Register 16 exit geometries per setup: targets of 0.25R, 0.50R, 0.75R, and 1.00R crossed with maximum holds of 1, 3, 5, and 7 daily sessions.
- Use only information available when each signal was armed.
- Resolve ambiguous target-and-stop bars as stops.
- Include fees, slippage, and the configured VDA tax treatment in expectancy.
- Select geometry only from historical backfill. The explicitly marked live tracker cohort is a separate retrospective validation set and must never influence selection or ranking.
- Use expanding chronological folds with a seven-day embargo, which covers the longest label horizon.
- Keep setup evidence separate. Do not pool evidence to make a weak setup appear stronger.

## Gates

A candidate must meet all frozen requirements:

- development sample at least 100;
- walk-forward sample at least 60;
- live retrospective sample at least 30;
- accuracy at least 50%;
- Wilson lower bound at least 40%;
- positive mean net R;
- positive mean after-tax R;
- at least two positive walk-forward folds.

Missing or invalid evidence is `not available`, never a pass. Even a research pass keeps `baseline_improved=false` and `eligible_for_live=false`; promotion requires a separate prospective protocol.

## Integrity

Every run stores an immutable replay, registered trials, manifest, and compact state. Verification checks the frozen contract, implementation, source tracker, report identity, and artifact hashes. Duplicate tracker signal IDs fail closed.

## Decision rule

If no geometry passes, retain the current engine and record a valid rejection. Do not relax the gates after seeing results. The next experiment must change signal selection rather than repeatedly searching exit geometry.

