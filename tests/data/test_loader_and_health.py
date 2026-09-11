from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime, time, timedelta

from tests.data.synth import daily, sessions, with_split
from tradedesk.broker.indstocks.models import IST, Candle, Interval
from tradedesk.broker.indstocks.rest import ApiError
from tradedesk.data.candle_store import CandleStore
from tradedesk.data.health import check_series, run_quality_report
from tradedesk.data.history_loader import default_start, load_history, plan_starts
from tradedesk.data.models import IssueKind


class FakeClient:
    """Serves synthetic daily candles for any code; records the calls it receives."""

    def __init__(self, days: list[date], fail_codes: set[str] | None = None) -> None:
        self.days = days
        self.calls: list[tuple[Interval, list[str], datetime, datetime]] = []
        self.fail_codes = fail_codes or set()

    async def candles_history(
        self, interval: Interval, codes: Sequence[str], start: datetime, end: datetime
    ) -> dict[str, list[Candle]]:
        self.calls.append((interval, list(codes), start, end))
        if any(c in self.fail_codes for c in codes):
            raise ApiError(503, "NetworkException")
        out: dict[str, list[Candle]] = {}
        for code in codes:
            out[code] = [c for c in daily(code, self.days) if start <= c.ts < end]
        return out


async def test_loader_batches_fresh_codes_and_is_incremental() -> None:
    days = sessions(date(2026, 1, 1), 30)
    end = datetime.combine(days[-1] + timedelta(days=1), time.min, tzinfo=IST)
    start = datetime.combine(days[0], time.min, tzinfo=IST)
    codes = [f"NSE_{i}" for i in range(7)]
    with CandleStore() as store:
        client = FakeClient(days)
        summary = await load_history(client, store, codes, Interval.D1, start=start, end=end)  # type: ignore[arg-type]
        assert not summary.errors and summary.fetched == 7 * 30
        assert sorted(len(c[1]) for c in client.calls) == [2, 5]  # 7 codes -> batches of 5 + 2
        assert store.count("NSE_0", Interval.D1) == 30

        # Second run: every code starts at its last stored bar, so only 1 bar is refetched.
        starts = plan_starts(store, codes, Interval.D1, start)
        assert all(s.date() == days[-1] for s in starts.values())
        client.calls.clear()
        summary2 = await load_history(client, store, codes, Interval.D1, start=start, end=end)  # type: ignore[arg-type]
        assert summary2.fetched == 7  # one refreshed bar per code
        assert all(c[2].date() == days[-1] for c in client.calls)
        assert store.count("NSE_0", Interval.D1) == 30  # no duplicates


async def test_loader_records_errors_per_batch_and_continues() -> None:
    days = sessions(date(2026, 1, 1), 5)
    end = datetime.combine(days[-1] + timedelta(days=1), time.min, tzinfo=IST)
    start = datetime.combine(days[0], time.min, tzinfo=IST)
    codes = [f"NSE_{i}" for i in range(6)]  # NSE_5 is alone in the second batch
    with CandleStore() as store:
        client = FakeClient(days, fail_codes={"NSE_5"})
        summary = await load_history(client, store, codes, Interval.D1, start=start, end=end)  # type: ignore[arg-type]
        assert [r.scrip_code for r in summary.errors] == ["NSE_5"]
        assert "NetworkException" in (summary.errors[0].error or "")
        assert store.count("NSE_0", Interval.D1) == 5 and store.count("NSE_5", Interval.D1) == 0


def test_default_start_depth() -> None:
    now = datetime(2026, 9, 11, tzinfo=IST)
    assert (now - default_start(Interval.D1, now)).days == 3650
    assert (now - default_start(Interval.M15, now)).days == 730


# ------------------------------------------------------------------ health checks


def _df(store: CandleStore, code: str):  # type: ignore[no-untyped-def]
    return store.load(code, Interval.D1, adjusted=False)


