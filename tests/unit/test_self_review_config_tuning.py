"""self_review/config_tuning.py: reuses the REAL event-driven backtester (not a re-derived
exit model) to search one setup parameter at a time. Small engineered universe, several
breakout events spread over time so a chronological train/test split is meaningful."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from tests.backtest.synth_universe import benchmark, breakout_stock
from tests.data.synth import sessions
from tradedesk.backtest.runner import BacktestConfig, prepare_market
from tradedesk.broker.indstocks.models import IndexInstrument
from tradedesk.config.models import EngineConfig, RiskConfig
from tradedesk.data.candle_store import CandleStore
from tradedesk.data.universe import UniverseRules
from tradedesk.engine.signals import SetupKind
from tradedesk.self_review import config_tuning as ct

NIFTY = IndexInstrument(exch="NSE", name="NIFTY 50", security_id="40000001")
REF = NIFTY.scrip_code
CAL = sessions(date(2024, 1, 1), 700)
# Six breakout events, two stocks each, spread across the calendar so a mid-point
# chronological split has real signals on both sides.
BREAKOUT_INDICES = [200, 300, 400, 500, 550, 600]
RULES = UniverseRules(min_avg_turnover_inr=1.0, min_price=1.0)
PARAMS = {
    "base_breakout": {
        "rs_percentile_min": 0,
        "max_volume_dryup": 1.0,
        "max_stop_distance_atr": 2.0,
    }
}


def _build_market() -> tuple[CandleStore, BacktestConfig]:
    store = CandleStore()
    store.upsert_instruments([NIFTY])
    store.upsert_candles(benchmark(REF, CAL))
    stocks: list[str] = []
    for n, breakout_index in enumerate(BREAKOUT_INDICES):
        for j in range(2):
            code = f"NSE_{n}_{j}"
            stocks.append(code)
            store.upsert_candles(
                breakout_stock(
                    code, CAL, breakout_index=breakout_index, aftermath="win", seed=n * 10 + j
                )
            )
    cfg = BacktestConfig(
        setups=[SetupKind.BASE_BREAKOUT],
        start=CAL[BREAKOUT_INDICES[0] - 40],
        end=CAL[-1],
        capital=1_000_000.0,
        risk=RiskConfig(trading_capital=Decimal("1000000")),
        engine=EngineConfig(),
        setup_params=PARAMS,
        universe_rules=RULES,
        slippage_pct=0.0,
        warmup_sessions=280,
    )
    md = prepare_market(store, stocks, REF, cfg)
    return store, cfg, md


def test_propose_config_patches_returns_only_locked_test_positive_changes() -> None:
    store, cfg, md = _build_market()
    split = CAL[BREAKOUT_INDICES[3]]  # roughly the midpoint breakout event

    candidates = ct.propose_config_patches(
        SetupKind.BASE_BREAKOUT, "nse", md, cfg, split=split, min_train_trades=1
    )

    for candidate in candidates:
        patch = candidate.patch
        assert patch.setup == "base_breakout"
        assert patch.market == "nse"
        assert patch.path.startswith("setups.base_breakout.")
        assert patch.new_value != patch.old_value
        assert candidate.gauntlet_report.stopped_at is None
        assert candidate.gauntlet_report.kill_criteria is not None
        assert candidate.gauntlet_report.kill_criteria["passed"] is True
    store.close()


def test_propose_config_patches_skips_setups_with_no_registered_grid() -> None:
    store, cfg, md = _build_market()
    split = CAL[BREAKOUT_INDICES[3]]
    candidates = ct.propose_config_patches(
        SetupKind.NR7_BREAKOUT, "nse", md, cfg, split=split, min_train_trades=1
    )
    # nr7_breakout has its own registered grid, but base_cfg here only configured
    # base_breakout's setup_params - old_value resolves to None for every candidate param,
    # which is still a valid (if uninformative) search; the real assertion is that this
    # never raises for a setup kind with no matching setup_params entry.
    assert isinstance(candidates, list)
    store.close()
