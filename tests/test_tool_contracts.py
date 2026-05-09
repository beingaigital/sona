from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from workflow.tool_contracts import ToolContractError, validate_tool_output


def test_extract_search_terms_contract_pass() -> None:
    payload = {
        "eventIntroduction": "测试事件",
        "searchWords": ["测试", "事件"],
        "timeRange": "2026-01-01 00:00:00;2026-01-07 23:59:59",
    }
    assert validate_tool_output("extract_search_terms", payload) == payload


def test_extract_search_terms_contract_missing_required_field_fail() -> None:
    with pytest.raises(ToolContractError):
        validate_tool_output("extract_search_terms", {"eventIntroduction": "x", "searchWords": []})


def test_analysis_sentiment_contract_error_type_fail() -> None:
    with pytest.raises(ToolContractError):
        validate_tool_output(
            "analysis_sentiment",
            {
                "statistics": {},
                "positive_summary": [],
                "negative_summary": [],
                "error": {"bad": "shape"},
            },
        )


def test_report_html_contract_pass() -> None:
    payload = {"html_file_path": "/tmp/r.html", "file_url": "file:///tmp/r.html"}
    assert validate_tool_output("report_html", payload) == payload
