"""Pydantic models for the YAML files under config/.

Every model is strict (`extra="forbid"`) so a typo in a rule name fails loudly instead of
silently loosening risk. Money and rates are `Decimal`; percentages are fractions
(0.001 == 0.1%). Defaults are the starting values from PLAN.md 1.2.
"""

from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

Fraction = Annotated[Decimal, Field(ge=0, le=1)]
Money = Annotated[Decimal, Field(ge=0)]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


# --------------------------------------------------------------------------- risk.yaml


class SttRates(Strict):
    delivery_buy: Fraction = Decimal("0.001")
    delivery_sell: Fraction = Decimal("0.001")
    intraday_sell: Fraction = Decimal("0.00025")


class StampDutyRates(Strict):
    delivery_buy: Fraction = Decimal("0.00015")
    intraday_buy: Fraction = Decimal("0.00003")


class DpCharge(Strict):
    amount: Money = Decimal("18.5")
    gst_applies: bool = True


class Brokerage(Strict):
    """IND Pricing: 0.1% or Rs 5 per executed order, whichever is lower; minimum Rs 2."""

    pct: Fraction = Decimal("0.001")
    max_per_order: Money = Decimal("5")
    min_per_order: Money = Decimal("2")


class ChargeSchedule(Strict):
    """Broker + statutory charges (PLAN.md 1.6). Verify against contract notes."""

    brokerage: Brokerage = Brokerage()
    stt: SttRates = SttRates()
    exchange_txn_pct: Fraction = Decimal("0.0000307")
    ipft_pct: Fraction = Decimal("0.000001")
    sebi_fee_pct: Fraction = Decimal("0.000001")
    stamp_duty: StampDutyRates = StampDutyRates()
    gst_pct: Fraction = Decimal("0.18")
    gst_on_exchange_txn: bool = True
    dp_charge: DpCharge = DpCharge()
    slippage_pct: Fraction = Decimal("0.0005")
    rounding: Literal["paise", "none"] = "paise"
    statutory_rounding: Literal["rupee", "paise"] = "rupee"  # STT and stamp duty per trade


class CryptoChargeSchedule(Strict):
    """CoinDCX + Indian tax charges (M13 Phase 4). Verified 2026-09-12 - see
    markets/costs.py::CryptoCostModel's docstring for sources; the maker/taker rate is
    corroborated via search, not a first-party fetch (CoinDCX's own fee page is a
    client-rendered SPA that could not be scraped directly) - re-verify against
    coindcx.com/fees before trusting this for real capital.

    No STT/stamp duty/DP/SEBI equivalent (those are NSE cash-equity statutory charges).
    No brokerage min/max caps - CoinDCX's fee is a flat percentage. No TradeType split:
    unlike NSE, TDS applies to every sell regardless of hold duration.
    """

    maker_taker_pct: Fraction = Decimal("0.002")  # 0.2%, base/VIP-0 tier, both sides
    tds_pct: Fraction = Decimal("0.01")  # Section 194S, 1% of the SELL leg's turnover
    gst_pct: Fraction = Decimal("0.18")  # on the trading fee only, not on TDS
    slippage_pct: Fraction = Decimal("0.0005")
    rounding: Literal["paise", "none"] = "paise"


class ConsecutiveLossPause(Strict):
    losses: int = Field(4, ge=1)
    sessions: int = Field(2, ge=1)


class TimeStop(Strict):
    sessions: int = Field(5, ge=1)
    min_r: Decimal = Decimal("1.0")


class TimeWindow(Strict):
    start: str = "09:15"
    end: str = "09:30"


class RegimeSizeMultiplier(Strict):
    risk_on: Fraction = Decimal("1.0")
    neutral: Fraction = Decimal("0.5")
    risk_off: Fraction = Decimal("0.0")


