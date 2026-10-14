"""my_picks.py: a pick is priced only from bars that start AFTER it was logged, and the log
cannot be back-dated, edited or padded with repeat taps."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path

import httpx
import pytest

import tradedesk.my_picks as my_picks
from tradedesk.broker.indstocks.models import IST, Candle, Interval
from tradedesk.my_picks import (
    PickError,
    add_pick,
    load_picks,
    load_sessions,
    score_pick,
    summarise,
    write_intraday_bars,
)

DAY = date(2026, 10, 14)
NEXT = date(2026, 10, 15)


def _resolver(market: str, symbol: str):
    known = {"TARIL": "NSE_15174", "IDBI": "NSE_1476", "SBIN": "NSE_3045"}
    code = known.get(symbol.strip().upper())
    return (code, symbol.strip().upper()) if code else None


def _at(day: date, hhmm: str) -> datetime:
    h, m = (int(x) for x in hhmm.split(":"))
    return datetime(day.year, day.month, day.day, h, m, tzinfo=IST)


def _session(day: date, bars: dict, *, final: bool = True) -> dict:
    written = _at(day, "15:31") if final else _at(day, "11:01")
    return {
        "market": "nse",
        "session": day.isoformat(),
        "written_at": written.isoformat(),
        "bars": bars,
    }


def _bars(prices: list[tuple[str, float, float]]) -> list[list]:
    """(HH:MM, open, close) -> stored bar rows."""
    return [[t, o, max(o, c), min(o, c), c] for t, o, c in prices]


# ------------------------------------------------------------------------ logging


def test_pick_is_stamped_by_the_server_and_chained(tmp_path: Path) -> None:
    path = tmp_path / "picks.jsonl"
    first = add_pick(
        "nse", "taril", "up", "results beat", now=_at(DAY, "09:05"), path=path, resolver=_resolver
    )
    second = add_pick("nse", "IDBI", "down", now=_at(DAY, "09:06"), path=path, resolver=_resolver)

    rows, intact = load_picks(path)
    assert intact and len(rows) == 2
    assert first["symbol"] == "TARIL" and first["scrip_code"] == "NSE_15174"
    assert first["logged_at"] == _at(DAY, "09:05").isoformat()
    assert second["previous_sha256"] == first["sha256"]


def test_second_tap_on_the_same_stock_and_direction_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "picks.jsonl"
    add_pick("nse", "TARIL", "up", now=_at(DAY, "09:05"), path=path, resolver=_resolver)
    with pytest.raises(PickError, match="only the first pick counts"):
        add_pick("nse", "TARIL", "up", now=_at(DAY, "11:00"), path=path, resolver=_resolver)
    add_pick("nse", "TARIL", "down", now=_at(DAY, "11:00"), path=path, resolver=_resolver)
    add_pick("nse", "TARIL", "up", now=_at(NEXT, "09:05"), path=path, resolver=_resolver)
    assert len(load_picks(path)[0]) == 3


@pytest.mark.parametrize(
    ("market", "symbol", "direction", "note", "message"),
    [
        ("crypto", "TARIL", "up", "", "market must be nse or bse"),
        ("nse", "TARIL", "sideways", "", "direction must be up or down"),
        ("nse", "  ", "up", "", "symbol is required"),
        ("nse", "NOSUCH", "up", "", "not a listed NSE equity"),
        ("nse", "TARIL", "up", "x" * 281, "limited to 280"),
    ],
)
def test_invalid_picks_are_refused(tmp_path, market, symbol, direction, note, message) -> None:
    with pytest.raises(PickError, match=message):
        add_pick(market, symbol, direction, note, path=tmp_path / "p.jsonl", resolver=_resolver)


def test_an_edited_log_fails_its_integrity_check(tmp_path: Path) -> None:
    path = tmp_path / "picks.jsonl"
    add_pick("nse", "TARIL", "up", now=_at(DAY, "09:05"), path=path, resolver=_resolver)
    row = json.loads(path.read_text(encoding="utf-8"))
    row["logged_at"] = _at(DAY, "08:00").isoformat()  # back-dating attempt
    path.write_text(json.dumps(row) + "\n", encoding="utf-8")
    assert load_picks(path)[1] is False


# ------------------------------------------------------------------- intraday bars


def _candle(code: str, ts: datetime, price: float) -> Candle:
    return Candle(
        scrip_code=code, interval=Interval.M15, ts=ts,
        open=price, high=price + 1, low=price - 1, close=price, volume=100,
    )  # fmt: skip


def test_collector_bars_keep_only_this_sessions_completed_bars(tmp_path: Path) -> None:
    now = _at(DAY, "10:01")
    candles = {
        "NSE_1": [
            _candle("NSE_1", _at(DAY - timedelta(days=1), "15:15"), 90.0),  # yesterday
            _candle("NSE_1", _at(DAY, "09:15"), 100.0),
            _candle("NSE_1", _at(DAY, "09:30"), 101.0),
            _candle("NSE_1", _at(DAY, "09:45"), 102.0),  # closes 10:00 <= 10:01: kept
            _candle("NSE_1", _at(DAY, "10:00"), 103.0),  # still forming at 10:01
        ]
    }
    old = tmp_path / "nse" / "intraday_bars" / f"{(DAY - timedelta(days=90)).isoformat()}.json"
    old.parent.mkdir(parents=True)
    old.write_text("{}", encoding="utf-8")

    write_intraday_bars("nse", candles, observed_at=now, output_root=tmp_path)

    sessions = load_sessions("nse", tmp_path)
    assert list(sessions) == [DAY]
    assert [b[0] for b in sessions[DAY]["bars"]["NSE_1"]] == ["09:15", "09:30", "09:45"]


# ------------------------------------------------------------------------ scoring


def _pick(logged: datetime, direction: str = "up", code: str = "NSE_1") -> dict:
    return {
        "logged_at": logged.isoformat(), "market": "nse", "scrip_code": code,
        "symbol": code, "direction": direction, "note": "",
    }  # fmt: skip


def _universe_day(day: date = DAY, *, final: bool = True) -> dict:
    return _session(
        day,
        {
            # the pick: jumps inside the 10:00 bar, then drifts a little higher
            "NSE_1": _bars(
                [("09:15", 100, 100), ("10:00", 100, 110), ("10:15", 110, 111), ("15:15", 111, 112)]
            ),
            "NSE_2": _bars(
                [("09:15", 50, 50), ("10:00", 50, 50), ("10:15", 50, 50), ("15:15", 50, 51)]
            ),
            "NSE_3": _bars(
                [("09:15", 20, 20), ("10:00", 20, 20), ("10:15", 20, 20), ("15:15", 20, 19)]
            ),
        },
        final=final,
    )


def test_a_pick_logged_mid_bar_is_priced_from_the_next_bar_not_the_move_already_seen() -> None:
    scored = score_pick(_pick(_at(DAY, "10:07")), {DAY: _universe_day()})

    assert scored["status"] == "scored"
    assert scored["reference_bar"] == "10:15"
    assert scored["reference_price"] == 110  # NOT 100: the 10:00 jump happened before the pick
    assert scored["same_day_return"] == pytest.approx(112 / 110 - 1)


def test_a_pre_market_pick_is_priced_from_the_opening_bar() -> None:
    scored = score_pick(_pick(_at(DAY, "08:50")), {DAY: _universe_day()})
    assert scored["reference_bar"] == "09:15"
    assert scored["same_day_return"] == pytest.approx(0.12)
    assert scored["percentile"] == 1.0  # beat both other stocks from the same bar
    assert scored["universe_same_day_mean"] == pytest.approx(((51 / 50 - 1) + (19 / 20 - 1)) / 2)


def test_a_pick_after_the_last_bar_waits_for_the_next_session() -> None:
    pick = _pick(_at(DAY, "15:25"))
    assert score_pick(pick, {DAY: _universe_day()})["status"] == "waiting"

    nxt = _universe_day(NEXT)
    scored = score_pick(pick, {DAY: _universe_day(), NEXT: nxt})
    assert scored["session"] == NEXT.isoformat()
    assert scored["reference_bar"] == "09:15"


def test_a_down_pick_is_right_when_the_stock_falls() -> None:
    scored = score_pick(_pick(_at(DAY, "08:50"), "down", "NSE_3"), {DAY: _universe_day()})
    assert scored["same_day_return"] == pytest.approx(-(19 / 20 - 1))
    assert scored["percentile"] == 1.0


def test_session_still_running_is_live_and_missing_stock_is_not_scored() -> None:
    live = score_pick(_pick(_at(DAY, "08:50")), {DAY: _universe_day(final=False)})
    assert live["status"] == "live"
    missing = score_pick(_pick(_at(DAY, "08:50"), code="NSE_9"), {DAY: _universe_day()})
    assert missing["status"] == "no_intraday_data"


def test_next_day_outcome_uses_the_same_reference_price() -> None:
    nxt = _universe_day(NEXT)
    scored = score_pick(_pick(_at(DAY, "08:50")), {DAY: _universe_day(), NEXT: nxt})
    assert scored["next_session"] == NEXT.isoformat()
    assert scored["next_day_return"] == pytest.approx(112 / 100 - 1)


def test_summary_refuses_a_verdict_on_too_few_picks() -> None:
    scored = [score_pick(_pick(_at(DAY, "08:50")), {DAY: _universe_day()})]
    summary = summarise(scored)
    assert summary["scored"] == 1
    assert summary["verdict"].startswith("too few picks to judge")
    assert summary["right_direction_rate"] == 1.0


# ---------------------------------------------------------------------- dashboard


async def test_dashboard_logs_and_lists_picks(tmp_path: Path, monkeypatch) -> None:
    from tradedesk.dashboard.app import create_app
    from tradedesk.dashboard.state import DashboardState

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(my_picks, "resolve_symbol", _resolver)
    app = create_app(DashboardState())
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        ok = await client.post(
            "/api/my-picks",
            json={"market": "nse", "symbol": "TARIL", "direction": "up", "note": "<b>x</b>",
                  "logged_at": "2020-01-01T09:00:00+05:30"},  # ignored: the server sets the time
        )  # fmt: skip
        assert ok.status_code == 200
        assert not ok.json()["logged_at"].startswith("2020")
        dup = await client.post(
            "/api/my-picks", json={"market": "nse", "symbol": "TARIL", "direction": "up"}
        )
        assert dup.status_code == 400 and "only the first pick counts" in dup.json()["error"]
        listed = (await client.get("/api/my-picks")).json()
    assert listed["chain_intact"] is True
    assert listed["picks"][0]["symbol"] == "TARIL"
    assert listed["picks"][0]["status"] in {"waiting", "no_intraday_data"}

    html = (
        Path(__file__).parents[2] / "src" / "tradedesk" / "dashboard" / "static" / "index.html"
    ).read_text(encoding="utf-8")
    assert 'id="my-picks-table"' in html
    assert "${esc(p.note)}" in html  # the free-text note is always escaped
