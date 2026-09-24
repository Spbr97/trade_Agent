import json

import numpy as np
import pandas as pd
import pytest
from tradedesk_lab.aem_benchmark_stats import summarize_timing_null


def _resolved(identifier, net_r, *, day="2026-09-18", target=None):
    if target is None:
        target = net_r > 0
    strict = bool(target and net_r > 0)
    return {
        "event_id": identifier,
        "session_date": day,
        "status": "resolved",
        "net_r": net_r,
        "strict_success": strict,
        "target_hit": target,
        "net_pnl": net_r * 100,
        "label": int(strict),
    }


def _unfilled(identifier, status="no_pullback_fill", *, day="2026-09-18"):
    return {"event_id": identifier, "session_date": day, "status": status}


def _cohorts(*row_lists):
    return pd.DataFrame(
        [
            {**row, "cohort_id": identifier}
            for identifier, rows in enumerate(row_lists)
            for row in rows
        ]
    )


def test_no_fills_stay_in_primary_denominator_and_rank_is_not_a_p_value():
    actual = pd.DataFrame([_resolved("a", 1.0), _resolved("b", -0.8)])
    cohorts = _cohorts(
        [_resolved("a", 1.0), _unfilled("b")],
        [_unfilled("a", "chased"), _unfilled("b", "unsizeable")],
        [_resolved("a", -0.2), _resolved("b", -0.6)],
    )
    report = summarize_timing_null(actual, cohorts)
    assert report["actual"]["mean_net_r_per_attempt"] == pytest.approx(0.1)
    assert report["actual"]["strict_success_rate_per_fill"] == 0.5
    first, second, _ = report["cohorts"]
    assert first["strict_success_rate_per_fill"] == 1.0
    assert first["mean_net_r_per_fill"] == 1.0
    assert first["mean_net_r_per_attempt"] == 0.5
    assert first["fill_rate"] == 0.5 and first["attempts"] == 2
    assert second["mean_net_r_per_attempt"] == 0.0
    assert second["strict_success_rate_per_fill"] is None
    comparison = report["comparison"]
    assert comparison["null_mean_net_r_per_attempt"] == pytest.approx(1 / 30)
    assert comparison["actual_minus_null_mean_net_r"] == pytest.approx(1 / 15)
    assert comparison["null_cohorts_at_least_actual"] == 1
    assert comparison["descriptive_upper_tail_fraction_plus_one"] == 0.5
    assert comparison["is_confirmatory_p_value"] is False
    assert comparison["null_cohort_mean_quantiles"]["p50"] == 0.0
    assert report["session_coverage"]["observed_sessions"] == 1
    assert report["session_coverage"]["zero_call_sessions"] is None
    assert report["eligible_for_live"] is False and report["never_live"] is True
    json.dumps(report, allow_nan=False)


def test_rows_and_cohort_order_do_not_change_report_and_observed_sessions_are_exact():
    rows = [_resolved("a", 0.5), _resolved("b", -1.0, day="2026-09-17")]
    actual = pd.DataFrame(rows)
    cohorts = _cohorts(rows, rows)
    expected = summarize_timing_null(actual, cohorts)
    shuffled = summarize_timing_null(actual.iloc[::-1], cohorts.iloc[::-1])
    assert shuffled == expected
    assert expected["session_coverage"]["attempts_by_session"] == {
        "2026-09-17": 1,
        "2026-09-18": 1,
    }
    assert expected["comparison"]["descriptive_upper_tail_fraction_plus_one"] == 1.0


@pytest.mark.parametrize("mutation", ["missing", "extra", "duplicate", "other_day"])
def test_cohorts_cannot_change_attempt_population(mutation):
    rows = [_resolved("a", 0.5), _resolved("b", -1.0)]
    changed = [dict(row) for row in rows]
    if mutation == "missing":
        changed.pop()
    elif mutation == "extra":
        changed.append(_resolved("c", 0.2))
    elif mutation == "duplicate":
        changed.append(changed[0])
    else:
        changed[0]["session_date"] = "2026-09-17"
    with pytest.raises(ValueError, match="unique|same exact"):
        summarize_timing_null(pd.DataFrame(rows), _cohorts(rows, changed))


