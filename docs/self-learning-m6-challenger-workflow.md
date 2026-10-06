# Self-learning Milestone 6 checkpoint — sealed challenger workflow

Completed: 2026-10-06

This checkpoint extends the session refresh without changing any active strategy, model,
eligibility gate, dashboard management route or trading authority.

## What now runs for NSE, BSE and crypto

Every market tracker rebuilds its own prospective learning dataset from the immutable
prediction ledger and refreshes:

- strict qualified-call accuracy and its 95% Wilson interval;
- Brier score and confidence buckets where a sealed probability exists;
- gross, net and reporting-only after-tax expectancy;
- the counterfactual shadow/rejected scorecard, kept separate from headline performance;
- contract-specific scorecards without pooling quick-profit and swing outcomes; and
- adjacent-window performance, confidence and prediction-feature drift.

Pending, invalid and never-triggered calls cannot enter any of those calculations. Failed
valid calls do enter both the performance scorecard and challenger training.

## Scheduled challenger rules

At least 20 newly mature sealed calls are required. A diagnostic challenger also needs at
least four independent sessions, both outcome classes in development data, one frozen
model/strategy/feature/outcome-contract cohort and usable variation in the preregistered
feature list.

The only learner in this checkpoint is a transparent, standardised L2 logistic regression.
Its feature list, seed, chronological split and minimum Brier improvement were fixed in
code before ledger evidence was inspected. Dynamic geometry and score-component fields are
not searched.

The final sessions are held out chronologically. Their signal IDs are written to a durable
locked-evidence registry. A later challenger excludes those IDs before fitting or testing,
so an inspected outcome can never be presented again as fresh locked evidence.

Every completed experiment stores:

- the immutable source, development and locked-test dataset snapshots;
- frozen sealed-model and challenger accuracy/Brier/selected-net-R scorecards;
- coefficients, imputation values and feature scaling needed to reproduce the candidate;
- performance by regime, sector and session;
- a deterministic 1,000-repeat matched random-selection control;
- negative, inconclusive or development-only conclusions; and
- exact promotion blockers.

An improvement seen in development but not in the chronological tail is explicitly recorded
as `completed_development_only_rejected`. Execution failures are also appended to the
experiment registry. Re-running the same dataset/configuration is idempotent.

## Authority remains frozen

Every report states:

- `active_model_changed: false`; and
- `promotion_authorized: false`.

Even a development candidate remains blocked until a valid random-timing control, a
registered forward prospective cohort and explicit human approval exist. Random timing is
not fabricated from the call ledger: it requires a separately frozen universe and candle
cohort, so it remains the next unfinished Milestone 6 control.

## Current live evidence

At implementation time the new prospective dataset still contained no mature sealed rows;
the existing records were legacy unsealed evidence. Therefore this checkpoint improves the
measurement and learning machinery but does **not** claim any increase in NSE, BSE or crypto
baseline accuracy. The first real challenger will run only after each market independently
meets the evidence requirements above.
