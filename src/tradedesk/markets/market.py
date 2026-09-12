"""Market bundle (M13 plan, Phase 1): the one object a second market has to supply.

Exploration before this file was written (see the approved plan) found the engine core
already market-agnostic:
  - engine/engine.py::scan_day, setups/*, engine/indicators.py, scoring.py: operate on
    OHLCV feature frames, nothing NSE-specific.
  - live/confirmation.py::confirm_trigger: already parameterised by SessionRules.
  - data/universe.py::trading_days derives the calendar from a *reference code's* candles
    - crypto just needs a different reference code (or "every day with a bar"), not new
      code.
  - engine/regime.py::classify_regime already accepts `vix: pd.Series | None` and degrades
    correctly when it's None - crypto's lack of a VIX equivalent needs no new code path.

So the only piece that genuinely differs in SHAPE (not just parameters) between NSE and a
crypto market is costs - see costs.py's module docstring, now with a second model
(CryptoCostModel, Phase 4) to prove the abstraction was worth it. `Market` is therefore a
plain bundle of the config each market already has (SessionRules, UniverseRules) plus the
one real abstraction (CostModel). Adding CalendarProvider/RegimeProvider classes now,
before a second market exists to prove them different, would be speculative - they stay
functions taking a reference/benchmark code, which is exactly what they are today.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import time

from tradedesk.config.models import Settings
from tradedesk.data.universe import UniverseRules
from tradedesk.live.models import SessionRules
from tradedesk.markets.costs import CostModel, CryptoCostModel, EquityCostModel


@dataclass(frozen=True)
class Market:
    name: str
    code_prefix: str
    costs: CostModel
    session_rules: SessionRules
    universe_rules: UniverseRules
    benchmark_name: str  # the reference instrument the calendar and regime derive from


def nse_market(settings: Settings) -> Market:
    """Bundle NSE's existing settings, unchanged - see the module docstring on why this is
    a straight wrap, not new behaviour. `benchmark_name` is config/universe.yaml's
    `benchmark` ("NIFTY 50") - callers still resolve it to a scrip code via the instrument
    store themselves (cli.py::_reference_code already does this); this field only carries
    the market's intent, it does not look anything up."""
    return Market(
        name="nse",
        code_prefix="NSE_",
        costs=EquityCostModel(settings.risk.costs),
        session_rules=SessionRules(),
        universe_rules=UniverseRules(
            min_avg_turnover_inr=float(settings.universe.min_avg_daily_turnover_inr),
            min_price=float(settings.universe.min_price),
        ),
        benchmark_name=settings.universe.benchmark,
    )


def crypto_market(settings: Settings) -> Market:
    """M13 Phase 4. `session_rules` here is a placeholder 24/7 stand-in
    (00:00-23:59, no-entry/late-trigger windows disabled) - not yet meaningfully
    exercised, since only daily crypto candles are loaded so far (Phase 3) and
    confirm_trigger only binds when intraday bars exist. Revisit once crypto intraday
    data lands (Phase 5, live feed)."""
    cfg = settings.crypto_market
    return Market(
        name="crypto",
        code_prefix="CDX_",
        costs=CryptoCostModel(cfg.costs),
        session_rules=SessionRules(
            session_open=time(0, 0),
            session_close=time(23, 59),
            no_entry_before=time(0, 0),
            late_trigger_after=time(23, 59),
            close_check_at=time(23, 55),
        ),  # fmt: skip
        universe_rules=UniverseRules(
            min_avg_turnover_inr=float(cfg.universe.min_avg_daily_turnover_inr),
            min_price=float(cfg.universe.min_price),
        ),
        benchmark_name=cfg.benchmark,
    )
