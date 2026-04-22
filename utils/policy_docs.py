"""Policy docs loader for harness prompt injection.

This module provides a tiny, low-risk bridge from repo-level policy documents
(SOUL.md / USER.md / AGENT.md / MEMORY.md) into the system prompt.

Design goals:
- Keep parsing extremely simple and tolerant: only extract content between markers.
- Avoid heavy dependencies and avoid YAML parsing to keep boot fast and robust.
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List

from utils.path import get_project_root


HARNESS_BEGIN = "<!-- HARNESS:BEGIN -->"
HARNESS_END = "<!-- HARNESS:END -->"


def _safe_read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""


def _extract_harness_block(text: str) -> str:
    if not text:
        return ""
    start = text.find(HARNESS_BEGIN)
    if start < 0:
        return ""
    end = text.find(HARNESS_END, start + len(HARNESS_BEGIN))
    if end < 0:
        return ""
    body = text[start + len(HARNESS_BEGIN) : end].strip()
    return body


def load_harness_policy_snippets(project_root: Path | None = None) -> Dict[str, str]:
    root = project_root if project_root is not None else get_project_root()
    files = {
        "SOUL": root / "SOUL.md",
        "USER": root / "USER.md",
        "AGENT": root / "AGENT.md",
        "MEMORY": root / "MEMORY.md",
    }
    out: Dict[str, str] = {}
    for key, path in files.items():
        text = _safe_read_text(path)
        snippet = _extract_harness_block(text)
        if snippet:
            out[key] = snippet
    return out


def format_harness_policy_for_prompt(project_root: Path | None = None, *, max_chars: int = 1800) -> str:
    """
    Create a short prompt section from HARNESS blocks.

    The output is intentionally short and is truncated to max_chars to avoid
    bloating system prompts.
    """
    snippets = load_harness_policy_snippets(project_root)
    if not snippets:
        return ""

    order: List[str] = ["SOUL", "USER", "AGENT", "MEMORY"]
    parts: List[str] = ["## 系统策略（来自 SOUL/USER/AGENT/MEMORY）", ""]
    for k in order:
        if k not in snippets:
            continue
        parts.append(f"### {k}")
        parts.append(snippets[k].strip())
        parts.append("")

    text = "\n".join(parts).strip()
    if len(text) <= max_chars:
        return text
    return (text[: max_chars - 20].rstrip() + "\n\n（已截断）").strip()

