"""Research study: does confirming a potential-movers pick after the open give an edge?

Usage: python scripts/potential_movers_confirmation_study.py <nse|bse> <output_dir>
Result (2026-10-13): no. See docs/potential-movers.md."""
import sys, time, json
sys.path.insert(0, 'src')
import duckdb, pandas as pd, numpy as np
from datetime import date, timedelta
from tradedesk.potential_movers import _read_daily, build_panel, LIST_SIZE
from tradedesk.potential_movers_model import _fit, FEATURES
from tradedesk.leader_discovery import _connect_read_only
from pathlib import Path

market = sys.argv[1]
db = Path({'nse': 'data/tradedesk.duckdb', 'bse': 'data/bse.duckdb'}[market])
frame, names = _read_daily(market, None)
panel = build_panel(frame, market)
con = _connect_read_only(db)
m15 = con.execute("select scrip_code, ts, open, high, low, close, volume from candles where interval='15minute' order by scrip_code, ts").df()
con.close()
m15['t'] = pd.to_datetime(m15.ts, unit='s', utc=True).dt.tz_convert('Asia/Kolkata')
m15['date'] = m15.t.dt.date
m15['hm'] = m15.t.dt.hour * 60 + m15.t.dt.minute
first = m15.date.min()
sessions = sorted(m15.date.unique())
print(market, 'intraday sessions', len(sessions), sessions[0], sessions[-1], 'codes', m15.scrip_code.nunique())

known = panel[panel.next_ret.notna()]
train = known[known.date < first - timedelta(days=10)]
model = _fit(train)
print('trained on', train.date.min(), '->', train.date.max(), len(train))

# panel row dated D-1 predicts session D; map to next session in intraday calendar
prev_of = {sessions[i]: sessions[i - 1] for i in range(1, len(sessions))}
panel_idx = panel.set_index(['date', 'code'])
by_day = {d: g for d, g in m15.groupby('date')}

rows = []
for d in sessions[1:]:
    prior = [x for x in panel.date.unique() if x < d]
    if not prior:
        continue
    pdte = max(prior)
    cand = panel[panel.date == pdte].dropna(subset=FEATURES)
    if cand.empty:
        continue
    cand = cand.assign(p=model.predict_proba(cand[FEATURES])[:, 1]).sort_values('p', ascending=False)
    top = set(cand.head(LIST_SIZE).code)
    universe = set(cand.code)
    day = by_day[d]
    for code, g in day.groupby('scrip_code'):
        if code not in universe:
            continue
        g = g.sort_values('t')
        if len(g) < 20 or g.hm.iloc[0] != 9 * 60 + 15:
            continue
        o = g.open.iloc[0]
        b1, b2 = g.iloc[0], g.iloc[1]
        c2 = b2.close
        cond = (c2 >= o * 1.01) and (c2 > b1.high) and (b2.close > b2.open)
        entry = g.iloc[2].open if len(g) > 2 else np.nan   # next bar open after the confirming bar
        exit_ = g.close.iloc[-1]
        gross = exit_ / entry - 1
        # hard-ish stop: the session low of first two bars; evaluate path
        stop = min(b1.low, b2.low)
        path_low = g.iloc[2:].low.min()
        stopped = path_low <= stop
        r_stop = (stop / entry - 1) if stopped else gross
        rows.append(dict(date=d, code=code, in_list=code in top, cond=cond, gross=gross, r_stop=r_stop,
                         prev_close=cand.set_index('code').close[code] if code in cand.set_index('code').index else np.nan,
                         entry=entry))
df = pd.DataFrame(rows)
cost = 0.0015  # rough intraday round-trip incl. slippage; reported alongside gross
def show(label, s):
    if len(s) == 0:
        print(f'{label:34s} n=0'); return
    print(f'{label:34s} n={len(s):6d} meanGross={s.gross.mean()*100:6.3f}% net~={(s.gross.mean()-cost)*100:6.3f}% winGross={(s.gross>0).mean()*100:4.1f}% stopMean={s.r_stop.mean()*100:6.3f}%')
show('all liquid, no filter, buy 09:30', df.assign(gross=df.gross))
show('confirm rule, whole universe', df[df.cond])
show('list only, no confirm', df[df.in_list])
show('list + confirm rule', df[df.in_list & df.cond])
show('universe confirm EXCLUDING list', df[~df.in_list & df.cond])
print('days', df.date.nunique())
df.to_csv(f"{sys.argv[2]}/confirm_{market}.csv", index=False)
