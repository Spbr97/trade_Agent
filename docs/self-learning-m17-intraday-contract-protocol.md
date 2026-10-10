# Self-learning Milestone 17 — frozen intraday execution-contract protocol

Protocol frozen: 2026-10-10, before any M17 intraday path was acquired, any M17 contract
was scored, or any M17 result was opened.

Status: preregistered research protocol. It cannot authorize a signal, alert, order,
active-model change or management action.

## Objective

M15 could rank some later price leaders, but its next-open daily-bar contract lost money.
M16 correctly abstained because its after-cost model found almost no positive-expected-R
candidates. M17 tests the remaining execution hypothesis: whether time-ordered intraday
paths and a smaller, quicker profit contract can convert a causal full-universe ranking
into a repeatable tradeable outcome.

The experiment asks whether one fixed market-specific contract can:

1. observe the exact entry, stop and target order instead of inferring it from daily bars;
2. improve strict target-before-stop accuracy without hiding no-fills or missing paths;
3. remain positive after configured slippage, fees and taxes; and
4. justify a new prospective cohort with a 70% qualification target and an 80% stretch
   target.

Historical success can only register a forward observer. It cannot prove a baseline
improvement or change a live call.

## Frozen evidence and chronological split

- NSE, BSE and crypto are evaluated independently. Pooling is forbidden.
- Decision population: the same `leader-causal-features-v1` liquid full-universe rows and
  `three-session-max-high-top5-v1` training label used by M15/M16.
- Window: exactly the latest 180 mature sessions available when the market's M17
  acquisition manifest is first sealed.
- Inputs to ranking: the 18 causal M14 features only.
- Fixed ranker: median imputation followed by a 200-tree bounded random forest, maximum
  depth 4, minimum 25 rows per leaf, square-root feature sampling,
  balanced-subsample class weights and deterministic seed 17.
- Split: first 102 sessions train the ranker; 3 sessions purge; the next 36 select one
  execution contract; 3 sessions purge; the final 36 are a consumed diagnostic block.
- The intraday contract race is the only model-selection use of the validation block. The
  diagnostic block cannot change the ranker, candidates, contract set, winner order or
  gates.
- These 180 sessions overlap earlier consumed research. They are development evidence,
  never a new locked test and never proof of improvement.

## Frozen acquisition manifest

The acquisition manifest is created and hash-sealed before requesting any M17 intraday
bar. For each validation and diagnostic decision session it contains:

- the ranker's top three instruments, with top one the primary policy;
- the `return_20_rank` momentum top one;
- twenty same-session matched-random instruments sampled without replacement with seed
  1701; and
- the exact next market-session start/end derived from the stored daily calendar.

The manifest is generated from the complete causal liquid universe before path
availability is inspected. A missing top-ranked path is invalid/unavailable; it can never
be replaced by the next available instrument. Existing bars, newly downloaded bars and
missing bars receive the same treatment.

Only manifest instruments and windows are acquired. “Full-universe” therefore refers to
causal generation and ranking over the full liquid universe, while path collection is
sparse and limited to every candidate used by a frozen policy or control. This avoids a
multi-gigabyte full-market minute archive without narrowing the decision universe.

## Frozen intraday path contract

- Required resolutions: M1, M5 and M15 for the complete next-session window.
- NSE/BSE: request all three observed intervals from INDstocks.
- Crypto: request observed M1 and M15 from CoinDCX; derive M5 deterministically from M1
  because CoinDCX exposes no M5 interval. The derived bars and provenance are explicit.
- Equity window: 09:15 inclusive to 15:30 exclusive in Asia/Kolkata on the next stored
  trading session.
- Crypto window: the next stored D1 candle open through exactly 24 hours later.
- Every bar timestamp is an open time. Bars must be ordered, unique, finite and inside the
  sealed window.
- M1 must have an unbroken expected timestamp grid. M5 and M15 must agree with M1 OHLCV
  aggregation. A shortened exchange session may be accepted only when its window is
  shortened by the stored market calendar before acquisition; it is never inferred from a
  partial response.
- Any missing, duplicate, unordered, out-of-window or cross-interval-inconsistent path is
  invalid. Unavailable is not a loss, is not a win and is never silently dropped from the
  coverage denominator.

## Frozen sizing, risk and costs

- Research capital: INR 100,000; per-trade gross risk budget: INR 250.
- Risk distance: the larger of 0.50 decision-close ATR and 1% of actual entry for NSE/BSE
  or 4% for crypto.
- Equities use whole shares; crypto uses its configured fractional step and minimum
  notional.
