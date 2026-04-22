"""Budget governance helpers (Day7).

Minimal, dependency-free helpers for stage-level budget checks:
- token budget (approximate, char-based)
- latency budget
- retry budget
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


def _env_int(name: str, default: int, *, minimum: int = 0, maximum: Optional[int] = None) -> int:
    try:
        value = int(str(os.environ.get(name, default)).strip())
    except Exception:
        value = default
    value = max(minimum, value)
    if maximum is not None:
        value = min(value, maximum)
    return value


def estimate_tokens(text: str) -> int:
    """Rough token estimate for mixed zh/en text."""
    t = str(text or "").strip()
    if not t:
        return 0
    # Keep estimate intentionally simple and monotonic.
    return max(1, len(t) // 2)


@dataclass(slots=True)
class BudgetSummary:
    stage: str
    token_budget: int
    latency_budget_ms: int
    retry_budget: int
    triggers: Dict[str, int] = field(default_factory=dict)
    actions: List[Dict[str, Any]] = field(default_factory=list)
    started_at_ms: int = field(default_factory=lambda: int(time.time() * 1000))
    ended_at_ms: int = 0

    def trigger(self, key: str, *, reason: str) -> None:
        self.triggers[key] = int(self.triggers.get(key, 0)) + 1
        self.actions.append({"action": "trigger", "key": key, "reason": reason})

    def add_action(self, action: str, **kwargs: Any) -> None:
        item: Dict[str, Any] = {"action": action}
        item.update(kwargs)
        self.actions.append(item)

    def finalize(self) -> Dict[str, Any]:
        self.ended_at_ms = int(time.time() * 1000)
        return {
            "stage": self.stage,
            "token_budget": self.token_budget,
            "latency_budget_ms": self.latency_budget_ms,
            "retry_budget": self.retry_budget,
            "triggers": self.triggers,
            "actions": self.actions,
            "started_at_ms": self.started_at_ms,
            "ended_at_ms": self.ended_at_ms,
            "elapsed_ms": max(0, self.ended_at_ms - self.started_at_ms),
        }


def sentiment_budget_from_env(default_timeout_sec: int) -> BudgetSummary:
    token_budget = _env_int("SONA_BUDGET_SENTIMENT_TOKEN_BUDGET", 12000, minimum=2000, maximum=100000)
    latency_budget_ms = _env_int(
        "SONA_BUDGET_SENTIMENT_LATENCY_MS",
        max(5000, int(default_timeout_sec) * 1000),
        minimum=1000,
        maximum=300000,
    )
    retry_budget = _env_int("SONA_BUDGET_SENTIMENT_RETRY_BUDGET", 1, minimum=0, maximum=3)
    return BudgetSummary(
        stage="sentiment",
        token_budget=token_budget,
        latency_budget_ms=latency_budget_ms,
        retry_budget=retry_budget,
    )

