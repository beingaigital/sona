"""Minimal evaluation runner for harness Day 1."""

from __future__ import annotations

import json
import os
import random
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from tests.evals.scorers import evaluate_case
from tests.evals.scorers.core import safe_div
from workflow.contracts import StageResult, ToolError, WorkflowContext, new_context

@dataclass(frozen=True)
class EvalCase:
    """Single evaluation case definition."""

    case_id: str
    target: str
    stage: Optional[str]
    suite: Optional[str]
    input_payload: Dict[str, Any]
    fixture_mode: str
    fixture_recorded_tools: Optional[str]
    expectations: Dict[str, Any]
    extra_suites: tuple[str, ...] = field(default_factory=tuple)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _run_id() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def _as_bool(value: Optional[str]) -> bool:
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _as_int(value: Optional[str], default: int) -> int:
    try:
        return int(str(value).strip())
    except Exception:
        return default


def _fixed_timestamp(enabled: bool) -> str:
    if enabled:
        return "2026-01-01T00:00:00+00:00"
    return _utc_now_iso()


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

    suites_raw = raw.get("suites")
    extra: tuple[str, ...] = tuple()
    if isinstance(suites_raw, list):
        extra = tuple(str(x).strip().lower() for x in suites_raw if str(x).strip())

    return EvalCase(
        case_id=case_id,
        target=target,
        stage=raw.get("stage"),
        suite=str(raw.get("suite") or "").strip() or None,
        input_payload=raw.get("input") if isinstance(raw.get("input"), dict) else {},
        fixture_mode=fixture_mode,
        fixture_recorded_tools=fixture.get("recorded_tools"),
        expectations=raw.get("expectations")
        if isinstance(raw.get("expectations"), dict)
        else {},
        extra_suites=extra,
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


def _load_replay_payload(case: EvalCase, project_root: Path) -> Dict[str, Any]:
    if case.fixture_mode != "replay":
        return {}
    if not case.fixture_recorded_tools:
        return {
            "status": "fail",
            "message": "Replay fixture path missing: fixtures.recorded_tools is required in replay mode.",
            "error": {
                "error_code": "E_REPLAY_FIXTURE_MISSING_PATH",
                "error_message": "fixtures.recorded_tools is required for replay mode",
                "retryable": False,
            },
        }
    replay_path = project_root / case.fixture_recorded_tools
    if not replay_path.exists():
        return {
            "status": "fail",
            "message": f"Replay fixture not found: {replay_path}",
            "error": {
                "error_code": "E_REPLAY_FIXTURE_NOT_FOUND",
                "error_message": f"Replay fixture not found: {replay_path}",
                "retryable": False,
            },
        }
    try:
        return json.loads(replay_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {
            "status": "fail",
            "message": f"Replay fixture invalid JSON: {replay_path}",
            "error": {
                "error_code": "E_REPLAY_FIXTURE_INVALID_JSON",
                "error_message": f"Replay fixture invalid JSON: {exc}",
                "retryable": False,
            },
        }


def _execute_case(case: EvalCase, project_root: Path) -> Dict[str, Any]:
    """Execute one case. Day1 skeleton keeps implementation deterministic."""
    replay_payload = _load_replay_payload(case, project_root)
    if case.fixture_mode == "replay":
        return replay_payload

    if case.target == "wiki":
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


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_trace(path: Path, events: List[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(event, ensure_ascii=False) for event in events]
    path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


def _matches_suite(case: EvalCase, suite: Optional[str]) -> bool:
    if not suite:
        return True
    normalized_suite = suite.strip().lower()
    case_suite = (case.suite or "").strip().lower()
    if case_suite == normalized_suite or case.case_id.lower().startswith(normalized_suite):
        return True
    return normalized_suite in case.extra_suites


def run_evaluation(
    *,
    project_root: Path,
    target: Optional[str],
    stage: Optional[str],
    case_id: Optional[str],
    suite: Optional[str],
    mode: Optional[str],
    run_id: Optional[str] = None,
    deterministic: bool = False,
) -> Dict[str, Any]:
    """Run evaluation cases and return summary object."""
    cases_dir = project_root / "tests" / "evals" / "cases"
    if not cases_dir.exists():
        raise FileNotFoundError(f"Cases directory not found: {cases_dir}")

    env_mode = (os.environ.get("EVAL_MODE") or "").strip().lower()
    resolved_mode = mode or (env_mode if env_mode in {"live", "replay"} else None)
    deterministic = bool(deterministic or _as_bool(os.environ.get("EVAL_DETERMINISTIC")))
    if deterministic:
        random.seed(0)

    active_run_id = run_id or _run_id()
    run_root = project_root / "eval_results" / active_run_id
    summaries: List[Dict[str, Any]] = []
    budget_max_latency_ms = _as_int(os.environ.get("EVAL_BUDGET_MAX_LATENCY_MS"), 30000)
    budget_max_fallback_rate = float(os.environ.get("EVAL_BUDGET_MAX_FALLBACK_RATE", "0.20") or 0.20)
    budget_trigger_count = 0
    budget_actions: List[Dict[str, Any]] = []

    for case_path in _iter_cases(cases_dir, case_id=case_id):
        case = _load_case(case_path)
        if target and case.target != target:
            continue
        if stage and str(case.stage or "") != stage:
            continue
        if resolved_mode and case.fixture_mode != resolved_mode:
            continue
        if not _matches_suite(case, suite):
            continue

        # Build context for every evaluation case (future-proof for workflow integration).
        ctx: WorkflowContext = new_context(
            run_id=active_run_id,
            query=str(case.input_payload.get("query", "")),
            mode=case.fixture_mode if case.fixture_mode in ("live", "replay") else "live",
        )
        ctx.diagnostics.update({"case_id": case.case_id, "target": case.target, "stage": case.stage})

        trace_events: List[Dict[str, Any]] = []
        start_ts = time.time()
        trace_events.append(
            {"ts": _fixed_timestamp(deterministic), "event": "case_start", "run_id": active_run_id, "case_id": case.case_id}
        )

        output = _execute_case(case, project_root)
        latency_ms = 0 if case.fixture_mode == "replay" else int((time.time() - start_ts) * 1000)
        if latency_ms > budget_max_latency_ms:
            budget_trigger_count += 1
            budget_actions.append(
                {
                    "case_id": case.case_id,
                    "action": "latency_budget_exceeded",
                    "threshold_ms": budget_max_latency_ms,
                    "actual_ms": latency_ms,
                }
            )
        status, metrics, fail_reasons = evaluate_case(case, output, latency_ms)

        stage_name = str(case.stage or case.target)
        ctx.set_stage_result(
            StageResult(
                stage=stage_name,
                status="failed" if status == "fail" else "success",
                metrics={"latency_ms": latency_ms, **metrics},
                artifacts={"output": output},
                error=_to_tool_error(output),
                fallback_used=False,
            )
        )
        ctx.artifacts.update({"output": output})
        ctx.budget.latency_budget_ms = budget_max_latency_ms
        ctx.budget.triggers["latency_budget_exceeded"] = budget_trigger_count
        ctx.budget.actions = budget_actions[-20:]

        trace_events.append(
            {
                "ts": _fixed_timestamp(deterministic),
                "event": "case_end",
                "run_id": active_run_id,
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
            "run_id": active_run_id,
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
            "reasons": fail_reasons,
            "fail_reasons": fail_reasons,
        }
        _write_json(case_root / "metrics.json", result)
        summaries.append(result)

    total = len(summaries)
    passed = sum(1 for item in summaries if item["status"] == "pass")
    warned = sum(1 for item in summaries if item["status"] == "warning")
    failed = sum(1 for item in summaries if item["status"] == "fail")
    blockers: List[str] = []
    for item in summaries:
        if item["status"] == "fail":
            blockers.extend(item.get("fail_reasons") or [])
    ci_status = "passed" if failed == 0 else "failed"
    summary = {
        "run_id": active_run_id,
        "timestamp": _fixed_timestamp(deterministic),
        "total_cases": total,
        "pass_cases": passed,
        "warning_cases": warned,
        "fail_cases": failed,
        "pass_rate": safe_div(float(passed), float(total)) if total else 0.0,
        "fallback_rate": safe_div(float(warned), float(total)) if total else 0.0,
        "budget_summary": {
            "max_latency_ms": budget_max_latency_ms,
            "max_fallback_rate": budget_max_fallback_rate,
            "trigger_count": budget_trigger_count,
            "actions": budget_actions[-50:],
            "fallback_rate_exceeded": (safe_div(float(warned), float(total)) if total else 0.0) > budget_max_fallback_rate,
        },
        "results": summaries,
        "ci_report": {
            "run_id": active_run_id,
            "status": ci_status,
            "blockers": blockers,
        },
    }
    _write_json(run_root / "summary.json", summary)
    _write_json(
        run_root / "cost_breakdown.json",
        {
            "run_id": active_run_id,
            "total_cases": total,
            "avg_latency_ms": safe_div(
                float(sum((item.get("metrics") or {}).get("latency_ms", 0) for item in summaries)),
                float(total),
            )
            if total
            else 0.0,
            "fallback_rate": safe_div(float(warned), float(total)) if total else 0.0,
            "budget_summary": summary["budget_summary"],
        },
    )
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

