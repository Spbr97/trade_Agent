# Research checkpoints

## Continued — 24 September 2026

The next isolated validation slice implements the conditional AEM random-policy
comparison and a pre-evaluation liquidity universe/download plan. See
[the benchmark checkpoint](aem-random-policy-checkpoint.md) for results and limitations.
The old pause instructions remain historical, not active. Nothing is promoted live.

## Resumed — 23 September 2026

The user explicitly resumed agents. The historical pause below is superseded.
The previously integrated checkpoint is `705895b`; the new isolated branch is
`codex/nse-anticipatory-validation`. Current work and verification are recorded in
[NSE/AEM validation checkpoint](nse-aem-validation-checkpoint.md).
No production code, dashboard, management service, strategy eligibility, or orders
are changed by this resumption. Existing unrelated Groww work remains excluded.

## Historical pause — 18 September 2026

The user explicitly requested that all agents remain parked until they say
"resume agents" again. No research agent, training run, forward watcher, or
performance experiment may be started before that instruction.

The resumed work saved after commit `6c01ffa` remains work in progress:

- Forward-integrity implementation and expanded regression tests.
- Stricter after-cost outcome accounting and immutable prediction records.
- Reliability model guards for time overlap, unresolved labels, unsafe features,
  abstention, and triggered-pool limitations.
- Support/resistance reversal and breakout/retest intraday research with portfolio
  limits and conservative completed-session checks.
- A separate clean historical dataset builder and new lab-only CLI commands.

Reported targeted checks before the pause were 19 passing forward tests and 13
passing intraday tests. The complete final lab suite was not run. Reliability tests
were blocked during SciPy native import, the new clean-dataset tests were not run,
and real-data evaluation was blocked by the production scanner's DuckDB lock.
The production scanner and dashboard were intentionally left alone.

No new dataset, model, policy, or forward cohort has been activated or promoted.
No accuracy improvement has been demonstrated. The checkpoint is not merge-ready.
Existing unrelated untracked Groww files are excluded from this research commit.

## Previous checkpoint — 16 September 2026

The user requested that work be wrapped up and committed, with agents paused until
the user explicitly asks to resume them. Do not automatically restart agents,
training, or the research forward watcher from this checkpoint.

Branch: `codex/reliability-80-research`, based on `0cba86e`.

## Saved work (unfinished; not deployed or promoted)

- `docs/reliability-70-80-plan.md`: audited research plan and acceptance criteria.
- `tradedesk_lab/outcomes.py`: `net-target-v2` outcome contract, invalid-geometry
  rejection, conservative entry-day ordering, and target success after modeled costs.
- `tradedesk_lab/reliability.py`: temporal model comparisons, calibration, selection
  trials, ensemble/stack candidates, and separate research artifacts.
- `tradedesk_lab/models.py`: Extra Trees research model option.
- `tradedesk_lab/intraday_research.py`: support/resistance reversal and breakout/retest
  prototypes using existing execution/risk components. Tests and empirical runs are pending.
- `tradedesk_lab/forward.py`: unfinished forward-evidence integrity changes including
  pinned cohorts, deadline checks, outcome-contract metadata, and watcher status/locking.
- Tests for the new outcomes and model-comparison modules.

No tracked production `src/` or `config/` files were changed in this checkpoint.
Existing dashboard and management code were not edited or restarted. Existing
untracked Groww work was excluded from this commit.

## Validation at pause

- `python -m pytest tests_lab -q`: 30 passed, 2 failed.
- Failure: `test_activation_excludes_every_watchlist_that_already_exists`; its mocked
  `_load_frozen` accepts one argument but the new collector passes a pinned-run argument.
- Failure: `test_outcome_resolution_changes_only_outcome_fields`; fixture resolves to
  `unsizeable` under the changed forward contract, rather than the expected `resolved`.
  Review both implementation and fixture; do not simply weaken assertions.
- All five new/changed research modules pass `py_compile`.
- Scoped Ruff check: five findings (import ordering, Iterator import location,
  one long line, and one unused import). Cleanup is pending.
- No full new historical evaluation, prospective validation, or performance improvement
  has been demonstrated. This checkpoint is not ready for live use or merge.

## Runtime pause

The three implementation agents were interrupted. The previous lab-only forward
watcher processes (28952 and 16560) were identified by their command lines and stopped.
No `python -m tradedesk_lab` process remained in the post-stop process check.
The production dashboard was left running. Existing frozen model/data artifacts
were not rebuilt or promoted.

## Resume only after explicit instruction

1. Review these partial changes, complete the forward/intraday regression tests, and
   resolve the recorded test/lint failures.
2. Build a separate clean dataset with `manifest['label_version'] = 'net-target-v2'`.
   Reject invalid fills/targets, rebuild point-in-time features, exclude future-dependent
   feature geometry and legacy results countdowns, and audit excluded rows. Preserve
   the old frozen dataset and reports.
3. Wire new CLI commands and lab-only report endpoints/views. The existing CLI and
   dashboard have not yet been wired to these new modules.
4. Run the historical comparisons and intraday diagnostics, then review costs,
   session-level accuracy, coverage, uncertainty, and risk-constrained returns.
5. Review/migrate the old version-1 forward state into a separately documented cohort
   before starting any updated watcher. Do not assume historical results are fresh
   prospective evidence or silently promote a model.

The 70–80% success objective remains a target, not an established result or a guarantee.
