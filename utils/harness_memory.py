"""Harness memory: project/session/example memories (persistent, auditable).

目标：
- 会话记忆（session memory）：按 task_id 写入 SessionManager 的 session json
- 项目记忆（project memory）：可版本化配置文件（如 workflow/domain_routing.json）
- 样例记忆（example memory）：高质量“事件→引用组合→输出结构”样例，用于回归评测

本模块不依赖额外第三方库，避免引入 YAML 依赖；默认使用 JSON/JSONL。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional


DOMAIN_ROUTING_FILENAME = "domain_routing.json"


@dataclass(frozen=True, slots=True)
class DomainPolicy:
    """Domain routing policy loaded from project config."""

    domain: str
    must_include: Dict[str, Any]
    prefer: Dict[str, Any]
    blocklist: Dict[str, Any]
    injection_limits: Dict[str, Any]


def _safe_read_json(path: Path) -> Optional[Dict[str, Any]]:
    try:
        if not path.is_file():
            return None
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            obj = json.load(f)
        return obj if isinstance(obj, dict) else None
    except Exception:
        return None


def project_domain_routing_path(project_root: Path) -> Path:
    return (project_root / "workflow" / DOMAIN_ROUTING_FILENAME).resolve()


def load_project_domain_routing(project_root: Path) -> Dict[str, Any]:
    """Load domain routing config (project memory). Returns empty dict if missing/invalid."""
    cfg = _safe_read_json(project_domain_routing_path(project_root))
    return cfg or {}


def get_domain_policy(project_root: Path, domain: str) -> Optional[DomainPolicy]:
    cfg = load_project_domain_routing(project_root)
    domains = cfg.get("domains")
    if not isinstance(domains, dict):
        return None
    raw = domains.get(str(domain or "").strip())
    if not isinstance(raw, dict):
        return None
    return DomainPolicy(
        domain=str(domain),
        must_include=raw.get("must_include") if isinstance(raw.get("must_include"), dict) else {},
        prefer=raw.get("prefer") if isinstance(raw.get("prefer"), dict) else {},
        blocklist=raw.get("blocklist") if isinstance(raw.get("blocklist"), dict) else {},
        injection_limits=raw.get("injection_limits") if isinstance(raw.get("injection_limits"), dict) else {},
    )


def get_session_prefs(session_data: Dict[str, Any]) -> Dict[str, Any]:
    """Read session memory (session prefs) from session json; tolerant to missing keys."""
    hm = session_data.get("harness_memory")
    if not isinstance(hm, dict):
        return {}
    prefs = hm.get("session_prefs")
    return prefs if isinstance(prefs, dict) else {}


def set_session_prefs(session_data: Dict[str, Any], *, patch: Dict[str, Any]) -> Dict[str, Any]:
    """Merge-patch session_prefs into session_data and return updated session_data (in-memory)."""
    if not isinstance(patch, dict) or not patch:
        return session_data
    hm = session_data.get("harness_memory")
    if not isinstance(hm, dict):
        hm = {}
        session_data["harness_memory"] = hm
    prefs = hm.get("session_prefs")
    if not isinstance(prefs, dict):
        prefs = {}
        hm["session_prefs"] = prefs
    for k, v in patch.items():
        prefs[str(k)] = v
    hm.setdefault("updated_at", datetime.now().isoformat())
    return session_data


def append_example_memory(project_root: Path, *, record: Dict[str, Any]) -> Path:
    """
    Append one example memory record to memory/examples.jsonl.

    The file is JSONL for easy diff/append and streaming evaluation.
    """
    mem_dir = (project_root / "memory").resolve()
    mem_dir.mkdir(parents=True, exist_ok=True)
    fp = mem_dir / "examples.jsonl"
    row = dict(record or {})
    row.setdefault("created_at", datetime.now().isoformat())
    with open(fp, "a", encoding="utf-8", errors="replace") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
    return fp


def normalize_session_pref_patch(patch: Dict[str, Any]) -> Dict[str, Any]:
    """
    Normalize a session preference patch. Keeps keys stable to help future harness evolution.

    Supported keys (best-effort):
    - wiki_style: "teach" | "concise"
    - wiki_topk: int
    - wiki_weibo_aux: bool
    """
    if not isinstance(patch, dict):
        return {}
    out: Dict[str, Any] = {}
    if "wiki_style" in patch:
        s = str(patch.get("wiki_style") or "").strip().lower()
        if s in {"teach", "concise"}:
            out["wiki_style"] = s
    if "wiki_topk" in patch:
        try:
            k = int(patch.get("wiki_topk"))
            out["wiki_topk"] = max(1, min(k, 12))
        except Exception:
            pass
    if "wiki_weibo_aux" in patch:
        out["wiki_weibo_aux"] = bool(patch.get("wiki_weibo_aux"))
    return out

