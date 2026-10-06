# Self-learning Milestone 11 checkpoint — promotion and rollback control

Completed: 2026-10-06

No model was promoted by this checkpoint.

The control module now supports an integrity-sealed promotion lifecycle while keeping user
approval outside automatic learning.

## Prospective registration

A challenger can register a forward cohort only after the production sample threshold,
the preregistered selector accuracy/net gate and matched random-timing margin have passed.
It must contain one frozen market/strategy/feature/model/outcome-contract cohort and one
explicit selector operating point.

Registration freezes the dataset/model/configuration hashes, features, coefficients,
selector policy, selector operating point, execution contract and a `starts_after` boundary.
It records zero known outcomes, `source: forward_only_live` and `backfill_allowed: false`.
Registering a different cohort over an existing record is refused.

## Review and explicit authority

A promotion review is unblocked only when:

- the forward result matches the registered cohort and passes at least 100 calls across 30
  sessions, 80% accuracy, 70% Wilson lower bound, positive net and after-tax expectancy,
  +0.10R over matched random timing and source integrity;
- an independent reproduction hash matches the frozen challenger; and
- the side-by-side frozen baseline/challenger scorecard is preserved.

Even then the review status is only `awaiting_explicit_user_approval`. Promotion raises a
permission error unless a caller supplies an explicit user-approval flag and a non-empty
approval reference. This work did not invoke that function.

Every promotion is hash sealed. The Personal Calls endpoint now validates that seal, the
approval evidence, exact experiment identity, contract and final gates. A handwritten or
tampered approval file cannot enable a call.

## Pause and rollback

The health transition converts an approved promotion to `paused` when data is stale,
performance/calibration drift is detected or the outcome contract mismatches. The previous
verified promotion is embedded in every new promotion so an explicitly requested rollback
can restore it. Rollback without an explicit request is refused.

Scheduled invocation of the post-promotion health transition and a raw-row prospective
collector are still unfinished. Accordingly, those roadmap items remain open and no active
promotion file exists for NSE, BSE or crypto.
