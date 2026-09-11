"""Load and validate the YAML files under config/.

`risk.yaml` is required; the others fall back to their defaults when absent.
YAML floats are parsed directly into `Decimal` from their source text so a rate such
as 0.0000297 never passes through binary floating point.
"""

from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml

from tradedesk.config.models import (
    AlertsConfig,
    ChargeSchedule,
    ClaudeConfig,
    EngineConfig,
    MlConfig,
    RiskConfig,
    ScheduleConfig,
    Settings,
    SetupsConfig,
    UniverseConfig,
)

__all__ = ["ChargeSchedule", "Settings", "load_config", "load_yaml"]

CONFIG_DIR = "config"

_FILES: dict[str, tuple[str, type[Any], bool]] = {
    # attribute -> (file name, model, required)
    "risk": ("risk.yaml", RiskConfig, True),
    "universe": ("universe.yaml", UniverseConfig, False),
    "setups": ("setups.yaml", SetupsConfig, False),
    "schedule": ("schedule.yaml", ScheduleConfig, False),
    "alerts": ("alerts.yaml", AlertsConfig, False),
    "claude": ("claude.yaml", ClaudeConfig, False),
    "ml": ("ml.yaml", MlConfig, False),
    "engine": ("engine.yaml", EngineConfig, False),
}


class _DecimalLoader(yaml.SafeLoader):
    """SafeLoader that turns YAML floats into Decimal built from the literal text."""


def _construct_decimal(loader: yaml.SafeLoader, node: yaml.ScalarNode) -> Decimal:
    text = loader.construct_scalar(node)
    lowered = text.lower()
    if lowered in {".inf", "+.inf", "-.inf", ".nan"}:
        raise ValueError(f"non-finite number not allowed in config: {text!r}")
    return Decimal(text.replace("_", ""))


_DecimalLoader.add_constructor("tag:yaml.org,2002:float", _construct_decimal)


def load_yaml(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as fh:
        data = yaml.load(fh, Loader=_DecimalLoader)  # noqa: S506 - SafeLoader subclass
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ValueError(f"{path}: top level must be a mapping")
    return data


def load_config(root: Path | str = ".") -> Settings:
    """Read every config file under `<root>/config/` into a validated `Settings`."""
    config_dir = Path(root) / CONFIG_DIR
    parts: dict[str, Any] = {}
    for attr, (name, model, required) in _FILES.items():
        path = config_dir / name
        if not path.exists():
            if required:
                raise FileNotFoundError(f"required config file missing: {path}")
            continue
        parts[attr] = model.model_validate(load_yaml(path))
    return Settings.model_validate(parts)
