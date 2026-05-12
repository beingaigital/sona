"""Tool I/O contracts (Day6).

This module defines minimal, stable schema validators for hotspot tools so that
field/shape regressions fail loudly in CI rather than being silently tolerated.

We intentionally avoid adding heavy dependencies; use lightweight runtime checks.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Mapping, Optional


class SchemaError(ValueError):
    """Raised when a payload violates an expected schema."""


def _is_str(x: object) -> bool:
    return isinstance(x, str)


def _is_int(x: object) -> bool:
    # bool is subclass of int -> exclude
    return isinstance(x, int) and not isinstance(x, bool)


def _require_mapping(obj: Any, *, where: str) -> Mapping[str, Any]:
    if not isinstance(obj, dict):
        raise SchemaError(f"{where}: expected object, got {type(obj).__name__}")
    return obj


def _require_keys(obj: Mapping[str, Any], keys: Iterable[str], *, where: str) -> None:
    missing = [k for k in keys if k not in obj]
    if missing:
        raise SchemaError(f"{where}: missing required fields: {missing}")


def validate_data_num_output(payload: Any) -> Dict[str, Any]:
    """Validate `tools/data_num.py` output JSON."""
    obj = _require_mapping(payload, where="data_num")
    _require_keys(
        obj,
        (
            "search_matrix",
            "total_count",
            "platform",
            "time_range",
            "threshold",
            "keyword_mode",
            "query_string",
            "allocate_by_platform",
        ),
        where="data_num",
    )

    if not isinstance(obj["search_matrix"], dict):
        raise SchemaError("data_num.search_matrix: expected object")
    for k, v in obj["search_matrix"].items():
        if not _is_str(k):
            raise SchemaError("data_num.search_matrix: keys must be strings")
        if not _is_int(v):
            raise SchemaError("data_num.search_matrix: values must be integers")

    if not _is_int(obj["total_count"]):
        raise SchemaError("data_num.total_count: expected integer")
    if not _is_str(obj["platform"]):
        raise SchemaError("data_num.platform: expected string")
    if not _is_str(obj["time_range"]):
        raise SchemaError("data_num.time_range: expected string")
    if not _is_int(obj["threshold"]):
        raise SchemaError("data_num.threshold: expected integer")
    if str(obj["keyword_mode"]) not in {"normal", "advanced"}:
        raise SchemaError("data_num.keyword_mode: expected 'normal'|'advanced'")
    if not _is_str(obj["query_string"]):
        raise SchemaError("data_num.query_string: expected string")
    if not isinstance(obj["allocate_by_platform"], bool):
        raise SchemaError("data_num.allocate_by_platform: expected boolean")

    if obj.get("allocate_by_platform") is True:
        for field in ("platform_counts", "platform_allocation"):
            if field not in obj:
                raise SchemaError(f"data_num.{field}: required when allocate_by_platform=true")
            if not isinstance(obj[field], dict):
                raise SchemaError(f"data_num.{field}: expected object")
            for k, v in obj[field].items():
                if not _is_str(k) or not _is_int(v):
                    raise SchemaError(f"data_num.{field}: must be str->int map")

    if "error" in obj and obj["error"]:
        if not _is_str(obj["error"]):
            raise SchemaError("data_num.error: expected string")

    if "warnings" in obj and obj["warnings"] is not None:
        if not isinstance(obj["warnings"], list) or any(not _is_str(x) for x in obj["warnings"]):
            raise SchemaError("data_num.warnings: expected list[str]")

    return dict(obj)


def validate_weibo_aisearch_output(payload: Any) -> Dict[str, Any]:
    """Validate `tools/weibo_aisearch.py` output JSON."""
    obj = _require_mapping(payload, where="weibo_aisearch")
    _require_keys(
        obj,
        (
            "topic",
            "url",
            "count",
            "results",
            "error",
            "fallback_used",
            "source",
            "authenticated",
            "fetched_at",
        ),
        where="weibo_aisearch",
    )
    if not _is_str(obj["topic"]):
        raise SchemaError("weibo_aisearch.topic: expected string")
    if not _is_str(obj["url"]):
        raise SchemaError("weibo_aisearch.url: expected string")
    if not _is_int(obj["count"]):
        raise SchemaError("weibo_aisearch.count: expected integer")
    if not isinstance(obj["results"], list):
        raise SchemaError("weibo_aisearch.results: expected list")
    for item in obj["results"][:50]:
        it = _require_mapping(item, where="weibo_aisearch.results[]")
        _require_keys(it, ("snippet",), where="weibo_aisearch.results[]")
        if not _is_str(it["snippet"]):
            raise SchemaError("weibo_aisearch.results[].snippet: expected string")
    if not _is_str(obj["error"]):
        raise SchemaError("weibo_aisearch.error: expected string")
    if not isinstance(obj["fallback_used"], bool):
        raise SchemaError("weibo_aisearch.fallback_used: expected boolean")
    if not _is_str(obj["source"]):
        raise SchemaError("weibo_aisearch.source: expected string")
    if not isinstance(obj["authenticated"], bool):
        raise SchemaError("weibo_aisearch.authenticated: expected boolean")
    if not _is_str(obj["fetched_at"]):
        raise SchemaError("weibo_aisearch.fetched_at: expected string")

    st = obj.get("structured", None)
    if st is not None:
        st_obj = _require_mapping(st, where="weibo_aisearch.structured")
        ver = st_obj.get("version", 1)
        if ver is not None and not _is_int(ver):
            raise SchemaError("weibo_aisearch.structured.version: expected int")
        slots = st_obj.get("slots")
        if slots is not None:
            slots_m = _require_mapping(slots, where="weibo_aisearch.structured.slots")
            for sk, sv in slots_m.items():
                if not _is_str(sk):
                    raise SchemaError("weibo_aisearch.structured.slots: keys must be strings")
                if not isinstance(sv, list) or any(not _is_str(x) for x in sv):
                    raise SchemaError("weibo_aisearch.structured.slots: values must be list[str]")
        rb = st_obj.get("report_bridge")
        if rb is not None:
            if not isinstance(rb, list):
                raise SchemaError("weibo_aisearch.structured.report_bridge: expected list")
            for i, it in enumerate(rb[:40]):
                if not isinstance(it, dict):
                    raise SchemaError(f"weibo_aisearch.structured.report_bridge[{i}]: expected object")
                th = it.get("template_hooks")
                fs = it.get("from_slots")
                wh = it.get("writer_hint", "")
                if th is not None and not isinstance(th, list):
                    raise SchemaError(f"weibo_aisearch.structured.report_bridge[{i}].template_hooks: expected list")
                if th is not None and any(not _is_str(x) for x in th):
                    raise SchemaError(f"weibo_aisearch.structured.report_bridge[{i}].template_hooks: list[str]")
                if fs is not None and not isinstance(fs, list):
                    raise SchemaError(f"weibo_aisearch.structured.report_bridge[{i}].from_slots: expected list")
                if fs is not None and any(not _is_str(x) for x in fs):
                    raise SchemaError(f"weibo_aisearch.structured.report_bridge[{i}].from_slots: list[str]")
                if wh is not None and not _is_str(wh):
                    raise SchemaError(f"weibo_aisearch.structured.report_bridge[{i}].writer_hint: expected string")
        disc = st_obj.get("disclaimer", "")
        if disc is not None and not _is_str(disc):
            raise SchemaError("weibo_aisearch.structured.disclaimer: expected string")

    return dict(obj)


def validate_data_collect_output(payload: Any) -> Dict[str, Any]:
    """Validate `tools/data_collect.py` output JSON (lightweight)."""
    obj = _require_mapping(payload, where="data_collect")
    _require_keys(obj, ("save_path", "meta"), where="data_collect")
    if not _is_str(obj["save_path"]):
        raise SchemaError("data_collect.save_path: expected string")
    meta = _require_mapping(obj["meta"], where="data_collect.meta")

    # In error cases meta might be {}, but save_path is expected to be empty.
    if obj.get("error"):
        if not _is_str(obj["error"]):
            raise SchemaError("data_collect.error: expected string")
        return dict(obj)

    _require_keys(meta, ("platform", "count", "fields", "search_summary"), where="data_collect.meta")
    if not _is_str(meta["platform"]):
        raise SchemaError("data_collect.meta.platform: expected string")
    if not _is_int(meta["count"]):
        raise SchemaError("data_collect.meta.count: expected integer")
    if not isinstance(meta["fields"], list) or any(not _is_str(x) for x in meta["fields"]):
        raise SchemaError("data_collect.meta.fields: expected list[str]")
    if not isinstance(meta["search_summary"], dict):
        raise SchemaError("data_collect.meta.search_summary: expected object")

    return dict(obj)

