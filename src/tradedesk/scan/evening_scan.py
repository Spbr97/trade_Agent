"""Evening scan (PLAN.md 2, 6): after the close, build tomorrow's watchlist.

Pipeline: market data as of `on` -> regime -> scan_day (the same function the backtester
runs) -> score/grade -> sizing -> filters (net R:R after costs, ATR band, turnover,
surveillance, regime) -> Watchlist, saved as JSON under data/watchlists/.

Parity rule (M6 'done when'): the signals on the watchlist for any past date are exactly
the backtester's signals for that date, because both call `build_snapshot` + `scan_day`.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from tradedesk.backtest.runner import (
    BacktestConfig,
    MarketData,
    build_snapshot,
    prepare_market,
    regime_on,
)
from tradedesk.broker.indstocks.models import IST, Interval
from tradedesk.config.models import Settings
from tradedesk.data.candle_store import CandleStore
from tradedesk.data.universe import UniverseRules
from tradedesk.engine.engine import scan_day
from tradedesk.engine.filters import apply_filters
from tradedesk.engine.regime import RegimeSnapshot
from tradedesk.engine.scoring import (
    Grade,
    Score,
    ScoreInputs,
    TrackRecord,
    pattern_quality,
    room_in_r,
    score_signal,
    trend_strength,
)
from tradedesk.engine.signals import SetupKind, Signal
from tradedesk.models import Side, TradeType
from tradedesk.risk.costs import leg_cost
from tradedesk.risk.sizing import SizeInputs, gap95_pct, position_size


@dataclass(frozen=True)
class OpenPositionInfo:
    """What the scan needs to know about a held position (from the journal in M9, or
    entered by hand until then)."""

    scrip_code: str
    entry: float
    stop: float
    qty: int
    sector: str | None = None


class WatchlistEntry(BaseModel):
    model_config = ConfigDict(frozen=True)

    signal: Signal
    score: int
    grade: Grade
    score_components: dict[str, float]
    score_notes: list[str]
    alertable: bool
    qty: int
    risk_amount: float
    risk_pct: float
    position_value: float
    size_caps: list[str]
    costs_round_trip: float
    net_rr_t1: float | None
    net_rr_t2: float | None
    results_in_sessions: int | None
    heat_before_pct: float
    heat_after_pct: float
    atr_pct: float | None
    avg_turnover: float | None
    rejected_for: list[str] = Field(default_factory=list)
    chart_path: str | None = None  # set by `tradedesk scan --charts`; attached to alerts

    @property
    def on_watchlist(self) -> bool:
        return not self.rejected_for


class Watchlist(BaseModel):
    model_config = ConfigDict(frozen=True)

    on: date
    generated_at: datetime
    regime: RegimeSnapshot | None
    capital: float
    entries: list[WatchlistEntry]  # sorted by score, highest first; includes rejected ones
    open_positions: list[dict[str, Any]]

    @property
    def active(self) -> list[WatchlistEntry]:
        return [e for e in self.entries if e.on_watchlist]

    def by_grade(self, grade: Grade) -> list[WatchlistEntry]:
        return [e for e in self.active if e.grade is grade]


def scan_config(
    settings: Settings, on: date, setups: Sequence[SetupKind] | None = None
) -> BacktestConfig:
    kinds = (
        list(setups)
        if setups
        else [SetupKind(k) for k, v in settings.setups.setups.items() if v.enabled]
    )
    if not kinds:
        kinds = list(SetupKind)
    return BacktestConfig(
        setups=kinds,
        start=on,
        end=on,
        capital=float(settings.risk.trading_capital),
        risk=settings.risk,
        engine=settings.engine,
        setup_params={k: v.model_dump() for k, v in settings.setups.setups.items()},
        universe_rules=UniverseRules(
            min_avg_turnover_inr=float(settings.universe.min_avg_daily_turnover_inr),
            min_price=float(settings.universe.min_price),
        ),
        slippage_pct=float(settings.risk.costs.slippage_pct),
    )


def _heat(positions: Sequence[OpenPositionInfo], capital: float) -> float:
    if capital <= 0:
        return 0.0
    return sum(max(0.0, p.entry - p.stop) * p.qty for p in positions) / capital


def _round_trip_cost(sig: Signal, qty: int, settings: Settings) -> float:
    if qty <= 0:
        return 0.0
    sched = settings.risk.costs
    buy = leg_cost(
        sched,
        side=Side.BUY,
        trade_type=TradeType.DELIVERY,
        qty=qty,
        price=Decimal(str(round(sig.trigger, 2))),
    )
    sell = leg_cost(
        sched,
        side=Side.SELL,
        trade_type=TradeType.DELIVERY,
        qty=qty,
        price=Decimal(str(round(sig.t1, 2))),
    )
    return float(buy.total + sell.total)


def build_watchlist(
    md: MarketData,
    cfg: BacktestConfig,
    settings: Settings,
    on: date,
    *,
    open_positions: Sequence[OpenPositionInfo] = (),
    track_records: Mapping[str, TrackRecord] | None = None,
    surveillance: Mapping[str, str] | None = None,
    sector_percentile: Mapping[str, float] | None = None,
) -> Watchlist:
    regime = regime_on(md, on, cfg)
    held = {p.scrip_code for p in open_positions}
    snapshot = build_snapshot(md, on, cfg, regime=regime, exclude=held)
    signals = scan_day(
        snapshot, cfg.setups, cfg.setup_params, max_hold_sessions=cfg.risk.max_hold_sessions
    )

    capital = cfg.capital
    heat_before = _heat(open_positions, capital)
    regime_name = regime.regime.value if regime else None
    mult = regime.size_multiplier if regime else 1.0
    entries: list[WatchlistEntry] = []
    for sig in signals:
        feats = snapshot.features[sig.scrip_code]
        last = feats.iloc[-1]
        track = (track_records or {}).get(sig.setup.value, TrackRecord())
        size = position_size(
            SizeInputs(
                equity=capital,
                entry=sig.trigger,
                stop=sig.stop,
                max_risk_pct=float(cfg.risk.max_risk_per_trade_pct),
                max_position_value_pct=float(cfg.risk.max_position_value_pct),
                size_multiplier=mult
                if mult > 0
                else 1.0,  # size shown even when risk_off blocks it
                gap_risk_cap_pct=float(cfg.risk.gap_risk_cap_pct),
                gap95_pct=gap95_pct(feats),
                available_heat_pct=float(cfg.risk.max_portfolio_heat_pct) - heat_before,
            )
        )
        atr_pct = float(last["atr_pct"]) if last.get("atr_pct") == last.get("atr_pct") else None
        turnover = (
            float((feats["close"] * feats["volume"]).iloc[-20:].mean())
            if len(feats) >= 20
            else None
        )
        flt = apply_filters(
            sig,
            qty=size.qty,
            atr_pct=atr_pct,
            avg_turnover=turnover,
            regime=regime_name,
            risk=cfg.risk,
            universe=settings.universe,
            surveillance=surveillance,
        )
        score: Score = score_signal(
            ScoreInputs(
                signal=sig,
                trend_strength=trend_strength(last),
                rs_percentile=sig.rs_percentile,
                sector_percentile=(sector_percentile or {}).get(sig.scrip_code),
                pattern_quality=pattern_quality(sig),
                room_r=room_in_r(sig),
                net_rr_t2=flt.net_rr_t2,
                regime=regime_name,
                track=track,
            )
        )
        rejected = list(flt.reasons)
        if not size.viable:
            rejected.append("size 0: " + ", ".join(size.caps))
        if regime_name == "neutral" and score.grade is not Grade.A:
            rejected.append("neutral regime: A-grade only")
        if score.benched:
            rejected.append("setup benched (negative rolling paper expectancy)")
        entries.append(
            WatchlistEntry(
                signal=sig,
                score=score.total,
                grade=score.grade,
                score_components=score.components,
                score_notes=score.notes,
                alertable=score.alertable and not rejected,
                qty=size.qty,
                risk_amount=size.risk_amount,
                risk_pct=size.risk_pct,
                position_value=size.position_value,
                size_caps=size.caps,
                costs_round_trip=_round_trip_cost(sig, size.qty, settings),
                net_rr_t1=flt.net_rr_t1,
                net_rr_t2=flt.net_rr_t2,
                results_in_sessions=snapshot.results_in_sessions.get(sig.scrip_code),
                heat_before_pct=heat_before,
                heat_after_pct=heat_before + size.risk_pct,
                atr_pct=atr_pct,
                avg_turnover=turnover,
                rejected_for=rejected,
            )
        )
    entries.sort(key=lambda e: (-e.score, e.signal.scrip_code))
    return Watchlist(
        on=on,
        generated_at=datetime.now(IST),
        regime=regime,
        capital=capital,
        entries=entries,
        open_positions=[p.__dict__ for p in open_positions],
    )


def run_evening_scan(
    store: CandleStore,
    settings: Settings,
    on: date,
    *,
    benchmark_code: str,
    codes: Sequence[str] | None = None,
    setups: Sequence[SetupKind] | None = None,
    vix_code: str | None = None,
    **kw: Any,
) -> Watchlist:
    cfg = scan_config(settings, on, setups)
    cfg.vix_code = vix_code
    universe = (
        list(codes)
        if codes
        else [c for c in store.codes(Interval.D1) if c not in (benchmark_code, vix_code)]
    )
    md = prepare_market(store, universe, benchmark_code, cfg)
    return build_watchlist(md, cfg, settings, on, **kw)


def save_watchlist(wl: Watchlist, folder: Path) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{wl.on.isoformat()}.json"
    path.write_text(json.dumps(wl.model_dump(mode="json"), indent=2), encoding="utf-8")
    return path


def load_watchlist(path: Path) -> Watchlist:
    return Watchlist.model_validate_json(path.read_text(encoding="utf-8"))
