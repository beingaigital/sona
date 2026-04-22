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

