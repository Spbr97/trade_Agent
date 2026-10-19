"""Exchange holidays: weekday-only equity jobs and their digests skip them, nothing else does."""

from __future__ import annotations

import json
import runpy
import sys
from datetime import date
from pathlib import Path

from tradedesk import market_calendar as mc

ROOT = Path(__file__).resolve().parents[2]
REAL = ROOT / "config" / "market_calendar.yaml"


def test_the_real_calendar_parses_and_names_only_scheduled_jobs() -> None:
    holidays = mc.equity_holidays(REAL)
    assert date(2026, 10, 20) in holidays and date(2026, 12, 25) in holidays
    assert all(d.weekday() < 5 for d in holidays)  # weekend closures need no entry
    schedule = json.loads((ROOT / "deploy" / "schedule.json").read_text(encoding="utf-8"))
    names = {j["command"][0] for j in schedule["jobs"] if j.get("command")}
    assert mc.equity_jobs(REAL) <= names  # a typo would silently skip nothing
    assert not any("crypto" in name for name in mc.equity_jobs(REAL))


def test_only_equity_jobs_skip_and_only_on_holidays() -> None:
    assert mc.skip_today("live-session", date(2026, 10, 20), REAL)
    assert not mc.skip_today("live-session", date(2026, 10, 19), REAL)  # a trading day
    assert not mc.skip_today("crypto-tracker", date(2026, 10, 20), REAL)
    assert not mc.skip_today("health-check", date(2026, 10, 20), REAL)


def test_an_unreadable_calendar_skips_nothing(tmp_path: Path) -> None:
    bad = tmp_path / "market_calendar.yaml"
    bad.write_text("equity_holidays: [not a date", encoding="utf-8")
    assert mc.equity_holidays(tmp_path / "missing.yaml") == set()
    assert mc.equity_holidays(bad) == set()
    assert not mc.skip_today("live-session", date(2026, 10, 20), bad)


def test_run_hidden_logs_the_skip_and_never_starts_the_job(tmp_path: Path, monkeypatch) -> None:
    module = runpy.run_path(str(ROOT / "scripts" / "run_hidden.py"))
    main = module["main"]
    explode = tmp_path / "explode.py"
    explode.write_text("raise SystemExit(7)\n", encoding="utf-8")
    monkeypatch.setitem(main.__globals__, "ROOT", tmp_path)
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "market_calendar.yaml").write_text(
        f"equity_holidays: [{date.today().isoformat()}]\nequity_jobs: [after-close]\n",
        encoding="utf-8",
    )
    stdout = sys.stdout
    try:
        monkeypatch.setattr(sys, "argv", ["run_hidden.py", "after-close", str(explode)])
        assert main() == 0  # skipped: the job, which would exit 7, never ran
        monkeypatch.setattr(sys, "argv", ["run_hidden.py", "crypto-tracker", str(explode)])
        assert main() == 7  # not an equity job: runs as usual
    finally:
        sys.stdout = sys.stderr = stdout
    log = (tmp_path / "data" / "logs" / "after-close.log").read_text(encoding="utf-8")
    assert "equity job skipped" in log
