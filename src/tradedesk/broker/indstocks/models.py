"""Typed views of INDstocks API payloads (docs/indstocks-api.md).

Market-data prices are floats: they come from JSON numbers and flow into pandas/DuckDB.
Accounting (costs, P&L, sizing) stays in Decimal - see tradedesk.risk.
"""

from datetime import datetime
from enum import StrEnum
from typing import Any
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field

IST = ZoneInfo("Asia/Kolkata")


class Interval(StrEnum):
    """Exact path values for /market/historical/{interval}; the doc's table is the full set."""

    M1 = "1minute"
    M2 = "2minute"
    M3 = "3minute"
    M4 = "4minute"
    M5 = "5minute"
    M10 = "10minute"
    M15 = "15minute"
    M30 = "30minute"
    H1 = "60minute"
    H2 = "120minute"
    H3 = "180minute"
    H4 = "240minute"
    D1 = "1day"
    W1 = "1week"
    MO1 = "1month"

    @property
    def max_window_days(self) -> int:
        """Max request window per call (doc: 7 days ≤30min, 15 days for hours, 1 year for ≥1day)."""
        if self in _MINUTE_INTERVALS:
            return 7
        if self in _HOUR_INTERVALS:
            return 15
        return 365

    @property
    def seconds(self) -> int:
        """Nominal candle length; daily/weekly/monthly are calendar-based, use with care."""
        return _SECONDS[self]


_MINUTE_INTERVALS = {
    Interval.M1, Interval.M2, Interval.M3, Interval.M4, Interval.M5,
    Interval.M10, Interval.M15, Interval.M30,
}  # fmt: skip
_HOUR_INTERVALS = {Interval.H1, Interval.H2, Interval.H3, Interval.H4}
_SECONDS = {
    Interval.M1: 60, Interval.M2: 120, Interval.M3: 180, Interval.M4: 240, Interval.M5: 300,
    Interval.M10: 600, Interval.M15: 900, Interval.M30: 1800,
    Interval.H1: 3600, Interval.H2: 7200, Interval.H3: 10800, Interval.H4: 14400,
    Interval.D1: 86400, Interval.W1: 7 * 86400, Interval.MO1: 30 * 86400,
}  # fmt: skip


class Candle(BaseModel):
    """One OHLCV bar. `ts` is the candle OPEN time (doc: candle covers [ts, ts + interval))."""

    model_config = ConfigDict(frozen=True)

    scrip_code: str
    interval: Interval
    ts: datetime  # tz-aware, Asia/Kolkata
    open: float
    high: float
    low: float
    close: float
    volume: int

    @classmethod
    def from_api(cls, scrip_code: str, interval: Interval, raw: dict[str, Any]) -> "Candle":
        return cls(
            scrip_code=scrip_code,
            interval=interval,
            ts=datetime.fromtimestamp(int(raw["ts"]), tz=IST),  # doc: ts is epoch SECONDS
            open=float(raw["o"]),
            high=float(raw["h"]),
            low=float(raw["l"]),
            close=float(raw["c"]),
            volume=int(raw["v"]),
        )

    @property
    def close_time(self) -> datetime:
        from datetime import timedelta

        return self.ts + timedelta(seconds=self.interval.seconds)


class Instrument(BaseModel):
    """A row of /market/instruments?source=equity|fno (16-column CSV)."""

    model_config = ConfigDict(frozen=True)

    exch: str
    segment: str
    security_id: str
    instrument_name: str
    trading_symbol: str
    symbol_name: str = ""
    series: str = ""
    tick_size: float | None = None
    lot_units: int | None = None
    custom_symbol: str = ""
    expiry_date: str = ""
    strike_price: float | None = None
    option_type: str = ""

    @property
    def scrip_code(self) -> str:
        """REST identifier: SEGMENT_TOKEN, e.g. NSE_3045."""
        return f"{self.exch}_{self.security_id}"

    @property
    def ws_code(self) -> str:
        """WebSocket identifier: SEGMENT:TOKEN, e.g. NSE:3045."""
        return f"{self.exch}:{self.security_id}"

    @property
    def is_cash_equity(self) -> bool:
        return self.instrument_name.upper() == "EQUITY" and self.series.upper() == "EQ"


