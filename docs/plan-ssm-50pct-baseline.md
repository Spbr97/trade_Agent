# Plan: SSM v1 evidence-backed path to a credible 50% baseline

Date: 29 September 2026

Status: Milestone 0 contract frozen in code; no SSM performance result; no production
or dashboard behavior changed.

## Why this hypothesis is different

SSM means **Same-Slot Micro-Momentum**. It predicts that a stock that was relatively
strong during a specific half-hour on prior sessions may again be relatively strong
during that same half-hour on a later session. The prediction is frozen before the
slot begins, so no bar from the predicted half-hour can enter its own features.

This mechanism was selected because it has direct published support rather than being
another chart-pattern variation:

- Murphy and Thirumalai documented on NSE data that half-hour returns predict the same
  half-hour's returns on subsequent days and connected the effect to institutions
  splitting parent orders over multiple sessions: [Journal of Financial Research,
  DOI 10.1111/jfir.12131](https://onlinelibrary.wiley.com/doi/10.1111/jfir.12131).
- Heston, Korajczyk, and Sadka found half-hour cross-sectional return continuation at
  exact daily multiples lasting as long as 40 trading days: [Journal of Finance,
  DOI 10.1111/j.1540-6261.2010.01573.x](https://onlinelibrary.wiley.com/doi/10.1111/j.1540-6261.2010.01573.x).
- NSE's own research summary describes the Indian result and its repetitive
  institutional-flow explanation: [NSE Market Pulse, October
  2021](https://nsearchives.nseindia.com/s3fs-public/inline-files/Market_Pulse_October_2021.pdf).

Published predictability does **not** prove that an OHLCV-only implementation remains
tradable on current NSE data after Indian costs. The source study had masked trader
identities and order submissions; this project has candles only. SSM is therefore a
high-quality hypothesis to falsify, not a promised baseline increase.

## Frozen causal contract

- Scope: research-only, long-side, same-session NSE cash calls on the existing liquid
  50-stock pilot before any broader-universe claim.
- Bars: one-minute candles grouped into non-overlapping 30-minute slots.
- Predicted slots: 09:45 through 15:15, represented by eleven starts from 09:45 to
  14:45. The opening 30 minutes and final unmatched 15 minutes are excluded.
- Decision: one minute before slot entry. Features may use prior completed sessions
  and completed current-session bars strictly before the slot, never the slot itself.
- History: up to 40 prior sessions, with at least 20 complete same-slot observations.
- Peers: at least 30 valid candidate-excluded same-slot stocks.
- Direction: long-only in v1 so its call accuracy remains directly comparable with the
  long-side AEM v1 canonical baseline.
- Availability: top-one, top-two, and top-three calls per session are reported; no
  session may silently disappear when nothing qualifies.

The two frozen predictor modes are:

1. `lag1_cross_sectional`: the previous session's same-slot return, ranked against
   the contemporaneous liquid-universe cross-section.
2. `multi_day_persistence`: same-slot mean return and positive-sign persistence over
   earlier sessions, with a 40-session ceiling.

The transparent rule requires an upper-decile cross-sectional signal; the alternative
is a regularized logistic selector trained and calibrated inside chronological folds.
Neither may lower the absolute call threshold or inspect outcome fields.

## Quick-profit geometries and economics

| ID | Target | Stop | Hold | Gross R:R |
|---|---:|---:|---:|---:|
| `slot_20_30_30` | 0.20% | 0.30% | 30 min | 0.67 |
| `slot_25_30_30` | 0.25% | 0.30% | 30 min | 0.83 |
| `slot_30_30_30` | 0.30% | 0.30% | 30 min | 1.00 |

These geometries intentionally test the user's smaller, quicker-profit objective.
They cannot earn promotion merely by manufacturing a high hit rate with a smaller
target: positive after-cost mean net R, matched-random advantage, drawdown, and stress
gates must pass simultaneously. They are not compatible with the current production
2.0 net-R:R gate and cannot be silently integrated.

## Frozen feature registry

Exactly 24 decision-time features cover:

- slot identity and minutes from open;
- lag-1/2/3 and 5/20/40-session same-slot returns;
- 5/20/40-session positive-return shares and 40-session volatility;
- lag-1/5/20 cross-sectional ranks and same-slot dispersion;
- prior same-slot volume and turnover;
- current-day pre-slot return, rank, and candidate-excluded breadth; and
- liquidity, impact, and modeled round-trip cost.

Symbol identity, future bars, current-slot bars, fill status, targets, stops, labels,
outcomes, and P&L are forbidden predictor inputs.

## Accuracy-first evidence gates

The bounded race contains exactly 12 specifications: two predictor modes by three
geometries by two selectors. Top-k views do not count as additional trials.

A development nomination requires all of the following together:

- at least 100 resolved calls over at least 30 active sessions;
- at least 40% active-session coverage;
- at least 50% observed strict success and a 40% Wilson lower bound;
- no unresolved selected calls;
- positive mean net R after modeled costs;
- at least +0.10R versus matched random calls with identical session/slot counts;
- no more than 25% of wins from one symbol or 35% from one half-hour; and
- stable positive economics across chronological outer folds.

Mandatory stresses remain 1.25x/1.5x costs, doubled slippage, one-bar delay, and
adversarial missed fills. All existing 120 sessions remain consumed development
evidence and cannot certify a new baseline.

## Checkbox roadmap

### Milestone 0 - freeze before measuring

- [x] Select a published, structurally different return mechanism with direct NSE
  evidence.
- [x] Freeze slot construction, predictor modes, quick-profit geometries, 24 causal
  feature names, 12-trial budget, top-k policies, and promotion gates.
- [x] Keep SSM isolated in `tradedesk_lab`; no production or dashboard import.

### Milestone 1 - causal slot, feature, and outcome engine

- [ ] Build exact complete-session half-hours from M1 without crossing session bounds.
- [ ] Prove every historical feature stops before the predicted slot and current-slot
  mutation cannot change a call.
- [ ] Build candidate-excluded ranks with at least 30 valid peers.
- [ ] Resolve next-bar fills, gaps, target/stop ambiguity, slot deadlines, costs,
  unfilled calls, and unresolved outcomes conservatively.

### Milestone 2 - real population and mechanism check

- [ ] Assemble every eligible stock/slot decision on the frozen 50-stock cohort.
- [ ] Reproduce the published mechanism first: estimate lag-1 through lag-40 same-slot
  cross-sectional continuation before testing trade geometry.
- [ ] Compare same-slot coefficients with adjacent-slot placebo lags and candidate-
  shuffled controls; stop if periodicity is absent.
- [ ] Freeze all calls, exclusions, source fingerprints, and mechanism diagnostics.

### Milestone 3 - bounded development race

- [ ] Run all 12 specifications with nested chronological walk-forward fitting,
  purge, embargo, fold-local preprocessing, and fold-local calibration.
- [ ] Report top-one/two/three accuracy, Wilson bounds, session consistency,
  availability, after-cost R, drawdown, symbol/slot concentration, and zero-call days.
- [ ] Compare unfiltered, adjacent-slot placebo, and matched-random controls.
- [ ] Reject SSM if no specification clears every frozen gate without threshold tuning.

### Milestone 4 - stress, portfolio, and independent evidence

- [ ] Re-resolve only a nominated candidate under every mandatory execution stress.
- [ ] Replay current position, cash, heat, sector, and concurrency limits.
- [ ] Freeze a passing candidate before later unconsumed or prospective evidence.
- [ ] Require 100 prospective resolved calls over 30 active sessions before calling it
  a 50% research baseline; continue toward the unchanged 80%/500-call final gates.

### Milestone 5 - broader NSE and integration

- [ ] Expand only after the liquid pilot qualifies, using preregistered liquidity and
  sector cohorts across the supported NSE universe.
- [ ] Report missing, unsupported, illiquid, and rejected stocks explicitly.
- [ ] Add read-only dashboard evidence only after a real SSM result exists.
- [ ] Require separate approval for any production integration; keep current calls,
  management, risk, broker, and order behavior unchanged until qualification.

## Stop rule

Stop SSM before a model race if the same-slot return continuation mechanism is absent,
no stronger than adjacent-slot placebos, or too small to clear modeled costs. After a
race, stop if accuracy, Wilson confidence, availability, economics, random advantage,
fold stability, or concentration fails. Do not lower the threshold or widen the search
after seeing results.
