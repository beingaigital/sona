"""In-memory task registry for the HTTP API (single-process demo / dev)."""

from __future__ import annotations

import threading
from typing import List, Optional

from api.schema import TaskEnvelope


class TaskStore:
    """Thread-safe store of TaskEnvelope by task_id."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._tasks: dict[str, TaskEnvelope] = {}

    def put(self, envelope: TaskEnvelope) -> None:
        with self._lock:
            self._tasks[envelope.task_id] = envelope

    def get(self, task_id: str) -> Optional[TaskEnvelope]:
        with self._lock:
            return self._tasks.get(task_id)

    def list_all(self) -> List[TaskEnvelope]:
        """Return all stored envelopes (newest last; order not guaranteed)."""
        with self._lock:
            return list(self._tasks.values())


_store = TaskStore()


def get_task_store() -> TaskStore:
    """Singleton used by FastAPI routes."""
    return _store
