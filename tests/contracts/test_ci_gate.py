"""Day9: CI gate suite must pass under replay + deterministic mode."""

from __future__ import annotations

import os
from pathlib import Path

from tests.evals.runner import run_evaluation


def test_ci_gate_replay_all_pass() -> None:
    root = Path(__file__).resolve().parents[2]
    prev_det = os.environ.get("EVAL_DETERMINISTIC")
    prev_mode = os.environ.get("EVAL_MODE")
    try:
        os.environ["EVAL_DETERMINISTIC"] = "1"
        os.environ["EVAL_MODE"] = "replay"
        summary = run_evaluation(
            project_root=root,
            target=None,
            stage=None,
            case_id=None,
            suite="ci-gate",
            mode="replay",
        )
    finally:
        if prev_det is None:
            os.environ.pop("EVAL_DETERMINISTIC", None)
        else:
            os.environ["EVAL_DETERMINISTIC"] = prev_det
        if prev_mode is None:
            os.environ.pop("EVAL_MODE", None)
        else:
            os.environ["EVAL_MODE"] = prev_mode

    assert int(summary.get("total_cases") or 0) >= 5
    assert int(summary.get("fail_cases") or 0) == 0
    ci = summary.get("ci_report")
    assert isinstance(ci, dict)
    assert ci.get("status") == "passed"
    assert ci.get("blockers") == []
