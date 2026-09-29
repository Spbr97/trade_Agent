# AEM index-context accuracy plan

Frozen: 30 September 2026

Status: **registered next accuracy experiment; acquisition checkpoint complete;
no accuracy improvement claimed**

## Objective

Test whether causal intraday index context can reject weak AEM-style calls and improve
strict selected-call accuracy, Wilson confidence, useful availability, and after-cost
net R. The near-term research target remains a credible **50% baseline**; the eventual
goal remains **70-80% successful calls with useful session coverage**. Neither target
may be reached by hiding losses, collapsing call volume, or changing outcomes.

The unchanged reference is the frozen 50-stock AEM v1 cohort:

- 149 strict wins from 693 resolved fills (**21.50%**);
- **-0.27471R** mean net R per resolved fill; and
- dataset `ec539cf66bea4c18ba994506f85a52d6`.

## Why this is next

AEM v2, OSR, and SSM supplied no promotable candidate. SSM's registered mechanism
failed before selection. A different model trained on the same information would
mostly increase search flexibility; index context adds genuinely new, causally
available information that the earlier AEM error taxonomy explicitly lacked.

The collection checkpoint now has 134,987/135,000 planned bars. This enables a
mechanism test, but does not itself raise the baseline.

## Frozen scope and leakage boundary

Version 1 uses completed one-minute bars from:

- NIFTY 50 (`NSE_40000001`);
- BANK NIFTY (`NSE_40000003`); and
- Nifty Financial (`NSE_40000100`).

All three series are uniform market/risk context available to every candidate. No
stock is assigned to a present-day index or sector membership. This deliberately
avoids applying current membership retrospectively. Historical membership can be a
separate future version only after point-in-time evidence is acquired and frozen.

The Nifty Financial 7 July 2026 bars from 15:17-15:29 IST are absent after a repeated
source request. They must never be imputed. Events whose registered context lookback
touches a missing or not-yet-completed bar are explicitly excluded with a reason.

All existing 120 sessions are consumed development evidence. They can kill this
hypothesis or nominate one frozen challenger; they cannot certify a new baseline.

## Registered mechanism hypothesis

The primary hypothesis is that AEM failures are less frequent when the stock's
decision-time impulse is supported by contemporaneous broad-market and financial-risk
direction, while stock return relative to those indices still leaves room for the
declared quick target.

The first implementation may derive only these causal feature families at the
decision timestamp:

- index returns over completed 1, 3, 5, and 15-minute windows;
- index position and slope relative to causal session VWAP;
- broad-versus-financial divergence and agreement;
- stock return minus each completed index return over the same windows; and
- index volatility/dispersion over completed bars.

No later close, daily final value, future high/low, outcome, post-decision bar, or
candidate membership label may enter a feature.

## Experiment sequence

### Checkpoint 1 — acquisition and integrity

- [x] Freeze the three-index/120-session plan and source fingerprints.
- [x] Collect 134,987/135,000 bars in 27 bounded, preflight-guarded requests.
- [x] Repeat the incomplete window and preserve the identical 13-bar source gap.
- [ ] Build the causal as-of join to every frozen decision/opportunity.
- [ ] Prove timestamp alignment, completed-bar availability, session boundaries, and
  fail-closed missingness with focused tests.
- [ ] Publish included/excluded decision counts and hash the joined artifact.

### Checkpoint 2 — mechanism before model

- [ ] Freeze no more than six simple alignment/relative-strength rules before reading
  their outcome results.
- [ ] Compare each rule with its inverse, time-shift/placebo, and matched label-shuffle
  controls on the same candidate population.
- [ ] Require consistent direction across chronological folds, a positive effect on
  strict success and mean net R, and an advantage over both placebos and shuffled
  controls.
- [ ] Stop the track if the mechanism is absent, reverses by fold, or is economically
  too small after costs. Do not run a selector race after a failed mechanism gate.

### Checkpoint 3 — bounded selector diagnostic

- [ ] Only after the mechanism passes, compare the best frozen simple rule with one
  regularized logistic control on identical chronological folds.
- [ ] Keep preprocessing, calibration, thresholding, and feature selection inside
  each training fold; purge overlapping outcome windows.
- [ ] Report every threshold's calls, fills, strict wins/losses, zero-call sessions,
  Wilson interval, mean net R, costs, and concentration.
- [ ] Nominate at most one rule/model only if development evidence reaches 50% strict
  success, at least 100 resolved fills, at least 30 active sessions, at least 40%
  active-session coverage, positive base/stressed net R, and a Wilson lower bound
  above the unchanged baseline.

Development qualification is permission to freeze a candidate, not evidence of a
new baseline.

### Checkpoint 4 — independent evidence

- [ ] Score the frozen candidate once on later unconsumed sessions or immutable
  prospective shadow predictions.
- [ ] Require the same sample, accuracy, Wilson, availability, economics, stress,
  portfolio, and concentration gates.
- [ ] Reject rather than retune if the locked evaluation fails.
- [ ] Require prospective evidence before any production discussion.

### Checkpoint 5 — dashboard and broader NSE

- [ ] Add a read-only dashboard panel only after a real frozen candidate exists. Show
  evidence class, denominator, strict accuracy, Wilson bound, availability, net R,
  trial count, missingness, and failed gates.
- [ ] Do not change scanner, call, management, risk, alert, broker, or order behavior
  from a development result.
- [ ] Expand beyond the 50-stock pilot only through preregistered liquidity cohorts
  and point-in-time eligibility data. Report unsupported and excluded NSE stocks.

## Controls and promotion rules

The same-contract controls are the unchanged AEM population, the unfiltered context
population, matched random selection, inverse alignment rules, temporal placebos, and
one regularized logistic model. A more complex tree, ensemble, or neural model is not
authorized unless the simple mechanism survives and the model adds measurable value
on later data.

Every result must include pooled and per-session strict accuracy, Wilson interval,
active/eligible/zero-call sessions, fills and unfilled attempts, base and stressed
net R, modeled costs, losing streaks, and symbol/week/regime concentration. One lucky
subset or a predicted probability is not a pass.

## Safety boundary

Implementation remains under `tradedesk_lab`, `tests_lab`, and tracked research
documentation. The isolated context store remains research-only. Production scanner,
dashboard, management, risk, alert, broker, and order paths stay unchanged until a
separate, evidence-backed checkpoint explicitly authorizes integration.

## Next executable task

Implement the causal context join and its integrity report. Do not train or tune a
selector in the same checkpoint. The join checkpoint is complete only when every
decision is either matched to completed context bars or retained with an explicit
exclusion reason, including the known 7 July closing gap.
