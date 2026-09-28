# Self-review loop, Phases 1-3 plus dashboard wiring (all implemented and tested; no
proposal applied to real production config yet)

Date: 27 September 2026

Status: **Phases 1 (config-tuning), 2 (retire/replace) and 3 (new detector code generation)
all implemented and tested end to end against real fixtures and a throwaway git repo. The
dashboard's Review tab is now wired to actually apply a self-review approval, per explicit
user request ("approve should mean something, not just bookkeeping - it should apply"): see
"Dashboard wiring" below. The rolling-failure monitor's CLI (`tradedesk review
rolling-check`) has been run for real against the live NSE/BSE/crypto signal-tracking logs -
see "Real first run" below - and correctly flagged all three live setups on all three
markets. Three market-scoped crypto retirement proposals now exist in the real review
queue, all still pending, and none has been applied to `config/setups.yaml`. They contain
no passing replacement candidate. Replacement research is currently fail-closed because
`tradedesk_lab`'s `verify_base()` detects legitimate production drift after the last human
manifest refresh; see "Known operational gap" and "Replacement research" below.**

## Why this exists

The user asked, across this session, for the agent to keep improving the accuracy baseline
by analyzing its own failed calls (including the daily predictions that never became
tradeable, not just live alerts) and fixing what's wrong - autonomously proposing changes,
but asking for one review before adapting anything. Given the project's own repeatedly
confirmed finding that the three live setups show no real edge at all against random timing,
"self-improvement" here cannot mean tuning parameters on a signal source with nothing to
tune into an edge - it has to mean searching for and rigorously validating real changes, the
same way this project's manual research already does, closed into a loop instead of needing
a human to drive every step.

See the approved plan for the full design and the three explicit scope/process decisions the
user made (config+retire+new-detector authority; per-change review, not blanket approval;
full existing gauntlet rigor required before any proposal is even shown).

## What is implemented

- **`rolling_failure_monitor.py`**: a continuous, sustained-failure trigger, separate from
  (not a replacement for) `signal_tracker.flag_setup_failures`'s lifetime-cumulative check.
  Computes a trailing rolling window daily, and only calls a setup's failure "sustained" after
  `consecutive_windows_required` (default 3) daily windows in a row are all below the floor -
  a single bad window instead triggers a lighter severity. Reads the exact same per-market
  `TrackedSignal` logs that already track every prediction a setup makes, tradeable or not.
- **`proposals.py`**: the structured, machine-actionable layer `review_queue.py` deliberately
  doesn't have - a `ConfigPatch`/`RetireSetup`/`NewDetectorCode` tagged union, each carrying
  the full `GauntletReport` that validated it. `ReviewItem` gained one additive
  `proposal_ref` field; every existing caller and row is unaffected.
- **`scripts/entry_search.py`**: extracted `optimize()`'s core loop into a reusable
  `optimize_rule()` (the CLI command is now a thin wrapper, behavior unchanged) - not
  currently used by the config-tuning path (see below), but available for a future
  RETIRE_SETUP replacement search (Phase 2).
- **`self_review/config_tuning.py`**: searches ONE of a failing setup's own named
  `config/setups.yaml` parameters at a time (a small, verified-real registry per setup - not
  a guessed or invented geometry model), using the REAL event-driven backtester
  (`backtest/runner.py::run_backtest` + `backtest/reports.py::walk_forward`), since these
  setups' actual exits (partial target + ATR trailing stop) don't fit `entry_search.py`'s
  simplified fixed-target model. Picks the TRAIN-half best value, requires the SAME run's
  TEST half to also be expectancy-positive, and only then runs the candidate through the full
  harness gauntlet.
- **`tradedesk_lab/harness/run_self_review_candidate.py`**: adapts the real backtest's closed
  trades into `HarnessTrade`s and calls `run_gauntlet` unmodified - the same random-entry
  benchmark, walk-forward, and kill-criteria bar that killed the three live setups and
  `MomentumContinuation`. Also checks the extra `must_beat_random_by_r=0.10` margin
  `engine/scoring.py::eligibility()` already requires of every live setup, computed from the
  gauntlet's own random-entry-benchmark numbers. A candidate proposal is only ever produced if
  ALL of local train/test positivity, the full gauntlet, AND this margin pass.
- **`self_review/decision_packet.py`**: exactly one `ReviewItem` + linked `Proposal` per
  gauntlet-passed candidate. Rejecting an item stamps a 30-day cooldown on its proposal (via
  `decide_with_cooldown`, not the base `review_queue.decide`), so the same
  (kind, market, setup) fix isn't silently re-proposed immediately - approving never sets a
  cooldown.
- **`self_review/apply.py`**: the only code anywhere that turns an approved item into a real
  change. Refuses unless `status == "approved"`. Patches `config/setups.yaml` with a
  surgical single-line text substitution (preserving every existing narrative comment
  byte-for-byte - not a full YAML parse/dump round trip, which would strip them), commits via
  git with a `[self-review]`-prefixed message and the same `Co-Authored-By` line this
  session's own commits use, and writes a hash-pinned before/after evidence file plus an
  appended entry in `docs/self-review-applied-log.md`. A positive `ALLOWED_WRITE_PATHS`
  allowlist, checked in the function's own code, refuses to write anywhere else - Phase 1
  only ever exercises the `config/setups.yaml` path; the setups/`engine/patterns.py` paths in
  the allowlist are there for Phase 3 and are unused until then.
- **`self_review/retire_replace.py`** (Phase 2): proposes retirement ONLY on `SUSTAINED`
  severity (never on a `SINGLE`-window flag - a config-tuning candidate is the response to
  that, not retirement). A bare retirement makes no new positive performance claim, so it is
  not run through the harness gauntlet - the sustained real failure record itself is the
  evidence. Also checks `tradedesk_lab/registry.py`'s SQLite for an already-gauntlet-passed
  candidate under a `<market>:<setup>:replacement` family, which today almost always returns
  nothing (nothing yet writes candidates under that convention) but is real, correct,
  tested code, not a stub.
- **`self_review/apply.py`** extended for `RETIRE_SETUP`: inserts a dated, item-id-stamped
  comment line directly above the retired setup's block (matching `config/setups.yaml`'s own
  convention of narrating every change inline) and flips its `enabled` to `false` - both via
  the same surgical, comment-preserving text patch as the config-tuning path. A
  `replacement_source`, if the proposal carries one, is informational only: `apply()` never
  auto-promotes it into config - a replacement is always its own separate proposal and
  approval, applied by its own separate `apply()` call.
- **Crypto correction**: the original plan assumed crypto's setups run on a separate H1/
  intraday path needing its own harness adapter. Verified against
  `scripts/crypto_signal_tracker.py` this session: crypto actually runs the exact same
  daily-interval (`Interval.D1`) setups through the same `backtest/runner.py` engine as
  NSE/BSE, just with `CryptoCostModel`/different `qty_step`/`min_notional`. Since
  `config_tuning.py` and the harness adapter already take a generically-built
  `MarketData`/`BacktestConfig` from the caller (they never hardcode NSE-specific costs or
  sizing), they are already crypto-compatible with no extra code - confirmed, not assumed.

## Phase 3: new-detector generation

- **`tradedesk_lab/candidates/`** (new package): mirrors the M14-M18 isolation contract.
  `base.py` defines `LabSignal`/`LabSetup` - a duck-typed stand-in for production's
  `Signal`/`Setup` that never constructs a real, enum-typed `Signal` (production's
  `SetupKind` is a fixed 3-member `StrEnum`; a candidate cannot be a real member of it
  before a human approves it, and pydantic would reject any other string). Because
  `evaluate_entry`/`evaluate_exit` (`backtest/fills.py`) only ever read attributes off
  whatever object they're given - Python doesn't enforce the `Signal` type hint at
  runtime, and `Position`/`ClosedTrade` are plain, non-validating dataclasses - a
  `LabSignal` duck-types cleanly through the exact same tested fill/exit primitives the
  real backtester uses, with no touch to `SetupKind`/`REGISTRY` at all. This is the
  design the user chose explicitly over provisionally registering an unapproved candidate.
- **`tradedesk_lab/candidates/mean_reversion_v1.py`**: a real, concrete candidate - the
  best-measured GROSS-edge rule in this project's history (`rsi2<10 & close>ema50`,
  entry_search.py, +0.077R vs random timing, t=7.04) wrapped into the production
  trigger/stop/partial-target shape, using the exact winning geometry (3x ATR stop, 2R
  target, 10-session hold) this project's own locked-test search already found for this
  rule. Used to prove the Phase 3 machinery end to end, not expected to newly succeed
  where this same rule has already failed under two other execution models.
- **`tradedesk_lab/harness/run_lab_candidate.py`**: the "separate, simpler validation
  loop" - single-position-per-stock, no portfolio/sector/heat caps, built from
  `backtest/fills.py::evaluate_entry`/`evaluate_exit` and
  `risk/sizing.py::position_size` directly (reused, not re-derived). Produces real
  `backtest.portfolio.ClosedTrade` records, and `to_harness_trades()` adapts them into
  `HarnessTrade`s (deriving `entry_bar`/`window_end` from the same daily frames used for
  simulation) for the unmodified harness gauntlet.
- **`self_review/detector_authoring.py`**: `verify_base()` gate, `Registry` bookkeeping
  (one experiment + one candidate row per authoring attempt, family
  `<market>:<name>:replacement` - the same convention `retire_replace.py`'s lab-registry
  query already looks for), the same `+0.10R`-over-random margin check as Phases 1-2,
  and - only on a real pass - generates the production-shaped counterpart source
  (`_mean_reversion_v1_production_source`, using `make_signal()`/`SetupKind.<NAME>`,
  templated per candidate rather than a generic lab-to-production transpiler) and writes
  it to a reviewable artifact file BEFORE any proposal or approval exists.
- **`self_review/apply.py`** extended for `NEW_DETECTOR`: reads that pre-generated,
  already-reviewable production source and copies it verbatim into
  `src/tradedesk/setups/<name>.py`; appends one `SetupKind` member to `engine/signals.py`;
  appends one import + one `REGISTRY` entry to `setups/__init__.py`; appends one
  `enabled: true` block to `config/setups.yaml` - four small, targeted text insertions,
  each independently tested against realistic fixtures, all in one commit. Refuses
  outright if the pre-generated source file is missing (never falls back to generating
  code at apply time).

## Dashboard wiring: approve now means apply

The user explicitly asked that "approve should mean something, not just bookkeeping - it
should apply." Implemented as a behavior split by whether a `ReviewItem` carries a
`proposal_ref`, not a new endpoint that bypasses the old one:

- **Every existing producer** (`tradedesk review week`, drift checks, `flag_setup_failures`,
  `flag_research_findings` - none of which set `proposal_ref`) is completely unchanged:
  `POST /api/review/decide` still only flips `status`/`decided_at`, exactly as
  `review_queue.py`'s own docstring has always promised.
- **A self-review item** (`proposal_ref` set): approving calls `decide_with_cooldown` (so
  a later rejection would still get its cooldown - approving itself sets none) and then
  IMMEDIATELY calls `self_review/apply.py::apply()` in the same request.
  `apply()`'s own guards still run (status must already be `"approved"`, the proposal file
  must still be present) - this closes the human-in-the-loop gap without weakening it: one
  click is now one action end to end, not two separate steps a human could forget the
  second half of.
- **If `apply()` refuses** (e.g. a missing pre-generated source, a git failure, `verify_base()`
  drift for a `NEW_DETECTOR`), the decision is NOT silently rolled back - the item stays
  `"approved"` and the response reports `apply_error` explicitly, so the dashboard shows a
  visible "approved, NOT applied" state rather than hiding the failure or pretending nothing
  happened. A human can fix the underlying issue and re-approve (idempotent on the file
  writes) or reject the item.
- **`GET /api/review/{item_id}/gauntlet`** (new): the full `GauntletReport` and proposal
  payload behind a self-review item, for a "show me the actual validation" detail view,
  rather than just the one-line summary `ReviewItem.proposal` shows.
- **`GET /api/review`**: now additionally reports `applied: true/false` for any item with a
  `proposal_ref`, computed by checking `self_review/apply.py`'s own `APPLIED_DIR` evidence
  file - lets the list correctly distinguish "approved and applied" from the rare
  "approved but apply failed" case.
- Frontend (`static/index.html`): the Review tab's Approve button now reads **"Approve &
  Apply"** for a self-review item (plain "Approve" for everything else), shows a
  "self-review" tag, and a decided self-review item shows "approved & applied" or
  "approved, NOT applied" instead of the old bare "approved". A collapsed-by-default
  **Details** disclosure (2026-09-27 request: "which call failed, why, and what the agent
  decided to self-learn from it") lazy-fetches `/api/review/{id}/gauntlet` on first open and
  renders it as plain English via `describeDecision()` - a `config_patch`/`retire_setup`/
  `new_detector` payload turned into one sentence ("Tune X: param a -> b" /
  "Retire X, suggested replacement: Y" / "Author new setup Z from lab candidate ...") plus
  the evidence sentence and pass/fail verdict, with the raw JSON still available underneath
  a second, nested disclosure for full transparency. Replaces the original bare `alert()`
  popup (a JSON dump with no explanation), which is not what "what did the agent decide"
  should mean.
- `safe_filename()` (the item-id-to-filename convention) was promoted from
  `proposals.py`'s private `_safe_filename` to a shared, public helper, since three places
  now need to agree on it (writing the proposal file, writing the applied-evidence file,
  and the dashboard's applied-status lookup) - one definition, not three copies that could
  drift apart.

Verified live: restarted the real dashboard process, confirmed `/api/review` returns the
additive `proposal_ref`/`applied` fields correctly for the real (non-self-review) queue
items, and confirmed `/api/review/{id}/gauntlet` correctly 404s for an item with no linked
proposal. 4 new tests in `tests/dashboard/test_review_endpoints.py` cover the plain-item
bookkeeping path, the self-review apply-on-approve path (including a real git commit and
file write against a throwaway repo), the reject-stamps-cooldown-never-applies path, and the
apply-refused-but-decision-recorded path. No visual browser screenshot was taken (no
browser-automation tool was available in this environment) - the frontend logic was verified
by direct correspondence to the tested API contract, not by rendering it.

## Resolved: `verify_base()` baseline refreshed, 2026-09-27

`tradedesk_lab/artifacts.py::protect()`'s baseline manifest was stale (16 September) against
13+ legitimate production changes made since. Flagged as a human decision above rather than
worked around unilaterally; the user refreshed it themselves on 2026-09-27 (deleted
`data/m14_m18/base_manifest.json`, kept a `.bak` copy, re-ran `protect()`). New baseline:
290 files hashed, `verify_base()` reports `{'unchanged': True, 'changed': []}` right after.
`detector_authoring.py::author_and_validate` can now actually author a Phase-3 candidate
against real data instead of refusing on `BaseDrifted`.

## Real first run

`tradedesk review rolling-check --market {nse,bse,crypto}` was run for real (27 September)
against the live signal-tracking logs. First-ever observations, so every result is `single`
severity (sustained requires 3 consecutive daily observations, which cannot exist yet):

- NSE: `nr7_breakout` 16% over 212 resolved; `trend_pullback` 21% over 103 resolved.
- BSE: `base_breakout` 10% over 221; `nr7_breakout` 22% over 1,327; `trend_pullback` 17% over 212.
- Crypto: `base_breakout` 8% over 37; `nr7_breakout` 17% over 363; `trend_pullback` 9% over 92.

All nine numbers are consistent with everything already found earlier this session via
`signal_tracker.flag_setup_failures` and the manual dashboard pulls - this is the same
underlying reality now flowing through the new, continuous monitor. `data/reviews/
rolling_windows.jsonl` now holds these first real observations; the monitor needs to run
daily for at least 3 days before it can produce a `SUSTAINED` (retire-eligible) result.

## Disclosed approximation

The harness adapter uses `Signal.t2` (the setup's own existing "final target" field, already
computed for its net reward:risk gate) as `HarnessTrade.target`, even though these setups
actually exit via a partial-target-plus-ATR-trailing-stop, not a single fixed target. This
means the gauntlet's random-entry benchmark compares against a fixed-target proxy rather than
replaying the real trailing-stop mechanics under a random entry. This is disclosed, not
silent - a stricter version (replaying the real exit logic itself under random entries) is a
worthwhile future improvement, not a Phase-1 blocker, since the primary mandatory bar (the
same +0.10R-over-random margin every live setup must already clear) is checked explicitly
rather than relying only on the gauntlet's own looser pass/fail.

## Verification

55 new tests across `tests/unit/test_proposals.py`,
`tests/unit/test_rolling_failure_monitor.py`, `tests/unit/test_entry_search_optimize_rule.py`,
`tests/unit/test_self_review_config_tuning.py`, `tests/unit/test_self_review_decision_packet.py`,
`tests/unit/test_self_review_retire_replace.py`, `tests/unit/test_self_review_apply.py`
(now including the two NEW_DETECTOR cases), `tests/unit/test_self_review_detector_authoring.py`,
and `tests_lab/test_run_lab_candidate.py` (including one full, real, end-to-end
`run_gauntlet` call), plus 3 additive tests in the existing `tests/unit/test_review_queue.py`.
Every `apply()` test runs against a throwaway `git init` repo under `tmp_path` via the
function's injectable `root`/`lab_output` parameters - nothing in this feature's test suite
ever writes to or commits into the real repository. `uv run --no-sync ruff check` is clean on
every new/changed file, and the full `uv run --no-sync pytest -q` plus
`uv run --no-sync python -m pytest tests_lab -q` regression suites pass with these
additions included.

## Safety boundary

`apply()`'s allowlist does not include anything under `broker/`, `orders/`, or any
order-placement path (M12 remains completely out of scope, untouched by design). No proposal
has ever been auto-applied without a prior explicit "approved" status on its specific
`ReviewItem`.

## Crypto activation, 2026-09-27 - two real bugs found running it for the first time

The self-analysis loop was NSE-only through 2026-09-26 (see CLAUDE.md's "Scope limits"); from
2026-09-27, on explicit user instruction, it also runs for crypto (BSE still excluded). The
loop's own validation path never depended on a paper book - only on the harness gauntlet's
backtest-based rigor, already market-generic and tested against crypto earlier this session -
so no code change was needed to make crypto *eligible*. Two real bugs surfaced running it for
real for the first time, neither ever caught by the test suite (both are pytest-vs-console-
script or single-run-vs-repeated-run gaps that only show up outside a test harness):

1. **Console-script import gap**: `tradedesk review self-review-run` failed with
   `ModuleNotFoundError: No module named 'tradedesk_lab'`. `orchestrate.py` (and
   `config_tuning.py`/`retire_replace.py`/`detector_authoring.py`/`apply.py`) import
   `tradedesk_lab` - a top-level package outside `src/` - at module level; `python -m`/pytest
   add the repo root to `sys.path` automatically, the `tradedesk` console script does not
   (the same gap CLAUDE.md already documented for the dashboard). Fixed once, in
   `self_review/__init__.py` (runs before any sibling submodule, since Python always
   initializes a parent package first), rather than repeating the fix in five files.
2. **Duplicate same-day observations manufacturing a false SUSTAINED verdict**: while
   re-running the command to debug bug #1, `rolling_failure_monitor.append_observations` was
   called on crypto three times in one day. It had no dedupe against its own documented
   contract ("one row per (market, setup, as_of_date)") and just appended every time.
   `find_sustained_failures`'s "most recent N observations" check then read those 3 duplicate
   same-day rows as 3 distinct days and classified all three crypto setups as `SUSTAINED`,
   which `orchestrate.py` correctly-per-its-own-logic turned into 3 real RETIRE proposals -
   `RETIRE base_breakout/nr7_breakout/trend_pullback on crypto` - actually written to
   `data/reviews/queue.jsonl` and `data/reviews/proposals/`. Caught before anyone could
   approve them (all three were still `pending`). Fixed `append_observations` to be
   idempotent per (market, setup, as_of_date); deduped the 3 accidental rows out of
   `data/reviews/rolling_windows.jsonl`; deleted the 3 bogus queue items and their proposal
   files outright (not "rejected" - a human never evaluated them, and reject's 30-day
   cooldown machinery would have been the wrong signal for "this was never real evidence").
   Added a regression test (`test_append_observations_is_idempotent_per_market_setup_and_day`)
   asserting a second same-day call is a no-op and cannot manufacture SUSTAINED from one real
   day. Re-ran clean afterward: `rolling-check --market crypto` correctly reports `SINGLE` for
   all three (8-17% hit rate over 37-366 resolved calls, each its own single flag), and
   `self-review-run --market crypto` correctly found the config-tuning search had nothing that
   cleared the harness gauntlet - "no new proposal submitted" - consistent with this project's
   standing finding that none of the three setups have a measured edge on crypto either.

## Orchestration: monitor to proposal, end to end

`src/tradedesk/self_review/orchestrate.py::run_self_review_for_market(market)` closes the
gap above: it calls `rolling_failure_monitor.run_daily_check(market)` for real, and for each
flagged failure dispatches by severity - `SUSTAINED` to `retire_replace.propose_retirement`
(never a config patch), `SINGLE` to `config_tuning.propose_config_patches` (never a
retirement) - then submits whatever comes back via `decision_packet.submit_for_review`. It
never applies or approves anything itself; a human still has to act on the resulting
`ReviewItem` in the dashboard, which is the point where "approved" now actually calls
`apply()` (see "Dashboard wiring" above). Market data (reference code, VIX, NSE sector
config) is loaded once per call via `load_real_market_data`, mirroring `cli.py::backtest`'s
own setup rather than re-deriving it, and only when there is at least one failure to act on
(no failures -> no market-data load, confirmed by test). An unknown setup name the monitor
tracks but that isn't a real production `SetupKind` is skipped, not crashed on.

Wired into the CLI as `tradedesk review self-review-run [--market nse|bse|crypto]
[--max-codes N]`, alongside the existing `week`/`rolling-check` subcommands in
`cli.py::review`. Tested in `tests/unit/test_self_review_orchestrate.py` (5 tests, including
one real, non-mocked run of `load_real_market_data("nse")` against the actual local DuckDB
store - confirmed it returns real features/calendar and the three real `SetupKind`s). The CLI
dispatch branch itself is a thin wrapper with no dedicated test, matching this codebase's
existing convention (`rolling-check` has none either) - the logic it calls is what's tested.

## Phase 3 wired into the daily automatic loop, 2026-09-27

Until now, `run_self_review_for_market`'s `SUSTAINED` branch only ever produced a retirement
proposal - Phase 3 (authoring a genuinely new candidate) existed and was tested, but nothing
called it automatically. On explicit user request ("see if we can add it in automatic daily
loop also"), a `SUSTAINED` failure now ALSO tries `detector_authoring.author_and_validate`
for every entry in the new `tradedesk_lab.candidates.CANDIDATE_LIBRARY` (today: just
`MeanReversionV1`) - alongside, never instead of, the plain retirement proposal, matching
`retire_replace.py`'s own "retirement and replacement are never bundled" rule. Three things
made this safe rather than just wired:

1. **`replacement_for` closes a real cross-module gap**: `author_and_validate` previously
   always registered a candidate under `<market>:<candidate.name>:replacement`, but
   `retire_replace.py::find_lab_replacement` looks up `<market>:<setup>:replacement` (keyed
   to the FAILING setup, not the candidate) - a mismatch `retire_replace.py`'s own docstring
   flagged as future integration work. `author_and_validate` now takes an optional
   `replacement_for: SetupKind`, registering under the setup-keyed family when the caller
   (orchestrate.py) provides it, so a passing candidate is now actually discoverable as a
   suggested replacement the next time a retirement is proposed for that exact setup.
2. **`already_authored_today` guards the expensive path**: the full harness gauntlet (null
   baseline, walk-forward, sensitivity, Monte Carlo, regime split) is real compute; without a
   guard, a SUSTAINED failure persisting for days would re-run it for the same
   (candidate, market) every single day. Checked against the Registry's own experiment
   metadata rather than a new tracking file.
3. **`BaseDrifted` never crashes the daily run**: caught per-candidate and `break`s out of
   just the candidate loop for that failure - the retirement proposal (and every other
   market's failures) still goes through even when Phase 3 authoring can't run that day.

`author_and_validate`'s return signature grew a 5th value, `artifact_path` (previously
reconstructed by the caller from `OUTPUT`/`experiment_id`, which only this function actually
owns - a caller-side reconstruction is exactly the kind of duplicated-knowledge fragility
this project avoids elsewhere). 4 new tests in `tests/unit/test_self_review_orchestrate.py`
cover: a passing candidate submitting a NEW_DETECTOR item alongside the RETIRE one, the
already-authored-today skip, a mid-run BaseDrifted not crashing the SUSTAINED branch, and a
SINGLE-severity flag never attempting candidate authoring at all.

**Immediate consequence**: this wiring itself touched 3 production files
(`orchestrate.py`, `detector_authoring.py`, this checkpoint doc), so `verify_base()` is
drifted again relative to the manifest the user refreshed earlier today - confirmed directly
(`verify_base()` -> `changed: [...]` naming exactly these 3 files). Phase 3 authoring will
correctly no-op (via `BaseDrifted`, not a crash) until the manifest is refreshed once more;
the retirement/config-tuning paths are unaffected. Same command as before:
```
Remove-Item data\m14_m18\base_manifest.json
uv run --no-sync python -c "from tradedesk_lab.artifacts import protect, verify_base; m = protect(); print(len(m)); print(verify_base())"
```

## Not yet built

- A fully generic lab-to-production transpiler for Phase 3 (today's code generation is
  templated per authored candidate, disclosed explicitly in `detector_authoring.py`'s
  docstring).

## Scheduled, 2026-09-27: `tradedesk-crypto-self-review`, then made twice-daily

The user registered a new Windows Scheduled Task, `tradedesk-crypto-self-review`
(`uv run --no-sync tradedesk review self-review-run --market crypto`, 20 minutes after
`tradedesk-crypto-tracker`'s own run so its log has the day's fresh resolutions first).
NSE/BSE are not scheduled - NSE has no `rolling-check`/`self-review-run` task yet either
(both are currently run-on-request only for NSE), and BSE stays out of the self-analysis loop
per CLAUDE.md's scope limits.

Same day, on the user's follow-up ("crypto trades 24h, should self-review run twice a
day?"): checked whether a second same-day run would actually find anything new before
scheduling it blindly. `rolling_failure_monitor`'s evidence source
(`crypto_signal_tracking.jsonl`) was, at the time, refreshed only once daily by
`tradedesk-crypto-tracker` - a second same-day self-review run would have hit the
idempotency guard and been a real no-op, not "twice the insight" (the same distinction
crypto's EOD-learning task already has right: it genuinely runs twice daily because its own
evidence source, `research_tracker.py`'s forward candidate log, is independent of the
signal-tracker's cadence). Given the user's explicit choice - make the tracker itself
twice-daily rather than leave self-review at a mismatched cadence - added a second daily
trigger to both tasks: `tradedesk-crypto-tracker` now fires at **00:00 and 12:00 IST**
(so `crypto_signal_tracking.jsonl` genuinely refreshes twice a day), and
`tradedesk-crypto-self-review` at **00:20 and 12:20 IST** to match. Also disclosed, not
acted on: `crypto_signal_tracker.py`'s own docstring says it expects to run at 07:00 IST
(after crypto's UTC-midnight daily bar settles, ~05:30 IST) but the real scheduled time is
00:00 IST - a pre-existing mismatch, unrelated to this change, left for the user to decide
on separately.

## Checkpoint questions answered, 2026-09-27

Three of the flagged open decisions above were put to the user directly (not left as passive
notes) and acted on the same day:

1. **NSE scheduling** - yes. Registered `tradedesk-nse-self-review` (16:25 IST, once daily,
   25 minutes after `tradedesk-after-close`'s 16:00 IST so `nse_signal_tracking.jsonl` has
   the day's fresh resolutions first). A separate `rolling-check`-only task was briefly
   registered too, then removed - `self-review-run` already calls `run_daily_check`
   internally, so a standalone rolling-check task adds nothing `self-review-run` doesn't
   already do, and its own console output isn't captured by Task Scheduler anyway. Matches
   the crypto precedent exactly (`tradedesk-crypto-self-review` is the only crypto task too).
2. **BSE scope** - yes, brought into the self-analysis loop (CLAUDE.md's "Scope limits"
   updated) on the same reasoning already applied to crypto: the harness gauntlet never
   needed a paper book, and BSE runs the identical daily engine NSE/crypto already do.
   Eligible now, but NOT scheduled - no `tradedesk-bse-rolling-check`/`self-review-run` task
   exists yet, run-on-request only, since that wasn't explicitly asked for.
3. **Crypto tracker timing (00:00 vs the docstring's assumed 07:00 IST)** - investigate
   first, not just move the time. Real finding, more significant than the original question:
   checked the actual stored data (`data/crypto.duckdb`, `CDX_BTCINR` daily bars) against
   real UTC-day math. CoinDCX's daily candle `time` field is the OPEN time (confirmed in
   `broker/coindcx/models.py`), so a UTC-midnight-open bar isn't actually CLOSED until 24h
   later (IST 05:30 the *next* day). The store's "latest" row right now (2026-09-27, queried
   at 18:38 IST) is `ts=2026-09-27 05:30 IST` - i.e. TODAY's bar, still `open`, not
   yesterday's closed one; its OHLC (volume=3, a normal-looking high/low spread) confirms
   it's real, continuously-updating intraday data, not a just-opened stub. This means: NO
   run time during the UTC day - not 00:00, not the docstring's 07:00, not 12:00 - actually
   sees a settled bar for "today," because CoinDCX serves a live, updating current-day
   candle and `crypto_signal_tracker.py`'s `today = store.last_ts(...)` / `day = today.date()`
   just takes whatever the newest row is, complete or not. The docstring's "settled well
   before this runs at 07:00 IST" assumption looks wrong for either run time, not just the
   00:00 IST one - this isn't a simple time-of-day fix; it would need `crypto_signal_tracker.py`
   to explicitly evaluate the last FULLY CLOSED bar (excluding a still-forming "today" row)
   the way NSE/BSE's after-close scan structurally can't help but do (it only ever runs after
   the exchange's real close).

## Fixed, 2026-09-27: `CandleStore.last_closed_ts()`

On the follow-up "fix the crypto timing the way you see right": added
`CandleStore.last_closed_ts(code, interval, *, now=None)` (`data/candle_store.py`) - a
`last_ts()` sibling that only returns a bar whose own `[ts, ts+period)` window has fully
elapsed, via a plain SQL `ts + period <= now` filter (`_INTERVAL_SECONDS` gives each
interval's fixed length; `1month` is deliberately excluded - variable length, not needed
today - so a caller gets a loud `KeyError` rather than a silently wrong answer if it ever is).
For NSE/BSE, whose candles are only ever loaded after the exchange's real session close,
this is a provable no-op - the fix is universal, not market-branched.

Swapped in at every call site that decides "which day to evaluate as of" for ARMING a new
signal (the geometry-computing use, not a touch/exit check - see below for why that
distinction matters): `cli.py::scan`'s `on == "today"` resolution, `crypto_signal_tracker.py`'s
own `today = ...`, and `analysis.py`'s `/api/analyze` (Lookup tab) endpoint. `nse_signal_tracker.py`/
`bse_signal_tracker.py` needed no change - both already read a saved watchlist file produced
by `cli.py::scan`, so they inherit the fix transitively. Verified against the real crypto
store: `last_ts` still returns today's (still-forming) bar; `last_closed_ts` correctly falls
back to yesterday's settled one (`2026-09-26 05:30 IST` vs `2026-09-27 05:30 IST`, queried at
`2026-09-27 18:48 IST`). New regression test,
`test_last_closed_ts_skips_a_bar_whose_own_period_has_not_elapsed`
(`tests/data/test_candle_store.py`), covers: mid-window (falls back to the previous bar),
past-window (returns the now-closed latest bar), and before-any-bar-closes (`None`).

**Deliberately left untouched**: `signal_tracker.py::resolve_outcomes` (checking whether an
*already-armed* signal's target/stop has been touched) still reads whatever the latest bar
is, closed or not. Touching a price level intraday is real regardless of whether the day has
finished, so using partial same-day data there is conservative, not wrong, in a way that using
a not-yet-final CLOSE to compute a brand-new signal's trigger/stop/target geometry is not -
a materially different problem, and out of scope for this fix. Also untouched: `cli.py`'s
`data load` straggler-sweep and `data status` (diagnostic, not an evaluation decision),
`paper update` (NSE-only, unaffected since NSE data is never continuously updating), and
`history_loader.py`'s own incremental-fetch resume point (wanting the freshest row, including
a still-updating one, is exactly correct there).

## First real SUSTAINED verdict, 2026-09-29 - and what it exposed

Crypto's 00:20 IST run on 2026-09-29 reached `SUSTAINED` for all three setups (three
consecutive daily windows below 30%: base_breakout 8% / 37 calls, nr7_breakout 17% / 375,
trend_pullback 9% / 92) and submitted three real `RETIRE` items. They were NOT approved,
because checking what approval would do turned up three real problems:

1. **Retirement was global, not per-market.** `config/setups.yaml` has one block per setup
   for every market; `apply()` flipped `enabled: false`, which would have switched all three
   off on NSE and BSE too - and with all three off, the live NSE `scan` (which passes
   `fallback_to_all_if_none_enabled=False`) would have produced nothing at all, while crypto's
   own tracker (default fallback `True`) would have silently kept running all three anyway.
   Fixed: `SetupConfig.retired_markets`/`markets` + `active_on(market)`; `apply()` now appends
   the market to `retired_markets`; the fallback only fires when nothing is enabled anywhere.
2. **"Retired" meant "stop learning".** Fixed: the scan paths use `include_retired=True`, and
   `build_watchlist` rejects a retired setup's entries ("retired by self-review on
   <market> (shadow-tracked only)"), so they never alert but are still logged with
   `TrackedSignal.shadow = true` and graded - the dashboard marks them "(shadow)".
3. **The replacement search had never actually run.** The Sept 27 crypto timing fix changed
   production files after the lab base manifest was refreshed, so `verify_base()` reported
   drift and `orchestrate.py` silently `break`-ed: zero candidates tested. Two more latent bugs
   underneath: every candidate was costed as an NSE equity trade in whole units (wrong for
   crypto, and made any coin above the per-trade risk budget unsizeable), and the registry
   recorded the score under `expectancy_r` instead of the gauntlet's `net_expectancy_r`, so
   `Registry.best()` could never have found a passing replacement anyway.

## Replacement research, 2026-09-29

Explicit user direction: retirement must never be the end of it - the agent has to keep
looking for something that works better, using other analysis methods, and a setup kept in
shadow must keep being re-examined with other trends/indicators. `self_review/
replacement_search.py` is that loop:

- **Strategy space** (`tradedesk_lab/candidates/rules.py`, 225 candidates when three setups
  are failing): improvement variants of each failing/retired setup (its own production
  pattern, called read-only, x 8 filters - any, above_ema200, bull_stack, trending,
  strong_trend, rsi_momentum, volume_confirm, calm_vol - x 2 exit styles), the hand-written
  `MeanReversionV1`, and 13 new-method triggers (Donchian 20/55 breakouts, 60-day
  time-series momentum, EMA 20/50 and 50/200 crosses, MACD cross, Bollinger squeeze break,
  volume thrust, ADX thrust, EMA20 bounce, RSI2 dip, Bollinger dip, RSI14 turn) x 8 filters
  x 2 exits suited to the family (trend: ATR-trailed runner or 3R target; reversion: 1R/2R).
  Every expression is causal rolling/shift pandas; `tests_lab/test_rule_candidates.py` is the
  look-ahead test for each, and proves the generated production module arms exactly what
  the lab version validated.
- **Memory**: everything tried is in the lab registry (family `<market>:replacement`). Each
  run does untested candidates first (improvements of the failing setups before new methods),
  then re-tests anything older than 14 days on fresh data, under a 20-minute budget.
- **Bar** (`detector_authoring.candidate_verdict`): full gauntlet + kill criteria (500-trade
  floor for NSE/BSE, 300 for crypto), +0.10R over random timing, random-benchmark p-value <=
  max(0.001, 0.05/space size) because testing ~225 rules would otherwise manufacture false
  winners, and net R after real market costs above the retired setups' best forward mean R
  (gross, so tilted against the candidate). Hit rate is reported next to it, not gated on.
- **Output**: a passing candidate is a `NEW_DETECTOR` item (scoped to that market via
  `markets: [<market>]`); every retirement item carries the search's current plan and is
  refreshed in place each run; the Review tab's "Replacement research" panel shows the plan
  and a leaderboard (net R, win rate, trades, what each candidate missed on).
- **Speed**: the lab simulator now reads plain arrays and skips bars a candidate's
  precomputed gate rules out (identical results; 98s -> 14s per new-method candidate on 150
  crypto pairs), and improvement variants share one cached pass of the production pattern
  detector per run (~190s once, then ~45s each). Full space ~85 minutes, i.e. 2-3 days of
  crypto's twice-daily runs, then continuous re-testing.

First real measurements (crypto, 150 most liquid pairs, before the timing work - numbers
unchanged after it): `r_donchian55_bull_stack_trend_trail` beat random timing but was
-0.076R/trade net (35% winners, 369 trades); `imp_nr7_breakout_trending_trend_trail` beat
random timing but was -0.419R (31%, 605 trades), and nr7 stayed around -0.42 to -0.49R under
every exit tried - the pattern, not the exit, is the problem there. Nothing has cleared the
bar yet; that is reported as-is, not worked around.

## Next checkpoint

The lab base manifest has to be deliberately reviewed and refreshed (a human decision -
see the 2026-09-27 section) before the replacement search can run: this change touched
production files, so `verify_base()` reports drift and the search reports itself BLOCKED
on every item until then. After that, crypto's twice-daily runs can work through the whole
strategy space in 2-3 days. The three pending crypto retirements (2026-09-29) are correctly
market-scoped and retain shadow tracking, but the queue summary currently renders them as
`gauntlet FAILED ()` because retirement is failure-evidence driven and has no passing
replacement. Do not treat that text as a successful gauntlet or approve the items as if a
replacement had qualified; the dashboard decision should explicitly distinguish retirement
evidence from replacement evidence first.
