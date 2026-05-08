"""Run event analysis workflow for API requests (sync, same path as Streamlit event mode)."""

from __future__ import annotations

import asyncio
import os
import sys

from cli.event_analysis_workflow import run_event_analysis_workflow
from cli.router import route_query
from utils.path import ensure_task_dirs, get_task_dir
from utils.session_manager import get_session_manager

from api.report_utils import extract_report_html_path
from api.schema import (
    ERROR_WORKFLOW,
    AnalyzeEventRequest,
    ApiError,
    TaskArtifacts,
    TaskEnvelope,
    TaskStatus,
)


def run_analyze_event(body: AnalyzeEventRequest) -> TaskEnvelope:
    """
    Create a session/task, run router + event pipeline, return standard envelope.

    Blocking call: suitable for uvicorn worker threadpool or single-user demo.
    """
    if sys.platform.startswith("win"):
        try:
            asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
        except Exception:
            pass

    manager = get_session_manager()
    label = body.query.strip()[:200]
    task_id = manager.create_session(label or "API analyze-event")
    ensure_task_dirs(task_id)

    prev_collab = os.environ.get("SONA_EVENT_COLLAB_MODE")
    os.environ["SONA_EVENT_COLLAB_MODE"] = "auto" if body.disable_blocking_prompts else "hybrid"
    try:
        try:
            _decision, route_data = route_query(body.query, task_id)
            data_result = route_data.get("data_result")
            existing_data_path = None
            skip_data_collect = False
            if (
                body.prefer_existing_data
                and data_result
                and getattr(data_result, "has_data", False)
                and getattr(data_result, "data_paths", None)
            ):
                existing_data_path = data_result.data_paths[0]
                skip_data_collect = True

            run_event_analysis_workflow(
                body.query,
                task_id,
                manager,
                debug=True,
                existing_data_path=existing_data_path,
                skip_data_collect=skip_data_collect,
            )
        except Exception as exc:  # noqa: BLE001 — surface to API client
            return TaskEnvelope(
                task_id=task_id,
                status=TaskStatus.FAILED,
                artifacts=TaskArtifacts(sandbox_dir=str(get_task_dir(task_id))),
                error=ApiError(error_code=ERROR_WORKFLOW, error_message=str(exc)),
            )

        session_data = manager.load_session(task_id) or {}
        report_path = extract_report_html_path(session_data)
        stm_file = manager.stm_dir / f"{task_id}.json"
        artifacts = TaskArtifacts(
            report_path=report_path,
            sandbox_dir=str(get_task_dir(task_id)),
            session_hint=str(stm_file),
        )
        return TaskEnvelope(
            task_id=task_id,
            status=TaskStatus.SUCCEEDED,
            artifacts=artifacts,
            error=None,
        )
    finally:
        if prev_collab is None:
            os.environ.pop("SONA_EVENT_COLLAB_MODE", None)
        else:
            os.environ["SONA_EVENT_COLLAB_MODE"] = prev_collab
