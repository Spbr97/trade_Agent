# Self-learning Milestone 6 checkpoint — session refresh

Completed: 2026-10-06

This is the first checkpoint of Milestone 6, not completion of the full scheduled
challenger workflow.

After every NSE, BSE or crypto tracker run, the workflow now:

- resolves mature calls through the market tracker;
- rebuilds the sealed prospective dataset deterministically;
- refreshes failure attribution and exclusion counts;
- measures newly mature sealed evidence since the last completed challenger run;
- records the remaining evidence needed for a challenger dataset; and
- writes an atomic market-specific status file.

The threshold is currently 20 newly mature sealed calls before the workflow can report
`ready_for_weekly_challenger`. Repeated status refreshes do not consume or forget that
evidence. Only a future completed challenger-training run may advance the consumed-evidence
boundary.

Every status explicitly records:

- `active_model_changed: false`;
- `promotion_authorized: false`;
- eligible/new evidence counts;
- exact blockers; and
- the current deterministic dataset ID.

The dashboard Learning panel exposes workflow state, eligible evidence, new challenger
evidence, blockers, and whether the active model changed.

Still required before Milestone 6 is complete:

- calibrated accuracy/expectancy refresh;
- performance, confidence and data-drift detection;
- versioned weekly baseline/challenger training;
- monthly locked chronological and random-control evaluation; and
- a durable registry of negative and inconclusive challenger experiments.
