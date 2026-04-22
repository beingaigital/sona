"""Runtime harness for online workflow supervision and scoring."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List


def _now_iso() -> str:
    return datetime.now().isoformat(sep=" ")


@dataclass
class RuntimeHarness:
    """Collect runtime events and emit scorecard for one workflow run."""

    task_id: str
    process_dir: Path
    user_query: str
    events: List[Dict[str, Any]] = field(default_factory=list)

    def record(self, event_type: str, details: Dict[str, Any]) -> None:
        """Append one structured runtime event."""
        self.events.append(
            {
                "ts": _now_iso(),
                "event_type": str(event_type or "").strip(),
                "details": details if isinstance(details, dict) else {},
            }
        )

    def _score_interaction_guard(self) -> Dict[str, Any]:
        first_decisions = [e for e in self.events if e.get("event_type") == "collect_plan_first_decision"]
        outcomes = [e for e in self.events if e.get("event_type") == "collect_plan_outcome"]
        if not first_decisions:
            return {
                "name": "collect_plan_interaction_guard",
                "status": "warning",
                "reason": "missing_interaction_event",
            }
        decision = str(first_decisions[-1].get("details", {}).get("decision", "")).strip().lower()
        outcome = str(outcomes[-1].get("details", {}).get("outcome", "")).strip().lower() if outcomes else ""
        if decision == "reject" and outcome in {"aborted", "edited_then_accept"}:
            return {"name": "collect_plan_interaction_guard", "status": "pass", "reason": "reject_respected"}
        if decision == "reject":
            return {"name": "collect_plan_interaction_guard", "status": "fail", "reason": "reject_not_respected"}
        return {"name": "collect_plan_interaction_guard", "status": "pass", "reason": "accepted_by_user"}

    def _score_sentiment_health(self) -> Dict[str, Any]:
        events = [e for e in self.events if e.get("event_type") == "sentiment_quality"]
        if not events:
            return {"name": "sentiment_quality_guard", "status": "warning", "reason": "missing_sentiment_event"}
        d = events[-1].get("details", {})
        total = int(d.get("total", 0) or 0)
        positive = int(d.get("positive_count", 0) or 0)
        negative = int(d.get("negative_count", 0) or 0)
        fallback_used = bool(d.get("fallback_used", False))
        skipped = bool(d.get("skipped", False))
        if skipped:
            return {"name": "sentiment_quality_guard", "status": "warning", "reason": "sentiment_skipped"}
        if total >= 120 and positive == 0 and negative > 0 and not fallback_used:
            return {"name": "sentiment_quality_guard", "status": "fail", "reason": "single_side_distribution_without_fallback"}
        return {"name": "sentiment_quality_guard", "status": "pass", "reason": "distribution_or_fallback_ok"}

    def _score_reference_recall(self) -> Dict[str, Any]:
        events = [e for e in self.events if e.get("event_type") == "reference_recall_quality"]
        if not events:
            return {"name": "reference_recall_quality", "status": "warning", "reason": "missing_reference_event"}
        d = events[-1].get("details", {})
        filtered_count = int(d.get("filtered_count", 0) or 0)
        dropped = int(d.get("dropped_count", 0) or 0)
        if filtered_count <= 0:
            return {"name": "reference_recall_quality", "status": "fail", "reason": "no_relevant_reference_after_filter"}
        if dropped > 0 and filtered_count <= 2:
            return {"name": "reference_recall_quality", "status": "warning", "reason": "recall_sparse_after_filter"}
        return {"name": "reference_recall_quality", "status": "pass", "reason": "recall_has_relevant_hits"}

    def _score_topic_relevance(self) -> Dict[str, Any]:
        events = [e for e in self.events if e.get("event_type") == "topic_relevance_quality"]
        if not events:
            return {"name": "topic_relevance_quality", "status": "warning", "reason": "missing_topic_relevance_event"}
        d = events[-1].get("details", {})
        coverage = float(d.get("coverage", 0.0) or 0.0)
        min_coverage = float(d.get("min_coverage", 0.12) or 0.12)
        overlap_count = int(d.get("overlap_count", 0) or 0)
        overrides = [e for e in self.events if e.get("event_type") == "topic_relevance_override"]
        if coverage < min_coverage:
            if overrides and bool(overrides[-1].get("details", {}).get("continued", False)):
                return {"name": "topic_relevance_quality", "status": "warning", "reason": "topic_drift_user_overrode"}
            return {"name": "topic_relevance_quality", "status": "fail", "reason": "topic_drift_detected"}
        if overlap_count <= 1:
            return {"name": "topic_relevance_quality", "status": "warning", "reason": "topic_overlap_too_sparse"}
        return {"name": "topic_relevance_quality", "status": "pass", "reason": "topic_alignment_ok"}

    def finalize(self) -> Dict[str, Any]:
        """Write trace + scorecard and return scorecard payload."""
        checks = [
            self._score_interaction_guard(),
            self._score_sentiment_health(),
            self._score_reference_recall(),
            self._score_topic_relevance(),
        ]
        failed = [c for c in checks if c.get("status") == "fail"]
        warned = [c for c in checks if c.get("status") == "warning"]
        status = "failed" if failed else ("warning" if warned else "passed")
        scorecard = {
            "task_id": self.task_id,
            "query": self.user_query,
            "created_at": _now_iso(),
            "status": status,
            "checks": checks,
            "event_count": len(self.events),
            "suggestions": [
                "若交互拒绝被忽略，请检查 collect_plan 分支是否存在非交互默认放行。",
                "若情感结果单边失真，优先启用/检查 CSV 情感列 fallback 与样本覆盖率。",
                "若参考检索跑题，收紧 query 构造并提高词项重合过滤阈值。",
                "若主题偏航，请提高 topic_relevance_guard 阈值并收紧 searchWords/queryTemplates。",
            ],
        }
        trace_path = self.process_dir / "runtime_harness_trace.json"
        scorecard_path = self.process_dir / "runtime_harness_scorecard.json"
        self.process_dir.mkdir(parents=True, exist_ok=True)
        trace_path.write_text(json.dumps({"events": self.events}, ensure_ascii=False, indent=2), encoding="utf-8")
        scorecard_path.write_text(json.dumps(scorecard, ensure_ascii=False, indent=2), encoding="utf-8")
        return scorecard
