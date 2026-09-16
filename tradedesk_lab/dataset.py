"""Immutable research inputs from cached signals and READ ONLY candles.

The original CSV is historical research, already inspected by earlier experiments.
Its last 20% must never be advertised as a fresh, previously unseen final test.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

from tradedesk.backtest.fills import Bar, Fill, FillReason, Position, evaluate_exit
from tradedesk.config import load_config
from tradedesk.engine.signals import ExitPlan, SetupKind, Signal
from tradedesk.markets.market import nse_market
from tradedesk.models import Side, TradeType, price_decimal
from tradedesk.prediction.labeling import triple_barrier
from tradedesk.risk.sizing import SizeInputs, gap95_pct, position_size
from tradedesk_lab.artifacts import ROOT, digest


@dataclass
class Dataset:
    frame: pd.DataFrame
    features: list[str]
    calendar: pd.DatetimeIndex
    bars: dict[str, pd.DataFrame]
    manifest: dict


def signal(row: pd.Series) -> Signal:
    return Signal(
        id=str(row.signal_id),
        scrip_code=str(row.scrip_code),
        symbol=str(row.scrip_code),
        setup=SetupKind(row.setup),
        armed_on=pd.Timestamp(row.armed_on).date(),
        trigger=float(row.entry),
        stop=float(row.stop),
        t1=float(row.t1),
        t2=float(row.t2),
        atr=float(row.atr),
        regime=str(row.regime),
        exit_plan=ExitPlan(partial_fraction=1.0),
    )


def bar(row: pd.Series, on: pd.Timestamp) -> Bar:
    return Bar(
        on=on.date(),
        open=float(row.open),
        high=float(row.high),
        low=float(row.low),
        close=float(row.close),
        volume=int(row.volume),
        ema10=float(row.ema10),
        atr=float(row.atr14),
    )


def prepare(root: Path = ROOT) -> Dataset:
    csv = root / "data/reports/ml_dataset_current.csv"
    geometry = root / "data/reports/barrier_signals.csv"
    old = pd.read_csv(csv)
    levels = pd.read_csv(geometry)
    features = [
        c
        for c in old.columns
        if c
        not in {
            "signal_id",
            "scrip_code",
            "setup",
            "armed_on",
            "entry_date",
            "label",
            "outcome",
            "realised_r",
            "plain_score",
            "sessions_to_results",
        }
    ]
    # Never reuse the legacy results countdown: that export predates the leakage fix.
    old = old.drop(columns=["label", "outcome", "realised_r", "sessions_to_results"])
    frame = old.merge(
        levels.drop(columns=["armed_on", "entry_date", "setup", "scrip_code"]),
        on="signal_id",
        validate="one_to_one",
    )
    frame["armed_on"] = pd.to_datetime(frame.armed_on)
    frame["entry_date"] = pd.to_datetime(frame.entry_date)
    frame["regime"] = np.where(
        frame.regime_risk_off > 0,
        "risk_off",
        np.where(frame.regime_risk_on > 0, "risk_on", "neutral"),
    )
    settings = load_config(root)
    market = nse_market(settings)
    from tradedesk.engine.indicators import atr, ema

    bars = {}
    output = []
    # Opening CandleStore would execute CREATE TABLE statements; use a read-only connection.
    with duckdb.connect(str(root / "data/tradedesk.duckdb"), read_only=True) as con:
        for i, (code, group) in enumerate(frame.groupby("scrip_code", sort=True)):
            data = con.execute(
                "SELECT ts,open,high,low,close,volume FROM candles "
                "WHERE scrip_code=? AND interval='1day' ORDER BY ts",
                [code],
            ).df()
            if data.empty:
                continue
            data.index = (
                pd.to_datetime(data.pop("ts"), unit="s", utc=True)
                .dt.tz_convert("Asia/Kolkata")
                .dt.tz_localize(None)
                .dt.normalize()
            )
            data = data[~data.index.duplicated(keep="last")].sort_index()
            data["ema10"] = ema(data.close, 10)
            data["atr14"] = atr(data, 14)
            bars[code] = data
            for _, row in group.iterrows():
                future = data.loc[row.entry_date :]
                if future.empty or row.entry <= row.stop:
                    continue
                lab = triple_barrier(
                    future,
                    entry=row.entry,
                    stop=row.stop,
                    target=row.t1,
                    max_hold=settings.risk.max_hold_sessions,
                )
                if lab.outcome == "insufficient":
                    continue
                history = data.loc[: row.armed_on]
                size = position_size(
                    SizeInputs(
                        equity=float(settings.risk.trading_capital),
                        entry=row.entry,
                        stop=row.stop,
                        max_risk_pct=float(settings.risk.max_risk_per_trade_pct),
                        max_position_value_pct=float(settings.risk.max_position_value_pct),
                        size_multiplier=0.5 if row.regime == "neutral" else 1.0,
                        gap_risk_cap_pct=float(settings.risk.gap_risk_cap_pct),
                        gap95_pct=gap95_pct(history),
                        available_heat_pct=0.025,
                    )
                )
                record = row.to_dict()
                record.update(
                    label=lab.label,
                    outcome=lab.outcome,
                    label_end_date=future.index[lab.sessions],
                    qty=size.qty,
                    gap95=gap95_pct(history),
                    net_r=np.nan,
                    trade_end_date=pd.NaT,
                )
                if size.viable:
                    pos = Position(
                        signal=signal(row),
                        entry_date=row.entry_date.date(),
                        entry_price=row.entry,
                        qty_initial=size.qty,
                        qty_open=size.qty,
                        stop=row.stop,
                        fills=[Fill(row.entry_date.date(), row.entry, size.qty, FillReason.ENTRY)],
                    )
                    for j, (on, candle) in enumerate(future.iloc[:11].iterrows()):
                        evaluate_exit(
                            pos, bar(candle, on), float(market.costs.slippage_pct), entry_day=j == 0
                        )
                        if pos.closed:
                            charges = sum(
                                float(
                                    market.costs.leg_cost(
                                        side=Side.SELL if fill.is_sell else Side.BUY,
                                        trade_type=TradeType.INTRADAY
                                        if on.date() == pos.entry_date
                                        else TradeType.DELIVERY,
                                        qty=fill.qty,
                                        price=price_decimal(fill.price),
                                    ).total
                                )
                                for fill in pos.fills
                            )
                            record["net_r"] = (pos.gross_pnl() - charges) / pos.initial_risk
                            record["trade_end_date"] = on
                            record["label_end_date"] = max(record["label_end_date"], on)
                            break
                output.append(record)
            if i % 200 == 0:
                print(f"Dataset: {i + 1} / {frame.scrip_code.nunique()} symbols", flush=True)
    result = pd.DataFrame(output).sort_values(["armed_on", "signal_id"]).reset_index(drop=True)
    dates = sorted({on for data in bars.values() for on in data.index})
    calendar = pd.DatetimeIndex(dates)
    return Dataset(
        result,
        features,
        calendar,
        bars,
        {
            "market": "nse",
            "source_csv_sha256": digest(csv),
            "geometry_sha256": digest(geometry),
            "feature_version": "legacy-export-with-results-countdown-removed-v1",
            "config_sha256": digest(root / "config/risk.yaml"),
            "historical_research": True,
            "rows": len(result),
            "symbols": len(bars),
            "from": str(result.armed_on.min().date()),
            "to": str(result.armed_on.max().date()),
            "notes": [
                "Previously researched historical window; forward evidence still required.",
                "Survivorship bias from the existing broker universe remains.",
                "Daily-bar entry/exit approximation inherited from the base backtest.",
                "Saved triggered-signal entry prices; exits repriced with current costs.",
                "Legacy results countdown excluded; base feature files were not changed.",
            ],
        },
    )
