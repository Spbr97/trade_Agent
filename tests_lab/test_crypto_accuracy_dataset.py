from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import duckdb
import pytest
from tradedesk_lab import crypto_accuracy_dataset as dataset_module
from tradedesk_lab.artifacts import ROOT
from tradedesk_lab.crypto_accuracy_dataset import (
    DEFAULT_GEOMETRIES,
    CryptoGeometry,
    materialize_crypto_dataset,
    record_universe_observation,
    verify_crypto_dataset,
)


def _unix(value: datetime) -> int:
    return int(value.timestamp())


def _create_db(path: Path) -> None:
    with duckdb.connect(str(path)) as con:
        con.execute(
            "CREATE TABLE candles ("
            "scrip_code VARCHAR, interval VARCHAR, ts BIGINT, open DOUBLE, "
            "high DOUBLE, low DOUBLE, close DOUBLE, volume BIGINT)"
        )


def _insert_daily(
    path: Path,
    code: str,
    start: datetime,
    periods: int,
    *,
    overrides: dict[int, tuple[float, float, float, float, int]] | None = None,
) -> None:
    overrides = overrides or {}
    rows = []
    for offset in range(periods):
        opened_at = start + timedelta(days=offset)
        open_, high, low, close, volume = overrides.get(
            offset, (100.0, 101.0, 99.0, 100.0, 10_000)
        )
        rows.append(
            (code, "1day", _unix(opened_at), open_, high, low, close, volume)
        )
    with duckdb.connect(str(path)) as con:
        con.executemany("INSERT INTO candles VALUES (?, ?, ?, ?, ?, ?, ?, ?)", rows)


def _write_universe_report(
    path: Path,
    *,
    codes: list[str],
    session: str,
    event_sha256: str,
) -> None:
    path.write_text(
        json.dumps(
            {
                "generated_at": f"{session}T07:00:00+05:30",
                "session": session,
                "active_codes": sorted(codes),
                "active_inr_pairs": len(codes),
                "scanned_pairs": len(codes),
                "pairs_with_closed_session": len(codes),
                "fetch_errors": 0,
                "universe_event_sha256": event_sha256,
            }
        ),
        encoding="utf-8",
    )