class RiskConfig(Strict):
    trading_capital: Annotated[Decimal, Field(gt=0)]
    max_risk_per_trade_pct: Annotated[Decimal, Field(gt=0, le=Decimal("0.01"))] = Decimal("0.0025")
    max_open_positions: int = Field(5, ge=1)
    max_per_sector: int = Field(2, ge=1, le=2)
    max_portfolio_heat_pct: Annotated[Decimal, Field(gt=0, le=Decimal("0.04"))] = Decimal("0.025")
    max_position_value_pct: Annotated[Decimal, Field(gt=0, le=Decimal("0.25"))] = Decimal("0.25")
    gap_risk_cap_pct: Annotated[Decimal, Field(gt=0, le=Decimal("0.01"))] = Decimal("0.01")
    max_new_entries_per_day: int = Field(3, ge=1, le=3)
    weekly_loss_limit_pct: Annotated[Decimal, Field(gt=0, le=Decimal("0.03"))] = Decimal("0.03")
    consecutive_loss_pause: ConsecutiveLossPause = ConsecutiveLossPause()
    reentry_cooldown_sessions: int = Field(3, ge=3)
    min_net_rr: Annotated[Decimal, Field(ge=2)] = Decimal("2.0")
    time_stop: TimeStop = TimeStop()
    max_hold_sessions: int = Field(10, ge=1)
    no_entry_window: TimeWindow = TimeWindow()
    regime_size_multiplier: RegimeSizeMultiplier = RegimeSizeMultiplier()
    costs: ChargeSchedule = ChargeSchedule()


# ----------------------------------------------------------------------- universe.yaml


class AtrBand(Strict):
    min: Fraction = Decimal("0.015")
    max: Fraction = Decimal("0.05")


class UniverseConfig(Strict):
    base_index: str = "NIFTY 500"
    min_avg_daily_turnover_inr: Money = Decimal("50000000")
    min_price: Money = Decimal("50")
    atr_pct_band: AtrBand = AtrBand()
    exclude_surveillance: bool = True
    benchmark: str = "NIFTY 50"
    sector_indices: list[str] = Field(default_factory=list)
    volatility_index: str = "INDIA VIX"


# --------------------------------------------------------------- markets/crypto.yaml


class CryptoUniverseConfig(Strict):
    """NSE's Rs 50,000,000 (5 crore) turnover floor does not transfer - see
    docs/signoff-crypto-phase3.md: even CDX_BTCINR (~Rs 4.6 crore/20-session-avg) and
    CDX_ETHINR (~Rs 2.6 crore) don't clear it, because most global crypto volume flows
    through USDT pairs elsewhere, not CoinDCX's INR pairs specifically.

    Default here (Rs 25,00,000 / 25 lakh) is DATA-INFORMED, not the NSE number rescaled
    by guesswork: from the real 20-session turnover distribution across all 338 active
    INR pairs (checked 2026-09-12), rank #10 sits at ~Rs 48 lakh and rank #50 at ~Rs 7.5
    lakh - 25 lakh lands inside that range, keeping roughly the top 15-20 pairs by
    turnover rather than the single pair (USDT) the NSE-inherited floor left. This is a
    capital-allocation choice, not a fact verified the way the cost numbers are -
    reconsider before relying on it for size."""

    min_avg_daily_turnover_inr: Money = Decimal("2500000")
    min_price: Money = Decimal("0")  # NSE's Rs 50 floor has no crypto equivalent
    exclude: list[str] = Field(default_factory=lambda: ["CDX_USDTINR", "CDX_USDCINR"])
    """INR stablecoins - see UniverseRules.exclude_codes for the measurements."""


class CryptoSizingConfig(Strict):
    """What makes crypto fractional. NSE trades whole shares (step 1); crypto does not,
    and flooring to whole units priced BTC/ETH/BNB out of every backtest entirely.

    Both numbers come from CoinDCX's markets_details, fetched live 2026-09-12.
    `min_notional_inr` is exact: a flat Rs 100 on every active INR pair. `qty_step` is a
    deliberate simplification - the real step is per-pair (BTCINR 0.00001, DOGEINR 1) -
    see markets/market.py::crypto_market for why one fine step is adequate for research
    and why it is NOT adequate for placing an order."""

    qty_step: Decimal = Decimal("0.00000001")
    min_notional_inr: Money = Decimal("100")


