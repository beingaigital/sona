"""FastAPI application skeleton for Sona HTTP API."""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import APIRouter, Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from api.event_runner import run_analyze_event
from api.schema import AnalyzeEventRequest, HealthResponse, TaskEnvelope, TaskListResponse, TaskStatus
from api.task_store import TaskStore, get_task_store


def _cors_settings() -> tuple[list[str], bool]:
    """Return (allow_origins, allow_credentials)."""
    raw = os.environ.get("SONA_API_CORS_ORIGINS", "").strip()
    if raw == "*":
        return ["*"], False
    if raw:
        origins = [o.strip() for o in raw.split(",") if o.strip()]
        return origins, True
    return (
        [
            "http://127.0.0.1:8501",
            "http://localhost:8501",
            "http://127.0.0.1:3000",
            "http://localhost:3000",
            "http://127.0.0.1:8765",
            "http://localhost:8765",
        ],
        True,
    )


v1_router = APIRouter(prefix="/v1", tags=["workflows"])


@v1_router.post("/analyze-event", response_model=TaskEnvelope)
def analyze_event(
    body: AnalyzeEventRequest,
    store: TaskStore = Depends(get_task_store),
) -> TaskEnvelope:
    """Run full event analysis (sync); result is stored for GET /v1/tasks/{task_id}."""
    envelope = run_analyze_event(body)
    store.put(envelope)
    return envelope


@v1_router.get("/tasks", response_model=TaskListResponse)
def list_tasks(store: TaskStore = Depends(get_task_store)) -> TaskListResponse:
    """List tasks stored in the current API process memory (for Streamlit GUI)."""
    return TaskListResponse(tasks=store.list_all())


@v1_router.get("/tasks/{task_id}", response_model=TaskEnvelope)
def get_task(task_id: str, store: TaskStore = Depends(get_task_store)) -> TaskEnvelope:
    """Return last known envelope for tasks created via this API process."""
    env = store.get(task_id)
    if env is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return env


@v1_router.get("/tasks/{task_id}/report")
def get_task_report(task_id: str, store: TaskStore = Depends(get_task_store)) -> FileResponse:
    """Return HTML report file when available (mode B in docs/api_design.md)."""
    env = store.get(task_id)
    if env is None:
        raise HTTPException(status_code=404, detail="Task not found")
    if env.status != TaskStatus.SUCCEEDED:
        raise HTTPException(
            status_code=409,
            detail="Task did not succeed; report unavailable",
        )
    raw_path = (env.artifacts.report_path or "").strip()
    if not raw_path:
        raise HTTPException(status_code=404, detail="Report path not recorded")
    path = Path(raw_path).expanduser()
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Report file missing on disk")
    return FileResponse(
        path,
        media_type="text/html; charset=utf-8",
        filename=path.name,
    )


def create_app() -> FastAPI:
    """Build FastAPI app with CORS and core routes."""
    application = FastAPI(
        title="Sona API",
        description="HTTP API for Sona workflows (see docs/api_design.md).",
        version="0.1.0",
    )

    allow_origins, allow_credentials = _cors_settings()
    application.add_middleware(
        CORSMiddleware,
        allow_origins=allow_origins,
        allow_credentials=allow_credentials,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @application.get("/health", response_model=HealthResponse, tags=["meta"])
    def health() -> HealthResponse:
        """Liveness probe for load balancers and local checks."""
        return HealthResponse()

    application.include_router(v1_router)

    return application


app = create_app()
