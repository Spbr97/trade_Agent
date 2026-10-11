# Self-learning M19 — pre-takeoff selector and forward-watch checkpoint

Checkpoint date: 2026-10-11 IST

Status: historical selector rejected; forward M15 research watch collecting. The
canonical 149/693 = **21.50%** baseline is unchanged. There is no live or order authority.

## What this checkpoint tested

M19 followed the frozen protocol in
[`self-learning-m19-pre-takeoff-protocol.md`](self-learning-m19-pre-takeoff-protocol.md).
It did not tune against the internal diagnostic. Because neither market produced a
calibration policy with both useful volume and positive after-cost expectancy, the
diagnostic stayed sealed.

| Market | Highest-volume frozen calibration point | Calls | Strict accuracy | Mean net R | Result |
|---|---|---:|---:|---:|---|
| NSE | bounded random forest, p >= 0.50 | 9/12 sessions | 4/9 = 44.44% | -0.244R | rejected |
| Crypto | regularized logistic, p >= 0.50 | 10/12 sessions | 5/10 = 50.00% | -0.249R | rejected |
| BSE | not run | — | unavailable | unavailable | M18-A path coverage not ready |

Headline accuracy from tiny subsets is not accepted. NSE's p >= 0.55 forest point was
3/4 = 75%, and crypto's p >= 0.60 logistic point was 5/6 = 83.33%, but both failed the
frozen minimum of eight calibration calls. Neither can be presented as a baseline.

## Information-set change implemented

The protocol's stop rule required new causal information instead of retuning the failed
daily selector. The forward collector now uses completed M15 bars to measure:

- distance below the prior 20-bar high;
- four-versus-prior-20 true-range contraction;
- current and recent relative volume;
- higher-low pressure and close location;
- rolling VWAP position and ATR;
- a frozen later-bar trigger, stop and +0.75R target.

A watch never counts as a trade. The trigger, stop and target are written to a hash-chained
plan ledger. Only bars strictly later than the decision bar can trigger it. Trigger/stop
ambiguity is invalidated; stop wins target/stop ambiguity after entry; gaps above the quick
target are invalidated; and every resolved trade is scored after configured costs.

Non-finite range or liquidity inputs are excluded. Undefined liquidity cannot pass.
Directionless stablecoin/INR pairs are now blocked from new directional plans. Existing
plans are not rewritten retroactively.

## First full-universe crypto collection

Run time: `2026-10-11T15:20:36+05:30`

- CoinDCX active INR pairs: **339**
- completed-M15 requests: **339**
- valid M15 feature rows: **302**
- invalid/unavailable rows: **37**
- pairs with current daily-ranker evidence: **26** (shown separately; missing daily rank
  is explicit and does not remove a pair from the M15 scan)
- displayed pre-break research watches: **12**
- tradeable calls: **0**
- immutable plans at the checkpoint: **17 total**, **15 pending trigger**, **2 invalidated
  before trigger**, **0 triggered trades resolved**
- strict accuracy and mean net R: **unavailable**, because zero trades have resolved

This run expands monitoring, but it does **not** prove better accuracy or profitability.
It gives the agent the causal before-takeoff snapshots and honest failure outcomes needed
to test a later precision selector.

## Dashboard and automation

The Self-learning tab now separates:

1. the rejected historical M19 threshold curves;
2. the current forward watch snapshot;
3. immutable plan states; and
4. resolved after-cost accuracy/mean net R when those values genuinely exist.

`scripts/configure_pre_takeoff_watch_task.ps1` registers the crypto collector every 15
minutes with overlap protection and a ten-minute execution limit. The task is research
only and cannot place an order.

## Next accuracy decision

Do not tune on pending plans. First collect terminal plans. Once there is a usable cohort,
freeze a setup-separated precision gate before evaluating it. Compare `arming` versus
ordinary `watch`, daily-rank available versus unavailable, contraction, participation,
VWAP support and distance-to-trigger. Keep NSE/BSE evidence separate and add their M15
collector only during market hours. No baseline claim is allowed until a frozen forward
policy has adequate calls, a positive after-cost mean R and the preregistered confidence
gate.
