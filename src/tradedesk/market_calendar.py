"""NSE/BSE equity-market holidays (config/market_calendar.yaml) and which jobs skip them."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import yaml

CONFIG = Path("config") / "market_calendar.yaml"


def _load(path: Path) -> dict[str, Any]:
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return {}
    return value if isinstance(value, dict) else {}


def equity_holidays(path: Path = CONFIG) -> set[date]:
    out = set()
    for item in _load(path).get("equity_holidays") or []:
        out.add(item if isinstance(item, date) else date.fromisoformat(str(item)))
    return out


def equity_jobs(path: Path = CONFIG) -> set[str]:
    return {str(name) for name in _load(path).get("equity_jobs") or []}


def is_equity_holiday(day: date, path: Path = CONFIG) -> bool:
    return day in equity_holidays(path)


def skip_today(job: str, day: date, path: Path = CONFIG) -> bool:
    """True when `job` is a weekday-only equity job and `day` is an exchange holiday."""

    return job in equity_jobs(path) and is_equity_holiday(day, path)