class CryptoMarketConfig(Strict):
    costs: CryptoChargeSchedule = CryptoChargeSchedule()
    universe: CryptoUniverseConfig = CryptoUniverseConfig()
    sizing: CryptoSizingConfig = CryptoSizingConfig()
    benchmark: str = "BTCINR"  # CDX_BTCINR: trades every day, stands in for an index


# --------------------------------------------------------------- markets/bse.yaml


class BseUniverseConfig(Strict):
    """Unlike crypto, BSE's statutory costs and price scale are IDENTICAL to NSE's (same
    STT/stamp/GST/SEBI rules, same rupee-priced equities) - see markets/market.py::bse_market.
    The only real unknown is liquidity: most of BSE's ~5,000 listings trade a fraction of
    their NSE-listed counterpart's volume (many are NSE-dual-listed and the NSE leg absorbs
    most flow), so NSE's turnover floor is inherited as a starting point, not re-derived -
    unlike crypto.yaml's floor, which WAS measured against real data before being set.
    Revisit once a real `data quality`/universe run on BSE's actual distribution exists."""

    min_avg_daily_turnover_inr: Money = Decimal("50000000")
    min_price: Money = Decimal("50")


class BseMarketConfig(Strict):
    universe: BseUniverseConfig = BseUniverseConfig()
    benchmark: str = "SENSEX"
    volatility_index: str | None = None
    """None on purpose (not yet True): BSE's own duckdb store starts with no VIX candles
    loaded into it, even though INDIA VIX itself is an NSE-domiciled index available in the
    same instrument master (verified 2026-09-12: `BSE_40000006` SENSEX candles fetch fine
    through the same broker/credentials as NSE). Wiring VIX into BSE mirrors the exact bug
    class CLAUDE.md's health check found for NSE (fail-open on a missing reading) if done
    half-way - set this once `data load --market bse` actually fetches VIX bars too."""


# ------------------------------------------------------------------------- setups.yaml


class SetupConfig(BaseModel):
    """Per-setup parameters. Extra keys allowed: each setup owns its own thresholds,
    validated by the setup module itself from M5 onward."""

    model_config = ConfigDict(extra="allow", frozen=True)
    enabled: bool = False


class EntryRules(Strict):
    confirm_timeframe_minutes: int = Field(15, ge=1)
    chased_atr_multiple: Decimal = Decimal("1.0")
    late_trigger_after: str = "15:00"
    setup_valid_sessions: int = Field(3, ge=1)


class EligibilityRules(Strict):
    """Evidence a setup must show before it may alert (SDD sections 18 and 23). Defaults
    match engine/scoring.py::EligibilityPolicy. Lowering any value here is a deliberate,
    reviewable edit - the standard is never dropped silently to produce signals."""

    min_score: int = 85
    min_trades: int = 500
    min_oos_trades: int = 100
    min_win_rate: float = 0.80
    min_expectancy_r: float = 0.0
    must_beat_random_by_r: float = 0.10


class SetupsConfig(Strict):
    setups: dict[str, SetupConfig] = Field(default_factory=dict)
    entry: EntryRules = EntryRules()
    eligibility: EligibilityRules = EligibilityRules()


# ----------------------------------------------------------------------- schedule.yaml


class ScheduleConfig(Strict):
    timezone: str = "Asia/Kolkata"
    startup: str = "08:30"
    morning_brief: str = "08:45"
    gap_check: TimeWindow = TimeWindow(start="09:15", end="09:30")
    monitor: TimeWindow = TimeWindow(start="09:30", end="15:00")
    close_check: TimeWindow = TimeWindow(start="15:00", end="15:20")
    evening_scan: str = "15:45"
    watchlist_send: str = "16:30"
    weekly_review_day: str = "saturday"


# ------------------------------------------------------------------------- alerts.yaml


class DesktopAlerts(Strict):
    enabled: bool = False
    sound: bool = True


class TelegramAlerts(Strict):
    enabled: bool = False
    allowed_chat_id: int | None = None


class DashboardAlerts(Strict):
    enabled: bool = False
    host: str = "127.0.0.1"
    port: int = 8765


class GradeRoute(Strict):
    min_score: int = Field(ge=0, le=100)
    channels: list[Literal["desktop", "telegram", "dashboard"]] = Field(default_factory=list)


