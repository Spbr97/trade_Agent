# Fair random-entry benchmark and same-day excess - 25 October 2026

Research only. Two new lab modules plus tests; no production file changed (`git status`
shows only the new files). Nothing here alters a verdict, alert, size or order.

## Why

`backtest/null_baseline.py` was built for intraday setups. The lab feeds it daily trades with
`session_start = entry_bar` and `session_end = exit_bar` (`run_lab_candidate.to_harness_trades`),
so for multi-session trades the random side is drawn only inside the real trade's window,
force-closed at the real trade's exit bar (for a trailing exit, just after the pullback that
fired it), given a fixed `t2` target instead of the real exit, and its net line is priced as
intraday. Separately, `load_real_daily_frames` picks the universe by liquidity over the LAST
60 sessions, so any long-hold rule inherits today's winners' past rise.

## Result: `r_ema20_bounce_strong_trend_trend_trail`

`python -m tradedesk_lab.harness.run_fair_null --market <m> --candidate r_ema20_bounce_strong_trend_trend_trail`

| Market, universe | Setup gross | Original null (margin) | Fair, filter days (margin, p) | Fair, any day (margin) |
|---|---|---|---|---|
| NSE, top 100 now | +0.309R | -0.075R (+0.384R) | +0.283R (+0.026R, p 0.27) | +0.498R (-0.189R) |
| BSE, top 100 now | +0.322R | -0.081R (+0.403R) | +0.290R (+0.032R, p 0.23) | +0.524R (-0.202R) |
| NSE, point-in-time top 100 of 300 | +0.078R (941 judged) | - | +0.052R (+0.026R, p 0.24) | +0.259R (-0.181R) |

BSE rules whose gauntlet history makes them PROVISIONAL-eligible (500+ trades, +0.20R net on
the laptop's search reports to about 17 Oct), judged on the point-in-time top 100 of 300 loaded:

| BSE rule | Judged trades | Setup gross (judged) | Original margin | Fair filter-day margin (p) | Fair any-day margin |
|---|---|---|---|---|---|
| r_high52_break_strong_trend_trend_trail | 892 | +0.130R | +0.352R | -0.061R (0.95) | -0.287R |
| r_tsmom20_strong_trend_trend_trail | 404 | +0.025R | +0.270R | +0.078R (0.06) | -0.133R |
| r_tsmom252_volume_confirm_trend_trail | 469 | +0.093R | +0.295R | +0.032R (0.26) | +0.032R |

None clears +0.10R over random with significance; after ~0.05R of delivery costs their
point-in-time results are about -0.03R to +0.08R. Whether any is alerting now depends on the
server's `data/reports/profit_gate_status.json` (live results, stops), not checked here.

- The original benchmark reproduces the stored +0.38-0.40R margin; the fair one shrinks it to
  about +0.03R, not significant, below the +0.10R bar.
- Random days on the same stocks with the same exit beat the rule: the ADX>30 filter picks
  stretched moments.
- Judged only while each stock was in the day's top-100 liquid set, the rule earns +0.078R
  gross, roughly +0.02R after costs, against +0.31R on the hindsight universe.
- Independent check (study script, all liquid NSE stocks): random long entries in the top 100
  by today's liquidity +0.20R gross vs +0.04R for the top 100 by liquidity at the time.

Implication: PROVISIONAL alerting (500+ gauntlet trades at +0.20R) rests on history inflated
by both effects. Suggested, not applied: require the fair benchmark's margin >= +0.10R on a
point-in-time universe before PROVISIONAL, and re-judge setups already provisional.

## Modules

- `tradedesk_lab/harness/fair_null.py` - `run_fair_null`: random entries from eligible bars
  within +/-126 sessions of each real entry, independent of its exit; each simulated once with
  the real `evaluate_entry`/`evaluate_exit` and the real trade's geometry; no forced close;
  20,000 cohorts (p resolves to 0.00005, so a Bonferroni threshold across the search is
  measurable); optional DELIVERY-cost net figures. `point_in_time_members` gives day-by-day
  top-N liquidity membership from prior turnover only.
- `tradedesk_lab/harness/run_fair_null.py` - CLI comparing original vs fair (filter days, any
  day), optional `--pit-top N`; writes `data/m14_m18/fair_null/<market>/<candidate>.json`.
- `tradedesk_lab/same_day_excess.py` - each distinct live call (judged contracts only, ATR-band
  rejects dropped, NSE/BSE duplicates merged) scored at 10 sessions against 10 random stocks
  of the same day and liquidity quintile, day as the unit, verdict from
  `profit_gate.confidence_sequence` with alpha split across setups. Writes
  `data/m14_m18/same_day_excess/<markets>.json`.
- Tests: `tests_lab/test_fair_null.py` (8), `tests_lab/test_same_day_excess.py` (6).

## Limits

The loaded set (most liquid now) still carries some survivorship even with point-in-time
judging; a full fix loads every code. The fixed-horizon excess measures selection and entry,
not exits. All numbers are on the laptop's data snapshot (to 16-17 Oct).