def test_check_series_reports_each_issue_kind() -> None:
    cal = sessions(date(2026, 1, 1), 40)
    with CandleStore() as store:
        # clean series
        store.upsert_candles(daily("NSE_OK", cal))
        assert check_series("NSE_OK", _df(store, "NSE_OK"), cal) == []

        # missing 3 sessions in the middle
        gap = daily("NSE_GAP", [d for i, d in enumerate(cal) if i not in (10, 11, 12)])
        store.upsert_candles(gap)
        issues = check_series("NSE_GAP", _df(store, "NSE_GAP"), cal)
        assert [i.kind for i in issues] == [IssueKind.MISSING_SESSIONS]
        assert issues[0].count == 3 and issues[0].on == cal[10]

        # bad OHLC + zero volume on one bar
        bad = daily("NSE_BAD", cal)
        bad[5] = bad[5].model_copy(update={"high": 90.0, "low": 95.0, "volume": 0})
        store.upsert_candles(bad)
        kinds = {i.kind for i in check_series("NSE_BAD", _df(store, "NSE_BAD"), cal)}
        assert kinds == {IssueKind.BAD_OHLC, IssueKind.ZERO_VOLUME}

        # unadjusted 1:1 bonus -> suspected split, and NOT double-counted as a big jump
        store.upsert_candles(with_split(daily("NSE_SPL", cal, start_price=500), cal[20], 0.5))
        issues = check_series("NSE_SPL", _df(store, "NSE_SPL"), cal)
        assert [i.kind for i in issues] == [IssueKind.SUSPECTED_UNADJUSTED]
        assert issues[0].on == cal[20]

        # a genuine crash -> big jump only
        crash = with_split(daily("NSE_CRASH", cal, start_price=500), cal[20], 0.62)  # stays down
        crash[20] = crash[20].model_copy(update={"open": 380.0, "high": 390.0, "low": 300.0})
        store.upsert_candles(crash)
        issues = check_series("NSE_CRASH", _df(store, "NSE_CRASH"), cal)
        assert [i.kind for i in issues] == [IssueKind.BIG_JUMP]

        # stale: last bar 5 sessions before calendar end
        store.upsert_candles(daily("NSE_OLD", cal[:-5]))
        issues = check_series("NSE_OLD", _df(store, "NSE_OLD"), cal)
        assert [i.kind for i in issues] == [IssueKind.STALE] and issues[0].count == 5

        # nothing stored
        assert check_series("NSE_NONE", _df(store, "NSE_NONE"), cal)[0].kind == IssueKind.NO_DATA


def test_run_quality_report_uses_reference_calendar_and_adjustments() -> None:
    """Exercises the adjustment mechanism, so it builds the store in the UNADJUSTED-feed
    configuration (`apply_corporate_actions=True`). `with_split` below injects a genuine
    split break, which the INDstocks feed would never contain - that feed already serves
    adjusted history, which is why the store default is not to re-apply factors."""
    from fractions import Fraction

    from tradedesk.broker.indstocks.models import IndexInstrument, Instrument
    from tradedesk.data.models import ActionKind, CorporateAction

    cal = sessions(date(2026, 1, 1), 40)
    nifty = IndexInstrument(exch="NSE", name="NIFTY 50", security_id="40000001")
    stock = Instrument(
        exch="NSE", segment="E", security_id="1", instrument_name="EQUITY",
        trading_symbol="SPL", symbol_name="SPL", series="EQ",
    )  # fmt: skip
    with CandleStore(apply_corporate_actions=True) as store:
        store.upsert_instruments([nifty, stock])
        store.upsert_candles(daily(nifty.scrip_code, cal, start_price=25000))
        store.upsert_candles(with_split(daily("NSE_1", cal, start_price=500), cal[20], 0.5))
        # Without the corporate action the split is flagged...
        rep = run_quality_report(store, ["NSE_1"], nifty.scrip_code, start=cal[0], end=cal[-1])
        assert rep.by_kind() == {IssueKind.SUSPECTED_UNADJUSTED: 1}
        # ...and once it is recorded, the adjusted series is clean.
        store.upsert_corporate_actions(
            [
                CorporateAction(
                    symbol="SPL",
                    ex_date=cal[20],
                    kind=ActionKind.BONUS,
                    price_factor=Fraction(1, 2),
                )
            ]
        )
        rep = run_quality_report(store, ["NSE_1"], nifty.scrip_code, start=cal[0], end=cal[-1])
        assert rep.issues == [] and rep.codes_checked == 1
        assert rep.to_frame().empty
