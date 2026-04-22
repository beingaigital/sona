from __future__ import annotations

import json
from pathlib import Path

import pytest

from workflow.tool_schemas import (
    SchemaError,
    validate_data_collect_output,
    validate_data_num_output,
    validate_weibo_aisearch_output,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _load_json(rel: str) -> object:
    path = PROJECT_ROOT / rel
    return json.loads(path.read_text(encoding="utf-8"))


def test_schema_data_num_platform_allocation_replay_fixture() -> None:
    payload = _load_json("tests/fixtures/tool_data_num_platform_001/tools.json")
    validate_data_num_output(payload)


def test_schema_weibo_aisearch_fixture() -> None:
    payload = _load_json("tests/fixtures/tool_weibo_aisearch_001/output.json")
    validate_weibo_aisearch_output(payload)


def test_schema_data_collect_fixture() -> None:
    payload = _load_json("tests/fixtures/tool_data_collect_001/output.json")
    validate_data_collect_output(payload)


def test_schema_errors_are_loud() -> None:
    with pytest.raises(SchemaError):
        validate_data_num_output({"total_count": 1})

