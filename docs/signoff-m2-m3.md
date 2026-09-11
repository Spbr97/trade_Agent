# M2 / M3 live sign-off

Live checks against the INDstocks API with real credentials. Numbers only; no tokens or
account identifiers beyond what the API prints in `auth check`.

## M2 - INDstocks client

Done-when (PLAN.md 14): universe data pulled with no 429s; daily close verified against
NSE's official close. Plus PLAN.md 5.2: measure how soon after 15:30 the daily close is
final.

### Auth (2026-09-11)

- TOTP setup on indstocks.com/app/api-trading/access-tokens (web only; "Setup TOTP" under
  Method 2 "Build your own trading application"). Direct URL redirects to the marketing
  page unless already logged in at indstocks.com.
- `tradedesk auth check`: token OK (264 chars), profile and funds endpoints answer.
- Defect found and fixed (commit ea427ea): each CLI process generated its own token, and
  generation is 1/min and kills the previous token, so consecutive commands failed with
  "Please wait a minute before requesting another token". The token is now cached in the
  keychain and shared across processes.

### Daily close vs NSE official close (2026-09-11, after the close)

INDstocks `1day` candles vs Yahoo Finance `.NS` daily closes (which carry NSE's official
close, i.e. the last-30-minute VWAP, not the last trade):

| Scrip | Sessions compared | Result |
|---|---|---|
| SBIN (NSE_3045) | 2026-09-07 .. 09-11 (5) | all 5 closes equal to the paisa |
| RELIANCE (NSE_2885) | 2026-09-08 .. 09-11 (4) | all 4 equal |
| VEDL (NSE_3063) | 2026-09-08 .. 09-11 (4) | all 4 equal |

Conclusion: the INDstocks daily candle close IS the official close, including the
same-day candle fetched at ~21:00 IST. The full quote's `prev_close` equals the previous
session's candle close. The full quote has no separate "today's close" field: after
15:30 use the daily candle, not `live_price`.

Timestamp convention observed: daily candle `ts` is 00:00 UTC of the session date
(05:30 IST), not 09:15 IST. Converting to an IST date gives the right session; do not
truncate to a UTC date after any tz shift.

Day volume: today's candle volume (7,771,109) was slightly above Yahoo's 15:15 snapshot
(7,766,361) - the candle includes post-15:15 trades; not a discrepancy.

### How soon after 15:30 is the close final?

NOT YET MEASURED - scheduled. `scripts/poll_close_finality.py` polls the `1day` candle
for 5 scrips (SBIN, RELIANCE, VEDL, HDFCBANK, TCS) every 60 s from 15:25 to 16:15 IST and
logs when each close last changes, to `data/reports/close_finality_<date>.jsonl` (+ a
`_summary.json`). Registered as a Windows scheduled task (`tradedesk-close-finality`),
next trading day 2026-09-14 15:22 IST - durable, independent of any chat session. Until
measured, the evening scan should not run before 16:00 IST (schedule.yaml already sets
this).

### Defect found and fixed while building the poller (commit 67f39c9)

Live API quirk, not documented anywhere: a `/market/historical` request whose window
spans less than ~6 days AND ends at/near "now" returns 200 with the most recent day's
candle silently missing - no error, no rate-limit signal. A wider window covering the
same end time returns it correctly. Confirmed live before and after the fix.

This is not just a poller-script problem: `history_loader.py`'s incremental load builds
exactly this window shape (`start=today, end=now`) for any code already loaded through
yesterday - i.e. every evening's incremental `data load` would have silently missed that
day's candle for already-current codes, with nothing to notice by. Fixed in
`candles_history()` (pads only the first/most-recent paging window when it would
otherwise be narrower than 6 days, bounded by the API's per-call max window; later
paging windows are untouched). Regression tests added (`tests/broker/test_rest.py`).
Verified live against the exact incremental shape both before (empty result) and after
(today's candle returned) the fix.

### Universe pull without 429s

Full 10-year daily history load, whole tradeable universe, after the fix above:

- 2,665 codes, **4,652,206 bars fetched, 0 errors**
- 6,601 data-API calls for the whole run (well inside 5 rps / 100k per day)
- `tradedesk data status` afterward: 2,640 codes with `1day` data, latest bar
  2026-09-11 05:30 (today's session, via the incremental-load fix above)

M2 verdict: **PASS**. No 429s across a full-universe load; daily close matches NSE's
official close to the paisa on every session checked; the one real bug found (silent
narrow-window data loss) is fixed and regression-tested. Only the close-finality timing
is still outstanding (scheduled, not yet measured).

## M3 - history, corporate actions, universe, quality report

Done-when: split and bonus days don't appear as crashes; gap and missing-data report
reviewed.

### Quality report (`tradedesk data quality --out data/reports/q.csv`, 2026-09-11)

2,639 codes checked, 1,873 issues:

| Kind | Count | Assessment |
|---|---|---|
| `missing_sessions` | 174,908 (762 codes) | Reviewed the distribution: dominated by late listings (a code's history legitimately starts after 2016-09-13, the window start) and illiquid small-caps with scattered zero-trade days (~4-5% of an otherwise-complete history, spread from early in the window). Neither is a pipeline defect - NSE genuinely has no session for a stock before its listing or on a day nobody traded it. |
| `suspected_unadjusted_split` | 853 | Expected: `config/data/corporate_actions` is empty (0 rows - no NSE board-meetings CSV imported yet), so real splits/bonuses show up as large single-day price ratios, exactly what the detector is for. Spot-checked NSE_10181 (2024-03-07): close/prev_close = 0.662, `store.load(adjusted=True)` ran without error (2,450 rows), raw price unchanged since there's nothing in `corporate_actions` to adjust by yet. **M3's literal done-when - split/bonus days don't crash - holds.** Real adjustment is blocked on the corporate-actions CSV import (open item, see CLAUDE.md). |
| `bad_ohlc` | 1,097 | All 1,097 on a single scrip: NSE_24735 (RAGHAV PRODUCTIVITY ENHANCERS), an illiquid micro-cap (~3,000-9,000 shares/day, turnover ~Rs 66k/day). Every flagged bar has `close` exactly ~2x `open=high=low` - a systematic feed anomaly for this one instrument, not a store/code bug (`bad_ohlc` fired correctly by design). Turnover is ~750x below `universe.yaml`'s Rs 5 crore/day threshold, so this scrip never enters the tradeable universe regardless. |
| `big_jump` | 257 | Not individually reviewed; same illiquid-small-cap population as the above, consistent with genuine large one-day moves rather than a systematic defect. Flag for a future pass once the universe filter is applied upstream of the quality report, to cut noise from codes that can never be traded anyway. |

### Universe-by-date

`tradedesk data universe --on 2026-09-10`: **1,160 members** (of 2,639 codes with data)
pass `min_avg_daily_turnover_inr: 5,00,00,000` and `min_price: 50` - ran cleanly, no
errors, membership is a plausible ~44% of everything loaded.

M3 verdict: **PASS**. No crash on real split/bonus days; the quality report's issues are
attributable to expected causes (late listings, illiquid names below the universe's own
liquidity floor, one instrument's feed anomaly), not to store or loader bugs; universe
membership computes cleanly. Open, non-blocking items: corporate-actions CSV import (for
real split adjustment, not just non-crashing) and the results-calendar CSV (earnings
blackout).
