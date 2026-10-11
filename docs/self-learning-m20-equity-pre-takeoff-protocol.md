# M20 Equity Pre-Takeoff Forward Protocol

Status: frozen before forward collection  
Authority: research watch only; never an order or live-trading pass

## Objective

Test whether completed-M15 compression immediately below resistance can identify NSE and
BSE moves before breakout while preserving strict, later-bar outcome accounting. M20 does
not claim a baseline improvement until the frozen evidence gates below are satisfied.

## Frozen scope

- Markets are evaluated separately: NSE evidence never fills or improves BSE evidence,
  and neither market is pooled with crypto.
- The scan covers every row in the market's current causal, daily-liquid decision
  universe. The initial frozen universe sizes are 1,130 NSE and 1,099 BSE instruments;
  this is not the former top-75 display shortlist.
- INDstocks history is requested only through the existing authenticated client and rate
  limiter, in its supported five-code batches.
- Each decision uses completed M15 bars only. A bar cannot trigger a plan created from
  that same bar.
- The existing M19 pre-takeoff feature rules, trigger, stop, +0.75R target, costs,
  conservative same-bar ambiguity handling, expiry, and timeout remain unchanged.
- At most twelve candidates are displayed per market snapshot. Display truncation does
  not truncate the universe that was checked.

## Session rules

- NSE/BSE collection runs on weekdays during the cash-session window.
- New plans may be registered only from 09:30 inclusive to 15:00 exclusive IST.
- Runs at or after 15:00 may resolve existing plans but may not create new ones.
- Weekend, closed-market, or delayed runs may refresh diagnostics and resolve genuinely
  later completed bars, but may not create retrospective plans.
- One combined equity task scans NSE and BSE sequentially under one client and one process
  lock. This prevents the two markets from competing for the same account rate limit.

## Immutable outcome contract

- Candidate snapshot, trigger, stop, and target are hash-bound when the plan is armed.
- Only later completed bars can trigger or resolve the plan.
- Trigger-and-stop ambiguity on one bar is invalidated, not counted as a success.
- If stop and target are both touched after entry on one bar, the stop wins.
- Accuracy is `strict_successes / resolved_triggered_trades`; unavailable is never a pass.
- Mean net R is measured after the market's configured slippage, brokerage, taxes, and
  minimum-size constraints.
- Invalid and never-triggered plans are shown operationally but are not disguised as
  failed or successful trades.

## Frozen checkpoints

### Operational checkpoint

Evaluate each market at the first run where both conditions are true:

- at least 20 resolved triggered trades; and
- evidence spans at least 10 distinct cash-market sessions.

Stop or redesign that market's hypothesis if strict accuracy is below 50% or mean net R
is not positive. Passing this checkpoint permits continued collection only; it does not
authorize live trading or declare a baseline improvement.

### Promotion-quality checkpoint

Evaluate each market only after at least 60 resolved triggered trades across at least 40
distinct cash-market sessions. All of the following must hold:

- strict accuracy at least 70%;
- 95% Wilson lower bound for strict accuracy at least 55%;
- mean net R greater than zero after costs; and
- maximum resolved loss streak no greater than six.

Any later promotion still requires the repository's independent review and safety gates.
The desired 70-80% success range is a target to prove, never a hard-coded dashboard value.

## Dashboard contract

For the selected market, the dashboard must show full-universe requested/valid coverage,
whether new-plan registration is open, current watch candidates, immutable plan states,
resolved strict accuracy, and mean net R. It must keep `Tradeable now = 0`,
`Baseline improved = NO`, and `WATCH ONLY` until real frozen forward evidence clears the
required gates.
