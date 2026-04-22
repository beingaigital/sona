from __future__ import annotations

from workflow.regression_dashboard import RunSnapshot, build_diff


def test_build_diff_with_previous() -> None:
    prev = RunSnapshot(
        run_id="r1",
        timestamp="t1",
        total_cases=2,
        pass_cases=2,
        warning_cases=0,
        fail_cases=0,
        pass_rate=1.0,
        p95_latency_ms=10.0,
        fallback_rate=0.1,
    )
    cur = RunSnapshot(
        run_id="r2",
        timestamp="t2",
        total_cases=3,
        pass_cases=2,
        warning_cases=0,
        fail_cases=1,
        pass_rate=0.666,
        p95_latency_ms=12.0,
        fallback_rate=0.2,
    )
    diff = build_diff(cur, prev)
    assert diff["has_previous"] is True
    assert diff["delta_fail_cases"] == 1
    assert diff["delta_total_cases"] == 1


def test_build_diff_without_previous() -> None:
    cur = RunSnapshot(
        run_id="r1",
        timestamp="t1",
        total_cases=1,
        pass_cases=1,
        warning_cases=0,
        fail_cases=0,
        pass_rate=1.0,
        p95_latency_ms=0.0,
        fallback_rate=0.0,
    )
    diff = build_diff(cur, None)
    assert diff["has_previous"] is False

