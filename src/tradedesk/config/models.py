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
    ipft_pct: Fraction = Decimal("0.000000001")
    sebi_fee_pct: Fraction = Decimal("0.000001")
    stamp_duty: StampDutyRates = StampDutyRates()
    gst_pct: Fraction = Decimal("0.18")
    gst_on_exchange_txn: bool = True
    dp_charge: DpCharge = DpCharge()
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


class SetupsConfig(Strict):
    setups: dict[str, SetupConfig] = Field(default_factory=dict)
    entry: EntryRules = EntryRules()


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
