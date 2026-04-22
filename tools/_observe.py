"""Lightweight timing / lifecycle logs for tools (stdlib logging, no extra deps)."""

from __future__ import annotations

import logging
import time
from contextlib import contextmanager
from typing import Any, Iterator

_LOG = logging.getLogger("sona.tools")


@contextmanager
def tool_span(tool_name: str, **fields: Any) -> Iterator[None]:
    """Log tool start/end and wall time (ms). Safe if logging is not configured."""
    t0 = time.perf_counter()
    if _LOG.isEnabledFor(logging.INFO):
        msg = " ".join(f"{k}={v!r}" for k, v in sorted(fields.items()) if v is not None and v != "")
        _LOG.info("tool_start %s %s", tool_name, msg)
    try:
        yield
    finally:
        ms = (time.perf_counter() - t0) * 1000.0
        _LOG.info("tool_end %s elapsed_ms=%.1f", tool_name, ms)
