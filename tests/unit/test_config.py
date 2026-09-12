from decimal import Decimal
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from tradedesk.config import Settings, load_config
from tradedesk.config.models import RiskConfig


def test_loads_repo_config(settings: Settings) -> None:
    assert settings.risk.trading_capital == Decimal("100000")
    assert settings.risk.max_open_positions == 5
    assert settings.risk.costs.gst_pct == Decimal("0.18")
    assert settings.claude.mode == "off"
    assert settings.ml.enabled is False
    assert all(s.enabled is False for s in settings.setups.setups.values())


def test_decimal_fields_have_no_float_drift(settings: Settings) -> None:
    # 0.0000307 as a float prints as 3.07e-05; as Decimal it must be exact.
    assert settings.risk.costs.exchange_txn_pct == Decimal("0.0000307")
    assert settings.risk.costs.brokerage.max_per_order == Decimal("5")
    assert str(settings.risk.costs.stt.delivery_buy) == "0.001"


def test_unknown_key_rejected() -> None:
    with pytest.raises(ValidationError):
        RiskConfig.model_validate({"trading_capital": 1, "not_a_rule": 5})


def test_missing_optional_files_fall_back_to_defaults(tmp_path: Path) -> None:
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "risk.yaml").write_text(
        yaml.safe_dump({"trading_capital": 50000}), encoding="utf-8"
    )
    s = load_config(tmp_path)
    assert s.risk.trading_capital == Decimal("50000")
    assert s.risk.max_open_positions == 5  # default from PLAN.md 1.2
    assert s.claude.mode == "off"


def test_risk_yaml_is_required(tmp_path: Path) -> None:
    (tmp_path / "config").mkdir()
    with pytest.raises(FileNotFoundError):
        load_config(tmp_path)


def test_fractions_are_bounded() -> None:
    with pytest.raises(ValidationError):
        RiskConfig.model_validate({"trading_capital": 1, "max_risk_per_trade_pct": 5})


def test_crypto_market_yaml_loads(settings: Settings) -> None:
    """config/markets/crypto.yaml (M13 Phase 4) - optional nested file, loaded the same
    way as every other config/*.yaml, just one directory deeper."""
    cm = settings.crypto_market
    assert cm.costs.maker_taker_pct == Decimal("0.002")
    assert cm.costs.tds_pct == Decimal("0.01")
    assert cm.costs.gst_pct == Decimal("0.18")
    assert cm.universe.min_avg_daily_turnover_inr == Decimal("2500000")
    assert cm.universe.min_price == Decimal("0")
    assert cm.benchmark == "BTCINR"


def test_crypto_market_falls_back_to_defaults_when_file_absent(tmp_path: Path) -> None:
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "risk.yaml").write_text(
        yaml.safe_dump({"trading_capital": 50000}), encoding="utf-8"
    )
    s = load_config(tmp_path)
    assert s.crypto_market.costs.maker_taker_pct == Decimal("0.002")
    assert s.crypto_market.universe.min_avg_daily_turnover_inr == Decimal("2500000")
