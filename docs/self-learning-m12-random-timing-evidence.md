# Milestone 12 checkpoint — matched prior eligible timing

Status: v3 evidence contract implemented; no market has passed it yet.

This checkpoint asks whether a prospectively frozen model and selector perform better
than comparable, previously qualified calls on the same instrument. It does **not** claim
that baseline accuracy improved, that 70–80% accuracy was reached, or that any market is
ready for personal-trading authority.

## Prospective selection contract

- The model parameters, source model/feature/strategy versions, exact exit-contract
  digest, selector version and exact `top_1_per_session` policy are sealed before any
  evaluated call.
- The contract records a `starts_after` boundary. Only sessions strictly after that
  boundary can enter the evaluated cohort; the data used to register the contract cannot
  become evidence for it later.
- Each session's complete sealed opportunity population is ranked before outcomes are
  inspected. Pending, invalid, resolved and never-triggered candidates all remain in that
  population before the top-one choice is frozen.
- A selected pending call pauses collection until it reaches a terminal state. A selected
  invalid call invalidates the batch. Neither can be dropped so that a lower-ranked call
  takes its place.
- The selection manifest seals the candidate population, scores and selected signal IDs.
  Evidence collected under a different selection context cannot be mixed into the same
  ledger.

## Matched prior eligible control

For each prospectively selected call, the control uses its twenty most recent earlier
qualified calls that match all of the following fields:

- symbol;
- setup;
- regime;
- exit-contract version and digest;
- strategy version;
- feature version; and
- source model version.

The historical matches must be recommended/qualified opportunities, must predate the
prospective contract boundary, and must also predate the selected call. Each matched call
keeps its own sealed entry, stop, target, sizing and outcome specification. The control
therefore compares against historically eligible calls rather than inventing entries on
arbitrary calendar dates.

The twenty alternatives are globally enumerated by matched-prior rank: rank 1 is the
nearest eligible prior call for every selected call, rank 2 is the second-nearest, and so
on through rank 20. The same rank is used across the entire cohort. This preserves the
shared time-series and cross-call dependence that would be destroyed by independently
resampling a different offset for every record.

These historical ranks are a fixed robustness control, not randomized exchangeable
assignments. Therefore the reported tail fraction is deliberately **not** called an
inferential p-value and cannot by itself prove causality or eliminate temporal drift.

## Candle replay and reconciliation

- The production versioned resolver replays both the selected call and every matched
  prior call with their sealed entry window, chase rule, stop, target, same-bar ordering,
  holding limit, slippage and fee schedule.
- Recorded `net_r` is never trusted. State, label, outcome, entry, exit and after-cost R
  must reconcile with the sealed ledger before a record is accepted.
- A valid call that never fills contributes 0R on either side and is not discarded.
- The armed-session close and ATR must match the sealed source snapshot.
- The source hash covers the full candle history from the beginning of the loaded series
  through the terminal exit, including every warm-up candle that can affect ATR. Candle
  source identity is computed before an experiment-cache result can be reused.
- Missing seals, candles, ATR history, sizing, contract identity or terminal outcomes
  make the control collect or fail explicitly; unavailable evidence is never a pass.

## Single frozen terminal test

Evidence is accumulated only for disjoint, uniquely identified `top_1_per_session` calls
under one sealed market, contract and selection context. NSE, BSE and crypto evidence is
never pooled.

There is one terminal look at the first 100 unique selected sessions. Once those 100
records are fixed, later calls cannot change that terminal cohort. For each matched rank,
the control calculates the mean after-cost R across the same 100 sessions. The fixed
empirical rank-tail probability is:

`tail = (1 + number of matched-rank means >= actual mean) / (20 + 1)`

The gate passes only if all of these conditions hold at that single look:

- exactly the first 100 paired calls represent 100 unique sessions;
- actual mean after-cost R is at least +0.10R above the mean of the twenty matched-rank
  cohort means; and
- the empirical rank-tail probability is at most 0.05.

With twenty enumerated controls, the smallest attainable tail probability is `1/21`, so
the stress gate requires the selected cohort mean to exceed all twenty matched-rank means.
There is no weekly or repeated optional test and no post-outcome policy selection. Because
the controls are earlier historical calls, the result remains a conservative stress gate,
not a standalone statistical claim of timing causality.

## Dashboard and current interpretation

The Learning view keeps prospective research-collector progress separate from this exact
contract-bound challenger gate. It identifies the market, contract and selector policy,
keeps their counts separate, and renders collecting, invalid and failed states differently
from a pass. An unavailable response remains `unavailable — not a pass`.

The implementation is complete, but the required prospective first-100-session cohort has
not passed for NSE, BSE or crypto. Therefore no accuracy improvement is established, no
active model is changed, and personal-trading authority remains unchanged. A passing
matched-timing result would still be only one required evidence gate; it would not replace
the independent prospective challenger, calibration, expectancy, stability and explicit
promotion requirements.