class AlertsConfig(Strict):
    desktop: DesktopAlerts = DesktopAlerts()
    telegram: TelegramAlerts = TelegramAlerts()
    dashboard: DashboardAlerts = DashboardAlerts()
    grades: dict[str, GradeRoute] = Field(
        default_factory=lambda: {
            "A": GradeRoute(min_score=80, channels=["desktop", "telegram", "dashboard"]),
            "B": GradeRoute(min_score=65, channels=["dashboard"]),
            "C": GradeRoute(min_score=0, channels=[]),
        }
    )


# ------------------------------------------------------------------------- claude.yaml


class ClaudeConfig(Strict):
    mode: Literal["off", "notify", "veto"] = "off"
    chart_read_model: str = "claude-sonnet-5"
    weekly_review_model: str = "claude-sonnet-5"
    trigger_note_model: str = "claude-haiku-4-5-20251001"
    max_setups_per_evening: int = Field(10, ge=1)
    monthly_spend_limit_usd: Decimal = Decimal("20")


# ----------------------------------------------------------------------------- ml.yaml


class MlConfig(Strict):
    enabled: bool = False
    shadow: bool = True
    grade_a_min_probability: Fraction = Decimal("0.50")
    grade_c_below_probability: Fraction = Decimal("0.40")
    half_size_below_probability: Fraction = Decimal("0.45")
    retrain: Literal["monthly", "weekly", "never"] = "monthly"
    embargo_sessions: int = Field(10, ge=1)


# ------------------------------------------------------------------------- engine.yaml


class IntRange(Strict):
    min: int = Field(ge=1)
    max: int = Field(ge=1)


class RegimeConfig(Strict):
    nifty_ema: int = 50
    ema_slope_lookback: int = 5
    breadth_ema: int = 50
    breadth_risk_on_pct: Decimal = Decimal("50")
    breadth_risk_off_pct: Decimal = Decimal("40")
    vix_calm_max: Decimal = Decimal("18")
    vix_spike_level: Decimal = Decimal("25")
    vix_spike_change_5d_pct: Decimal = Decimal("25")


class RelativeStrengthConfig(Strict):
    lookbacks_sessions: list[int] = Field(default_factory=lambda: [21, 63, 126])
    weights: list[Decimal] = Field(
        default_factory=lambda: [Decimal("0.4"), Decimal("0.3"), Decimal("0.3")]
    )
    top_quartile_pct: Decimal = Decimal("75")


class PatternConfig(Strict):
    pivot_bars: int = 3
    base_len: IntRange = IntRange(min=5, max=25)
    base_max_depth_pct: Decimal = Decimal("0.15")
    base_close_within_pct: Decimal = Decimal("0.03")
    flag_pole_min_gain_pct: Decimal = Decimal("0.10")
    flag_pole_max_len: int = 10
    flag_len: IntRange = IntRange(min=3, max=15)
    flag_max_retrace: Decimal = Decimal("0.5")
    pullback_ema: int = 20
    pullback_len: IntRange = IntRange(min=2, max=5)
    pullback_touch_pct: Decimal = Decimal("0.01")
    near_high_pct: Decimal = Decimal("0.05")
    double_bottom_tolerance_pct: Decimal = Decimal("0.02")
    overhead_lookback: int = 250


class EngineConfig(Strict):
    regime: RegimeConfig = RegimeConfig()
    relative_strength: RelativeStrengthConfig = RelativeStrengthConfig()
    patterns: PatternConfig = PatternConfig()


# ------------------------------------------------------------------------------ bundle


class Settings(Strict):
    risk: RiskConfig
    universe: UniverseConfig = UniverseConfig()
    setups: SetupsConfig = SetupsConfig()
    schedule: ScheduleConfig = ScheduleConfig()
    alerts: AlertsConfig = AlertsConfig()
    claude: ClaudeConfig = ClaudeConfig()
    ml: MlConfig = MlConfig()
    engine: EngineConfig = EngineConfig()
    crypto_market: CryptoMarketConfig = CryptoMarketConfig()
    bse_market: BseMarketConfig = BseMarketConfig()
