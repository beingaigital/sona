"""Pydantic models for the public HTTP API (see docs/api_design.md)."""

from __future__ import annotations

from enum import StrEnum
from typing import Any, List, Optional

from pydantic import BaseModel, ConfigDict, Field


class TaskStatus(StrEnum):
    """Lifecycle of an API-tracked workflow run."""

    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class ApiError(BaseModel):
    """Structured error returned to API clients."""

    model_config = ConfigDict(extra="forbid")

    error_code: str = Field(..., description="Machine-readable error code.")
    error_message: str = Field(..., description="Human-readable message.")


class TaskArtifacts(BaseModel):
    """Paths and hints produced by a workflow; keys may grow over time."""

    model_config = ConfigDict(extra="allow")

    report_path: str = Field(default="", description="HTML report filesystem path, if any.")
    trace_path: str = Field(default="", description="Optional trace or debug log path.")
    sandbox_dir: str = Field(default="", description="Optional sandbox root for this task.")
    session_hint: str = Field(
        default="",
        description="Optional note linking to SessionManager / Streamlit session.",
    )


class TaskEnvelope(BaseModel):
    """Standard task response body: status, optional artifacts, optional error."""

    model_config = ConfigDict(extra="forbid")

    task_id: str = Field(..., description="Task or session identifier (UUID or app-defined).")
    status: TaskStatus
    artifacts: TaskArtifacts = Field(default_factory=TaskArtifacts)
    error: Optional[ApiError] = Field(default=None, description="Set when status is failed or partial error info is exposed.")


class AnalyzeEventRequest(BaseModel):
    """Request body for POST /v1/analyze-event."""

    model_config = ConfigDict(extra="forbid")

    query: str = Field(..., min_length=1, description="Natural language event analysis input.")
    prefer_existing_data: bool = Field(default=True)
    disable_blocking_prompts: bool = Field(default=False)


class HealthResponse(BaseModel):
    """GET /health payload."""

    model_config = ConfigDict(extra="forbid")

    status: str = Field(default="ok")
    service: str = Field(default="sona-api")
    version: str = Field(default="0.1.0")


class TaskListResponse(BaseModel):
    """GET /v1/tasks — in-memory tasks for the current API process."""

    model_config = ConfigDict(extra="forbid")

    tasks: List[TaskEnvelope] = Field(default_factory=list)


class ReportPathResponse(BaseModel):
    """JSON style for GET /v1/tasks/{id}/report when returning path only (mode A)."""

    model_config = ConfigDict(extra="forbid")

    report_path: str


# Common error_code literals (optional reference for callers; not enforced by schema)
ERROR_VALIDATION = "VALIDATION_ERROR"
ERROR_WORKFLOW = "WORKFLOW_ERROR"
ERROR_NOT_FOUND = "NOT_FOUND"
ERROR_NOT_READY = "NOT_READY"


def task_envelope_from_dict(data: dict[str, Any]) -> TaskEnvelope:
    """Parse a loose dict (e.g. from in-memory store) into TaskEnvelope."""

    return TaskEnvelope.model_validate(data)
