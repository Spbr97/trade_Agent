# M14-M18 isolated research preview

Branch: `codex/m14-m18-research-preview`. Existing files, configs, scheduled tasks,
management code and dashboard stay byte-for-byte intact. The 234 original tracked
and untracked source/config/document files were hashed before implementation;
`python -m tradedesk_lab verify-base` checks the saved manifest.

## Run

From the repository root, using its existing Python 3.12 environment:

```powershell
.\.venv\Scripts\python.exe -m tradedesk_lab prepare
.\.venv\Scripts\python.exe -m tradedesk_lab train
.\.venv\Scripts\python.exe -m tradedesk_lab serve --port 8766
.\.venv\Scripts\python.exe -m tradedesk_lab forward
.\.venv\Scripts\python.exe -m tradedesk_lab forward --watch --interval-seconds 900
.\.venv\Scripts\python.exe -m pytest tests_lab -q
.\.venv\Scripts\python.exe -m tradedesk_lab verify-base
```

Preview: http://127.0.0.1:8766. The existing dashboard remains on port 8765.
New outputs, SQLite registry, model bundles, dataset cache and optional dependencies
live under the already-ignored `data/m14_m18/`. No lab output is written into the
production model, journal, watchlist, review queue or signal log directories.

Optional CatBoost dependencies can be installed without modifying `.venv`:

```powershell
uv pip install --python .venv/Scripts/python.exe --target data/m14_m18/deps --no-deps -r tradedesk_lab/requirements-optional.txt
```

The lab entry point adds only this dependency directory to its own import path.
Unavailable CatBoost/LightGBM families are reported explicitly; two families are
required for an agreement measurement. XGBoost and logistic reuse installed libraries.

## Milestones

- **M14:** actual label intervals, session embargo, nested chronological validation,
  15 CPCV splits reconstructed into five complete paths, DSR function, symmetric
  setup-level PBO with ranks and tied-rank treatment. CPCV uses predefined family
  parameters, independently refit per split, not the tuned winner's parameters.
- **M15:** optional CatBoost, calibrated logistic/XGBoost/LightGBM, temporal held-out
  calibration, per-family inner-fold tuning, average-probability ensemble, fixed
  risk-on and agreement vetoes. Thresholds are chosen inside each outer training
  fold; abstention threshold 1.01 means no qualifying positive-net population.
- **M16:** immutable display-only decisions retaining original prices, sizes and
  rejection reasons. An eligible but unconfirmed entry is WATCHLIST. A TRADE needs
  confirmed trigger, healthy data and risk approval. Existing holdings are HOLD;
  reduction/exit decisions require supplied existing-rule events. Model drift and
  proximity to a stop do not create a new trading rule.
- **M17:** separate SQLite registry with WAL, experiment and candidate UUIDs,
  attempted configuration records, unavailable-family reporting, content/config
  hashes, artifact SHA-256 and one-time market/date holdout reservation. Database
  read routes open SQLite read-only and never create tables.
- **M18:** separate dashboard, comparison charts, development/holdout toggle,
  model diagnostics, CPCV/PBO, existing-call explanations and experiment details.

## What the numbers mean

The initial benchmark covers NSE's existing saved candidates, with real cached
candles, actual outcome end dates and the base exit/cost functions. The old CSV's
results countdown is excluded because it predates the project's leakage fix.
The same pre-existing candidate population is used for every model. Portfolio
comparisons rerun the base position, heat, sector, gap and loss limits per selection.
The existing partial sector mapping is inherited. Candidates omitted by the original
generator while holding a symbol cannot be reconstructed by filtering this export.

**This historical period has already been examined in earlier research.** The latest
20% is a frozen diagnostic holdout within this experiment, not new forward evidence.
The registry reserves it before scoring to prevent accidental repeat selection. A
failed run after reservation does not erase that reservation. New forward evidence
must be accumulated after the selected model is frozen before a live-promotion claim.

Accuracy is the T1-before-stop label rate; net win rate and expectancy use actual
current exit rules and costs. These can differ due to the five-session time stop.
Every accuracy result includes sample size, coverage and Wilson's lower bound.
Economic lift uncertainty uses paired ten-session block bootstrap samples.

DSR's implementation uses unannualized Sharpe and Pearson kurtosis. A displayed
stage-comparison DSR has a narrower scope than the entire project's search history;
unknown past feature/parameter trials prevent an honest project-wide DSR. It is
never a promotion criterion. PBO compares three setup return streams on the same
calendar; this is distinct from proving superiority over random entry timing.

All live eligibility defaults remain binding. Higher displayed model probabilities
are not proof of higher actual hit rate, and fewer calls are acceptable if no filter
has adequate evidence. No model or setup is automatically promoted by the lab.

## Review / later merge

Review only new `tradedesk_lab/`, `tests_lab/`, and `docs/m14-m18-preview.md` files.
The pre-existing dirty files and Groww additions remain the user's work. No push,
merge, scheduled task change or production restart is part of this preview.

## First measured run — 2026-09-16

Run `a5f42cd2f0784b59b96683395cad801a` evaluated 29,964 historical NSE candidates
across 1,527 symbols. The frozen diagnostic segment starts 2026-02-02.

| Historical diagnostic | Candidate count | T1 hit rate | Replay trades | Net replay P&L |
| --- | ---: | ---: | ---: | ---: |
| Existing candidate population | 6,657 | 26.5% | 149 | −₹9,694 |
| Each model / ensemble selection | 0 | Not estimable | 0 | ₹0 |

Logistic, XGBoost and CatBoost completed. LightGBM was unavailable. No threshold
qualified on development data, so all models abstained on the historical holdout.
This does **not** establish improved accuracy or a profitable edge. Replay losses
are hypothetical losses for frozen candidates, not the user's live portfolio.

All 15 CPCV splits completed, giving five paths. The fixed 0.50 stress threshold
selected 9, 43, 2, 0 and 0 candidates respectively; all nonempty paths had negative
net expectancy. Setup PBO was 0, which can mean stable relative rankings among
losing setups; it is not a profitability test. Stage DSR was not estimable because
selected strategies had flat returns. Project-wide DSR remains unavailable.

The original plan's modifications to production CLI, scan wiring, dependency files,
dashboard, PLAN.md and CLAUDE.md were intentionally replaced by isolated equivalents
to respect the no-base-changes requirement. Decisions expose missing forecasts as
null rather than inventing expected returns or holding periods. Agreement shown
here is frozen historical scoring, not newly scored live calls.

## Prospective shadow evidence

`tradedesk_lab forward` freezes the completed run's artifact hashes and creates a
prospective cohort. Its activation boundary is the newest watchlist already present,
so older files cannot be relabelled as forward evidence. Later runs ingest only newer
NSE watchlists, construct features using candles available at the arming close, save
the model-family probabilities once, and later resolve triggered calls with the base
daily entry and triple-barrier rules. The first run activates the clock; `--watch`
polls for new watchlists and newly available candles.

The collector reads production watchlists and DuckDB candles but writes only
`data/m14_m18/forward/state.json`. Scores never flow back into a watchlist, journal,
portfolio, configuration or production dashboard. The dashboard's Forward evidence
tab distinguishes every scored observation from the research-selected cohort. The
current frozen run has no development champion, so selected-call count remains zero
while all new candidates are still observed for calibration and later research.
