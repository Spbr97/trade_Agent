"""Claude API wrapper (PLAN.md 9). Advisory only: every method returns a text-only model
or None. Modes come from config/claude.yaml (`off` never calls the API). Failures are
logged and swallowed - a missing note must never break a scan or delay an alert.

Credentials resolve the SDK's normal way (ANTHROPIC_API_KEY, or an `ant auth login`
profile); nothing is stored here. Usage is appended to data/claude_usage.jsonl and the
monthly spend cap from config stops further calls once reached.
"""

from __future__ import annotations

import base64
import json
import logging
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, TypeVar, cast

from pydantic import BaseModel

from tradedesk.broker.indstocks.models import IST
from tradedesk.claude.models import ChartRead, TriggerNote, WeeklyReview
from tradedesk.config.models import ClaudeConfig

if TYPE_CHECKING:
    from anthropic.types import MessageParam

log = logging.getLogger(__name__)
T = TypeVar("T", bound=BaseModel)

# USD per million tokens (input, output) - keep in step with the models you configure.
PRICES: dict[str, tuple[float, float]] = {
    "claude-opus-5": (5.0, 25.0),
    "claude-sonnet-5": (2.0, 10.0),
    "claude-haiku-4-5": (1.0, 5.0),
    "claude-haiku-4-5-20251001": (1.0, 5.0),
}

SYSTEM = (
    "You are the evening chart analyst for a rules-based NSE swing-trading system. The "
    "rules have already chosen the setup, trigger, stop and targets; you never propose "
    "prices, levels, quantities or new trades. You read the chart images and the setup "
    "facts and answer only what the schema asks, in plain language, one sentence per "
    "field. Be specific about structure (bases, prior highs, volume), skeptical of "
    "extended moves, and say 'remove' when the chart contradicts the setup's premise."
)


class ClaudeAdvisor:
    def __init__(
        self,
        cfg: ClaudeConfig,
        *,
        usage_log: Path | None = Path("data/claude_usage.jsonl"),
        client: Any | None = None,
        parse: Callable[..., Any] | None = None,
    ) -> None:
        self.cfg = cfg
        self.usage_log = usage_log
        self._client = client
        self._parse = parse  # test hook: (model, messages, output_format) -> (parsed, usage)

    @property
    def enabled(self) -> bool:
        return self.cfg.mode != "off"

    # --------------------------------------------------------------- calls

    def _call(
        self, model: str, messages: list[dict[str, Any]], output: type[T], max_tokens: int = 1024
    ) -> T | None:
        if not self.enabled or self.over_budget():
            return None
        try:
            if self._parse is not None:
                parsed, usage = self._parse(model, messages, output)
            else:
                import anthropic

                client = self._client or anthropic.Anthropic()
                self._client = client
                response = client.messages.parse(
                    model=model,
                    max_tokens=max_tokens,
                    system=SYSTEM,
                    messages=cast("list[MessageParam]", messages),
                    output_format=output,
                )
                if response.stop_reason == "refusal":
                    log.warning("claude refused (%s)", getattr(response, "stop_details", None))
                    return None
                parsed = response.parsed_output
                usage = {
                    "input_tokens": response.usage.input_tokens,
                    "output_tokens": response.usage.output_tokens,
                }
            self._record_usage(model, usage)
            return parsed if isinstance(parsed, output) else output.model_validate(parsed)
        except Exception as exc:  # noqa: BLE001 - advisory, never fatal
            log.warning("claude call failed: %s", exc)
            return None

    def read_chart(
        self, setup_facts: dict[str, Any], daily_png: Path | None, hourly_png: Path | None = None
    ) -> ChartRead | None:
        content: list[dict[str, Any]] = []
        for label, png in (("Daily chart", daily_png), ("Hourly chart", hourly_png)):
            if png and png.exists():
                content.append({"type": "text", "text": f"{label}:"})
                content.append(_image_block(png))
        content.append(
            {
                "type": "text",
                "text": (
                    "Setup facts (computed by code; do not restate numbers):\n"
                    + json.dumps(setup_facts, indent=2, default=str)
                    + "\nGive the three-line read and a verdict."
                ),
            }
        )
        return self._call(
            self.cfg.chart_read_model, [{"role": "user", "content": content}], ChartRead
        )

    def trigger_note(self, setup_facts: dict[str, Any], bar_summary: str) -> TriggerNote | None:
        text = (
            "A setup just triggered on a 15-minute close. Setup facts:\n"
            + json.dumps(setup_facts, default=str)
            + f"\nLast 15-minute bars: {bar_summary}\nOne sentence: does the intraday action look "
            "like genuine demand or a fade risk? No numbers."
        )
        return self._call(
            self.cfg.trigger_note_model,
            [{"role": "user", "content": text}],
            TriggerNote,
            max_tokens=256,
        )

    def weekly_review(
        self, stats: dict[str, Any], trades: list[dict[str, Any]], tags: list[dict[str, Any]]
    ) -> WeeklyReview | None:
        text = (
            "Weekly coaching review for a rules-based swing trader. Stats:\n"
            + json.dumps(stats, indent=2, default=str)
            + "\nTrades this week:\n"
            + json.dumps(trades, indent=2, default=str)
            + "\nTags (rule breaks, FOMO, revenge):\n"
            + json.dumps(tags, indent=2, default=str)
            + "\nWrite the review. Name ONE concrete process change for next week. Do not "
            "suggest new setups, levels or sizes."
        )
        return self._call(
            self.cfg.weekly_review_model,
            [{"role": "user", "content": text}],
            WeeklyReview,
            max_tokens=2048,
        )

    # --------------------------------------------------------------- spend

    def _record_usage(self, model: str, usage: dict[str, Any]) -> None:
        if self.usage_log is None:
            return
        self.usage_log.parent.mkdir(parents=True, exist_ok=True)
        pin, pout = PRICES.get(model, (5.0, 25.0))
        cost = usage.get("input_tokens", 0) / 1e6 * pin + usage.get("output_tokens", 0) / 1e6 * pout
        with self.usage_log.open("a", encoding="utf-8") as fh:
            fh.write(
                json.dumps(
                    {
                        "at": datetime.now(IST).isoformat(),
                        "model": model,
                        **usage,
                        "usd": round(cost, 6),
                    }
                )
                + "\n"
            )

    def month_spend_usd(self) -> float:
        if self.usage_log is None or not self.usage_log.exists():
            return 0.0
        month = datetime.now(IST).strftime("%Y-%m")
        total = 0.0
        for line in self.usage_log.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if str(row.get("at", "")).startswith(month):
                total += float(row.get("usd", 0.0))
        return total

    def over_budget(self) -> bool:
        cap = float(self.cfg.monthly_spend_limit_usd)
        return cap > 0 and self.month_spend_usd() >= cap


def _image_block(png: Path) -> dict[str, Any]:
    data = base64.standard_b64encode(png.read_bytes()).decode("ascii")
    return {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": data}}
