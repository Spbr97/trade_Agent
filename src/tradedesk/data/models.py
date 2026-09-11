"""Domain models for the data layer: corporate actions, results events, quality issues."""

from __future__ import annotations

from datetime import date
from enum import StrEnum
from fractions import Fraction

from pydantic import BaseModel, ConfigDict, Field


class ActionKind(StrEnum):
    SPLIT = "split"
    BONUS = "bonus"
    OTHER = "other"  # dividends, rights, buybacks: recorded, never used to adjust prices


class CorporateAction(BaseModel):
    """A price-affecting event. `price_factor` multiplies every price BEFORE `ex_date`
    (and divides volume) so the series is continuous through the event.

    Split from face value 10 to 2  -> factor 2/10 = 0.2
    Bonus a:b (a new for every b)  -> factor b/(a+b); 1:1 -> 0.5
    """

    model_config = ConfigDict(frozen=True)

    symbol: str
    ex_date: date
    kind: ActionKind
    price_factor: Fraction = Fraction(1)
    purpose: str = ""
    source: str = "nse"

    @property
    def adjusts_prices(self) -> bool:
        return self.kind in (ActionKind.SPLIT, ActionKind.BONUS) and self.price_factor != 1


class ResultsEvent(BaseModel):
    """A board meeting / results announcement date for a symbol."""

    model_config = ConfigDict(frozen=True)

    symbol: str
    event_date: date
    purpose: str = ""
    source: str = "nse"


class IssueKind(StrEnum):
    MISSING_SESSIONS = "missing_sessions"
    BAD_OHLC = "bad_ohlc"
    ZERO_VOLUME = "zero_volume"
    BIG_JUMP = "big_jump"
    SUSPECTED_UNADJUSTED = "suspected_unadjusted_split"
    NO_DATA = "no_data"
    STALE = "stale"


class QualityIssue(BaseModel):
    model_config = ConfigDict(frozen=True)

    scrip_code: str
    kind: IssueKind
    on: date | None = None
    detail: str = ""
    count: int = Field(default=1, ge=0)
