# Plan: OSR v1 opening sweep-reclaim path to a credible 50% baseline

Date: 29 September 2026

Status: Milestone 0 contract frozen in code; no OSR performance result; no production
or dashboard behavior changed.

## Objective

Build and test a genuinely different causal signal family that can improve on the
canonical AEM v1 result of 149 strict wins from 693 resolved fills (21.50%, 18.60%
Wilson lower bound, -0.27471R mean net R). The intermediate research gate is at least
50% strict success with positive after-cost expectancy. The eventual user objective
remains 70-80% successful calls within sessions, but that is not claimed or guaranteed
by this plan.

OSR means **Opening Sweep-Reclaim**. It looks for a failed early downside auction:
price gaps or sweeps lower after the open, rejects the low, and causally reclaims a
known opening reference. This is a long-side, same-session NSE cash hypothesis aimed at
anticipating a rebound and taking a smaller, quicker profit. It is not another
parameter change to AEM v2's impulse, confirmed-pullback, or breakout-retest generator.

## Frozen signal hypotheses

Only information from completed bars at the decision timestamp may be used.

1. `gap_down_reclaim`: the session opens 0.30-2.50% below the prior close, establishes
   a lower early extreme, then a completed M1 bar reclaims the session open and the
   causal VWAP with a strong close.
2. `opening_low_sweep_reclaim`: after the first 15 completed minutes define the opening
   range, price sweeps at least 0.10% below its low and a later completed M1 bar closes
   back above that already-known level with a strong close.

The event engine must issue its prediction after the reclaim bar closes. Entry can
only occur on one of the next two M1 bars, with a maximum 0.10% chase. A same-bar
target/stop ambiguity is a loss. Missing bars, incomplete outcome windows, invalid
geometry, unfilled entries, and unresolved calls remain explicit and never count as
wins.

The frozen quick-profit geometries are:

| ID | Target | Stop | Maximum hold | Gross R:R |
|---|---:|---:|---:|---:|
| `reclaim_30_22_20` | 0.30% | 0.22% | 20 min | 1.36 |
| `reclaim_40_28_35` | 0.40% | 0.28% | 35 min | 1.43 |
| `reclaim_55_36_50` | 0.55% | 0.36% | 50 min | 1.53 |

These are research geometries. They do not satisfy the current production 2.0 net-R:R
gate and cannot be silently integrated into live scanning.

## Accuracy-first evidence rules

- Strict success requires a prediction before entry, an executable later fill, target
  before stop or deadline, and positive net P&L after modeled costs.
- Every result reports calls, fills, wins, losses/timeouts, unfilled calls, unresolved
  calls, zero-call sessions, active-session coverage, 70%/80% session consistency,
  Wilson uncertainty, mean net R, drawdown, and symbol/session concentration.
- The first bounded race contains no more than 12 specifications: two frozen modes by
  three geometries by two transparent selectors. Top-one, top-two, and top-three call
  policies are reported, not counted as extra signal specifications.
- A development nomination requires at least 100 resolved selected fills, 30 active
  sessions, 40% active-session coverage, 50% observed strict success, a 40% Wilson
  lower bound, positive mean net R, and at least +0.10R versus matched random timing.
- No single symbol may contribute more than 25% of the candidate's strict wins.
- Mandatory stresses remain base costs, 1.25x/1.5x costs, doubled slippage, one-bar
  delay, and adversarial missed fills. Failure on any registered stress rejects the
  candidate.
- The existing 120 sessions have already been inspected repeatedly. Any OSR result on
  them is development diagnostic evidence only and cannot create a new baseline.

## Checkbox roadmap

### Milestone 0 - freeze before measuring

- [x] Define the two structurally different event modes.
- [x] Freeze entry ordering, small-profit geometries, 30 causal feature names, trial
  budget, top-k policies, evidence classes, and promotion gates.
- [x] Fingerprint the contract, protocol, implementation, and plan.
- [x] Keep OSR isolated in `tradedesk_lab`; no production or dashboard import.

### Milestone 1 - causal event and outcome engine

- [ ] Reconstruct both modes from completed M1 bars and prior completed daily bars.
- [ ] Enforce a fully completed 15-minute opening range and future-bar invariance.
- [ ] Resolve later fills, chase, gaps, target/stop order, deadline, costs, and every
  exclusion conservatively.
- [ ] Test missing bars, same-bar ambiguity, future mutation, and boundary timestamps.

### Milestone 2 - feature and dataset integrity

- [ ] Compute the 30 frozen decision-time features without symbol identity or outcomes.
- [ ] Exclude the candidate from cross-sectional peer values and require a minimum peer
  population.
- [ ] Assemble all eligible OSR opportunities on the frozen 50-stock cohort, retaining
  every exclusion and source fingerprint.
- [ ] Report mode/geometry counts and prove future-bar mutation cannot alter features.

### Milestone 3 - bounded development race

- [ ] Use nested chronological walk-forward folds with purge and embargo.
- [ ] Compare one transparent rule score and one regularized nonlinear/tree challenger;
  calibrate fold-locally and never train on a row it scores.
- [ ] Report unfiltered and matched-random controls for every specification.
- [ ] Reject zero-call, tiny-sample, concentrated, negative-expectancy, or unstable
  apparent winners.

### Milestone 4 - execution and portfolio gates

- [ ] Re-resolve only the selected real fills under all mandatory stresses.
- [ ] Replay existing position, cash, heat, sector, and concurrent-entry constraints.
- [ ] Reject any candidate with nonpositive stressed expectancy or unusable call
  availability, even if its hit rate exceeds 50%.

### Milestone 5 - independent evidence

- [ ] Freeze one candidate before seeing later evidence.
- [ ] Use later unconsumed history if acquired, otherwise collect prospective shadow
  calls. Minimum first review: 100 resolved calls across 30 active sessions.
- [ ] Require at least 50% strict success, 40% Wilson lower bound, positive stressed
  net R, and the frozen availability/session-consistency gates to call it a 50%
  research baseline.
- [ ] Continue toward the unchanged ultimate 80%/500-total/100-OOS gates without
  reusing development evidence as confirmation.

### Milestone 6 - broader NSE and integration

- [ ] If and only if the 50-stock pilot qualifies, preregister sector/liquidity cohorts
  and expand across the supported tradable NSE universe.
- [ ] Report unsupported, illiquid, missing-history, and rejected stocks; examples are
  not a symbol allowlist.
- [ ] Add read-only dashboard evidence only after a real OSR run exists.
- [ ] Production integration requires a separate user decision and unchanged broker,
  order-management, risk, and existing setup behavior until then.

## Stop rules

Stop the OSR path and preserve the negative result if the real event population is too
small, all bounded specifications remain below 50%, Wilson confidence is inadequate,
after-cost or stressed expectancy is nonpositive, matched random is not beaten, or the
result depends on one symbol or period. Do not widen the search after seeing results.
The next hypothesis must then be registered as a new family before evaluation.
