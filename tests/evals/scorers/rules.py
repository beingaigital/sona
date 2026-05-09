"""Scoring rules for pass/warning/fail aggregation."""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

from tests.evals.scorers.core import compute_metrics

HARD_METRICS: Tuple[str, ...] = (
    "structure_completeness",
    "traceability_score",
    "latency_ms",
)
SOFT_METRICS: Tuple[str, ...] = ("relevance_score",)


def evaluate_case(case: Any, output: Dict[str, Any], latency_ms: int) -> Tuple[str, Dict[str, float], List[str]]:
    """Evaluate one case into status, metrics, and human-readable reasons."""
    expectations = case.expectations if isinstance(case.expectations, dict) else {}
    required_fields = expectations.get("required_fields")
    required_fields = required_fields if isinstance(required_fields, list) else []
    thresholds = expectations.get("thresholds")
    thresholds = thresholds if isinstance(thresholds, dict) else {}

    metrics = compute_metrics(case, output, latency_ms)
    fail_reasons: List[str] = []
    warning_reasons: List[str] = []

    for metric_name in HARD_METRICS:
        threshold = thresholds.get(metric_name)
        if metric_name == "latency_ms":
            max_latency = expectations.get("max_latency_ms", threshold)
            if max_latency is not None and metrics["latency_ms"] > float(max_latency):
                fail_reasons.append(
                    f"latency_ms too high: {metrics['latency_ms']:.0f} > {float(max_latency):.0f}"
                )
            continue
        if threshold is not None and metrics.get(metric_name, 0.0) < float(threshold):
            fail_reasons.append(
                f"{metric_name} below threshold: {metrics.get(metric_name, 0.0):.3f} < {float(threshold):.3f}"
            )

    if required_fields and metrics["structure_completeness"] < 1.0:
        fail_reasons.append("required fields missing from output")

    if not fail_reasons:
        for metric_name in SOFT_METRICS:
            threshold = thresholds.get(metric_name)
            if threshold is None:
                continue
            if metrics.get(metric_name, 0.0) < float(threshold):
                warning_reasons.append(
                    f"{metric_name} below soft threshold: "
                    f"{metrics.get(metric_name, 0.0):.3f} < {float(threshold):.3f}"
                )

    if fail_reasons:
        return "fail", metrics, fail_reasons
    if warning_reasons:
        return "warning", metrics, warning_reasons
    return "pass", metrics, []
