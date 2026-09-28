"""optimize_rule(): the reusable core the CLI `optimize` command wraps, extracted so the
self-review loop's config-tuning path can call it programmatically too. Runs against the real
(small, `max_codes`-limited) local DB, same as `optimize()` always has - this is treated as an
analysis-script integration check, not a mocked unit test, matching the project's own
"backtest/train aren't unit-tested, they're run and read" precedent."""

from __future__ import annotations

from pathlib import Path

from scripts.entry_search import RULES, optimize_rule


def test_optimize_rule_returns_a_well_formed_result(tmp_path: Path) -> None:
    result = optimize_rule(
        "rsi2<10 & above ema50",
        risk_pct=0.005,
        max_codes=50,
        out=tmp_path / "entry_optimize.csv",
    )

    assert result.rule == "rsi2<10 & above ema50"
    assert result.cells_simulated > 0
    assert result.csv_path.exists()
    assert not result.train_table.empty
    # A geometry with enough train samples was found, so the locked-test half was scored.
    assert result.best_stop_atr is not None
    assert result.test_n > 0
    assert result.test_win_rate is not None
    assert 0.0 <= result.test_win_rate <= 1.0


def test_optimize_rule_rejects_unknown_rule(tmp_path: Path) -> None:
    import pytest

    with pytest.raises(ValueError, match="unknown rule"):
        optimize_rule("not a real rule", max_codes=1, out=tmp_path / "x.csv")


def test_optimize_rule_echo_receives_the_same_lines_the_cli_would_print(tmp_path: Path) -> None:
    lines: list[str] = []
    optimize_rule(
        "below ema10 & above ema50",
        risk_pct=0.005,
        max_codes=30,
        out=tmp_path / "entry_optimize.csv",
        echo=lines.append,
    )
    assert any("optimizing" in line for line in lines)
    assert any("cells simulated" in line for line in lines)


def test_every_registered_rule_name_is_a_valid_optimize_rule_argument() -> None:
    # optimize_rule's own ValueError guard should never trip for a name RULES itself owns.
    assert "rsi2<10 & above ema50" in RULES
    assert "below ema10 & above ema50" in RULES