@pytest.mark.parametrize(
    "status", [None, "", "not_triggered", "no_executable_bar", "insufficient_execution_horizon"]
)
@pytest.mark.parametrize("where", ["actual", "cohort"])
def test_missing_or_unresolved_status_invalidates_whole_comparison(status, where):
    rows = [_resolved("a", 0.5)]
    actual = pd.DataFrame(rows)
    cohorts = _cohorts(rows)
    target = actual if where == "actual" else cohorts
    target.loc[0, "status"] = status
    with pytest.raises(ValueError, match="status"):
        summarize_timing_null(actual, cohorts)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("net_r", np.nan),
        ("net_r", np.inf),
        ("net_r", True),
        ("net_r", "1.0"),
        ("net_r", -0.5),
        ("strict_success", None),
        ("strict_success", "True"),
        ("strict_success", 1),
        ("target_hit", False),
        ("net_pnl", -10.0),
        ("net_pnl", np.inf),
        ("label", 0),
    ],
)
def test_resolved_fields_must_be_finite_and_consistent(field, value):
    row = _resolved("a", 0.5)
    bad = {**row, field: value}
    with pytest.raises(ValueError):
        summarize_timing_null(pd.DataFrame([bad]), _cohorts([row]))


@pytest.mark.parametrize(
    ("field", "value"),
    [("net_r", 1.0), ("net_pnl", -1.0), ("target_hit", True), ("strict_success", True)],
)
def test_no_fill_cannot_hide_a_contradictory_outcome(field, value):
    row = {**_unfilled("a"), field: value}
    with pytest.raises(ValueError, match="no-fill"):
        summarize_timing_null(pd.DataFrame([row]), _cohorts([_unfilled("a")]))


def test_minimal_resolved_schema_is_supported_but_strict_win_requires_positive_net_r():
    rows = [
        {
            key: value
            for key, value in _resolved("a", 0.5).items()
            if key not in {"target_hit", "net_pnl", "label"}
        }
    ]
    assert summarize_timing_null(pd.DataFrame(rows), _cohorts(rows))["actual"]["fill_rate"] == 1.0
    rows[0]["net_r"] = 0.0
    with pytest.raises(ValueError, match="positive"):
        summarize_timing_null(pd.DataFrame(rows), _cohorts(rows))


@pytest.mark.parametrize("event_id", [None, "", " ", 123])
def test_actual_requires_nonempty_unique_string_identifiers(event_id):
    row = _resolved(event_id, 0.5)
    with pytest.raises(ValueError, match="event_id"):
        summarize_timing_null(pd.DataFrame([row]), _cohorts([row]))


def test_duplicate_actual_events_nontrade_attempts_and_bad_dates_are_rejected():
    row = _resolved("a", 0.5)
    with pytest.raises(ValueError, match="unique"):
        summarize_timing_null(pd.DataFrame([row, row]), _cohorts([row]))
    with pytest.raises(ValueError, match="TRADE"):
        summarize_timing_null(pd.DataFrame([{**row, "decision": "WATCH"}]), _cohorts([row]))
    for day in (None, 123, "bad-date", "2026-09-18T10:00:00"):
        with pytest.raises(ValueError, match="session_date"):
            summarize_timing_null(pd.DataFrame([{**row, "session_date": day}]), _cohorts([row]))


@pytest.mark.parametrize("cohort_id", [None, "", True, 1.5])
def test_cohort_identifiers_must_be_valid(cohort_id):
    row = _resolved("a", 0.5)
    cohorts = _cohorts([row])
    cohorts["cohort_id"] = cohort_id
    with pytest.raises(ValueError, match="cohort_id"):
        summarize_timing_null(pd.DataFrame([row]), cohorts)


def test_empty_inputs_never_invent_a_baseline_or_an_accuracy():
    report = summarize_timing_null(pd.DataFrame(), pd.DataFrame())
    assert report["status"] == "insufficient_data"
    assert report["comparison"] is None and report["actual"] is None
    json.dumps(report, allow_nan=False)
    rows = [_resolved("a", 0.5)]
    report = summarize_timing_null(pd.DataFrame(rows), pd.DataFrame())
    assert report["status"] == "insufficient_data" and report["comparison"] is None
    assert report["actual"]["attempts"] == 1
    with pytest.raises(ValueError, match="no attempts"):
        summarize_timing_null(pd.DataFrame(), _cohorts(rows))


def test_finite_large_inputs_never_emit_nonfinite_json_statistics():
    rows = [
        {
            "event_id": identifier,
            "session_date": "2026-09-18",
            "status": "resolved",
            "net_r": 1e308,
            "strict_success": True,
        }
        for identifier in ("a", "b")
    ]
    report = summarize_timing_null(pd.DataFrame(rows), _cohorts(rows, rows))
    assert report["actual"]["mean_net_r_per_attempt"] == 1e308
    json.dumps(report, allow_nan=False)
    opposite = [{**row, "net_r": -1e308, "strict_success": False} for row in rows]
    with pytest.raises(ValueError, match="gap must remain finite"):
        summarize_timing_null(pd.DataFrame(rows), _cohorts(opposite))
