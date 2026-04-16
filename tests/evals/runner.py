"""Minimal evaluation runner for harness Day 1."""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from workflow.contracts import StageResult, ToolError, WorkflowContext, new_context


HARD_METRICS: Tuple[str, ...] = (
    "structure_completeness",
    "traceability_score",
    "latency_ms",
)


@dataclass(frozen=True)
class EvalCase:
    """Single evaluation case definition."""

    case_id: str
    target: str
    stage: Optional[str]
    input_payload: Dict[str, Any]
    fixture_mode: str
    fixture_recorded_tools: Optional[str]
    expectations: Dict[str, Any]


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _run_id() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def _load_case(path: Path) -> EvalCase:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"Case file must be JSON object: {path}")
    case_id = str(raw.get("id") or path.stem).strip()
    target = str(raw.get("target") or "").strip().lower()
    if target not in {"workflow", "tool", "wiki"}:
        raise ValueError(f"Unsupported target '{target}' in {path}")

    fixture = raw.get("fixtures") if isinstance(raw.get("fixtures"), dict) else {}
    fixture_mode = str(fixture.get("mode") or "live").strip().lower()
    if fixture_mode not in {"live", "replay"}:
        fixture_mode = "live"

    return EvalCase(
        case_id=case_id,
        target=target,
        stage=raw.get("stage"),
        input_payload=raw.get("input") if isinstance(raw.get("input"), dict) else {},
        fixture_mode=fixture_mode,
        fixture_recorded_tools=fixture.get("recorded_tools"),
        expectations=raw.get("expectations")
        if isinstance(raw.get("expectations"), dict)
        else {},
    )


def _iter_cases(
    cases_dir: Path,
    *,
    case_id: Optional[str] = None,
) -> Iterable[Path]:
    candidates = sorted(cases_dir.glob("*.json"))
    if case_id:
        for path in candidates:
            if path.stem == case_id:
                return [path]
        raise FileNotFoundError(f"Case not found: {case_id}")
    return candidates


def _safe_div(numerator: float, denominator: float) -> float:
    if denominator <= 0:
        return 0.0
    return numerator / denominator


def _contains_any_evidence(answer: str, snippets: List[str]) -> float:
    if not answer.strip():
        return 0.0
    statements = [s.strip() for s in answer.replace("；", "。").split("。") if len(s.strip()) >= 8]
    if not statements:
        return 0.0
    supported = 0
    for statement in statements:
        tokens = _keywords(statement)
        hit = any(
            token and len(token) >= 2 and token in snippet
            for token in tokens
            for snippet in snippets
        )
        if hit:
            supported += 1
    return _safe_div(float(supported), float(len(statements)))


def _keywords(text: str) -> List[str]:
    if not text:
        return []
    raw_tokens = re.findall(r"[a-zA-Z0-9]{2,}|[\u4e00-\u9fff]{2,}", text)
    tokens: List[str] = []
    for tok in raw_tokens:
        tok = tok.strip()
        if not tok:
            continue
        tokens.append(tok)
        if re.fullmatch(r"[\u4e00-\u9fff]{4,}", tok):
            # Expand Chinese phrases into small n-grams for better overlap matching.
            max_n = min(4, len(tok))
            for n in range(2, max_n + 1):
                for i in range(0, len(tok) - n + 1):
                    tokens.append(tok[i : i + n])
    # Preserve order, remove duplicates.
    deduped: List[str] = []
    seen = set()
    for token in tokens:
        if token in seen:
            continue
        seen.add(token)
        deduped.append(token)
    return deduped[:40]


def _keyword_overlap(query: str, answer: str) -> float:
    q = set(_keywords(query))
    if not q:
        return 0.0
    a = set(_keywords(answer))
    return _safe_div(float(len(q & a)), float(len(q)))


def _structure_completeness(output: Dict[str, Any], required_fields: List[str]) -> float:
    if not required_fields:
        return 1.0
    hit = sum(1 for field in required_fields if field in output and output[field] not in (None, "", []))
    return _safe_div(float(hit), float(len(required_fields)))


