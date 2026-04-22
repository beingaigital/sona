"""Minimal evaluation runner for harness Day 1."""

from __future__ import annotations

import json
import os
import random
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from workflow.contracts import StageResult, ToolError, WorkflowContext, new_context
from workflow.wiki_cli import answer_wiki_query
from workflow.tool_schemas import (
    SchemaError,
    validate_data_collect_output,
    validate_data_num_output,
    validate_weibo_aisearch_output,
)
from tests.evals.scorers import evaluate_case


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
    suite: str
    suite_tags: frozenset[str]


def _utc_now_iso() -> str:
    if _deterministic_enabled():
        return "2000-01-01T00:00:00+00:00"
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _run_id() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")

def _deterministic_enabled() -> bool:
    return os.getenv("EVAL_DETERMINISTIC", "").strip() in {"1", "true", "True", "YES", "yes"}


def _maybe_seed_random() -> None:
    if not _deterministic_enabled():
        return
    random.seed(0)


def _suite_tags_from_raw(raw: dict) -> tuple[str, frozenset[str]]:
    """Primary suite label plus all tags (primary + optional `suites` array)."""
    tags: List[str] = []
    primary = str(raw.get("suite") or "basic").strip()
    if primary:
        tags.append(primary)
    extra = raw.get("suites")
    if isinstance(extra, list):
        for item in extra:
            t = str(item).strip()
            if t and t not in tags:
                tags.append(t)
    if not tags:
        tags = ["basic"]
    return tags[0], frozenset(tags)


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

    suite, suite_tags = _suite_tags_from_raw(raw)

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
        suite=suite,
        suite_tags=suite_tags,
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


def _load_replay_payload(case: EvalCase, project_root: Path) -> Dict[str, Any]:
    if case.fixture_mode != "replay":
        return {}
    if not case.fixture_recorded_tools:
        return {"_replay_error": "Replay fixture path is missing in case.fixtures.recorded_tools"}
    replay_path = project_root / case.fixture_recorded_tools
    if not replay_path.exists():
        return {"_replay_error": f"Replay fixture not found: {replay_path}"}
    return json.loads(replay_path.read_text(encoding="utf-8"))


def _validate_replay_schema(case: EvalCase, payload: Dict[str, Any]) -> Optional[str]:
    """Validate replay payload with tool schema when applicable."""
    if case.target != "tool":
        return None

    stage = str(case.stage or "").strip().lower()
    try:
        if stage == "data_num":
            validate_data_num_output(payload)
        elif stage == "data_collect":
            validate_data_collect_output(payload)
        elif stage == "weibo_aisearch":
            validate_weibo_aisearch_output(payload)
    except SchemaError as exc:
        return f"Replay schema validation failed ({stage}): {exc}"
    return None


def _execute_case(case: EvalCase, project_root: Path) -> Dict[str, Any]:
    """Execute one case. Day1 skeleton keeps implementation deterministic."""
    replay_payload = _load_replay_payload(case, project_root)
    if replay_payload:
        if replay_payload.get("_replay_error"):
            return {"error": replay_payload["_replay_error"]}
        schema_error = _validate_replay_schema(case, replay_payload)
        if schema_error:
            return {"error": schema_error}
        return replay_payload

    if case.target == "wiki":
        query = str(case.input_payload.get("query", "")).strip()
        options = case.input_payload.get("options") if isinstance(case.input_payload.get("options"), dict) else {}
        topk = int(options.get("topk") or 6)
        style = str(options.get("style") or "concise")
        return answer_wiki_query(query=query, topk=topk, style=style, project_root=project_root)

    if case.target == "tool":
        return {
            "status": "warning",
            "message": (
                f"Target 'tool' live execution not wired (stage={case.stage!r}). "
                "Use fixtures.mode=replay and fixtures.recorded_tools for contract checks."
            ),
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
    _maybe_seed_random()
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
        if suite and suite not in case.suite_tags:
            continue

        # Build context for every evaluation case (future-proof for workflow integration).
        ctx: WorkflowContext = new_context(
            run_id=run_id,
            query=str(case.input_payload.get("query", "")),
            mode=case.fixture_mode if case.fixture_mode in ("live", "replay") else "live",
        )
        ctx.diagnostics.update({"case_id": case.case_id, "target": case.target, "stage": case.stage})

        trace_events: List[Dict[str, Any]] = []
        trace_events.append({"ts": _utc_now_iso(), "event": "case_start", "run_id": run_id, "case_id": case.case_id})

        start_ts = time.time()
        output = _execute_case(case, project_root)
        if _deterministic_enabled() and case.fixture_mode == "replay":
            latency_ms = 0
        else:
            latency_ms = int((time.time() - start_ts) * 1000)
        query = str(case.input_payload.get("query", ""))
        status, metrics, reasons = evaluate_case(
            query=query,
            output=output,
            latency_ms=latency_ms,
            expectations_raw=case.expectations,
            project_root=project_root,
        )

        stage_name = str(case.stage or case.target)
        stage_status = "success" if status == "pass" else ("warning" if status == "warning" else "failed")
        ctx.set_stage_result(
            StageResult(
                stage=stage_name,
                status=stage_status,
                metrics={"latency_ms": latency_ms, **metrics},
                artifacts={"output": output},
                error=_to_tool_error(output),
                fallback_used=False,
            )
        )
        ctx.artifacts.update({"output": output})

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
            "suite": case.suite,
            "stage": case.stage,
            "status": status,
            "metrics": metrics,
            "artifacts": {
                "trace": str(trace_path.relative_to(project_root)),
                "output": str(output_path.relative_to(project_root)),
                "context": str((case_root / "context.json").relative_to(project_root)),
            },
            "fail_reasons": reasons,
        }
        _write_json(case_root / "metrics.json", result)
        summaries.append(result)

    total = len(summaries)
    passed = sum(1 for item in summaries if item["status"] == "pass")
    warned = sum(1 for item in summaries if item["status"] == "warning")
    failed = sum(1 for item in summaries if item["status"] == "fail")
    summary = {
        "run_id": run_id,
        "timestamp": _utc_now_iso(),
        "total_cases": total,
        "pass_cases": passed,
        "warning_cases": warned,
        "fail_cases": failed,
        "pass_rate": _safe_div(float(passed), float(total)) if total else 0.0,
        "results": summaries,
    }
    _write_json(run_root / "summary.json", summary)

    blockers: List[Dict[str, Any]]
    if suite and total == 0:
        ci_status = "failed"
        blockers = [
            {
                "case_id": None,
                "target": None,
                "stage": None,
                "fail_reasons": [f"No cases matched suite={suite!r}"],
            }
        ]
    else:
        blockers = [
            {
                "case_id": item["case_id"],
                "target": item["target"],
                "stage": item.get("stage"),
                "fail_reasons": item.get("fail_reasons") or [],
            }
            for item in summaries
            if item.get("status") == "fail"
        ]
        ci_status = "failed" if failed else ("warning" if warned else "passed")
    ci_report: Dict[str, Any] = {
        "run_id": run_id,
        "timestamp": summary["timestamp"],
        "suite_filter": suite,
        "total_cases": total,
        "pass_cases": passed,
        "warning_cases": warned,
        "fail_cases": failed,
        "pass_rate": summary["pass_rate"],
        "status": ci_status,
        "blockers": blockers,
    }
    _write_json(run_root / "ci_report.json", ci_report)
    summary["ci_report"] = ci_report

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

