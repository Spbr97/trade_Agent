from decimal import Decimal
from pathlib import Path

import pytest

from tradedesk.config import Settings, load_config
from tradedesk.config.models import ChargeSchedule

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="session")
def settings() -> Settings:
    return load_config(ROOT)


@pytest.fixture(scope="session")
def schedule(settings: Settings) -> ChargeSchedule:
    return settings.risk.costs


@pytest.fixture(scope="session")
def default_schedule() -> ChargeSchedule:
    """INDmoney's equity rates (IND Pricing page, Sept 2026, verified against FY25-26 ledger
    bills), pinned here so the unit tests don't move when config/risk.yaml is tuned."""
    return ChargeSchedule.model_validate(
        {
            "brokerage": {
                "pct": Decimal("0.001"),
                "max_per_order": Decimal("5"),
                "min_per_order": Decimal("2"),
            },
            "stt": {
                "delivery_buy": Decimal("0.001"),
                "delivery_sell": Decimal("0.001"),
                "intraday_sell": Decimal("0.00025"),
            },
            "exchange_txn_pct": Decimal("0.0000307"),
            "ipft_pct": Decimal("0.000001"),
            "sebi_fee_pct": Decimal("0.000001"),
            "stamp_duty": {"delivery_buy": Decimal("0.00015"), "intraday_buy": Decimal("0.00003")},
            "gst_pct": Decimal("0.18"),
            "gst_on_exchange_txn": True,
            "dp_charge": {"amount": Decimal("18.5"), "gst_applies": True},
            "slippage_pct": Decimal("0.0005"),
            "rounding": "paise",
            "statutory_rounding": "rupee",
        }
    )


@pytest.fixture(scope="session")
def flat20_schedule(default_schedule: ChargeSchedule) -> ChargeSchedule:
    """The Rs 20-per-order assumption PLAN.md's worked examples were written with."""
    return default_schedule.model_copy(
        update={
            "brokerage": default_schedule.brokerage.model_copy(
                update={
                    "pct": Decimal("1"),
                    "max_per_order": Decimal("20"),
                    "min_per_order": Decimal("20"),
                }
            )
        }
    )
