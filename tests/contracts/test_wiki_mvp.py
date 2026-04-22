from __future__ import annotations

from pathlib import Path

import pytest

from utils.path import get_opinion_analysis_kb_root
from workflow.wiki_cli import answer_wiki_query


def test_wiki_answer_contract_minimal() -> None:
    root = Path(__file__).resolve().parents[2]
    out = answer_wiki_query("什么是舆情反转？", topk=4, style="teach", project_root=root)
    assert isinstance(out, dict)
    assert "answer" in out
    assert "sources" in out
    assert isinstance(out["answer"], str)
    assert isinstance(out["sources"], list)
    if out["sources"]:
        first = out["sources"][0]
        assert "title" in first
        assert "path" in first
        assert "snippet" in first
        assert "score" in first


def test_wiki_meme_query_avoids_concept_teach_boilerplate() -> None:
    """「什么梗」类问题不得套用概念课三句模板。"""
    root = Path(__file__).resolve().parents[2]
    wiki_root = get_opinion_analysis_kb_root(root) / "references" / "wiki"
    if not wiki_root.is_dir():
        pytest.skip("wiki corpus not present")
    out = answer_wiki_query(
        "粉底液将军是什么梗？",
        topk=6,
        style="teach",
        project_root=root,
    )
    assert "可先把它理解为一个可验证的概念问题" not in str(out.get("answer", ""))


def test_wiki_event_overview_avoids_concept_teach_boilerplate() -> None:
    """事件梗概类问题不应套用「可验证的概念问题」模板（见 wiki_event_overview_004）。"""
    root = Path(__file__).resolve().parents[2]
    wiki_root = get_opinion_analysis_kb_root(root) / "references" / "wiki"
    if not wiki_root.is_dir():
        pytest.skip("wiki corpus not present")
    out = answer_wiki_query(
        "粉底液将军是什么事件？",
        topk=6,
        style="teach",
        project_root=root,
    )
    ans = str(out.get("answer", ""))
    assert "可先把它理解为一个可验证的概念问题" not in ans
    assert "立善志" not in ans and "行善业" not in ans