def _load_replay_payload(case: EvalCase, project_root: Path) -> Dict[str, Any]:
    if case.fixture_mode != "replay" or not case.fixture_recorded_tools:
        return {}
    replay_path = project_root / case.fixture_recorded_tools
    if not replay_path.exists():
        return {"_replay_error": f"Replay fixture not found: {replay_path}"}
    return json.loads(replay_path.read_text(encoding="utf-8"))


def _execute_case(case: EvalCase, project_root: Path) -> Dict[str, Any]:
    """Execute one case. Day1 skeleton keeps implementation deterministic."""
    replay_payload = _load_replay_payload(case, project_root)

    if case.target == "wiki":
        if replay_payload.get("_replay_error"):
            return {"answer": "", "sources": [], "error": replay_payload["_replay_error"]}
        if replay_payload:
            return replay_payload
        query = str(case.input_payload.get("query", "")).strip()
        return {
            "answer": f"[stub] wiki answer for: {query}" if query else "[stub] wiki answer",
            "sources": [{"title": "stub_source", "path": "N/A"}],
        }

    return {
        "status": "warning",
        "message": f"Target '{case.target}' execution not wired in Day1. Use replay fixtures or wiki target first.",
    }


def _to_tool_error(output: Dict[str, Any]) -> Optional[ToolError]:
    err = output.get("error")
    if not err:
        return None
    # v0: accept string error only
    if isinstance(err, str):
        return ToolError(error_code="E_RUNTIME", error_message=err, retryable=False)
    if isinstance(err, dict):
        return ToolError(
            error_code=str(err.get("error_code") or "E_RUNTIME"),
            error_message=str(err.get("error_message") or err.get("message") or "unknown error"),
            retryable=bool(err.get("retryable") or False),
            result_file_path=err.get("result_file_path"),
        )
    return ToolError(error_code="E_RUNTIME", error_message=str(err), retryable=False)


