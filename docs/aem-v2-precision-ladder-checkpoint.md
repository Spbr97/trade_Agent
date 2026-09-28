# AEM v2 Milestone 3: Precision Ladder selector mechanics

Date: 26 September 2026

Branch: `codex/aem-v2-accuracy-first`

Status: **selector mechanics implemented and tested; not run against real data; not
evaluated for accuracy; no live change**

## Outcome

Milestone 3 has four planned sub-items in
[the plan](plan-aem-v2-50pct-baseline.md): (1) smoothed evidence contributions, sparse-
regime shrinkage and hard vetoes, (2) fold-local calibration, absolute thresholding,
per-session ranking and symbol deduplication, (3) a logistic control and an optional
tree challenger on identical folds, and (4) proof that outcome columns and future bars
cannot enter prediction code. This checkpoint completes (1), (2) and the logistic half
of (3); the tree challenger is explicitly optional in the plan text and is not built.
(4) is proven both by construction and by a dedicated leakage test.

Nothing in this checkpoint has been fit or scored against real AEM v2 data - every test
uses synthetic feature frames. The canonical AEM v1 baseline therefore remains
**149/693 = 21.50% strict success**, an **18.60% Wilson lower bound** and **−0.27471R
mean net R**, unchanged and unchallenged.

## What is implemented

`tradedesk_lab/aem_v2_precision_ladder.py`:

- **Evidence contributions**: each of the 34 registered features (plus two
  preregistered, economically motivated interactions - momentum agreeing with
  cross-sectional breadth, and price extension agreeing with volume confirmation) is
  quantile-binned on the training fold only; each bin's contribution is its shrunk
  log-odds relative to the training base rate. A sparse bin's contribution shrinks
  toward the base rate (additive-k smoothing) rather than reporting an unstable extreme.
- **Hard veto layer**: the section-5.3 vetoes that are computable purely from already-
  validated features and geometry - cost-negative targets, excessive impact proxy,
  inadequate liquidity, no remaining causal room, an already-extended move, excess
  chase, and a past-deadline decision. Portfolio/position/sector/heat limits are
  deliberately not reimplemented here; `select_calls` takes a `portfolio_filter` hook
  for the existing, already-tested risk manager to plug into later.
- **Fold-local out-of-fold calibration**: training rows are split into chronological
  inner folds; each fold is scored only by contributions fit on every OTHER inner fold,
  and the isotonic calibrator is fit only on those genuinely out-of-fold scores.
- **Absolute threshold + ranking + deduplication**: a candidate must clear a fixed 50%
  calibrated-probability bar and carry no veto before it is even eligible for ranking;
  only after that filter does per-session ranking, same-symbol deduplication (the same
  session's repeated signals on one symbol collapse to its single best-scoring
  candidate) and the top-1/2/3 cap apply. A high raw rank can never rescue an
  unqualified candidate.
- **Logistic control**: a regularized (`C=0.5`) logistic regression on the same 34
  columns, the transparent baseline the plan requires the custom selector to beat.

## Verification

Twelve focused tests cover: training and scoring frames are rejected outright if their
columns are not exactly the 34 registered names (no outcome column, no stray extra
column can ride along); labels must be a matching binary array; a fitted contribution
recovers the correct direction and magnitude for a genuinely informative feature versus
a noise feature; **every out-of-fold score is reproduced independently by fitting a
model on that fold's held-out rows and re-scoring** - a real, verified leakage proof, not
an assertion of intent; every hard-veto condition fires on a synthetic values dict built
to trigger it and not on a benign one; an unqualified (vetoed or sub-threshold)
candidate is never selected regardless of its raw score; symbol deduplication and the
top-k cap behave correctly; and the logistic control enforces the same column contract.

`uv run --no-sync ruff check tradedesk_lab/aem_v2_precision_ladder.py
tests_lab/test_aem_v2_precision_ladder.py` is clean, as is the full repository suite (no
failing or erroring test).

Machine-readable evidence is stored in
[`docs/evidence/aem-v2-precision-ladder.json`](evidence/aem-v2-precision-ladder.json),
pinned to the current module/test file hashes.

## Safety boundary

The selector exists only in `tradedesk_lab` and is not imported by production scanning,
ranking, alerts, management, risk, dashboard or order code. It has never been fit on
real data, so it cannot yet make even a research-only prediction about a real AEM v2
candidate.

## Known gaps carried forward (not done, tracked explicitly)

*Update, same day: Milestone 2's remaining two sub-items (the universe integrity audit
and the frozen development dataset) are now complete - see the
[Milestone 2 universe-audit checkpoint](aem-v2-universe-audit-checkpoint.md). The
paragraph below is left as it was written for this checkpoint's own record; it is no
longer current.*

- **Milestone 3** (this checkpoint): the optional tree-model challenger on identical
  folds is not built (explicitly optional in the plan text). `select_calls`'s
  `portfolio_filter` hook exists but nothing calls it with the real, already-tested
  risk manager yet - that wiring belongs to Milestone 4, where real candidates first
  exist.
- **Milestone 4** (not started): register the bounded ≤24-specification pipeline race,
  run nested chronological walk-forward evaluation on the consumed development data,
  and publish the accuracy/availability/economics curve including every failed trial.
  This is the first point at which an actual accuracy number for AEM v2 can exist.
- **Milestones 5-7** (not started): independent locked-evaluation gate, prospective
  shadow evidence, and broader NSE qualification - all explicitly downstream of a
  Milestone 4 result that does not yet exist.

## Next checkpoint

Milestone 4: complete the two outstanding Milestone 2 sub-items first (the universe
integrity audit and the frozen development dataset - the Precision Ladder cannot fit
against real data until a frozen dataset exists), then register a bounded set of
pipeline specifications and run the first real nested walk-forward evaluation. That is
the first checkpoint that can honestly report an accuracy number for AEM v2.
