from __future__ import annotations

from pathlib import Path


def test_m21_endpoint_is_crypto_only_and_fail_closed() -> None:
    source = (
        Path(__file__).parents[2]
        / "src"
        / "tradedesk"
        / "dashboard"
        / "app.py"
    ).read_text(encoding="utf-8")

    assert '@app.get("/api/self-learning/momentum-ignition-watch")' in source
    assert 'if market != "crypto":' in source
    assert '"status": "not_applicable"' in source
    assert '"tradeable_calls": 0' in source
    assert '"eligible_for_live": False' in source
    assert '"baseline_accuracy_improved": False' in source
    assert "load_momentum_ignition_watch" in source


def test_m21_dashboard_is_separate_from_m20_and_fetches_last() -> None:
    html = (
        Path(__file__).parents[2]
        / "src"
        / "tradedesk"
        / "dashboard"
        / "static"
        / "index.html"
    ).read_text(encoding="utf-8")

    assert "M20 compression benchmark" in html
    assert "M21 momentum-ignition challenger" in html
    assert "/api/self-learning/momentum-ignition-watch?market=${market}" in html
    assert "preTakeoffWatchResponse] = await Promise.all" not in html
    assert html.index(
        "preTakeoffWatchResponse, momentumIgnitionResponse, "
        "momentumIgnitionM22Response] = await Promise.all"
    ) > 0
    assert html.index("/api/self-learning/pre-takeoff-watch?market=${market}") < html.index(
        "/api/self-learning/momentum-ignition-watch?market=${market}"
    )
    assert html.index("/api/self-learning/momentum-ignition-watch?market=${market}") < (
        html.index("/api/self-learning/momentum-ignition-watch-m22?market=${market}")
    )

    for element_id in (
        "momentum-ignition-status",
        "momentum-ignition-snapshot",
        "momentum-ignition-health",
        "momentum-ignition-coverage",
        "momentum-ignition-timeframes",
        "momentum-ignition-registration",
        "momentum-ignition-candidates",
        "momentum-ignition-ignition",
        "momentum-ignition-reclaims",
        "momentum-ignition-economics",
        "momentum-ignition-active",
        "momentum-ignition-resolved",
        "momentum-ignition-accuracy",
        "momentum-ignition-net-r",
        "momentum-ignition-sessions",
        "momentum-ignition-wilson",
        "momentum-ignition-loss-streak",
        "momentum-ignition-qualification",
        "momentum-ignition-progress",
        "momentum-ignition-improved",
        "momentum-ignition-authority",
        "momentum-ignition-detail",
        "momentum-ignition-table",
        "pre-takeoff-m20-m21-table",
    ):
        assert f'id="{element_id}"' in html

    assert "M21 evidence remains separate from M20" in html
    assert "unavailable is not a pass" in html
