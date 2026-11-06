"""positioning: real file quirks, no look-ahead, and a long side must beat simply being long."""

from __future__ import annotations

import math
from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest
from tradedesk_lab import edge_hunt as eh
from tradedesk_lab import positioning as ps

COLUMNS = (
    "Client Type,Future Index Long,Future Index Short,Future Stock Long,Future Stock Short\t,"
    "Option Index Call Long,Option Index Put Long,Option Index Call Short,Option Index Put Short,"
    "Option Stock Call Long,Option Stock Put Long,Option Stock Call Short,Option Stock Put Short,"
    "Total Long Contracts,Total Short Contracts\r\n"
)


def _file(title: str, parties=("Client", "DII", "FII", "Pro", "TOTAL")) -> str:  # noqa: ANN001
    rows = "".join(f"{p},100,50,1,1,2,3,4,5,1,1,1,1,9,9\r\n" for p in parties)
    return title + ",,,,,,,,,,,,,,\r\n" + COLUMNS + rows


def test_each_title_style_is_read_and_the_total_row_ignored() -> None:
    day = date(2018, 1, 2)
    head = "Participant wise Open Interest (no. of contracts) in Equity Derivatives as on"
    old = _file(f'"{head} Jan 02, 2018"')
    new = _file(f'""{head} Jan 02,2018""')
    for text in (old, new):
        rows = ps.parse_file(text, day)
        assert {r["party"] for r in rows} == set(ps.PARTIES)
        assert rows[0]["fut_idx_long"] == 100.0


def test_a_file_for_another_day_is_refused() -> None:
    text = _file('"Participant wise Open Interest as on Jan 02, 2018"')
    assert ps.parse_file(text, date(2018, 1, 3)) is None


def test_a_file_missing_a_participant_or_a_title_is_refused() -> None:
    title = '"Participant wise Open Interest as on Jan 02, 2018"'
    assert ps.parse_file(_file(title, ("Client", "FII")), date(2018, 1, 2)) is None
    assert ps.parse_file("<html>not found</html>", date(2018, 1, 2)) is None


def test_the_rank_uses_only_today_and_before() -> None:
    values = pd.Series([0.1, 0.2, 0.3, 0.4, 0.5, 0.05])
    got = ps.trailing_rank(values, window=5)
    assert math.isnan(got.iloc[3])
    assert got.iloc[4] == pytest.approx(1.0)
    assert got.iloc[5] == pytest.approx(0.2)
    # A later value cannot change an earlier rank.
    longer = ps.trailing_rank(pd.concat([values, pd.Series([9.0])], ignore_index=True), window=5)
    assert longer.iloc[5] == got.iloc[5]


def _shares(fii: list[float]) -> pd.DataFrame:
    days = [date(2020, 1, 1) + timedelta(days=i) for i in range(len(fii))]
    return pd.DataFrame({"FII": fii, "Client": fii}, index=days)


def test_follow_and_fade_are_opposites(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ps, "RANK_WINDOW", 5)
    monkeypatch.setattr(ps.trailing_rank, "__defaults__", (5,))
    shares = _shares([0.5, 0.5, 0.5, 0.5, 0.5, 0.9, 0.1])
    follow, fade = ps.rule_signal("fii_follow", shares), ps.rule_signal("fii_fade", shares)
    assert follow.iloc[5] == 1 and fade.iloc[5] == -1
    assert follow.iloc[6] == -1 and fade.iloc[6] == 1
    assert ps.rule_signal("client_fade", shares).iloc[5] == -1


def test_the_shift_rule_compares_with_five_sessions_ago() -> None:
    shares = _shares([0.5] * 5 + [0.56, 0.44, 0.5, 0.5, 0.5, 0.5])
    got = ps.rule_signal("fii_shift", shares)
    assert got.iloc[5] == 1
    assert got.iloc[6] == -1
    assert got.iloc[4] == 0  # no five-sessions-ago yet


def _nifty(opens: list[float], closes: list[float]) -> pd.DataFrame:
    days = [date(2020, 1, 1) + timedelta(days=i) for i in range(len(opens))]
    return pd.DataFrame({"open": opens, "close": closes}, index=days)


def test_a_signal_enters_at_the_next_open_never_the_same_session() -> None:
    nifty = _nifty([100, 110, 100, 100], [105, 121, 100, 100])
    signal = pd.Series([1, 0, 0, 0], index=nifty.index)
    (row,) = ps.trades(signal, nifty, 1, base=0.0).to_dict("records")
    assert row["day"] == nifty.index[1]
    assert row["net"] == pytest.approx(0.10 - 2 * eh.FUT_COST)  # 110 to 121, not 100 to 105