- Buy-side slippage is applied to entry and sell-side slippage to every exit.
- Same-session equity trades use the existing `INTRADAY` charge contract. Crypto uses its
  existing maker/taker fee, GST and sell-side TDS model; trade type does not alter those
  charges.
- Net R is calculated by the existing market-specific cost model against actual entry,
  stop and exit execution prices.

## Frozen six-contract race

Each entry rule is paired with exactly two targets, `+0.50R` and `+0.75R`, producing six
contracts. The stop is `-1R`; every open position exits at the final M1 close if neither
barrier has resolved it.

1. **Next open** — enter at the first M1 open plus slippage.
2. **Prior-close pullback** — eligible only when the next session opens above the decision
   close. Through the first 120 minutes, enter at the decision close when a later M1 bar
   trades down to it. An opening price below the limit on the triggering bar receives the
   better observed open before buy slippage. Otherwise the contract is never triggered.
3. **Opening-range breakout** — after the first M15 bar has closed, use its high as the
   trigger. From minute 16 through minute 120, enter at the trigger or a higher observed
   M1 open before buy slippage. Otherwise the contract is never triggered.

The entry bar participates in exit resolution only after the entry event. If stop and
target are both reachable later in the same M1 bar and tick order is unavailable, stop
wins. A gap through a stop exits at the observed open. A gap above a target exits at the
observed open only on a later bar; target slippage still applies.

Never-triggered contracts are explicit no-calls. They do not enter strict accuracy or
mean-net-R numerators/denominators, but they remain in selection availability, coverage
and no-call-frequency reporting.

## Frozen contract selection and controls

A validation contract is eligible only with:

- valid paths for at least 32 of 36 primary selections;
- at least 24 filled trades across at least 24 sessions;
- no-call frequency no greater than one third of path-valid selections; and
- mean after-cost net R above zero.

Among eligible contracts, select exactly one by:

1. higher strict accuracy;
2. higher 95% Wilson lower bound;
3. higher mean after-cost net R;
4. more filled trades;
5. fixed tie order: prior-close pullback, opening-range breakout, next open, then the
   smaller target.

Controls for the selected contract:

- identical contract on momentum top one;
- 2,000 deterministic matched-random top-one repetitions using the twenty sealed random
  candidates in every identical session; and
- the same ranker's next-open `+0.75R` contract as the direct M15-style execution anchor.

## Historical registration gate

The validation-selected contract is evaluated once on the 36-session diagnostic block.
A prospective observer is registered only if every gate passes:

- valid primary path coverage at least 32/36;
- at least 24 filled trades across at least 24 sessions;
- strict accuracy at least 55%;
- 95% Wilson lower bound at least 35%;
- mean and median after-cost net R both above zero;
- maximum losing streak no greater than six;
- strict accuracy at least 10 percentage points above matched random;
- mean net R at least `+0.10R` above matched random;
- matched-random net-R tail probability at most 0.10;
- no-call frequency no greater than one third; and
- validation/diagnostic strict-accuracy gap no greater than 20 percentage points.

Failure creates `development_rejected` and no prospective cohort. Missing or corrupt
evidence creates `invalid_or_unavailable`, never a pass. Passing creates only a sealed
forward observer; baseline accuracy remains unproven and live authority remains `NONE`.

## Fresh prospective qualification

When the historical gate passes, refit the unchanged ranker on all 180 sessions, bind it
to the selected contract and begin strictly after the latest closed session known at
registration. Persist the causal decision before the next session starts, then resolve it
only from later observed M1/M5/M15 paths.

Qualification requires at least 60 resolved fills across 40 sessions and all of:

- strict accuracy at least 70%;
- 95% Wilson lower bound at least 55%;
- mean after-cost net R above zero;
- matched-random accuracy and net-R tails at most 0.05;
- maximum losing streak no greater than six; and
- zero prediction timing, path integrity, model binding or market-mixing violations.

Report progress toward 80% separately. Never shorten, backfill, substitute or relabel the
cohort to reach either percentage. A prospective pass authorizes human review only.

## Required artifacts and dashboard

- Content-addressed full-universe causal source snapshot and split manifest.
- Hash-sealed sparse acquisition manifest created before any request.
- Per-request acquisition ledger and immutable path-integrity report.
- Content-addressed M1/M5/M15 path bundle with provenance.
- Six validation scorecards, frozen winner and one diagnostic scorecard.
- Momentum, execution-anchor and matched-random controls.
- Append-only experiment registry and prospective registration only after every gate.
- Fail-closed Learning-dashboard panel showing path coverage, filled trades, no-call rate,
  strict accuracy, Wilson interval, net R, exact blockers and live authority `NONE`.

