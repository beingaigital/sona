"""Regression dashboard aggregation helpers (Day8)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass(slots=True)
class RunSnapshot:
    run_id: str
    timestamp: str
    total_cases: int
    pass_cases: int
    warning_cases: int
    fail_cases: int
    pass_rate: float
    p95_latency_ms: float
    fallback_rate: float

    def to_dict(self) -> Dict[str, Any]:
        return {
            "run_id": self.run_id,
            "timestamp": self.timestamp,
            "total_cases": self.total_cases,
            "pass_cases": self.pass_cases,
            "warning_cases": self.warning_cases,
            "fail_cases": self.fail_cases,
            "pass_rate": self.pass_rate,
            "p95_latency_ms": self.p95_latency_ms,
            "fallback_rate": self.fallback_rate,
        }


def _percentile(sorted_values: List[float], p: float) -> float:
    if not sorted_values:
        return 0.0
    if len(sorted_values) == 1:
        return float(sorted_values[0])
    p = max(0.0, min(100.0, p))
    rank = int(round((p / 100.0) * (len(sorted_values) - 1)))
    return float(sorted_values[rank])


def _to_snapshot(summary: Dict[str, Any]) -> RunSnapshot:
    results = summary.get("results") if isinstance(summary.get("results"), list) else []
    latencies: List[float] = []
    fallback_rates: List[float] = []
    for item in results:
        if not isinstance(item, dict):
            continue
        metrics = item.get("metrics") if isinstance(item.get("metrics"), dict) else {}
        try:
            latencies.append(float(metrics.get("latency_ms", 0.0) or 0.0))
        except Exception:
            latencies.append(0.0)
        try:
            fallback_rates.append(float(metrics.get("fallback_rate", 0.0) or 0.0))
        except Exception:
            fallback_rates.append(0.0)
    latencies.sort()
    p95 = _percentile(latencies, 95.0)
    fallback_avg = (sum(fallback_rates) / len(fallback_rates)) if fallback_rates else 0.0
    return RunSnapshot(
        run_id=str(summary.get("run_id") or ""),
        timestamp=str(summary.get("timestamp") or ""),
        total_cases=int(summary.get("total_cases") or 0),
        pass_cases=int(summary.get("pass_cases") or 0),
        warning_cases=int(summary.get("warning_cases") or 0),
        fail_cases=int(summary.get("fail_cases") or 0),
        pass_rate=float(summary.get("pass_rate") or 0.0),
        p95_latency_ms=p95,
        fallback_rate=fallback_avg,
    )


def collect_run_summaries(eval_root: Path) -> List[RunSnapshot]:
    snapshots: List[RunSnapshot] = []
    for path in sorted(eval_root.glob("*/summary.json")):
        try:
            summary = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(summary, dict):
                snapshots.append(_to_snapshot(summary))
        except Exception:
            continue
    snapshots.sort(key=lambda x: x.run_id)
    return snapshots


def build_diff(current: RunSnapshot, previous: Optional[RunSnapshot]) -> Dict[str, Any]:
    if previous is None:
        return {
            "has_previous": False,
            "current_run_id": current.run_id,
            "message": "No previous run to compare.",
        }
    return {
        "has_previous": True,
        "current_run_id": current.run_id,
        "previous_run_id": previous.run_id,
        "delta_pass_rate": round(current.pass_rate - previous.pass_rate, 6),
        "delta_p95_latency_ms": round(current.p95_latency_ms - previous.p95_latency_ms, 3),
        "delta_fallback_rate": round(current.fallback_rate - previous.fallback_rate, 6),
        "delta_fail_cases": current.fail_cases - previous.fail_cases,
        "delta_total_cases": current.total_cases - previous.total_cases,
    }


def render_markdown(history: List[RunSnapshot], diff: Dict[str, Any]) -> str:
    lines: List[str] = []
    lines.append("# Eval Regression Dashboard")
    lines.append("")
    lines.append("## Recent Runs")
    lines.append("")
    lines.append("| run_id | timestamp | pass_rate | p95_latency_ms | fallback_rate | fail/total |")
    lines.append("|---|---|---:|---:|---:|---:|")
    for item in list(reversed(history[-20:])):
        lines.append(
            f"| {item.run_id} | {item.timestamp} | {item.pass_rate:.3f} | "
            f"{item.p95_latency_ms:.1f} | {item.fallback_rate:.3f} | {item.fail_cases}/{item.total_cases} |"
        )
    lines.append("")
    lines.append("## Latest vs Previous")
    lines.append("")
    if not diff.get("has_previous"):
        lines.append("- No previous run to compare.")
    else:
        lines.append(f"- current: `{diff.get('current_run_id')}`")
        lines.append(f"- previous: `{diff.get('previous_run_id')}`")
        lines.append(f"- delta_pass_rate: `{diff.get('delta_pass_rate')}`")
        lines.append(f"- delta_p95_latency_ms: `{diff.get('delta_p95_latency_ms')}`")
        lines.append(f"- delta_fallback_rate: `{diff.get('delta_fallback_rate')}`")
        lines.append(f"- delta_fail_cases: `{diff.get('delta_fail_cases')}`")
    lines.append("")
    return "\n".join(lines)