def test_a_day_trade_pays_no_carry_and_a_long_pays_it_over_days() -> None:
    nifty = _nifty([100] * 8, [100] * 8)
    signal = pd.Series([1] + [0] * 7, index=nifty.index)
    (row,) = ps.trades(signal, nifty, 5, base=0.0).to_dict("records")
    assert row["net"] == pytest.approx(-2 * eh.FUT_COST - eh.CARRY * 5 / 252)


def test_a_short_earns_the_carry() -> None:
    nifty = _nifty([100] * 8, [100] * 8)
    signal = pd.Series([-1] + [0] * 7, index=nifty.index)
    (row,) = ps.trades(signal, nifty, 5, base=0.0).to_dict("records")
    assert row["net"] == pytest.approx(-2 * eh.FUT_COST + eh.CARRY * 5 / 252)


def test_positions_do_not_overlap() -> None:
    nifty = _nifty([100] * 12, [100] * 12)
    signal = pd.Series([1] * 12, index=nifty.index)
    got = ps.trades(signal, nifty, 5, base=0.0)
    assert list(got["day"]) == [nifty.index[1], nifty.index[6]]


def test_being_long_in_a_rising_market_earns_no_excess() -> None:
    opens = [100.0] * 40
    closes = [101.0] * 40  # up 1% every session
    nifty = _nifty(opens, closes)
    signal = pd.Series([1] * 40, index=nifty.index)
    got = ps.trades(signal, nifty, 1)
    assert got["net"].mean() > 0
    assert got["excess"].mean() < 0


def _frame(excess: list[float], sides: list[int], start: date = date(2019, 1, 1)) -> pd.DataFrame:
    days = [start + timedelta(days=14 * i) for i in range(len(excess))]
    return pd.DataFrame({"day": days, "side": sides, "net": excess, "excess": excess})


def test_a_real_two_sided_edge_passes() -> None:
    n = 120
    excess = list(np.resize([0.01, 0.03], n))
    assert ps.judge(_frame(excess, list(np.resize([1, 1, -1, -1], n))))["passed"]


def test_a_rule_that_works_on_one_side_only_fails() -> None:
    n = 120
    sides = list(np.resize([1, -1], n))
    excess = [0.03 if s == 1 else -0.002 for s in sides]
    got = ps.judge(_frame(excess, sides))
    assert any("both sides" in f for f in got["fails"])


def test_too_few_trades_fail_and_an_empty_record_is_safe() -> None:
    assert not ps.judge(_frame([0.02, 0.03] * 10, [1, -1] * 10))["passed"]
    assert ps.judge(pd.DataFrame(columns=["day", "side", "net", "excess"]))["trades"] == 0


def test_a_filter_must_keep_the_better_calls() -> None:
    days = [date(2019, 1, 7) + timedelta(days=7 * i) for i in range(160)]
    bad = pd.Series([i % 2 == 0 for i in range(160)])
    wobble = [0.01 if i % 4 < 2 else -0.01 for i in range(160)]
    good = [0.05 + w if not b else -0.05 + w for w, b in zip(wobble, bad, strict=True)]
    calls = pd.DataFrame({"day": days, "net": good, "excess": good})
    assert ps.judge_filter(calls, bad)["passed"]
    assert not ps.judge_filter(calls, ~bad)["passed"]


def test_a_full_month_name_is_read() -> None:
    text = _file('"Participant wise Open Interest as on June 01, 2018"')
    assert ps.parse_file(text, date(2018, 6, 1))


def test_a_full_month_name_for_the_wrong_day_is_refused() -> None:
    text = _file('"Participant wise Open Interest as on June 01, 2018"')
    assert ps.parse_file(text, date(2018, 6, 4)) is None


def test_a_file_with_no_title_is_read_from_its_header() -> None:
    body = _file('"title as on Jan 02, 2018"').split("\r\n", 1)[1]
    rows = ps.parse_file(body, date(2018, 6, 13))
    assert {r["party"] for r in rows} == set(ps.PARTIES)


def test_a_repeated_table_is_flagged_as_stale(tmp_path) -> None:  # noqa: ANN001
    path = tmp_path / "t.csv"
    head = ",".join(ps.FIELDS)
    lines = [head, "2020-01-01,FII,1,2,3,4,5,6", "2020-01-02,FII,1,2,3,4,5,6",
             "2020-01-03,FII,9,2,3,4,5,6"]  # fmt: skip
    path.write_text("\n".join(lines), encoding="utf-8")
    assert ps.stale_days(path) == ["2020-01-02"]
