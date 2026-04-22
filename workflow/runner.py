"""Workflow runner: dispatches the event-analysis pipeline (workflow-local orchestration)."""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional

from workflow.budget import estimate_tokens, sentiment_budget_from_env
from workflow.event_analysis_pipeline import run_event_analysis_pipeline


@dataclass(slots=True)
class EventAnalysisRunRequest:
    """Typed orchestration input for the event analysis workflow."""

    user_query: str
    task_id: str
    session_manager: object
    debug: bool = False
    default_threshold: int = 2000
    existing_data_path: Optional[str] = None
    skip_data_collect: bool = False
    force_fresh_start: Optional[bool] = None
    report_length: Optional[str] = None


def _env_flag(name: str, default: str) -> str:
    return str(os.environ.get(name, default)).strip().lower()


def _validate_run_request(request: EventAnalysisRunRequest) -> None:
    """Apply lightweight guardrails before dispatching workflow stages."""
    if not str(request.user_query or "").strip():
        raise ValueError("user_query 不能为空")
    if not str(request.task_id or "").strip():
        raise ValueError("task_id 不能为空")


def _execute_pipeline_stage(request: EventAnalysisRunRequest) -> str:
    """Execute the current full pipeline stage (single-node orchestration)."""
    return run_event_analysis_pipeline(
        user_query=request.user_query,
        task_id=request.task_id,
        session_manager=request.session_manager,
        debug=request.debug,
        default_threshold=request.default_threshold,
        existing_data_path=request.existing_data_path,
        skip_data_collect=request.skip_data_collect,
        force_fresh_start=request.force_fresh_start,
        report_length=request.report_length,
    )


def orchestrate_event_analysis(request: EventAnalysisRunRequest) -> str:
    """Orchestrate workflow stages; keeps `runner.py` as stable composition layer."""
    _validate_run_request(request)
    return _execute_pipeline_stage(request)


def run_event_analysis_workflow(
    *,
    user_query: str,
    task_id: str,
    session_manager: object,
    debug: bool = False,
    default_threshold: int = 2000,
    existing_data_path: Optional[str] = None,
    skip_data_collect: bool = False,
    force_fresh_start: Optional[bool] = None,
    report_length: Optional[str] = None,
) -> str:
    """Backward-compatible workflow entrypoint backed by runner orchestration."""
    request = EventAnalysisRunRequest(
        user_query=user_query,
        task_id=task_id,
        session_manager=session_manager,
        debug=debug,
        default_threshold=default_threshold,
        existing_data_path=existing_data_path,
        skip_data_collect=skip_data_collect,
        force_fresh_start=force_fresh_start,
        report_length=report_length,
    )
    return orchestrate_event_analysis(request)


