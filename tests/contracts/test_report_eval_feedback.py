from __future__ import annotations

import json
from pathlib import Path

from tools.report_html import _build_eval_feedback_block, _extract_eval_report_feedback


def test_extract_eval_report_feedback_from_latest_summary(tmp_path: Path) -> None:
    run_dir = tmp_path / "eval_results" / "20260422-120000"
    run_dir.mkdir(parents=True, exist_ok=True)
    summary = {
        "run_id": "20260422-120000",
        "results": [
            {
                "case_id": "workflow_report_006_warning_baseline",
                "stage": "report",
                "status": "warning",
                "fail_reasons": [
                    "placeholder_leakage: hit 2 placeholder markers",
                    "lifecycle_consistency: KPI stage '爆发期' conflicts with body stages ['衰退期']",
                ],
            }
        ],
    }
    (run_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False), encoding="utf-8")

    feedback = _extract_eval_report_feedback(project_root=tmp_path)
    assert feedback.get("has_feedback") is True
    assert feedback.get("run_id") == "20260422-120000"
    reasons = feedback.get("reasons")
    assert isinstance(reasons, list)
    assert any("placeholder_leakage" in str(x) for x in reasons)

    block = _build_eval_feedback_block(feedback)
    assert "上次评测回灌（自动）" in block
    assert "需修复问题" in block
    assert "本次生成硬约束" in block


def test_extract_eval_report_feedback_without_eval_results() -> None:
    feedback = _extract_eval_report_feedback(project_root=None)
    assert feedback.get("has_feedback") is False
    assert _build_eval_feedback_block(feedback) == ""
