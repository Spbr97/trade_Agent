# AEM v2 Milestone 4: mandatory execution-stress gates (code, no nomination yet)

Date: 27 September 2026

Branch: `codex/aem-v2-accuracy-first`

Status: **stress-gate replay implemented and tested against a real, reconstructed
opportunity/outcome fixture; the real pipeline race completed but nominated no
specification, so a real stress replay is not applicable and no live change follows**

## Outcome

The pipeline-race checkpoint explicitly deferred the frozen protocol's mandatory
execution stresses (`aem_v2_contract.DEFAULT_AEM_V2_PROTOCOL.mandatory_stress_cases`):
cost inflation (1.25x / 1.5x), doubled slippage, a one-minute execution delay, and an
adversarial "best 10% of fills never execute" case. This closes that gap: a new module,
`tradedesk_lab/aem_v2_stress_gates.py`, re-resolves the REAL fills a nominated
`(spec_id, top_k)` selected - not a resampled or simulated approximation - through the
same Milestone-1 `resolve_opportunity` engine, under every registered stress case, and
reports the worst-case mean net R. The pass bar is the one AEM v1's own scorecard
already uses (`aem_scorecard.py`'s `stress_economics` gate): that worst case must stay
strictly positive.

## What is implemented

`tradedesk_lab/aem_v2_stress_gates.py`:

- **`run_stress_gates(spec_id, top_k, ...)`**: loads Stage 1's frozen development
  dataset, calls the pipeline race's new `selected_fills_for_spec()` (below) to get the
  exact real fills that policy chose, then re-resolves each one under six cases -
  `base_costs`, `costs_1p25x`, `costs_1p50x`, `slippage_2x`, `one_bar_delay`,
  `adverse_missed_fills` - reusing `aem_v2_events.reconstruct_session_opportunities` and
  `resolve_opportunity` directly against the real minute bars (via the same
  `load_real_session_sources` Stage 1 uses - see below), not a re-derivation.
- **`_CostStress`**: a `CostModel` wrapper scaling `round_trip_cost().total` and
  `slippage_pct` independently, mirroring `aem_staged_validation._CostStress` for AEM
  v2's own cost-model shape.
- **The adversarial case**: the best-net-R 10% of the base case's resolved fills are
  converted to `missed_fill` - the same "if it worked, it probably didn't fill"
  sensitivity AEM v1's validation applies, not a new methodology.
- **`freeze_stress_gates(...)`**: writes the run to
  `data/m14_m18/aem_v2/stress_gates/runs/<id>/report.json` with the same
  register/no-register decision discipline as every other AEM v2 checkpoint.

Two small, disclosed refactors made this possible without duplicating code:

- `aem_v2_development_experiment.py`: extracted the real-data loading (universe audit +
  Milestone-1 manifest + staged minute/daily bars) that `build_development_dataset`
  already did into a new public `load_real_session_sources()`, so the stress replay
  reads through the exact same verified path. `_fixed_risk_quantity` was made public
  (`fixed_risk_quantity`) for the same reason. Stage 1's own behavior and output are
  unchanged - confirmed by its existing 6 tests still passing unmodified.
- `aem_v2_pipeline_race.py`: added `selected_fills_for_spec()`, which re-runs one
  registered spec's nested walk-forward selection with `keep_selected_rows=True` and
  returns the exact selected rows instead of just aggregated metrics. Deterministic:
  the same dataset/spec/top_k/splits/embargo/seed always reproduces the same selection,
  since every fold's fit and ranking is already seeded. `run_pipeline_race`'s own
  output and its 8 existing tests are unchanged.

## A real environment issue found and fixed along the way

Verifying this module's tests failed at import time with `ImportError: DLL load
failed while importing _sgd_fast: An Application Control policy has blocked this
file` - Windows Smart App Control blocking scikit-learn 1.9.1's SGD solver DLL (the
same failure class already documented in `CLAUDE.md` for numpy/pandas/mypy on this
machine). Per that precedent, pinned `scikit-learn==1.5.2` in `pyproject.toml` (was
`>=1.5`) and relocked (`uv lock`, without a full `uv sync` while the always-on
dashboard task holds its lock) - confirmed working via a direct
`from sklearn.linear_model import LogisticRegression` import and the full
`aem_v2_pipeline_race` test suite (which fits a logistic control) passing again. This
was necessary for Stage 2's own tests, not only this module's.

## A real fixture gap found and fixed

The shared test fixture's impulse session (`tests_lab/test_aem_v2_development_experiment.py::_impulse_session`)
padded exactly 60 flat bars after the trigger - just enough for the largest quick-profit
geometry's 60-minute hold window under normal timing, but one bar short once the
`one_bar_delay` stress case shifts the decision timestamp forward. This surfaced as a
real, correct `unresolved: missing_or_incomplete_outcome_window` outcome, not a bug in
`resolve_opportunity` - the fixture simply had zero slack. Extended the suffix to 65
bars (a small, disclosed test-only change); the six pre-existing development-dataset
tests still pass unmodified since none of them depend on the exact suffix length. In
real staged data (375 minute bars/session) this edge only matters for a fill very close
to session close, and the registered adverse-missed-fills/cost/slippage cases already
capture the more common failure modes; the real run will show whether any real fills
sit close enough to session close for one-bar-delay to matter.

## Verification

Five tests, `tests_lab/test_aem_v2_stress_gates.py`: `_CostStress` scales charges and
slippage independently and correctly; `_stress_stats` computes rates correctly and
raises on any status outside `{resolved, missed_fill}`; `run_stress_gates` replays a
REAL development-dataset fixture (the same anticipatory-impulse bar pattern used
elsewhere in this project) end to end and confirms every mandatory case is reported
with a finite mean net R, and that inflating costs by 1.5x never IMPROVES the worst-case
mean net R (a genuine behavioral check on real resolved fills, not just shape); it
raises when a nominated policy selected nothing; and `freeze_stress_gates` writes a
report and its `latest.json` pointer correctly.

`uv run --no-sync ruff check tradedesk_lab/aem_v2_stress_gates.py
tradedesk_lab/aem_v2_pipeline_race.py tradedesk_lab/aem_v2_development_experiment.py
tests_lab/test_aem_v2_stress_gates.py tests_lab/test_aem_v2_development_experiment.py
tests_lab/test_aem_v2_pipeline_race.py` is clean;
`uv run --no-sync python -m pytest tests_lab/test_aem_v2_stress_gates.py
tests_lab/test_aem_v2_development_experiment.py tests_lab/test_aem_v2_pipeline_race.py
tests_lab/test_aem_v2_checkpoint.py tests_lab/test_registry_server.py` passes in full.

Machine-readable evidence is in
[`docs/evidence/aem-v2-stress-gates.json`](evidence/aem-v2-stress-gates.json), honestly
marked `real_run_completed: false` (no specification has been nominated yet).

## Safety boundary

The module only reads Stage 1's frozen CSV and real staged minute/daily bars, and
exists only in `tradedesk_lab`. It is not imported by production scanning, ranking,
alerts, management, risk, dashboard or order code.

## Next checkpoint

The real Stage-2 pipeline race completed against 177,351 rows and none of its 24
registered specifications cleared every development gate. Therefore nothing can be
passed to this replay and Milestone 5 is moot for AEM v2. If a future, genuinely
different candidate is nominated under a frozen contract, its selected fills must run
through these same stress cases before any independent locked evaluation or baseline
claim.