def run_sentiment_stage(
    *,
    user_query: str,
    search_plan: Dict[str, Any],
    save_path: str,
    debug: bool,
    sentiment_timeout_sec: int,
    analysis_sentiment_tool: Any,
    invoke_tool_with_timeout: Callable[..., Dict[str, Any]],
    fallback_from_csv: Callable[[str], Dict[str, Any]],
    append_log: Callable[..., None],
) -> tuple[Dict[str, Any], float]:
    """Run sentiment stage with legacy-compatible behavior."""
    t0 = time.time()
    budget = sentiment_budget_from_env(sentiment_timeout_sec)
    force_sentiment_rerun = _should_force_sentiment_rerun(user_query)
    force_llm_sentiment = _env_flag("SONA_SENTIMENT_FORCE_LLM", "") in {"1", "true", "yes", "y", "on"}
    prefer_existing_sentiment = _env_flag("SONA_SENTIMENT_PREFER_EXISTING", "") in {"1", "true", "yes", "y", "on"}
    event_intro = str(search_plan.get("eventIntroduction") or "")
    est_tokens = estimate_tokens(event_intro)

    if est_tokens > budget.token_budget:
        budget.trigger("token_over_budget", reason=f"estimated_tokens={est_tokens}")
        clip_chars = max(1200, budget.token_budget * 2)
        event_intro = event_intro[:clip_chars]
        budget.add_action("clip_context", estimated_tokens=est_tokens, clipped_chars=clip_chars)
        prefer_existing_sentiment = True
        budget.add_action("prefer_existing_sentiment_column", reason="token_over_budget")

    prefer_existing_flag = prefer_existing_sentiment and (not force_sentiment_rerun) and (not force_llm_sentiment)
    payload = {
        "eventIntroduction": event_intro,
        "dataFilePath": save_path,
        "preferExistingSentimentColumn": prefer_existing_flag,
    }

    sentiment_json: Dict[str, Any] = {}
    total_attempts = 0
    max_attempts = max(1, budget.retry_budget + 1)
    for attempt in range(1, max_attempts + 1):
        total_attempts = attempt
        sentiment_json = invoke_tool_with_timeout(
            analysis_sentiment_tool,
            payload,
            timeout_sec=sentiment_timeout_sec,
            tool_name="analysis_sentiment",
        )
        if not str(sentiment_json.get("error", "") or "").strip():
            break
        if attempt < max_attempts:
            budget.trigger("retry_used", reason=f"attempt={attempt}")
            budget.add_action("retry_tool_call", attempt=attempt + 1)

    if str(sentiment_json.get("error", "") or "").strip():
        sentiment_json = {
            "error": str(sentiment_json.get("error", "") or "analysis_sentiment 执行失败"),
            "statistics": {},
            "positive_summary": [],
            "negative_summary": [],
            "result_file_path": "",
        }

    if str(sentiment_json.get("error", "") or "").strip() and save_path:
        fallback_json = fallback_from_csv(save_path)
        if not str(fallback_json.get("error", "") or "").strip():
            sentiment_json = fallback_json
            budget.add_action("fallback_from_csv", reason="analysis_sentiment_failed")
            append_log(
                run_id="event_analysis_sentiment",
                hypothesis_id="H36_sentiment_fallback_from_existing_column",
                location="workflow/runner.py:run_sentiment_stage",
                message="analysis_sentiment 失败，已用 CSV 情感列生成兜底统计",
                data={"data_file_path": save_path},
            )
    elif save_path:
        # 健康检查：即便 analysis_sentiment 未报错，也避免“小样本/单边失真”污染报告。
        st = sentiment_json.get("statistics") if isinstance(sentiment_json.get("statistics"), dict) else {}
        source = str(st.get("sentiment_source", "") or "").strip()
        total = int(st.get("total", 0) or 0)
        llm_coverage = float(st.get("llm_coverage", 0.0) or 0.0)
        positive = int(st.get("positive_count", 0) or 0)
        negative = int(st.get("negative_count", 0) or 0)
        neutral = int(st.get("neutral_count", 0) or 0)
        skewed_single_side = (positive == 0 and negative > 0 and total >= 120) or (
            negative == 0 and positive > 0 and total >= 120
        )
        low_effective_coverage = source == "llm_scoring" and total >= 120 and llm_coverage < 0.5
        all_neutral = source == "llm_scoring" and total >= 120 and neutral == total

        if skewed_single_side or low_effective_coverage or all_neutral:
            fallback_json = fallback_from_csv(save_path)
            fallback_error = str(fallback_json.get("error", "") or "").strip()
            fb_st = fallback_json.get("statistics") if isinstance(fallback_json.get("statistics"), dict) else {}
            fb_total = int(fb_st.get("total", 0) or 0)
            if not fallback_error and fb_total >= 50:
                sentiment_json = fallback_json
                budget.add_action(
                    "fallback_from_csv",
                    reason="analysis_sentiment_quality_guard",
                    llm_total=total,
                    llm_coverage=llm_coverage,
                    fallback_total=fb_total,
                )
                append_log(
                    run_id="event_analysis_sentiment",
                    hypothesis_id="H47_sentiment_quality_guard_fallback",
                    location="workflow/runner.py:run_sentiment_stage",
                    message="情感结果触发质量保护，已回退到 CSV 情感列统计",
                    data={
                        "llm_total": total,
                        "llm_coverage": llm_coverage,
                        "positive": positive,
                        "negative": negative,
                        "neutral": neutral,
                        "fallback_total": fb_total,
                    },
                )

    elapsed = round(time.time() - t0, 3)
    elapsed_ms = int(elapsed * 1000)
    if elapsed_ms > budget.latency_budget_ms:
        budget.trigger("latency_over_budget", reason=f"elapsed_ms={elapsed_ms}")
        budget.add_action("latency_observed", elapsed_ms=elapsed_ms)

    budget.add_action(
        "budget_snapshot",
        estimated_tokens=est_tokens,
        prefer_existing_sentiment_column=prefer_existing_flag,
        attempts=total_attempts,
    )
    sentiment_json["_budget_summary"] = budget.finalize()
    return sentiment_json, elapsed


def _should_force_sentiment_rerun(user_query: str) -> bool:
    q = str(user_query or "").strip().lower()
    keys = (
        "重新跑情感",
        "重跑情感",
        "重新分析情感",
        "重算情感",
        "重做情感",
        "rerun sentiment",
        "re-run sentiment",
    )
    return any(k in q for k in keys)
