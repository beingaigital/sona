"""Shared workflow contracts.

This module defines stable, typed contracts that workflow stages use to
communicate. It is intentionally small and non-opinionated so it can be
introduced without changing current behavior, then expanded incrementally.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Literal, Optional


RunMode = Literal["live", "replay"]
StageStatus = Literal["success", "warning", "failed", "skipped"]


@dataclass(slots=True)
class BudgetState:
    """Budget and governance state for a single run.

    Fields are deliberately generic; concrete keys can be added as the budget
    governance layer is implemented.
    """

    token_budget: Optional[int] = None
    latency_budget_ms: Optional[int] = None
    retry_budget: Optional[int] = None
    triggers: Dict[str, int] = field(default_factory=dict)
    actions: List[Dict[str, Any]] = field(default_factory=list)


@dataclass(slots=True)
class ToolError:
    """Structured tool error contract (gradual convergence target)."""

    error_code: str
    error_message: str
    retryable: bool = False
    result_file_path: Optional[str] = None


@dataclass(slots=True)
class StageResult:
    """Standard stage output stored into `WorkflowContext.stage_outputs`."""

    stage: str
    status: StageStatus
    metrics: Dict[str, Any] = field(default_factory=dict)
    artifacts: Dict[str, Any] = field(default_factory=dict)
    error: Optional[ToolError] = None
    fallback_used: bool = False
    started_at: Optional[datetime] = None
    ended_at: Optional[datetime] = None


@dataclass(slots=True)
class WorkflowContext:
    """Shared runtime state for one workflow run."""

    run_id: str
    query: str
    mode: RunMode = "live"
    task_id: Optional[str] = None

    stage_outputs: Dict[str, StageResult] = field(default_factory=dict)
    artifacts: Dict[str, Any] = field(default_factory=dict)
    diagnostics: Dict[str, Any] = field(default_factory=dict)
    budget: BudgetState = field(default_factory=BudgetState)
    policy: Dict[str, Any] = field(default_factory=dict)
    errors: List[ToolError] = field(default_factory=list)

    def set_stage_result(self, result: StageResult) -> None:
        self.stage_outputs[result.stage] = result
        if result.error is not None:
            self.errors.append(result.error)


def new_context(*, run_id: str, query: str, mode: RunMode = "live", task_id: Optional[str] = None) -> WorkflowContext:
    """Convenience constructor."""

    return WorkflowContext(run_id=run_id, query=query, mode=mode, task_id=task_id)

