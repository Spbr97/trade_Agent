# Overnight research, 15 Oct 2026 (research only, nothing here is an alert, signal or order)

## One-paragraph answer
Crypto has no tradeable long-only edge. NSE and BSE have a real-looking intraday pattern ("fade
the stocks with the strongest early move": short them after the first 15-120 minutes). The 49
NSE rules from yesterday were too broad to trade as a list (they flag ~500 of ~1,000 stocks a
day), but ranking stocks by how many rules agree held up on days the ranking had not seen. That
ranking is now tracked forward from this morning's open. It is evidence, not proof: the first
real test is the live paper picks starting 09:31 today.

## Crypto (long-only, what CoinDCX spot allows)
- Cleaned the daily panel first: 339k rows had junk (a 3-million-percent "return" on TRX in 2020
  from a price-unit glitch). Kept 2021+, coins trading >= Rs 5 lakh/day, dropped 50 bad rows.
- Daily mover search, holds of 1/3/5/10/20 days: 626 conditions, 1 passed training, 0 survived.
- The one that looked good (dip-buy: below 50-day average and far below the 20-day high) is an
  artefact: 10-20 day holds overlap from day to day, which inflated the t-stat. With
  non-overlapping entries training t is ~0.6 and validation is -5% to -13% over the hold.
- Intraday long-only: already 0 passed (costs ~1.57% round trip).
- Conclusion: no. Not a cost-only problem; there is no long-side edge in this data at all.

## Crypto short side (needs futures, your call)
- The same "fade the early spike" pattern exists in crypto before costs: excess over same-day
  coins +0.5% to +1.1% per trade (r_open top 10%), +1.1% to +2.4% with a volume filter. Positive in
  both halves; validation t is 0.7-2.5, so suggestive, not proven. Universe short drift is also
  +0.65% (coins fell in this period), so absolute gross is flattered by the bear market.
- Spot cannot short. Perpetual futures could, but I do not know whether you can trade them, their
  fees/funding, or the tax treatment. Check that before anything else. Coins here are thin
  (>= Rs 5 lakh/day), so real slippage on shorts will be worse than modelled.

## NSE: what held up and what did not
1. The 49 rules are not a pick list. Together they flag ~500 stocks per checkpoint per day.
   Averaged over all flagged stocks: gross +0.15%, net ~0 after costs, and ~-0.09% if hedged
   against the market. Part of the earlier "+0.21% net" was just stocks drifting down intraday.
2. Agreement ranking works. Using only conditions that passed on the first 70% of days, then
   ranking the untouched last 30% (71 days) by how many conditions flag each stock:
   - top 3 per checkpoint: gross +0.74%, net +0.58%, vs market-hedged +0.48% (t ~2.9)
   - top 5: net +0.42%; top 10: net +0.36%
   - random 5 from the same pool: net -0.06%; whole universe: net -0.06%
   - strongest in the lowest-liquidity third (+0.76% net) but still ~+0.25% net in the most
     liquid third (> Rs 50 crore/day average), so it is not only illiquid names
3. A stop is required and it halves the edge. Replaying the same 852 out-of-sample picks bar by
   bar (fill at the open if it gaps past the stop, conservative if touched):
   no stop +0.58% net but worst trade -12.6% and 5th percentile -5.4%;
   2% stop +0.27% net, worst -2.0%, win rate 44%; 3% stop +0.26% net; 1.5% stop +0.23%.
   Per trade that is ~0.13R. Thin: it needs many trades and low costs to matter.
4. Month by month the top picks were positive in July and September, flat in August and a small
   loss in the first half of October. 71 days is one regime.

## BSE
- Backfilled 400 days of 15-minute bars: 1,585 codes, 10.25M bars (22k requests, ~85 min).
- Same search as NSE: 22 survivors, all "fade the early spike" shorts, all positive in both halves.
- Pure out-of-sample consensus: top 3 per checkpoint gross +0.66%, net +0.50%, hedged +0.39%
  (t 2.6, 78 days). Caveat: many BSE stocks are cross-listed on NSE, so this is only partly an
  independent confirmation.
- Not yet tracked live: the live registration hook covers NSE only. BSE is research-only until
  you say to wire it in.

## What changed in the code (not committed)
- tradedesk_lab/intraday_mover_study.py: `register_live` now also records `consensus_top3:<checkpoint>`
  (the 3 stocks most short rules agree on; ties broken by liquidity) as an extra hash-chained
  forward row. The existing per-rule rows are unchanged. Forward scoring and the dashboard pick
  it up through the same path, against the same-entry-bar universe null.
- tests_lab/test_intraday_mover_study.py: new test; 14 tests pass; ruff clean.
- Dry run on 14 Oct's real bars: 4 consensus rows registered at 09:31/09:46/10:16/11:16. In
  hindsight 9 of 12 picks won (+0.99% gross average), but that day was probably inside the
  search window, so treat it as an illustration only.

## What it means for "catching winners"
These are shorts entered AFTER the first move, not longs on the stocks that run. They do not catch
IDBI/Vedanta-type winners on the way up. They harvest the part of early spikes that fades by the
close. Today's results are the first honest out-of-sample numbers.

## Decisions for you
1. Perpetual futures for crypto shorts: allowed/wanted? (fees, funding, tax unknown to me)
2. Wire BSE into live registration and the nightly/Saturday search tasks?
3. Where to read today's consensus picks: `data/m14_m18/intraday_mover_study/nse/forward_picks.jsonl`
   (rows named consensus_top3:*). I have NOT pushed them as notifications because that would
   work like an alert for a candidate that has not cleared the eligibility gate. Say if you want
   a "paper picks" notification at each checkpoint, clearly labelled research-only.
4. Still open from before: push the 8 (now 8 + this change) local commits, news-source terms,
   configure_hidden_tasks.ps1 -Apply, and the close-finality task.
