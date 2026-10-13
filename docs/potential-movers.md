# Potential movers (research watch)

Added 2026-10-13 after Bandhan Bank and Birlasoft rose more than 5% and no setup listed them.
Code: `src/tradedesk/potential_movers.py`, `potential_movers_model.py`,
`scripts/potential_movers.py`. Output: `data/m14_m18/potential_movers/<market>/`.
Tasks: `tradedesk-potential-movers-{nse,bse,crypto}` (`scripts/configure_potential_movers_tasks.ps1`).
No live authority; it lists names, never entries, stops or sizes.

## Why the setups missed them
Every rule needs an uptrend or a breakout near highs. Both stocks were far below EMA50 near the
bottom of their six-month range and reversed on 2-5x volume, mostly at the open.

## Method
Per liquid stock-day, causal features known at the close (distance under EMA50, six-month range
position, 1/5/20-day returns, gap, close location, ATR %, range and volume vs normal) predict
whether the next close is >= +5%. Rows are split by date: train, validation, 3-session purge,
locked block (last 120 sessions). The learned model replaces the fixed tier rules only if its
top-25 per session beat the rules on both validation and locked blocks with a positive mean
next-day return. Retrained weekly. Each evening's list is written once, only if written before the
next session opens (crypto: within two hours of the new candle), and scored against the same-day
universe later. Incomplete candles are never used.

## Locked-block result (top 25 per session)
| Market | Closed >= +5% next day | All stocks | Old rules |
|---|---|---|---|
| NSE | 13.4% (4.2x) | 3.2% | 3.4% |
| BSE | 12.9% (3.9x) | 3.3% | 3.7% |
| Crypto | 24.2% (2.0x) | 12.1% | 16.0% |

## What it does not do
The picks' mean close-to-close return is +0.29% but open-to-close is -0.35%: the move happens at
the open. A model trained directly on open-to-close >= +3% finds those days 19% of the time (7%
base) yet averages ~0 open-to-close. A confirmation rule (strong first two 15-minute bars, enter
the next bar open, exit at the close; `scripts/potential_movers_confirmation_study.py`) was
about zero on the whole universe (NSE -0.25% gross, 217 trades, 508 sessions; BSE -0.36%, 127
trades, 72 sessions) and worse on listed names (-0.75% gross buying at 09:30). Listed names
reverse more often than they follow through. This is a screen for what to watch, not a trade.
The forward hit rate in `forward_tracking` is the evidence to watch; nothing has been promoted.

## Updates, 2026-10-13 (later)
- New listings are included (45+ daily bars, was 260) with listing age as a feature: Shringar
  House of Mangalsutra (202 bars) and Caliber Mining (56) rose 16% and 15% on 48x/64x volume and
  had been excluded by design. Locked-block top-25 hit rate: NSE 14.3% (4.4x base).
- Results calendar: `tradedesk data refresh-results` reads NSE's public board-meetings API
  (task `tradedesk-results-calendar`, weekdays 07:30 and 16:50). The calendar had stopped on
  11 Sep, so HCL Tech's 12 Oct results were unknown when it rose 7.4%. Results timing did not
  improve the top-25 ranking, so the report also carries a separate "results due" list (ranked by
  each stock's own past results move) whose direction is unknown by construction. On past
  results days a >=5% move happened 30% of the time vs 6.7% on ordinary days.
- Stale daily data is flagged (`data_stale`, `sessions_behind`) in the report and dashboard.
