"""案例库自动生成与 Wiki cases 召回。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from workflow.case_library_generator import write_event_analysis_case_wiki
from workflow.wiki_cli import answer_case_query, retrieve_wiki_sources


def test_case_write_and_wiki_retrieval_hits(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    root = tmp_path / "proj"
    wiki_root = root / "opinion_analysis_kb" / "references" / "wiki"
    wiki_root.mkdir(parents=True)
    (wiki_root / "index.md").write_text("# Wiki\n\n## 页面目录\n\n", encoding="utf-8")

    task_id = "abc-test-uuid-0001"
    proc = root / "sandbox" / task_id / "过程文件"
    res = root / "sandbox" / task_id / "结果文件"
    proc.mkdir(parents=True)
    res.mkdir(parents=True)

    (proc / "interpretation.json").write_text(
        json.dumps(
            {
                "interpretation": {
                    "narrative_summary": "地铁乘客冲突事件在短视频平台扩散，运营方回应后进入降温观察。",
                    "domain": "交通",
                    "event_type": "公共服务争议",
                    "stage": "扩散期",
                    "key_events": ["2026-01-02 话题登上同城热搜"],
                    "key_risks": ["服务争议", "回应节奏风险"],
                    "theory_names": ["生命周期规律"],
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (proc / "graph_rag_enrichment.json").write_text(
        json.dumps({"similar_cases": {"items": [{"title": "历史案例A", "summary": "反转路径"}]}}, ensure_ascii=False),
        encoding="utf-8",
    )
    (proc / "reference_insights.json").write_text(
        json.dumps({"items": [{"title": "智库条目", "snippet": "建议尽快披露权威信息"}]}, ensure_ascii=False),
        encoding="utf-8",
    )
    html_path = res / "report_demo.html"
    html_path.write_text("<html/>", encoding="utf-8")

    search_plan = {
        "version": "search_plan_v1",
        "eventIntroduction": "某市地铁乘客冲突引发短视频围观",
        "searchWords": ["地铁", "乘客", "冲突"],
        "timeRange": "2026-01-01 00:00:00;2026-01-07 23:59:59",
        "verificationChecklist": ["核对监控时间轴", "区分事实与传言"],
        "evidenceSnippets": ["现场视频片段传播极快"],
    }

    monkeypatch.setenv("SONA_WIKI_ROOT", str(wiki_root))
    meta = write_event_analysis_case_wiki(
        project_root=root,
        task_id=task_id,
        process_dir=proc,
        search_plan=search_plan,
        user_query="分析地铁乘客冲突舆情",
        html_report_path=str(html_path),
        timeline_json={"timeline": [{"time": "2026-01-02", "summary": "话题登上同城热搜"}]},
        sentiment_json={"negative_summary": ["质疑运营方回应过慢"]},
    )

    case_rel = str(meta.get("case_rel") or "")
    case_fp = wiki_root / "cases" / Path(case_rel).name
    assert case_fp.is_file()
    text = case_fp.read_text(encoding="utf-8")
    for key in ("title:", "domain:", "actors:", "timeline:", "risk_patterns:", "response_tactics:", "evidence:", "report_path:"):
        assert key in text
    assert case_rel in (wiki_root / "index.md").read_text(encoding="utf-8")

    case_answer = answer_case_query("找几个地铁服务争议案例", project_root=root)
    assert case_answer["cases"]
    assert "案例文件" in case_answer["answer"]

    srcs = retrieve_wiki_sources("地铁乘客冲突 舆情", topk=8, project_root=root)
    paths = [s.path.replace("\\", "/") for s in srcs]
    assert any("/references/wiki/cases/" in p for p in paths), paths
