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

from tradedesk.config.models import AtrBand, Settings
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
    atr_pct_band: AtrBand | None = None  # None: engine/filters.py skips the ATR-band check
    qty_step: float = 1.0  # smallest tradeable increment; 1.0 = whole units (NSE shares)
    min_notional_inr: float = 0.0  # exchange minimum order value; 0 = no minimum
    vix_required: bool = False
    """True when a missing VIX reading must fail the regime closed (RISK_OFF) rather than
    degrade quietly. NSE's regime is defined in terms of VIX (PLAN.md 6.1: "risk_on ...
    VIX calm"), so a missing reading there is a data problem, not a fact about the market -
    the hard rule ("fail closed: stale data ... pause alerts") applies. Crypto has no VIX
    by design (not a data gap), so it stays False and `vix=None` there means exactly what
    it says: no such index exists. See engine/regime.py::classify_regime.

    Found as a real bug 2026-09-12: a case-mismatched instrument lookup silently left NSE's
    own vix_code unresolved, and classify_regime's `vix_calm = vix_last is None or ...`
    treated the missing reading as calm - so the VIX risk_off trigger could never fire for
    the project's entire history, and no error surfaced it. The lookup is now
    case-insensitive, but this flag exists so a FUTURE data gap (a failed load, a renamed
    index) fails safe instead of silently reverting to the same fail-open behaviour."""


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
        atr_pct_band=settings.universe.atr_pct_band,
        vix_required=True,
    )


def crypto_market(settings: Settings) -> Market:
    """M13 Phase 4. `session_rules` here is a placeholder 24/7 stand-in
    (00:00-23:59, no-entry/late-trigger windows disabled) - not yet meaningfully
    exercised, since only daily crypto candles are loaded so far (Phase 3) and
    confirm_trigger only binds when intraday bars exist. Revisit once crypto intraday
    data lands (Phase 5, live feed). `atr_pct_band` is left None: no crypto-calibrated
    band exists yet, so engine/filters.py skips that check for this market entirely
    rather than applying NSE's.

    `qty_step` is ONE value for the whole market, but CoinDCX publishes a step PER PAIR
    (markets_details, fetched live 2026-09-12: BTCINR 0.00001, ETHINR 0.0001, SOLINR
    0.001, ADAINR/TRXINR/XRPINR 0.1, DOGEINR/HBARINR/XLMINR 1) along with a per-pair
    min_quantity. A uniform fine step is deliberate for now: it is what makes BTC/ETH
    sizeable at all, and rounding 0.003228 BTC to the real 0.00001 step moves the
    quantity by under 0.3%, far below anything that changes a backtest verdict. It is
    NOT good enough to place a real order with - per-pair step and min_quantity must be
    stored and enforced before M12 touches crypto. `min_notional_inr` needs no such
    caveat: markets_details gives a flat Rs 100 across every INR pair."""
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
            exclude_codes=frozenset(cfg.universe.exclude),
        ),
        benchmark_name=cfg.benchmark,
        qty_step=float(cfg.sizing.qty_step),
        min_notional_inr=float(cfg.sizing.min_notional_inr),
    )
