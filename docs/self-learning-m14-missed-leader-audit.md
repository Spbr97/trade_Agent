# Self-learning Milestone 14 — missed-leader coverage audit

Date: 2026-10-09

Status: implemented and integrated as a read-only observer for NSE, BSE and crypto.
No baseline accuracy improvement is claimed.

## Why this checkpoint exists

The agent can correctly reject weak calls and still miss the instruments that subsequently
make the market's strongest moves. Call-performance learning cannot detect that blind spot:
it contains only instruments for which an existing setup created a candidate. This
checkpoint adds the missing negative space—the liquid full-market population that produced
no setup candidate at all.

This is not another rerun of the rejected generic ranking experiments. Those experiments
remain negative. M14 first measures coverage and creates a causal dataset. A later ranker is
allowed only if a preregistered test finds repeatable pre-move separation.

## Frozen diagnostic contract

The contract was fixed before opening the results:

- Primary horizon: the next three market sessions.
- Opportunity measure: maximum future high relative to the decision close.
- Leader: top 5% of the same-session liquid universe and at least +2% gross movement for
  NSE/BSE or +4% for crypto.
- Minimum history: 60 sessions.
- Minimum 20-session average turnover: ₹5 crore for NSE/BSE and ₹25 lakh for crypto.
- Minimum price: ₹50 for NSE/BSE and ₹0.01 for crypto.
- Crypto's USDT/INR and USDC/INR stablecoin pairs remain excluded.
- Markets are physically and logically separate.

An opportunity label is hindsight-only. It is **not** a call, fill, target hit, successful
trade or addition to the performance denominator. A stock that rose intraday and then
closed lower can be an opportunity leader; that fact says nothing yet about whether a
causal entry/stop/target contract could capture it.

## Causal inputs

Every feature is available at the decision close:

- 1/3/5/20-session returns;
- opening gap, daily range and close location;
- 20-session volume and turnover ratios;
- distance from 20/50-session averages and prior 20-session high;
- 14-session ATR percentage and average turnover; and
- same-session cross-sectional ranks of momentum, volume and breakout distance.

Future close, high and low values live only in the separate `opportunity` object. Tests
verify that changing outcome columns cannot change the feature object.

## Coverage attribution

For each leader, M14 records one of:

- `qualified`: the existing scanner qualified a call;
- `shadow`: a setup detected it but remained shadow-only;
- `rejected`: a setup detected it and a declared gate rejected it;
- `no_setup_candidate`: the tracker ran for the session but no existing setup saw it; or
- `tracker_not_observed`: there is no proof the tracker ran, so absence is not mislabeled
  as a setup miss.

Rejection causes are retained separately from the later execution state. Pending, invalid
and never-triggered calls remain visible but are never converted into wins.

## First development result

The saved datasets contain 60 mature sessions per market:

| Market | Dataset rows | Tracker-observed mature sessions | Leaders in observed sessions | Existing setup candidates | Candidate recall | Qualified leaders |
|---|---:|---:|---:|---:|---:|---:|
| NSE | 69,228 | 8 | 463 | 36 | 7.78% | 0 |
| BSE | 66,780 | 1 | 56 | 0 | 0.00% | 0 |
| Crypto | 844 | 19 | 30 | 0 | 0.00% | 0 |

Latest comparable sessions:

| Market | Session | Liquid universe | Leaders | Candidate recall | Qualified recall | Main miss |
|---|---|---:|---:|---:|---:|---|
| NSE | 2026-10-05 | 1,148 | 58 | 6/58 = 10.34% | 0/58 | 52 had no setup pattern |
| BSE | 2026-09-25 | 1,111 | 56 | 0/56 | 0/56 | all 56 had no setup pattern |
| Crypto | 2026-10-05 | 26 | 2 | 0/2 | 0/2 | both had no setup pattern |

BSE's comparable session is older because that is its only tracker-observed mature session
inside the current dataset. The audit does not present unobserved sessions as scanner
failures.

The result answers the motivating question: the present setup grammar does not even create
a candidate for most short-horizon leaders. Improving filters alone cannot solve that
coverage gap. This is a diagnosis, not evidence that the new feature set can predict the
leaders.

## Integrity and integration

- `src/tradedesk/leader_discovery.py` owns extraction, attribution, hashes and validation.
- `scripts/missed_leader_audit.py --market all` refreshes all three markets independently.
- Each market tracker refreshes its own audit only after its established ledger is saved;
  failure is explicit and cannot alter existing calls.
- Dataset JSONL and summary JSON are SHA-256 bound.
- Every tracker-observed session strictly after 2026-10-09 receives an immutable
  prospective freeze. A later candle or prediction-time rewrite raises instead of
  replacing that evidence. Resolver outcome maturation is allowed separately.
- The Learning dashboard shows the latest coverage, observed-history recall, miss reasons
  and top leaders through `/api/self-learning/missed-leaders?market=...`.
- The dashboard always states diagnostic-only authority and `Baseline improved: NO`.
- Active signal generation, eligibility, order paths and management code are unchanged.

## Next accuracy-changing checkpoint — M15

Preregister a pre-move separability experiment before inspecting model results:

1. Freeze chronological development, validation and untouched forward boundaries for each
   market separately.
2. Compare a transparent regularized logistic model and one bounded tree model with simple
   momentum, existing-setup and matched-random baselines.
3. Evaluate top-one and top-three per-session recall, precision, no-call frequency and
   calibration—not pooled row accuracy.
4. Replay selected instruments through an executable next-session entry, stop, quick
   target, costs and slippage contract. Hindsight high capture is forbidden.
5. Reject the layer if lift is unstable by time block, disappears after costs, fails to
   beat matched random timing, or depends on the development window.
6. If no causal separation exists, change the information set rather than retuning the
   same failed model family.

Until M15 passes, M14 cannot create a personal call or improve the reported baseline.
