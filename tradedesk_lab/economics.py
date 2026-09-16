"""Replay frozen candidate selections using the original position/risk/cost engine."""

from __future__ import annotations

import numpy as np
import pandas as pd

from tradedesk.backtest.fills import Fill, FillReason, Position, evaluate_exit
from tradedesk.backtest.portfolio import Portfolio
from tradedesk.config import load_config
from tradedesk.markets.market import nse_market
from tradedesk.risk.sizing import SizeInputs, position_size
from tradedesk_lab.artifacts import ROOT
from tradedesk_lab.dataset import Dataset, bar, signal


def replay(dataset: Dataset, df: pd.DataFrame, selected: np.ndarray) -> tuple[dict, pd.Series]:
    """A new portfolio per cohort; exclusions free capacity for later candidates.

    This replays previously generated candidates. It cannot recreate candidates the
    original generator omitted while holding a symbol. This limitation is disclosed.
    """
    settings = load_config(ROOT)
    market = nse_market(settings)
    portfolio = Portfolio(settings.risk, market.costs, float(settings.risk.trading_capital))
    # Use the same partial sector mapping as production; never infer missing sectors.
    import duckdb
    import yaml

    sectors = yaml.safe_load((ROOT / "config/sector_membership.yaml").read_text(encoding="utf-8"))
    with duckdb.connect(str(ROOT / "data/tradedesk.duckdb"), read_only=True) as con:
        instruments = con.execute("SELECT scrip_code,trading_symbol FROM instruments").fetchall()
    mapping = {symbol: sector for sector, symbols in sectors.items() for symbol in symbols}
    portfolio.sector_of = {
        code: mapping[symbol] for code, symbol in instruments if symbol in mapping
    }
    sessions = dataset.calendar[
        (dataset.calendar >= df.entry_date.min()) & (dataset.calendar <= df.label_end_date.max())
    ]
    groups = {on: group for on, group in df.loc[selected].groupby("entry_date")}
    rejected = {}
    for si, on in enumerate(sessions):
        portfolio.start_session(on.date(), si)
        for code, pos in list(portfolio.open.items()):
            data = dataset.bars[code]
            if on in data.index:
                fills = evaluate_exit(pos, bar(data.loc[on], on), float(market.costs.slippage_pct))
                portfolio.record_fills(pos, fills)
        for _, row in groups.get(on, df.iloc[:0]).sort_values(["setup", "scrip_code"]).iterrows():
            sig = signal(row)
            mult = {"risk_on": 1.0, "neutral": 0.5, "risk_off": 0.0}.get(row.regime, 0.0)
            ok, reason = portfolio.can_enter(sig, mult)
            if not ok:
                rejected[reason] = rejected.get(reason, 0) + 1
                continue
            size = position_size(
                SizeInputs(
                    equity=portfolio.equity,
                    entry=row.entry,
                    stop=row.stop,
                    max_risk_pct=float(settings.risk.max_risk_per_trade_pct),
                    max_position_value_pct=float(settings.risk.max_position_value_pct),
                    size_multiplier=mult,
                    gap_risk_cap_pct=float(settings.risk.gap_risk_cap_pct),
                    gap95_pct=row.gap95,
                    available_heat_pct=portfolio.available_heat_pct(),
                )
            )
            if not size.viable:
                rejected["size zero"] = rejected.get("size zero", 0) + 1
                continue
            pos = Position(
                sig,
                on.date(),
                row.entry,
                size.qty,
                size.qty,
                row.stop,
                fills=[Fill(on.date(), row.entry, size.qty, FillReason.ENTRY)],
            )
            portfolio.open_position(pos)
            fills = evaluate_exit(
                pos,
                bar(dataset.bars[row.scrip_code].loc[on], on),
                float(market.costs.slippage_pct),
                entry_day=True,
            )
            portfolio.record_fills(pos, fills)
        closes = {
            code: float(dataset.bars[code].loc[on, "close"])
            for code in portfolio.open
            if on in dataset.bars[code].index
        }
        portfolio.mark_to_market(closes)
    eq = pd.Series([x[1] for x in portfolio.equity_curve], index=sessions, dtype=float)
    initial = float(settings.risk.trading_capital)
    daily = eq.pct_change()
    if len(daily):
        daily.iloc[0] = eq.iloc[0] / initial - 1
    sd = float(daily.std(ddof=1)) if len(daily) > 1 else 0.0
    sharpe = float(daily.mean() / sd * np.sqrt(252)) if sd > 0 else None
    peak = eq.cummax().clip(lower=initial)
    trades = portfolio.closed
    return {
        "trades": len(trades),
        "net_pnl": sum(t.net_pnl for t in trades),
        "return_pct": float(eq.iloc[-1] / initial - 1) if len(eq) else 0.0,
        "max_drawdown": float((1 - eq / peak).max()) if len(eq) else 0.0,
        "sharpe": sharpe,
        "costs": sum(t.costs for t in trades),
        "net_win_rate": sum(t.net_pnl > 0 for t in trades) / len(trades) if trades else None,
        "open_at_end": len(portfolio.open),
        "rejections": rejected,
        "equity": [{"date": str(d.date()), "value": round(float(v), 2)} for d, v in eq.items()],
        "scope": "Risk-constrained replay of frozen historical candidates, current exit rules",
        "notes": [
            "Existing candidates only; no regeneration after different holdings.",
            "Sector limits use production's partial sector mapping.",
            "Corporate-event availability and daily-bar ambiguity inherit source limitations.",
        ],
    }, daily.fillna(0)
