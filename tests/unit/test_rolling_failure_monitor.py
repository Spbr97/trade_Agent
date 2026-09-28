"""rolling_failure_monitor.py: the sustained/continuous failure trigger for the self-review
loop, distinct from signal_tracker.flag_setup_failures' lifetime-cumulative check."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from tradedesk import rolling_failure_monitor as mon
from tradedesk.signal_tracker import TrackedSignal


def _signal(setup: str, resolved_on: str | None, outcome: str | None, n: int) -> TrackedSignal:
    return TrackedSignal(
        signal_id=f"{setup}:{n}",
        scrip_code="NSE_1",
        symbol="X",
        setup=setup,
        grade="C",
        armed_on="2026-01-01",
        entry=100.0,
        stop=98.0,
        t1=104.0,
        t2=106.0,
        net_rr_t1=1.0,
        net_rr_t2=2.0,
        rejected_for=["only 0 resolved trades, need 500"],
        logged_at="2026-01-01T09:15:00+05:30",
        outcome=outcome,
        resolved_at=f"{resolved_on}T15:30:00+05:30" if resolved_on else None,
    )


def _rows(setup: str, resolutions: list[tuple[str, str]]) -> dict[str, TrackedSignal]:
    """resolutions: list of (resolved_on_date, outcome)."""
    rows = {}
    for i, (resolved_on, outcome) in enumerate(resolutions):
        sig = _signal(setup, resolved_on, outcome, i)
        rows[sig.signal_id] = sig
    return rows


def test_compute_window_returns_none_when_nothing_resolved_in_range():
    rows = list(_rows("setup_a", [("2026-01-01", "target")]).values())
    result = mon.compute_window(rows, as_of=date(2026, 3, 1), window_days=20)
    assert result is None


def test_compute_window_counts_only_calls_in_the_trailing_window():
    resolutions = [("2026-01-01", "target")] + [("2026-01-15", "stop")] * 4
    rows = list(_rows("setup_a", resolutions).values())
    n, hit_rate = mon.compute_window(rows, as_of=date(2026, 1, 20), window_days=20)
    assert n == 5
    assert hit_rate == 0.2

    # A 5-day window excludes the Jan 1 win entirely.
    n2, hit_rate2 = mon.compute_window(rows, as_of=date(2026, 1, 20), window_days=5)
    assert n2 == 4
    assert hit_rate2 == 0.0


def test_append_observations_writes_one_row_per_setup_and_returns_qualifying_only(
    tmp_path: Path,
):
    windows_log = tmp_path / "windows.jsonl"
    resolutions_a = [("2026-01-10", "stop")] * 20  # qualifies (>=15)
    resolutions_b = [("2026-01-10", "target")] * 3  # too few
    rows = {**_rows("setup_a", resolutions_a), **_rows("setup_b", resolutions_b)}

    qualifying = mon.append_observations(
        "nse", rows, as_of=date(2026, 1, 15), windows_log=windows_log
    )
    assert [o.setup for o in qualifying] == ["setup_a"]

    all_written = mon.load_observations(windows_log)
    assert {o.setup for o in all_written} == {"setup_a", "setup_b"}


def test_find_sustained_failures_requires_consecutive_failing_windows(tmp_path: Path):
    windows_log = tmp_path / "windows.jsonl"
    # Three separate days, each independently appended, all below the 30% floor.
    for as_of, resolutions in [
        (date(2026, 1, 1), [("2025-12-15", "stop")] * 15),
        (date(2026, 1, 2), [("2025-12-16", "stop")] * 15),
        (date(2026, 1, 3), [("2025-12-17", "stop")] * 15),
    ]:
        rows = _rows("bad_setup", resolutions)
        mon.append_observations("nse", rows, as_of=as_of, windows_log=windows_log)

    observations = mon.load_observations(windows_log)
    failures = mon.find_sustained_failures("nse", observations)
    assert len(failures) == 1
    failure = failures[0]
    assert failure.setup == "bad_setup"
    assert failure.severity == mon.FailureSeverity.SUSTAINED


def test_find_sustained_failures_single_bad_window_is_only_single_severity(tmp_path: Path):
    windows_log = tmp_path / "windows.jsonl"
    # First two days are healthy, only the latest breaches the floor.
    good = [("2025-12-10", "target")] * 15
    bad = [("2025-12-17", "stop")] * 15
    mon.append_observations(
        "nse", _rows("flaky_setup", good), as_of=date(2026, 1, 1), windows_log=windows_log
    )
    mon.append_observations(
        "nse", _rows("flaky_setup", good), as_of=date(2026, 1, 2), windows_log=windows_log
    )
    mon.append_observations(
        "nse", _rows("flaky_setup", bad), as_of=date(2026, 1, 3), windows_log=windows_log
    )

    observations = mon.load_observations(windows_log)
    failures = mon.find_sustained_failures("nse", observations)
    assert len(failures) == 1
    assert failures[0].severity == mon.FailureSeverity.SINGLE


def test_find_sustained_failures_none_when_latest_window_is_healthy(tmp_path: Path):
    windows_log = tmp_path / "windows.jsonl"
    bad = [("2025-12-10", "stop")] * 15
    good = [("2025-12-17", "target")] * 15
    mon.append_observations(
        "nse", _rows("recovered_setup", bad), as_of=date(2026, 1, 1), windows_log=windows_log
    )
    mon.append_observations(
        "nse", _rows("recovered_setup", good), as_of=date(2026, 1, 2), windows_log=windows_log
    )

    observations = mon.load_observations(windows_log)
    assert mon.find_sustained_failures("nse", observations) == []


def test_append_observations_is_idempotent_per_market_setup_and_day(tmp_path: Path):
    """Real bug, found 2026-09-27 activating this for crypto: calling append_observations
    twice on the same day (e.g. `rolling-check` then `self-review-run` right after) used to
    write a second same-day row, and find_sustained_failures' "most recent N observations"
    logic then counted those two duplicate rows as if they were two distinct days - turning
    one real bad day into a false SUSTAINED (retire-trigger) verdict after only two same-day
    calls. A second call for the same (market, setup, as_of_date) must be a no-op."""
    windows_log = tmp_path / "windows.jsonl"
    rows = _rows("flaky_setup", [("2025-12-17", "stop")] * 15)

    first = mon.append_observations(
        "crypto", rows, as_of=date(2026, 1, 3), windows_log=windows_log
    )
    second = mon.append_observations(
        "crypto", rows, as_of=date(2026, 1, 3), windows_log=windows_log
    )

    assert len(first) == 1
    assert second == []  # nothing new written the second time
    all_written = mon.load_observations(windows_log)
    assert len(all_written) == 1

    # Confirm the practical consequence: two same-day calls must NOT manufacture SUSTAINED.
    failures = mon.find_sustained_failures("crypto", all_written)
    assert len(failures) == 1
    assert failures[0].severity == mon.FailureSeverity.SINGLE


def test_run_daily_check_end_to_end(tmp_path: Path):
    log_path = tmp_path / "nse_signal_tracking.jsonl"
    windows_log = tmp_path / "windows.jsonl"
    from tradedesk.signal_tracker import save_log

    rows = _rows("dead_setup", [("2026-01-05", "stop")] * 20)
    save_log(rows, log_path)

    failures = mon.run_daily_check(
        "nse", log_path=log_path, windows_log=windows_log, as_of=date(2026, 1, 25)
    )
    # First call: only one observation exists yet, so severity is SINGLE (not enough
    # history for SUSTAINED regardless of how bad the single window is).
    assert any(
        f.setup == "dead_setup" and f.severity == mon.FailureSeverity.SINGLE for f in failures
    )
