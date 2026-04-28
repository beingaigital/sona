from __future__ import annotations

import os
import json
from pathlib import Path


def test_event_strict_search_words_levels(monkeypatch) -> None:
    from workflow import event_analysis_pipeline as p

    monkeypatch.setenv("SONA_EVENT_QUERY_STRICT_MODE", "true")
    user_query = "12306回应家长和孩子相隔14个车厢事件"
    base_words = ["12306回应家长和孩子相隔14个车厢事件", "高铁", "回应"]

    w1, level1 = p._pick_search_words_for_round(base_words=base_words, user_query=user_query, round_idx=1)
    assert level1 == "core"
    assert "铁路12306" in w1
    assert any("相隔" in x and "车厢" in x for x in w1)

    w2, level2 = p._pick_search_words_for_round(base_words=base_words, user_query=user_query, round_idx=2)
    assert level2 in {"extended", "core"}
    assert len(w2) >= len(w1)

    w3, level3 = p._pick_search_words_for_round(base_words=base_words, user_query=user_query, round_idx=3)
    assert level3 == "broad"
    assert len(w3) >= len(w2)


def test_topic_relevance_composite_has_phrase_signal() -> None:
    from workflow import event_analysis_pipeline as p

    relevance = p._topic_relevance_metrics(
        user_query="12306回应家长孩子相隔14车厢",
        search_words=["铁路12306", "家长孩子相隔14车厢"],
        top_keywords=["铁路12306", "相隔14车厢", "回应", "安置", "乘务员"],
    )
    assert relevance["coverage"] >= 0.0
    assert relevance["coverage_phrase"] > 0.0
    assert relevance["composite"] > 0.0
    assert any("12306" in x for x in relevance.get("phrase_hits", []))


def test_count_channels_from_csv_platform_column(tmp_path: Path) -> None:
    from workflow import event_analysis_pipeline as p

    csv_path = tmp_path / "sample.csv"
    csv_path.write_text(
        "平台,内容\n微博,a\n微博,b\n小红书,c\n",
        encoding="utf-8",
    )
    counts = p._count_channels_from_csv(str(csv_path))
    assert counts.get("微博") == 2
    assert counts.get("小红书") == 1


def test_timeline_event_relevance_filters_unrelated_rows() -> None:
    # Keep this test pure (no model calls): validate the relevance pre-filter
    import importlib

    tl = importlib.import_module("tools.analysis_timeline")

    rows = [
        {"内容": "粤超足球比赛今晚开赛，门票热卖", "发布时间": "2026-04-23 10:00:00"},
        {"内容": "12306回应：家长孩子相隔14车厢将优化安排", "发布时间": "2026-04-23 11:00:00"},
        {"内容": "铁路12306客服：会协助调换座位", "发布时间": "2026-04-23 12:00:00"},
    ]
    filtered = tl._filter_by_event_relevance(rows, "内容", "12306 家长孩子 相隔14车厢", min_hits=1)
    assert len(filtered) == 2
    assert all("12306" in r["内容"] for r in filtered)


def test_golden_case_12306_report_harness_passed() -> None:
    case_dir = (
        Path(__file__).resolve().parents[2]
        / "eval_results"
        / "golden_cases"
        / "event_analysis_12306_14cars_20260426"
    )
    scorecard_path = case_dir / "runtime_harness_scorecard.json"
    report_meta_path = case_dir / "report_meta.json"
    report_html_path = case_dir / "report.html"

    assert scorecard_path.exists()
    assert report_meta_path.exists()
    assert report_html_path.exists()

    scorecard = json.loads(scorecard_path.read_text(encoding="utf-8"))
    assert scorecard.get("status") == "passed"
    checks = scorecard.get("checks") if isinstance(scorecard.get("checks"), list) else []
    assert any(c.get("name") == "topic_relevance_quality" and c.get("status") == "pass" for c in checks)
    assert any(c.get("name") == "reference_recall_quality" and c.get("status") == "pass" for c in checks)

    meta = json.loads(report_meta_path.read_text(encoding="utf-8"))
    assert meta.get("has_summary") is True
    assert meta.get("has_timeline") is True
    assert meta.get("has_recommendations") is True
    frameworks = meta.get("theory_frameworks") if isinstance(meta.get("theory_frameworks"), list) else []
    assert "议程设置" in frameworks


def test_golden_case_disney_channel_mapping_and_volume_series() -> None:
    from tools import report_html_template as r

    case_dir = (
        Path(__file__).resolve().parents[2]
        / "eval_results"
        / "golden_cases"
        / "event_analysis_disney_smoking_20260427"
    )
    channel_obj = json.loads((case_dir / "process_channel_distribution.json").read_text(encoding="utf-8"))
    pie = r._build_channel_pie_data(channel_obj)
    assert pie
    assert pie[0]["name"] == "微博"
    assert all(x["name"] != "total_count" for x in pie)

    vol_obj = json.loads((case_dir / "process_volume_stats.json").read_text(encoding="utf-8"))
    dates, post_counts, heat_norm, _raw = r._extract_volume_series(vol_obj)
    assert len(dates) == len(post_counts) == len(heat_norm)
    assert max(post_counts) >= 1000  # 2026-04-25 单日破千
    assert 0 <= max(heat_norm) <= 100  # 热度已标准化

