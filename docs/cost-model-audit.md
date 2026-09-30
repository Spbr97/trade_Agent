# Cost-model audit (Phase 2 of plan-baseline-accuracy.md), crypto - 30 September 2026

Scope: how `CryptoCostModel` treats the 1% Section 194S TDS, what the search's best
candidates actually pay per trade, and what an after-tax view would change. NSE fixed-cost
sizing (the other half of Phase 2) is not covered here. **Nothing in the models or gates was
changed**; this records what was found. Tax treatment below is the author's reading and
should be confirmed with a tax professional before it drives any decision.

## What the model does today

`markets/costs.py::CryptoCostModel` charges, per round trip: 0.2% fee on each leg, 18% GST
on the fee, slippage 0.05% per leg, and **1% TDS on the sell leg's full value as a cost,
win or lose**. The costs are correct as a statement of cash leaving the account.

## What the candidates actually pay (from the stored evidence, gross vs net R)

| Candidate | Trades | Gross R | Net R | Cost per trade |
|---|---|---|---|---|
| r_donchian20_volume_confirm_trend_trail | 1275 | +0.233 | +0.135 | 0.098R |
| r_tsmom20_calm_vol_trend_trail | 1987 | +0.158 | +0.065 | 0.093R |
| r_donchian55_rsi_momentum_trend_trail | 881 | +0.151 | +0.046 | 0.105R |

Roughly 0.1R per trade. TDS is about two thirds of that cost (1.0% of about 1.6% round-trip).

## Finding 1 - TDS is a prepayment, not a fee

TDS is credited against the holder's tax liability and refunded if it exceeds it. Treating it
as a permanent 1% cost overstates the true cost by roughly 0.06R per trade. If it were
excluded, net expectancy for the three rows above would be about +0.20, +0.13 and +0.11R.
This is the direction the plan expected: the model is conservative on this line.

## Finding 2 - the 30% VDA tax matters far more, and cuts the other way

Indian crypto gains are taxed at a flat 30% (Section 115BBH) with **no set-off of losses**,
including against other crypto gains. Each winning trade is taxed on its gain and each losing
trade is not refunded. So tax per trade is about 30% x the average positive gain, not 30% of
the expectancy.

Rough estimate for `r_tsmom20_calm_vol_trend_trail` (39% winners, average loss near -1R,
which puts the average win near +2.05R): tax is about 0.30 x 0.39 x 2.05 = **0.24R per
trade**. That exceeds its gross edge of 0.158R. On this estimate every trend-following
candidate found so far is **negative after tax** at this win rate, however good its
pre-tax net R. The same arithmetic applies to any strategy with a low win rate and large
winners; it penalises exactly the payoff shape trend rules have.

This is an estimate from summary statistics, not a per-trade computation. Confirming it needs
each candidate's trade list run through a per-trade tax function.

## Implications (decisions for the user, not made here)

1. The eligibility gate measures pre-tax net R after fees and TDS. For a personal taxable
   account that is not the number that matters. An after-tax figure should at least be
   REPORTED beside it for any candidate that reaches review.
2. If after-tax edge is the real objective, the search should be judged on it. Low-win-rate
   trend rules would then need a much higher gross edge, and higher-win-rate short-horizon
   rules (RSI(2)-style mean reversion) become relatively more attractive because tax scales
   with win size.
3. The +0.046R to +0.135R "net" figures in the review queue overstate what would be kept.

## Recommended next step

Add a reporting-only `after_tax_r` to the gauntlet report (per-trade 30% on positive net
gains, no offset, TDS credited), show it in the review item's evidence line, and re-score the
existing survivors. It changes no gate; it makes the review honest. Say if you want it built.