def _evaluate_metrics(
    case: EvalCase,
    output: Dict[str, Any],
    latency_ms: int,
) -> Tuple[Dict[str, float], List[str]]:
    expectations = case.expectations
    required_fields = expectations.get("required_fields")
    required_fields = required_fields if isinstance(required_fields, list) else []

    answer = str(output.get("answer", ""))
    query = str(case.input_payload.get("query", ""))
    sources = output.get("sources")
    source_snippets: List[str] = []
    if isinstance(sources, list):
        for item in sources:
            if isinstance(item, dict):
                source_snippets.append(str(item.get("snippet", "")))
                source_snippets.append(str(item.get("title", "")))
            elif isinstance(item, str):
                source_snippets.append(item)

    metrics: Dict[str, float] = {
        "latency_ms": float(latency_ms),
        "traceability_score": _contains_any_evidence(answer, source_snippets),
        "relevance_score": _keyword_overlap(query, answer),
        "structure_completeness": _structure_completeness(output, required_fields),
        "fallback_rate": 0.0,
    }

    thresholds = expectations.get("thresholds")
    thresholds = thresholds if isinstance(thresholds, dict) else {}

    fail_reasons: List[str] = []
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

    # additional hard guard
    if required_fields and metrics["structure_completeness"] < 1.0:
        fail_reasons.append("required fields missing from output")

    return metrics, fail_reasons


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_trace(path: Path, events: List[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(event, ensure_ascii=False) for event in events]
    path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


def run_evaluation(
    *,
    project_root: Path,
    target: Optional[str],
    stage: Optional[str],
    case_id: Optional[str],
    suite: Optional[str],  # reserved for Day2
    mode: Optional[str],
) -> Dict[str, Any]:
    """Run evaluation cases and return summary object."""
    _ = suite  # planned in Day2
    cases_dir = project_root / "tests" / "evals" / "cases"
    if not cases_dir.exists():
        raise FileNotFoundError(f"Cases directory not found: {cases_dir}")

    run_id = _run_id()
    run_root = project_root / "eval_results" / run_id
    summaries: List[Dict[str, Any]] = []

    for case_path in _iter_cases(cases_dir, case_id=case_id):
        case = _load_case(case_path)
        if target and case.target != target:
            continue
        if stage and str(case.stage or "") != stage:
            continue
        if mode and case.fixture_mode != mode:
            continue

        # Build context for every evaluation case (future-proof for workflow integration).
        ctx: WorkflowContext = new_context(
            run_id=run_id,
            query=str(case.input_payload.get("query", "")),
            mode=case.fixture_mode if case.fixture_mode in ("live", "replay") else "live",
        )
        ctx.diagnostics.update({"case_id": case.case_id, "target": case.target, "stage": case.stage})

        trace_events: List[Dict[str, Any]] = []
        start_ts = time.time()
        trace_events.append({"ts": _utc_now_iso(), "event": "case_start", "run_id": run_id, "case_id": case.case_id})

        output = _execute_case(case, project_root)
        latency_ms = int((time.time() - start_ts) * 1000)
        metrics, fail_reasons = _evaluate_metrics(case, output, latency_ms)

        stage_name = str(case.stage or case.target)
        ctx.set_stage_result(
            StageResult(
                stage=stage_name,
                status="success" if not fail_reasons else "failed",
                metrics={"latency_ms": latency_ms, **metrics},
                artifacts={"output": output},
                error=_to_tool_error(output),
                fallback_used=False,
            )
        )
        ctx.artifacts.update({"output": output})

        status = "pass" if not fail_reasons else "fail"
        trace_events.append(
            {
                "ts": _utc_now_iso(),
                "event": "case_end",
                "run_id": run_id,
                "case_id": case.case_id,
                "status": status,
                "latency_ms": latency_ms,
            }
        )

        case_root = run_root / case.case_id
        output_path = case_root / "artifacts" / "output.json"
        trace_path = case_root / "trace.jsonl"

        _write_json(output_path, output)
        # Also persist context snapshot for inspection and future replay hooks.
        _write_json(case_root / "context.json", ctx_to_json(ctx))
        _write_trace(trace_path, trace_events)

        result = {
            "run_id": run_id,
            "case_id": case.case_id,
            "target": case.target,
            "stage": case.stage,
            "status": status,
            "metrics": metrics,
            "artifacts": {
                "trace": str(trace_path.relative_to(project_root)),
                "output": str(output_path.relative_to(project_root)),
                "context": str((case_root / "context.json").relative_to(project_root)),
            },
            "fail_reasons": fail_reasons,
        }
        _write_json(case_root / "metrics.json", result)
        summaries.append(result)

    total = len(summaries)
    passed = sum(1 for item in summaries if item["status"] == "pass")
    summary = {
        "run_id": run_id,
        "timestamp": _utc_now_iso(),
        "total_cases": total,
        "pass_cases": passed,
        "pass_rate": _safe_div(float(passed), float(total)) if total else 0.0,
        "results": summaries,
    }
    _write_json(run_root / "summary.json", summary)
    return summary


def ctx_to_json(ctx: WorkflowContext) -> Dict[str, Any]:
    return {
        "run_id": ctx.run_id,
        "task_id": ctx.task_id,
        "query": ctx.query,
        "mode": ctx.mode,
        "diagnostics": ctx.diagnostics,
        "policy": ctx.policy,
        "artifacts": ctx.artifacts,
        "budget": {
            "token_budget": ctx.budget.token_budget,
            "latency_budget_ms": ctx.budget.latency_budget_ms,
            "retry_budget": ctx.budget.retry_budget,
            "triggers": ctx.budget.triggers,
            "actions": ctx.budget.actions,
        },
        "errors": [
            {
                "error_code": e.error_code,
                "error_message": e.error_message,
                "retryable": e.retryable,
                "result_file_path": e.result_file_path,
            }
            for e in ctx.errors
        ],
        "stage_outputs": {
            k: {
                "stage": v.stage,
                "status": v.status,
                "metrics": v.metrics,
                "artifacts": v.artifacts,
                "fallback_used": v.fallback_used,
                "error": (
                    {
                        "error_code": v.error.error_code,
                        "error_message": v.error.error_message,
                        "retryable": v.error.retryable,
                        "result_file_path": v.error.result_file_path,
                    }
                    if v.error
                    else None
                ),
            }
            for k, v in ctx.stage_outputs.items()
        },
    }

