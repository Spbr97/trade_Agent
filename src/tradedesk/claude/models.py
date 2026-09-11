"""What Claude is allowed to say (PLAN.md 9). Every output schema here is TEXT ONLY: there
is deliberately no numeric field anywhere, so nothing Claude returns can become a price,
level, quantity or score. tests/claude enforces that structurally."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Verdict = Literal["keep", "downgrade", "remove"]
Confidence = Literal["low", "medium", "high"]


class ChartRead(BaseModel):
    """Three-line read of a setup's daily/hourly charts (evening)."""

    model_config = ConfigDict(extra="forbid")

    pattern_quality: str = Field(description="One sentence on how clean the structure is")
    overhead_supply: str = Field(description="One sentence on where sellers may sit")
    failure_condition: str = Field(description="One sentence on what would make this fail")
    verdict: Verdict = Field(description="keep, downgrade one grade, or remove from the list")
    confidence: Confidence


class TriggerNote(BaseModel):
    """One line on the 15-minute chart at trigger time, sent after the alert."""

    model_config = ConfigDict(extra="forbid")

    note: str


class WeeklyReview(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: str
    what_went_well: list[str]
    what_hurt: list[str]
    rule_breaks: list[str]
    one_change_next_week: str


TEXT_ONLY_MODELS = (ChartRead, TriggerNote, WeeklyReview)
