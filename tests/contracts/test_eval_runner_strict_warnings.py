"""Day10: eval_runner optional strict-warnings exit code."""

from __future__ import annotations

import sys
from unittest.mock import patch

import pytest


@pytest.fixture()
def _fake_summary() -> dict:
    return {
        "run_id": "test-run",
        "timestamp": "2000-01-01T00:00:00+00:00",
        "total_cases": 1,
        "pass_cases": 0,
        "warning_cases": 1,
        "fail_cases": 0,
        "pass_rate": 0.0,
        "results": [
            {
                "case_id": "stub_warning",
                "status": "warning",
                "fail_reasons": ["relevance_score below threshold: 0.100 < 0.750"],
            }
        ],
        "ci_report": {
            "run_id": "test-run",
            "status": "warning",
            "blockers": [],
        },
    }


def test_strict_warnings_exits_4(_fake_summary: dict) -> None:
    import scripts.eval_runner as eval_runner

    argv = ["eval_runner", "--suite", "ci-gate", "--strict-warnings", "--mode", "replay"]
    with patch.object(sys, "argv", argv):
        with patch("tests.evals.runner.run_evaluation", return_value=_fake_summary):
            assert eval_runner.main() == 4


def test_strict_warnings_not_set_passes_with_warnings(_fake_summary: dict) -> None:
    import scripts.eval_runner as eval_runner

    argv = ["eval_runner", "--suite", "ci-gate", "--mode", "replay"]
    with patch.object(sys, "argv", argv):
        with patch("tests.evals.runner.run_evaluation", return_value=_fake_summary):
            assert eval_runner.main() == 0