class IndexInstrument(BaseModel):
    """A row of /market/instruments?source=index (3 columns, read positionally)."""

    model_config = ConfigDict(frozen=True)

    exch: str
    name: str
    security_id: str

    @property
    def scrip_code(self) -> str:
        return f"{self.exch}_{self.security_id}"


class LtpQuote(BaseModel):
    model_config = ConfigDict(frozen=True)

    scrip_code: str
    live_price: float


class FullQuote(BaseModel):
    """One entry of /market/quotes/full. Unknown extra keys are kept in `extra`."""

    model_config = ConfigDict(frozen=True)

    scrip_code: str
    live_price: float
    day_open: float | None = None
    day_high: float | None = None
    day_low: float | None = None
    prev_close: float | None = None
    day_change: float | None = None
    day_change_percentage: float | None = None
    week52_high: float | None = Field(default=None)
    week52_low: float | None = Field(default=None)
    upper_circuit: float | None = None
    lower_circuit: float | None = None
    volume: int | None = None
    market_depth: dict[str, Any] | None = None

    @classmethod
    def from_api(cls, scrip_code: str, raw: dict[str, Any]) -> "FullQuote":
        return cls(
            scrip_code=scrip_code,
            live_price=float(raw["live_price"]),
            day_open=_opt_float(raw.get("day_open")),
            day_high=_opt_float(raw.get("day_high")),
            day_low=_opt_float(raw.get("day_low")),
            prev_close=_opt_float(raw.get("prev_close")),
            day_change=_opt_float(raw.get("day_change")),
            day_change_percentage=_opt_float(raw.get("day_change_percentage")),
            week52_high=_opt_float(raw.get("52week_high")),
            week52_low=_opt_float(raw.get("52week_low")),
            upper_circuit=_opt_float(raw.get("upper_circuit")),
            lower_circuit=_opt_float(raw.get("lower_circuit")),
            volume=int(raw["volume"]) if raw.get("volume") is not None else None,
            market_depth=raw.get("market_depth"),
        )


class Tick(BaseModel):
    """One price-feed WebSocket message. `data` keys depend on mode (ltp: {"ltp": ...})."""

    model_config = ConfigDict(frozen=True)

    mode: str
    instrument: str
    timestamp: datetime  # tz-aware IST; doc: epoch milliseconds
    data: dict[str, Any]

    @classmethod
    def from_api(cls, raw: dict[str, Any]) -> "Tick":
        return cls(
            mode=str(raw["mode"]),
            instrument=str(raw["instrument"]),
            timestamp=datetime.fromtimestamp(int(raw["timestamp"]) / 1000, tz=IST),
            data=dict(raw.get("data") or {}),
        )

    @property
    def ltp(self) -> float | None:
        v = self.data.get("ltp")
        return float(v) if v is not None else None


class Profile(BaseModel):
    model_config = ConfigDict(frozen=True, extra="allow")

    user_id: str
    email: str = ""
    first_name: str = ""
    last_name: str = ""
    ucc: str = ""
    is_nse_onboarded: bool | None = None
    is_ddpi_active: bool | None = None


class Funds(BaseModel):
    """Subset of /funds; `eq_charges` and `brokerage` are the day's accrued charges."""

    model_config = ConfigDict(frozen=True, extra="allow")

    sod_balance: float = 0.0
    withdrawal_balance: float = 0.0
    realized_pnl: float = 0.0
    unrealized_pnl: float = 0.0
    brokerage: float = 0.0
    eq_charges: float = 0.0
    fno_charges: float = 0.0


def _opt_float(v: Any) -> float | None:
    return float(v) if v is not None else None
