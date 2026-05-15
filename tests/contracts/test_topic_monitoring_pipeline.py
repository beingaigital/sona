"""专题监控流水线测试。"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

from workflow.topic_monitoring_pipeline import TopicMonitoringPipeline, run_high_speed_rail_demo


class DummyDB:
    def __init__(self):
        self.topics = {
            "topic-1": {
                "id": "topic-1",
                "name": "高铁舆情",
                "domain": "交通",
                "description": "示例高铁舆情专题",
                "is_active": True,
            }
        }
        self.snapshots = [
            {
                "id": "snap-1",
                "topic_id": "topic-1",
                "created_at": "2026-05-10T10:00:00",
                "post_count": 12,
                "engagement_sum": 320,
                "avg_sentiment": -0.1,
                "top_keywords": ["高铁", "服务", "投诉"],
                "volume_trend": "up",
                "summary": "热度上升",
            }
        ]
        self.alerts = []
        self.cases = [
            {
                "id": "case-1",
                "topic_id": "topic-1",
                "case_title": "高铁服务争议案例",
                "case_domain": "交通",
                "case_url": "opinion_analysis_kb/references/wiki/cases/case_example.md",
                "relevance_score": 0.92,
                "evidence": "匹配服务争议",
            }
        ]

    def get_topic_by_id(self, topic_id: str) -> Optional[Dict[str, Any]]:
        return self.topics.get(topic_id)

    def get_topic_keywords(self, topic_id: str) -> List[Dict[str, Any]]:
        return [{"keyword": "高铁"}, {"keyword": "服务争议"}]

    def get_latest_snapshot(self, topic_id: str) -> Optional[Dict[str, Any]]:
        return self.snapshots[0] if self.snapshots else None

    def get_snapshots(self, topic_id: str, limit: int = 10) -> List[Dict[str, Any]]:
        return self.snapshots[:limit]

    def list_alerts(
        self,
        topic_id: Optional[str] = None,
        is_resolved: Optional[bool] = None,
        severity: Optional[str] = None,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        return [a for a in self.alerts if topic_id is None or a["topic_id"] == topic_id]

    def get_linked_cases(self, topic_id: str, min_score: float = 0.5) -> List[Dict[str, Any]]:
        return [c for c in self.cases if c["topic_id"] == topic_id and c["relevance_score"] >= min_score]

    def get_collected_posts(self, topic_id: str, limit: int = 100, since=None) -> List[Dict[str, Any]]:
        return []


def test_generate_periodic_report_writes_markdown(tmp_path: Path) -> None:
    pipeline = TopicMonitoringPipeline(db=DummyDB())
    result = pipeline.generate_periodic_report("topic-1", period="weekly", output_dir=tmp_path)

    report_path = Path(result["report_path"])
    assert report_path.exists()
    content = report_path.read_text(encoding="utf-8")
    assert "# 高铁舆情 周报报告" in content
    assert "## 活动告警" in content
    assert "## 关联案例" in content


def test_monitor_demo_runs_without_external_database(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_KEY", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("POSTGRES_URL", raising=False)

    result = run_high_speed_rail_demo(cycles=1, output_dir=tmp_path)
    assert result["topic"]["name"] == "高铁舆情"
    assert Path(result["report"]["report_path"]).exists()


def test_scan_topic_emits_viral_post_alert_for_hot_single_item() -> None:
    from workflow.topic_monitoring_pipeline import InMemoryTopicStore, MonitorConfig, TopicMonitoringPipeline

    db = InMemoryTopicStore()
    cfg = MonitorConfig(single_post_viral_threshold=100, viral_threshold=50_000)
    pipeline = TopicMonitoringPipeline(db=db, config=cfg)
    topic = pipeline.create_topic(name="测", domain="综合舆情", keywords=["事故"], description="")
    tid = str(topic["id"])
    batch = [
        {
            "id": "p-viral-1",
            "url": "https://example.com/1",
            "platform": "微博",
            "author": "a",
            "title": "突发",
            "content": "内容",
            "likes": 80,
            "comments": 30,
            "shares": 10,
            "sentiment": "negative",
        }
    ]
    out = pipeline.scan_topic(tid, batch)
    assert out["alerts"]
    assert any(str(a.get("alert_type")) == "viral_post" for a in out["alerts"])


def test_supabase_config_requires_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_KEY", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("POSTGRES_URL", raising=False)

    from workflow.supabase_client import SupabaseConfig

    with pytest.raises(ValueError, match="SUPABASE_URL/SUPABASE_KEY 或 DATABASE_URL/POSTGRES_URL"):
        SupabaseConfig.from_env()
