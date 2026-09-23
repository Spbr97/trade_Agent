import json
import sys

import duckdb
import pytest
from tradedesk_lab import __main__ as cli
from tradedesk_lab import aem_dataset, nse_universe


def test_locked_source_records_blocker_without_replacing_dataset(tmp_path, monkeypatch, capsys):
    latest = tmp_path / "aem" / "latest.json"
    latest.parent.mkdir()
    latest.write_text('{"id":"prior-dataset"}')
    before = latest.read_bytes()

    def blocked(**kwargs):
        raise duckdb.IOException("source is locked by existing writer")

    monkeypatch.setattr(cli, "OUTPUT", tmp_path)
    monkeypatch.setattr(aem_dataset, "prepare_aem", blocked)
    monkeypatch.setattr(sys, "argv", ["tradedesk_lab", "aem-prepare", "--sessions", "5"])
    with pytest.raises(SystemExit) as error:
        cli.main()
    assert error.value.code == 1
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "blocked_source_unavailable"
    assert report["eligible_for_live"] is False
    assert report["sessions_requested"] == 5
    assert latest.read_bytes() == before
    assert not (tmp_path / "aem" / "datasets").exists()
    saved = list((tmp_path / "aem" / "blocked").glob("*.json"))
    assert len(saved) == 1 and json.loads(saved[0].read_text()) == report


@pytest.mark.parametrize(
    "status",
    ["historical_daily_screen_only", "blocked_source_unavailable", "blocked_no_daily_calendar"],
)
def test_universe_cli_signals_source_availability(monkeypatch, capsys, status):
    monkeypatch.setattr(nse_universe, "run_nse_screen", lambda **kwargs: {"status": status})
    monkeypatch.setattr(sys, "argv", ["tradedesk_lab", "nse-screen"])
    if status.startswith("blocked_"):
        with pytest.raises(SystemExit) as error:
            cli.main()
        assert error.value.code == 1
    else:
        cli.main()
    assert json.loads(capsys.readouterr().out)["status"] == status


@pytest.mark.parametrize(
    "arguments", [["aem-prepare", "--sessions", "0"], ["nse-screen", "--shortlist-size", "0"]]
)
def test_rejects_nonpositive_research_windows(monkeypatch, arguments):
    monkeypatch.setattr(sys, "argv", ["tradedesk_lab", *arguments])
    with pytest.raises(SystemExit) as error:
        cli.main()
    assert error.value.code == 2