def _latest_event_sha(output: Path) -> str:
    event = json.loads(
        (output / "universe_observations.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()[-1]
    )
    return str(event["event_sha256"])


def _coverage_by_code(state: dict) -> dict[str, dict]:
    return {row["scrip_code"]: row for row in state["coverage"]}


def _artifact_path(output: Path, state: dict, name: str) -> Path:
    path = Path(state["artifacts"][name]["path"])
    return path if path.is_absolute() else output / path


def _labels(output: Path, state: dict) -> list[dict]:
    path = _artifact_path(output, state, "labels")
    assert path.is_file()
    with duckdb.connect(":memory:") as con:
        frame = con.execute("SELECT * FROM read_parquet(?)", [str(path)]).df()
    assert state["artifacts"]["labels"]["rows"] == len(frame)
    return frame.to_dict(orient="records")


def _daily(output: Path, state: dict) -> list[dict]:
    path = _artifact_path(output, state, "daily")
    with duckdb.connect(":memory:") as con:
        frame = con.execute("SELECT * FROM read_parquet(?)", [str(path)]).df()
    return frame.to_dict(orient="records")


def _reason_text(row: dict) -> str:
    return " ".join(
        str(value)
        for key, value in row.items()
        if key in {"status", "reason", "reasons"}
    )


def _record(rows: list[dict], code: str, decision_session: str, geometry: str) -> dict:
    return next(
        row
        for row in rows
        if row["scrip_code"] == code
        and row["decision_session"] == decision_session
        and row["geometry"] == geometry
    )


def test_default_crypto_geometries_are_bounded_and_named() -> None:
    assert DEFAULT_GEOMETRIES
    assert len({item.name for item in DEFAULT_GEOMETRIES}) == len(DEFAULT_GEOMETRIES)
    assert all(item.stop_atr > 0 for item in DEFAULT_GEOMETRIES)
    assert all(item.target_r > 0 for item in DEFAULT_GEOMETRIES)
    assert all(item.max_hold_sessions > 0 for item in DEFAULT_GEOMETRIES)


def test_activation_after_latest_close_writes_an_empty_forward_label_artifact(
    tmp_path: Path,
) -> None:
    output = tmp_path / "dataset"
    database = tmp_path / "crypto.duckdb"
    report = tmp_path / "universe.json"
    code = "CDX_TESTINR"
    record_universe_observation(
        output,
        [code],
        observed_at=datetime(2026, 2, 1, 6, tzinfo=UTC),
    )
    _create_db(database)
    _insert_daily(database, code, datetime(2025, 12, 1, tzinfo=UTC), 62)
    _write_universe_report(
        report,
        codes=[code],
        session="2026-01-31",
        event_sha256=_latest_event_sha(output),
    )

    state = materialize_crypto_dataset(
        ROOT,
        output,
        database,
        report,
        as_of=datetime(2026, 2, 1, 12, tzinfo=UTC),
    )

    assert state["coverage_summary"]["label_rows"] == 0
    assert _labels(output, state) == []
    assert state["status"] == "not_ready_collecting_point_in_time_history"


def test_universe_observations_are_append_only_and_bound_membership(
    tmp_path: Path,
) -> None:
    output = tmp_path / "dataset"
    database = tmp_path / "crypto.duckdb"
    report = tmp_path / "universe.json"
    first_seen = datetime(2026, 1, 10, tzinfo=UTC)
    transition = datetime(2026, 1, 23, tzinfo=UTC)
    as_of = datetime(2026, 2, 1, 12, tzinfo=UTC)

    record_universe_observation(
        output,
        ["CDX_AINR", "CDX_BINR"],
        observed_at=first_seen,
    )
    record_universe_observation(
        output,
        ["CDX_BINR", "CDX_CINR"],
        inactive_codes=["CDX_AINR"],
        observed_at=transition,
    )

    observations = [
        json.loads(line)
        for line in (output / "universe_observations.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert [row["observed_at"] for row in observations] == [
        first_seen.isoformat(),
        transition.isoformat(),
    ]
    assert observations[0]["active_codes"] == ["CDX_AINR", "CDX_BINR"]
    assert observations[1]["active_codes"] == ["CDX_BINR", "CDX_CINR"]
    assert observations[1]["known_inactive_codes"] == ["CDX_AINR"]

    with pytest.raises(ValueError, match="chronological|append|newer"):
        record_universe_observation(
            output,
            ["CDX_AINR"],
            observed_at=first_seen - timedelta(seconds=1),
        )

    _create_db(database)
    start = datetime(2025, 12, 1, tzinfo=UTC)
    for code in ("CDX_AINR", "CDX_BINR", "CDX_CINR"):
        _insert_daily(database, code, start, 63)
    _write_universe_report(
        report,
        codes=["CDX_BINR", "CDX_CINR"],
        session="2026-01-31",
        event_sha256=_latest_event_sha(output),
    )

    state = materialize_crypto_dataset(
        ROOT,
        output,
        database,
        report,
        as_of=as_of,
    )
    labels = _labels(output, state)

    decisions = {
        code: [
            datetime.fromisoformat(row["decision_at"])
            for row in labels
            if row["scrip_code"] == code and row["point_in_time_eligible"]
        ]
        for code in ("CDX_AINR", "CDX_BINR", "CDX_CINR")
    }
    assert decisions["CDX_AINR"]
    assert min(decisions["CDX_AINR"]) >= first_seen
    assert max(decisions["CDX_AINR"]) < transition
    assert min(decisions["CDX_BINR"]) >= first_seen
    assert min(decisions["CDX_CINR"]) >= transition
    assert any(
        row["membership_status"] == "pre_observation_membership_unknown"
        and not row["point_in_time_eligible"]
        for row in _daily(output, state)
    )
    assert state["membership"]["pre_activation"] == "unknown_not_inferred"
    assert state["status"]
    assert state["eligible_for_live"] is False


def test_all_observed_pairs_have_explicit_missing_stale_or_invalid_ohlcv(
    tmp_path: Path,
) -> None:
    output = tmp_path / "dataset"
    database = tmp_path / "crypto.duckdb"
    report = tmp_path / "universe.json"
    observed_at = datetime(2026, 1, 10, tzinfo=UTC)
    as_of = datetime(2026, 2, 1, 12, tzinfo=UTC)
    codes = [
        "CDX_GOODINR",
        "CDX_MISSINGINR",
        "CDX_STALEINR",
        "CDX_INVALIDINR",
    ]

    record_universe_observation(output, codes, observed_at=observed_at)
    _create_db(database)
    start = datetime(2025, 12, 1, tzinfo=UTC)
    _insert_daily(database, "CDX_GOODINR", start, 63)
    _insert_daily(database, "CDX_STALEINR", start, 58)
    _insert_daily(
        database,
        "CDX_INVALIDINR",
        start,
        63,
        overrides={61: (100.0, 99.0, 98.0, 100.0, 10_000)},
    )
    _write_universe_report(
        report,
        codes=codes,
        session="2026-01-31",
        event_sha256=_latest_event_sha(output),
    )

    state = materialize_crypto_dataset(
        ROOT,
        output,
        database,
        report,
        as_of=as_of,
    )
    coverage = _coverage_by_code(state)

    assert set(coverage) == set(codes)
    assert coverage["CDX_GOODINR"]["status"] == "current"
    assert "no_daily" in _reason_text(coverage["CDX_MISSINGINR"])
    assert "stale" in _reason_text(coverage["CDX_STALEINR"])
    assert coverage["CDX_INVALIDINR"]["invalid_ohlcv_sessions"] == 1
    assert state["coverage_summary"]["materialized_pairs"] == len(codes)
    assert state["coverage_summary"]["no_history_pairs"] == 1
    assert state["coverage_summary"]["stale_pairs"] == 1
    assert state["coverage_summary"]["invalid_ohlcv_sessions"] == 1
    assert _artifact_path(output, state, "daily").is_file()
    assert _artifact_path(output, state, "missing_sessions").is_file()
    assert state["eligible_for_live"] is False


def test_configured_exclusions_are_materialized_but_never_labelled(
    tmp_path: Path,
) -> None:
    output = tmp_path / "dataset"
    database = tmp_path / "crypto.duckdb"
    report = tmp_path / "universe.json"
    included = "CDX_BTCINR"
    excluded = "CDX_USDTINR"
    codes = [included, excluded]
    observed_at = datetime(2025, 12, 1, tzinfo=UTC)
    as_of = datetime(2026, 2, 1, 12, tzinfo=UTC)

    record_universe_observation(output, codes, observed_at=observed_at)
    _create_db(database)
    for code in codes:
        _insert_daily(database, code, observed_at, 62)
    _write_universe_report(
        report,
        codes=codes,
        session="2026-01-31",
        event_sha256=_latest_event_sha(output),
    )

    state = materialize_crypto_dataset(
        ROOT,
        output,
        database,
        report,
        as_of=as_of,
    )
    coverage = _coverage_by_code(state)
    daily = _daily(output, state)
    labels = _labels(output, state)

    assert set(coverage) == set(codes)
    assert coverage[included]["configured_excluded"] is False
    assert coverage[excluded]["configured_excluded"] is True
    assert state["coverage_summary"]["materialized_pairs"] == 2
    assert state["coverage_summary"]["configured_excluded_pairs"] == 1
    assert any(row["scrip_code"] == excluded for row in daily)
    assert all(
        row["configured_excluded"] and not row["point_in_time_eligible"]
        for row in daily
        if row["scrip_code"] == excluded
    )
    assert any(row["scrip_code"] == included for row in labels)
    assert all(row["scrip_code"] != excluded for row in labels)


def test_only_closed_candles_are_hashed_and_frozen_sources_are_verified(
    tmp_path: Path,
) -> None:
    output = tmp_path / "dataset"
    second_output = tmp_path / "dataset-again"
    database = tmp_path / "crypto.duckdb"
    report = tmp_path / "universe.json"
    observed_at = datetime(2026, 1, 10, tzinfo=UTC)
    as_of = datetime(2026, 2, 1, 12, tzinfo=UTC)
    forming = datetime(2026, 2, 1, tzinfo=UTC)

    for destination in (output, second_output):
        record_universe_observation(
            destination,
            ["CDX_TESTINR"],
            observed_at=observed_at,
        )
    _create_db(database)
    _insert_daily(database, "CDX_TESTINR", datetime(2025, 12, 1, tzinfo=UTC), 62)
    _insert_daily(database, "CDX_TESTINR", forming, 2)
    _write_universe_report(
        report,
        codes=["CDX_TESTINR"],
        session="2026-01-31",
        event_sha256=_latest_event_sha(output),
    )

    first = materialize_crypto_dataset(
        ROOT,
        output,
        database,
        report,
        as_of=as_of,
    )
    assert verify_crypto_dataset(output, database)["passed"] is True

    with duckdb.connect(str(database)) as con:
        con.execute(
            "UPDATE candles SET close=777 WHERE scrip_code='CDX_TESTINR' AND ts=?",
            [_unix(forming)],
        )

    assert verify_crypto_dataset(output, database)["passed"] is True
    second = materialize_crypto_dataset(
        ROOT,
        second_output,
        database,
        report,
        as_of=as_of,
    )
    assert second["source_sha256"] == first["source_sha256"]

    closed = datetime(2026, 1, 15, tzinfo=UTC)
    with duckdb.connect(str(database)) as con:
        con.execute(
            "UPDATE candles SET close=101 WHERE scrip_code='CDX_TESTINR' AND ts=?",
            [_unix(closed)],
        )
    failed = verify_crypto_dataset(output, database)
    assert failed["passed"] is False
    assert any("closed source candles changed" in error for error in failed["errors"])


def test_labels_are_deterministic_next_open_gap_aware_and_stop_wins_ties(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    geometry = CryptoGeometry(
        name="one_atr_one_r_two_sessions",
        stop_atr=1.0,
        target_r=1.0,
        max_hold_sessions=2,
    )
    monkeypatch.setattr(dataset_module, "DEFAULT_GEOMETRIES", (geometry,))
    database = tmp_path / "crypto.duckdb"
    report = tmp_path / "universe.json"
    first_output = tmp_path / "first"
    second_output = tmp_path / "second"
    observed_at = datetime(2025, 12, 1, tzinfo=UTC)
    as_of = datetime(2026, 2, 1, 12, tzinfo=UTC)
    start = datetime(2025, 12, 1, tzinfo=UTC)
    decision_offset = (datetime(2026, 1, 20, tzinfo=UTC) - start).days
    event_offset = decision_offset + 2
    codes = [
        "CDX_GAPWININR",
        "CDX_TIEINR",
        "CDX_GAPLOSSINR",
        "CDX_HOLDINR",
    ]

    for destination in (first_output, second_output):
        record_universe_observation(destination, codes, observed_at=observed_at)
    _create_db(database)
    _insert_daily(
        database,
        "CDX_GAPWININR",
        start,
        63,
        # The opening gap proves the target happened before the later low.  Stop-wins-ties
        # applies only when OHLC cannot establish which intrabar touch came first.
        overrides={event_offset: (130.0, 131.0, 90.0, 100.0, 10_000)},
    )
    _insert_daily(
        database,
        "CDX_TIEINR",
        start,
        63,
        overrides={event_offset: (100.0, 105.0, 95.0, 100.0, 10_000)},
    )
    _insert_daily(
        database,
        "CDX_GAPLOSSINR",
        start,
        63,
        overrides={event_offset: (90.0, 91.0, 89.0, 90.0, 10_000)},
    )
    _insert_daily(database, "CDX_HOLDINR", start, 63)
    _write_universe_report(
        report,
        codes=codes,
        session="2026-01-31",
        event_sha256=_latest_event_sha(first_output),
    )

    first = materialize_crypto_dataset(
        ROOT,
        first_output,
        database,
        report,
        as_of=as_of,
        geometries=(geometry,),
    )
    second = materialize_crypto_dataset(
        ROOT,
        second_output,
        database,
        report,
        as_of=as_of,
        geometries=(geometry,),
    )

    first_labels = _labels(first_output, first)
    second_labels = _labels(second_output, second)

    projection = lambda rows: sorted(  # noqa: E731
        (
            row["scrip_code"],
            row["decision_at"],
            row["entry_at"],
            row["geometry"],
            row["label"],
            row["outcome"],
            row["exit_price"],
            row["gross_r"],
            row["net_r"],
            row["after_tax_r"],
        )
        for row in rows
        if row["status"] == "resolved"
    )
    assert projection(first_labels) == projection(second_labels)

    win = _record(first_labels, "CDX_GAPWININR", "2026-01-20", geometry.name)
    tie = _record(first_labels, "CDX_TIEINR", "2026-01-20", geometry.name)
    loss = _record(first_labels, "CDX_GAPLOSSINR", "2026-01-20", geometry.name)
    hold = _record(first_labels, "CDX_HOLDINR", "2026-01-20", geometry.name)

    assert win["entry_session"] == "2026-01-21"
    assert win["label"] == 1 and win["outcome"] == "target"
    assert win["entry_price"] < win["exit_price"] < 130.0
    assert win["exit_at"] == datetime(2026, 1, 22, tzinfo=UTC).isoformat()
    assert win["gross_r"] > 0
    assert win["net_r"] < win["gross_r"]
    assert win["after_tax_r"] < win["gross_r"]

    assert tie["label"] == 0 and tie["outcome"] == "stop"
    assert loss["label"] == 0 and loss["outcome"] == "gap_stop"
    assert loss["exit_at"] == datetime(2026, 1, 22, tzinfo=UTC).isoformat()
    assert loss["exit_price"] == pytest.approx(90.0 * (1 - 0.0005))
    assert loss["gross_r"] < -1.0
    assert hold["outcome"] == "max_hold"
    assert hold["exit_at"] == datetime(2026, 1, 23, tzinfo=UTC).isoformat()
    assert all(
        row[key] is not None
        for row in (win, tie, loss)
        for key in ("gross_r", "net_r", "after_tax_r")
    )
    assert all(row["eligible_for_live"] is False for row in first_labels)
    assert first["eligible_for_live"] is False


def test_duplicate_ist_day_keeps_later_real_bar_and_audits_discarded_filler(
    tmp_path: Path,
) -> None:
    first_output = tmp_path / "first"
    second_output = tmp_path / "second"
    database = tmp_path / "crypto.duckdb"
    report = tmp_path / "universe.json"
    code = "CDX_DUPINR"
    observed_at = datetime(2025, 12, 1, tzinfo=UTC)
    as_of = datetime(2026, 2, 1, 12, tzinfo=UTC)
    real_open = datetime(2026, 1, 15, tzinfo=UTC)
    filler_open = real_open - timedelta(seconds=1)

    for output in (first_output, second_output):
        record_universe_observation(output, [code], observed_at=observed_at)
    _create_db(database)
    _insert_daily(database, code, observed_at, 62)
    with duckdb.connect(str(database)) as con:
        con.execute(
            "UPDATE candles SET open=103,high=107,low=102,close=106,volume=9000 "
            "WHERE scrip_code=? AND ts=?",
            [code, _unix(real_open)],
        )
        con.execute(
            "INSERT INTO candles VALUES (?, '1day', ?, 105, 105, 105, 105, 0)",
            [code, _unix(filler_open)],
        )
    _write_universe_report(
        report,
        codes=[code],
        session="2026-01-31",
        event_sha256=_latest_event_sha(first_output),
    )

    first = materialize_crypto_dataset(
        ROOT,
        first_output,
        database,
        report,
        as_of=as_of,
    )
    daily = [row for row in _daily(first_output, first) if row["session"] == "2026-01-15"]

    assert len(daily) == 1
    assert daily[0]["ts"] == _unix(real_open)
    assert daily[0]["close"] == 106
    assert daily[0]["volume"] == 9000
    duplicate_keys = [key for key in first["artifacts"] if "duplicate" in key]
    assert len(duplicate_keys) == 1
    duplicate_artifact = first["artifacts"][duplicate_keys[0]]
    assert duplicate_artifact["rows"] == 1
    with duckdb.connect(":memory:") as con:
        discarded = con.execute(
            "SELECT * FROM read_parquet(?)",
            [str(_artifact_path(first_output, first, duplicate_keys[0]))],
        ).df()
    assert discarded.iloc[0]["ts"] == _unix(filler_open)
    summary_duplicate_counts = [
        value
        for key, value in first["coverage_summary"].items()
        if "duplicate" in key and isinstance(value, int)
    ]
    assert 1 in summary_duplicate_counts

    # A discarded filler is an audit input, not part of the canonical candle source.
    with duckdb.connect(str(database)) as con:
        con.execute(
            "UPDATE candles SET open=777,high=777,low=777,close=777 "
            "WHERE scrip_code=? AND ts=?",
            [code, _unix(filler_open)],
        )
    assert verify_crypto_dataset(first_output, database)["passed"] is True
    second = materialize_crypto_dataset(
        ROOT,
        second_output,
        database,
        report,
        as_of=as_of,
    )
    assert second["source_sha256"] == first["source_sha256"]


@pytest.mark.parametrize("break_kind", ["missing", "invalid"])
def test_atr_requires_fourteen_contiguous_valid_daily_sessions_after_a_break(
    tmp_path: Path,
    break_kind: str,
) -> None:
    output = tmp_path / break_kind
    database = tmp_path / f"{break_kind}.duckdb"
    report = tmp_path / f"{break_kind}-universe.json"
    code = f"CDX_{break_kind.upper()}INR"
    start = datetime(2026, 1, 1, tzinfo=UTC)
    broken = datetime(2026, 1, 31, tzinfo=UTC)
    as_of = datetime(2026, 3, 17, 12, tzinfo=UTC)
    geometry = CryptoGeometry("atr_recovery", 1.0, 1.0, 1)

    record_universe_observation(output, [code], observed_at=start)
    _create_db(database)
    _insert_daily(database, code, start, 75)
    with duckdb.connect(str(database)) as con:
        if break_kind == "missing":
            con.execute(
                "DELETE FROM candles WHERE scrip_code=? AND ts=?",
                [code, _unix(broken)],
            )
        else:
            con.execute(
                "UPDATE candles SET high=98 WHERE scrip_code=? AND ts=?",
                [code, _unix(broken)],
            )
    _write_universe_report(
        report,
        codes=[code],
        session="2026-03-16",
        event_sha256=_latest_event_sha(output),
    )

    state = materialize_crypto_dataset(
        ROOT,
        output,
        database,
        report,
        as_of=as_of,
        geometries=(geometry,),
    )
    labels = _labels(output, state)

    for offset in range(1, 14):
        decision = _record(
            labels,
            code,
            (broken + timedelta(days=offset)).date().isoformat(),
            geometry.name,
        )
        assert decision["status"] == "excluded"
        assert "atr" in decision["exclusion_reason"]
    recovered = _record(labels, code, "2026-02-14", geometry.name)
    assert recovered["status"] == "resolved"
    assert recovered["exclusion_reason"] is None


@pytest.mark.parametrize("report_hash", ["stale", "missing"])
def test_materializer_rejects_stale_or_missing_universe_event_hash(
    tmp_path: Path,
    report_hash: str,
) -> None:
    output = tmp_path / report_hash
    report = tmp_path / f"{report_hash}.json"
    database = tmp_path / "unused.duckdb"
    first = record_universe_observation(
        output,
        ["CDX_AINR"],
        observed_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    record_universe_observation(
        output,
        ["CDX_AINR", "CDX_BINR"],
        observed_at=datetime(2026, 1, 2, tzinfo=UTC),
    )
    _write_universe_report(
        report,
        codes=["CDX_AINR", "CDX_BINR"],
        session="2026-01-02",
        event_sha256=first["event_sha256"],
    )
    if report_hash == "missing":
        payload = json.loads(report.read_text(encoding="utf-8"))
        payload.pop("universe_event_sha256")
        report.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="universe|observation|hash"):
        materialize_crypto_dataset(
            ROOT,
            output,
            database,
            report,
            as_of=datetime(2026, 1, 3, tzinfo=UTC),
        )


def test_removed_pair_with_stale_history_does_not_block_current_readiness(
    tmp_path: Path,
) -> None:
    output = tmp_path / "dataset"
    database = tmp_path / "crypto.duckdb"
    report = tmp_path / "universe.json"
    active = "CDX_ACTIVEINR"
    removed = "CDX_DELISTEDINR"
    start = datetime(2025, 12, 1, tzinfo=UTC)
    removed_at = datetime(2026, 1, 20, tzinfo=UTC)
    as_of = datetime(2026, 2, 15, 12, tzinfo=UTC)

    record_universe_observation(output, [active, removed], observed_at=start)
    record_universe_observation(
        output,
        [active],
        inactive_codes=[removed],
        observed_at=removed_at,
    )
    _create_db(database)
    _insert_daily(database, active, start, 76)
    _insert_daily(database, removed, start, 50)
    _write_universe_report(
        report,
        codes=[active],
        session="2026-02-14",
        event_sha256=_latest_event_sha(output),
    )

    state = materialize_crypto_dataset(
        ROOT,
        output,
        database,
        report,
        as_of=as_of,
    )
    coverage = _coverage_by_code(state)

    assert coverage[removed]["stale_trailing_sessions"] > 0
    assert state["membership"]["current_active_pairs"] == 1
    assert state["membership"]["ever_observed_active_pairs"] == 2
    assert state["status"] == "dataset_ready_research_only"


def test_readiness_requires_minimum_history_for_every_current_eligible_pair(
    tmp_path: Path,
) -> None:
    output = tmp_path / "dataset"
    database = tmp_path / "crypto.duckdb"
    report = tmp_path / "universe.json"
    mature = "CDX_MATUREINR"
    new = "CDX_NEWINR"
    start = datetime(2026, 1, 1, tzinfo=UTC)
    as_of = datetime(2026, 2, 15, 12, tzinfo=UTC)

    record_universe_observation(output, [mature, new], observed_at=start)
    _create_db(database)
    _insert_daily(database, mature, start, 45)
    _insert_daily(database, new, datetime(2026, 2, 14, tzinfo=UTC), 1)
    _write_universe_report(
        report,
        codes=[mature, new],
        session="2026-02-14",
        event_sha256=_latest_event_sha(output),
    )

    state = materialize_crypto_dataset(
        ROOT,
        output,
        database,
        report,
        as_of=as_of,
    )

    assert state["membership"]["point_in_time_sessions"] >= 30
    assert state["membership"]["minimum_pair_point_in_time_sessions"] == 1
    assert state["membership"]["pairs_meeting_minimum_sessions"] == 1
    assert state["membership"]["required_active_pairs"] == 2
    assert state["status"] == "not_ready_collecting_point_in_time_history"


def test_interrupted_build_never_publishes_partial_run_and_retry_succeeds(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "dataset"
    database = tmp_path / "crypto.duckdb"
    report = tmp_path / "universe.json"
    code = "CDX_ATOMICINR"
    start = datetime(2026, 1, 1, tzinfo=UTC)
    as_of = datetime(2026, 2, 15, 12, tzinfo=UTC)

    record_universe_observation(output, [code], observed_at=start)
    _create_db(database)
    _insert_daily(database, code, start, 45)
    _write_universe_report(
        report,
        codes=[code],
        session="2026-02-14",
        event_sha256=_latest_event_sha(output),
    )

    original_save = dataset_module._LabelWriter.save

    def fail_save(_writer, _path) -> None:
        raise RuntimeError("simulated interrupted publication")

    monkeypatch.setattr(dataset_module._LabelWriter, "save", fail_save)
    with pytest.raises(RuntimeError, match="interrupted"):
        materialize_crypto_dataset(
            ROOT,
            output,
            database,
            report,
            as_of=as_of,
        )

    runs = output / "runs"
    assert runs.is_dir()
    assert list(runs.iterdir()) == []

    monkeypatch.setattr(dataset_module._LabelWriter, "save", original_save)
    state = materialize_crypto_dataset(
        ROOT,
        output,
        database,
        report,
        as_of=as_of,
    )
    assert (runs / state["id"] / "manifest.json").is_file()
    assert verify_crypto_dataset(output, database)["passed"] is True
