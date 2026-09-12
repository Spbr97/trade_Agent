"""Shared on-demand "what does the agent think of this symbol right now" lookup, used by
both mcp_server.py's `analyze` tool and the dashboard's /api/analyze endpoint - one
implementation, so the two surfaces can never disagree. Market-aware (NSE or crypto):
same engine, same scan_day, just a different store/benchmark/cost model per markets/market.py.

This is advisory only (CLAUDE.md hard rule): it reports whether a setup would arm today
and what it looks like, and whether you already hold a position - it never places, sizes
or invents an order. There is deliberately no "SELL" signal for a position you don't
hold; the closest reading is "no active call" (nothing armed) - CLAUDE.md's home
directive that a new signal or price can only come from engine/engine.py::scan_day.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Any

from tradedesk.config import Settings

NSE_DB = Path("data/tradedesk.duckdb")
CRYPTO_DB = Path("data/crypto.duckdb")
BSE_DB = Path("data/bse.duckdb")
NSE_JOURNAL = Path("data/journal.sqlite")
CRYPTO_LOG = Path("data/reports/crypto_signal_tracking.jsonl")


def db_for(market: str) -> Path:
    if market == "crypto":
        return CRYPTO_DB
    if market == "bse":
        return BSE_DB
    return NSE_DB


def setup_hit_rate(market: str, setup: str) -> dict[str, float | int | None]:
    """Historical "hit target before stop" rate for one setup, kept per-market on purpose
    (NSE's paper book and crypto's tracker are independent datasets, different costs and
    universes - CLAUDE.md M13 note): NSE reads journal.stats.track_record (the paper book,
    which already scores every triggered signal); crypto reads its own resolved JSONL log
    since it has no paper book. Returns {"n": 0, "hit_rate": None} if nothing has resolved
    yet - not a guarantee for the NEXT trade, just what history says about this setup."""
    if market == "crypto":
        if not CRYPTO_LOG.exists():
            return {"n": 0, "hit_rate": None}
        done = []
        for line in CRYPTO_LOG.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            if row.get("outcome") is not None and row.get("setup") == setup:
                done.append(row)
        if not done:
            return {"n": 0, "hit_rate": None}
        wins = sum(1 for r in done if r["outcome"] == "target")
        return {"n": len(done), "hit_rate": wins / len(done)}
    if market == "bse":
        # No paper book or call log for BSE yet (paper/book.py stays NSE-only, and there's
        # no BSE equivalent of crypto_signal_tracker.py) - honestly nothing to report,
        # rather than borrowing NSE's numbers for a different exchange's stocks.
        return {"n": 0, "hit_rate": None}
    from tradedesk.journal import Journal
    from tradedesk.journal.stats import track_record

    with Journal(NSE_JOURNAL) as jn:
        rec = track_record(jn, setup)
    return {"n": rec.trades, "hit_rate": rec.win_rate if rec.trades else None}


def analyze_symbol(
    settings: Settings, market: str, code: str, on: str | None = None
) -> dict[str, Any]:
    """`market` is "nse", "crypto" or "bse"; `code` is a scrip code (NSE_3045 / CDX_BTCINR /
    BSE_500325)."""
    from tradedesk.backtest.runner import build_snapshot, prepare_market, regime_on
    from tradedesk.broker.indstocks.models import Interval
    from tradedesk.data.candle_store import CandleStore
    from tradedesk.engine.engine import scan_day
    from tradedesk.engine.patterns import find_base, find_flag, find_pullback, volatility_squeeze
    from tradedesk.markets import bse_market, crypto_market, nse_market
    from tradedesk.scan import scan_config

    mkt = {"crypto": crypto_market, "bse": bse_market}.get(market, nse_market)(settings)
    ref: str | None
    with CandleStore(db_for(market)) as store:
        if market == "crypto":
            ref = f"{mkt.code_prefix}{mkt.benchmark_name}"
            vix = None
        elif market == "bse":
            ref = store.index_code(mkt.benchmark_name, exch="BSE")
            if ref is None:
                return {"error": "benchmark not in instruments table; run data sync-instruments --market bse"}  # noqa: E501
            vix = None
        else:
            ref = store.index_code(settings.universe.benchmark)
            if ref is None:
                return {"error": "benchmark not in instruments table; run data sync-instruments"}
            vix = store.index_code(settings.universe.volatility_index)
        last = store.last_ts(code, Interval.D1)
        if last is None:
            return {"error": f"no daily candles for {code}", "market": market}
        day: date = datetime.strptime(on, "%Y-%m-%d").date() if on else last.date()
        cfg = scan_config(settings, day, market=mkt)
        cfg.vix_code = vix
        md = prepare_market(store, [code], ref, cfg)
        symbol = md.symbols.get(code, code)
    if code not in md.features or day not in md.pos_by_date.get(code, {}):
        return {"error": f"no bar for {code} on {day}", "market": market, "symbol": symbol}
    feats = md.features[code].iloc[: md.pos_by_date[code][day] + 1]
    last_row = feats.iloc[-1]
    pcfg = settings.engine.patterns
    regime = regime_on(md, day, cfg)
    snap = build_snapshot(md, day, cfg, regime=regime)
    signals = scan_day(snap, list(cfg.setups), cfg.setup_params)
    call = "BUY WATCH" if signals else "HOLD (no setup armed)"
    hold_days = signals[0].exit_plan.max_hold_sessions if signals else settings.risk.max_hold_sessions  # noqa: E501
    track = [
        {"setup": s.setup.value, **setup_hit_rate(market, s.setup.value)} for s in signals
    ]
    return {
        "market": market,
        "code": code,
        "symbol": symbol,
        "on": day,
        "call": call,
        "hold_days": hold_days,
        "track_record": track,
        "close": float(last_row["close"]),
        "ema20": float(last_row["ema20"]),
        "ema50": float(last_row["ema50"]),
        "ema200": float(last_row["ema200"]),
        "adx14": float(last_row["adx14"]) if last_row["adx14"] == last_row["adx14"] else None,
        "atr_pct": float(last_row["atr_pct"]) if last_row["atr_pct"] == last_row["atr_pct"] else None,  # noqa: E501
        "rs_percentile": snap.rs_percentile.get(code),
        "regime": regime.regime.value if regime else None,
        "patterns": {
            "base": (b.model_dump() if (b := find_base(feats, pcfg)) else None),
            "flag": (f.model_dump() if (f := find_flag(feats, pcfg)) else None),
            "pullback": (p.model_dump() if (p := find_pullback(feats, pcfg)) else None),
            "squeeze": volatility_squeeze(feats, pcfg).model_dump(),
        },
        "signals": [s.model_dump(mode="json") for s in signals],
    }
