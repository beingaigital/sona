"""话题监控流水线与周期报告生成。

支持基于 Supabase/Postgres 的专题监控、快照分析、风险告警和日报/周报输出。

编排增强（与事件分析的关系）：

- **共用工具**：关键词精炼可复用 ``extract_search_terms``（见 ``workflow/topic_monitoring_workflow``）；
  网察侧词表构建与事件分析一致时可复用 ``workflow/netinsight_keywords.build_data_num_search_words``。
- **专题差异**：滚动增量、多峰值时间线、单帖爆发预警（``viral_post``）与聚合告警并存；
  日报/周报中增加「周期内增量帖子」统计（按 ``collected_at`` 窗口）。
- **定时**：仓库提供 ``scripts/run_topic_monitor_tick.py`` 供 cron 调用；拉数需注入 ``search_func``。
"""

from __future__ import annotations

import re
import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import jieba

from workflow.supabase_client import SupabaseDB, get_db, topic_store_configured

SearchFunc = Callable[[List[str], str, int], List[Dict[str, Any]]]


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


@dataclass
class MonitorConfig:
    scan_interval_minutes: int = 60
    min_posts_for_trend: int = 10
    viral_threshold: int = 1000
    """周期快照维度：总互动量告警阈值（聚合）。"""
    single_post_viral_threshold: int = 5000
    """单帖维度：likes+comments+shares 超过即触发 ``viral_post`` 告警。"""
    alert_cooldown_hours: int = 24
    snapshot_interval_hours: int = 6
    netinsight_row_cap_hint: int = 10_000


@dataclass
class TopicMonitor:
    topic_id: str
    name: str
    domain: str
    keywords: List[Dict[str, Any]] = field(default_factory=list)
    config: MonitorConfig = field(default_factory=MonitorConfig)


class InMemoryTopicStore:
    """轻量内存数据层：用于本地 demo / 测试；外部库配置后会自动切换 Supabase/Postgres。"""

    def __init__(self) -> None:
        self.topics: Dict[str, Dict[str, Any]] = {}
        self.keywords: Dict[str, List[Dict[str, Any]]] = {}
        self.posts: Dict[str, List[Dict[str, Any]]] = {}
        self.snapshots: Dict[str, List[Dict[str, Any]]] = {}
        self.alerts: List[Dict[str, Any]] = []
        self.case_links: List[Dict[str, Any]] = []

    def _now(self) -> str:
        return _utcnow().isoformat(timespec="seconds")

    def create_monitor_topic(
        self,
        name: str,
        domain: str,
        description: str = "",
        owner: str = "system",
    ) -> Dict[str, Any]:
        topic_id = str(uuid.uuid4())
        row = {
            "id": topic_id,
            "name": name,
            "domain": domain,
            "description": description,
            "owner": owner,
            "is_active": True,
            "config": {},
            "created_at": self._now(),
            "updated_at": self._now(),
        }
        self.topics[topic_id] = row
        return dict(row)

    def get_topic_by_id(self, topic_id: str) -> Optional[Dict[str, Any]]:
        row = self.topics.get(str(topic_id))
        return dict(row) if row else None

    def list_monitor_topics(
        self,
        is_active: Optional[bool] = None,
        domain: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        rows = list(self.topics.values())
        if is_active is not None:
            rows = [r for r in rows if bool(r.get("is_active")) is bool(is_active)]
        if domain:
            rows = [r for r in rows if str(r.get("domain") or "") == str(domain)]
        return [dict(r) for r in rows]

    def add_topic_keywords(self, topic_id: str, keywords: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        for kw in keywords:
            word = str(kw.get("keyword") or "").strip()
            if not word:
                continue
            row = {
                "id": str(uuid.uuid4()),
                "topic_id": str(topic_id),
                "keyword": word,
                "keyword_type": str(kw.get("keyword_type") or "include"),
                "weight": float(kw.get("weight") or 1.0),
                "created_at": self._now(),
            }
            self.keywords.setdefault(str(topic_id), []).append(row)
            rows.append(dict(row))
        return rows

    def get_topic_keywords(self, topic_id: str) -> List[Dict[str, Any]]:
        return [dict(r) for r in self.keywords.get(str(topic_id), [])]

    def bulk_collect_posts(self, topic_id: str, posts: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        for post in posts:
            row = {
                "id": str(uuid.uuid4()),
                "topic_id": str(topic_id),
                **dict(post),
                "collected_at": self._now(),
            }
            self.posts.setdefault(str(topic_id), []).append(row)
            rows.append(dict(row))
        return rows

    def get_collected_posts(
        self,
        topic_id: str,
        limit: int = 100,
        since: Optional[datetime] = None,
    ) -> List[Dict[str, Any]]:
        rows = list(self.posts.get(str(topic_id), []))
        if since is not None:
            kept: List[Dict[str, Any]] = []
            for row in rows:
                raw = str(row.get("collected_at") or "")
                try:
                    ts = datetime.fromisoformat(raw.replace("Z", "+00:00"))
                    if ts.tzinfo is not None:
                        ts = ts.replace(tzinfo=None)
                except Exception:
                    ts = _utcnow()
                if ts >= since:
                    kept.append(row)
            rows = kept
        rows.sort(key=lambda r: str(r.get("collected_at") or ""), reverse=True)
        return [dict(r) for r in rows[: max(1, int(limit or 100))]]

    def create_snapshot(self, topic_id: str, snapshot_data: Dict[str, Any]) -> Dict[str, Any]:
        row = {
            "id": str(uuid.uuid4()),
            "topic_id": str(topic_id),
            **dict(snapshot_data),
            "created_at": self._now(),
        }
        self.snapshots.setdefault(str(topic_id), []).insert(0, row)
        return dict(row)

    def get_latest_snapshot(self, topic_id: str) -> Optional[Dict[str, Any]]:
        rows = self.snapshots.get(str(topic_id), [])
        return dict(rows[0]) if rows else None

    def get_snapshots(self, topic_id: str, limit: int = 10) -> List[Dict[str, Any]]:
        return [dict(r) for r in self.snapshots.get(str(topic_id), [])[: max(1, int(limit or 10))]]

    def create_alert(
        self,
        topic_id: str,
        alert_type: str,
        title: str,
        message: str,
        severity: str = "info",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        row = {
            "id": str(uuid.uuid4()),
            "topic_id": str(topic_id),
            "alert_type": alert_type,
            "title": title,
            "message": message,
            "severity": severity,
            "metadata": metadata or {},
            "is_resolved": False,
            "created_at": self._now(),
        }
        self.alerts.insert(0, row)
        return dict(row)

    def list_alerts(
        self,
        topic_id: Optional[str] = None,
        is_resolved: Optional[bool] = None,
        severity: Optional[str] = None,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        rows = list(self.alerts)
        if topic_id:
            rows = [r for r in rows if str(r.get("topic_id") or "") == str(topic_id)]
        if is_resolved is not None:
            rows = [r for r in rows if bool(r.get("is_resolved")) is bool(is_resolved)]
        if severity:
            rows = [r for r in rows if str(r.get("severity") or "") == str(severity)]
        return [dict(r) for r in rows[: max(1, int(limit or 50))]]

    def resolve_alert(self, alert_id: str) -> Dict[str, Any]:
        for row in self.alerts:
            if str(row.get("id") or "") == str(alert_id):
                row["is_resolved"] = True
                row["resolved_at"] = self._now()
                return dict(row)
        return {}

    def link_case(
        self,
        topic_id: str,
        case_title: str,
        case_domain: str,
        case_url: str,
        relevance_score: float = 1.0,
        evidence: str = "",
    ) -> Dict[str, Any]:
        row = {
            "id": str(uuid.uuid4()),
            "topic_id": str(topic_id),
            "case_title": case_title,
            "case_domain": case_domain,
            "case_url": case_url,
            "relevance_score": float(relevance_score),
            "evidence": evidence,
            "linked_at": self._now(),
        }
        self.case_links.insert(0, row)
        return dict(row)

    def get_linked_cases(self, topic_id: str, min_score: float = 0.5) -> List[Dict[str, Any]]:
        return [
            dict(r)
            for r in self.case_links
            if str(r.get("topic_id") or "") == str(topic_id)
            and float(r.get("relevance_score") or 0.0) >= float(min_score)
        ]

    def update_monitor_topic(self, topic_id: str, updates: Dict[str, Any]) -> Dict[str, Any]:
        """合并更新专题行（与 SupabaseDB.update_monitor_topic 语义对齐）。"""
        row = self.topics.get(str(topic_id))
        if not row:
            return {}
        for k, v in updates.items():
            if k == "config" and isinstance(v, dict):
                base = dict(row.get("config") or {})
                base.update(v)
                row["config"] = base
            else:
                row[k] = v
        row["updated_at"] = self._now()
        return dict(row)


_DEFAULT_MEMORY_STORE = InMemoryTopicStore()


class TopicMonitoringPipeline:
    """话题监控流水线"""

    def __init__(
        self,
        db: Optional[Any] = None,
        config: Optional[MonitorConfig] = None,
        *,
        use_external_db: Optional[bool] = None,
    ):
        if db is not None:
            self.db = db
        elif use_external_db is True or (use_external_db is None and topic_store_configured()):
            self.db = get_db()
        else:
            self.db = _DEFAULT_MEMORY_STORE
        self.config = config or MonitorConfig()
        raw_v = os.environ.get("SONA_MONITOR_VIRAL_AGG_THRESHOLD")
        if raw_v:
            try:
                self.config.viral_threshold = max(10, int(raw_v))
            except ValueError:
                pass
        raw_sp = os.environ.get("SONA_MONITOR_VIRAL_POST_THRESHOLD")
        if raw_sp:
            try:
                self.config.single_post_viral_threshold = max(50, int(raw_sp))
            except ValueError:
                pass
        raw_cap = os.environ.get("SONA_MONITOR_NETINSIGHT_ROW_CAP")
        if raw_cap:
            try:
                self.config.netinsight_row_cap_hint = max(500, int(raw_cap))
            except ValueError:
                pass

    def create_topic(
        self,
        name: str,
        domain: str,
        keywords: List[str],
        description: str = "",
        owner: str = "system",
    ) -> Dict[str, Any]:
        topic = self.db.create_monitor_topic(
            name=name,
            domain=domain,
            description=description,
            owner=owner,
        )
        topic_id = str(topic["id"])

        if keywords:
            keyword_records = [
                {"keyword": kw, "keyword_type": "include", "weight": 1.0}
                for kw in keywords
                if str(kw).strip()
            ]
            if keyword_records:
                self.db.add_topic_keywords(topic_id, keyword_records)

        self.db.create_snapshot(topic_id, {
            "post_count": 0,
            "engagement_sum": 0,
            "avg_sentiment": 0.0,
            "top_keywords": [],
            "volume_trend": "stable",
            "summary": "话题初始化",
        })
        return topic

    def patch_topic_config(self, topic_id: str, patch: Dict[str, Any]) -> Dict[str, Any]:
        """合并写入 ``monitor_topics.config``（内存库 / Postgres 均支持 update_monitor_topic）。"""
        topic = self.db.get_topic_by_id(topic_id)
        if not topic:
            return {}
        base = dict(topic.get("config") or {})
        base.update(patch)
        if hasattr(self.db, "update_monitor_topic"):
            return self.db.update_monitor_topic(topic_id, {"config": base})
        return {}

    def scan_topic(
        self,
        topic_id: str,
        search_results: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        viral_post_alerts: List[Dict[str, Any]] = []
        if search_results:
            posts = []
            for item in search_results:
                posts.append({
                    "post_id": str(item.get("id", "") or ""),
                    "post_url": str(item.get("url", "") or ""),
                    "platform": str(item.get("platform", "unknown") or "unknown"),
                    "author": str(item.get("author", "") or ""),
                    "title": str(item.get("title", "") or ""),
                    "content": str(item.get("content", "") or ""),
                    "likes": int(item.get("likes") or 0),
                    "comments": int(item.get("comments") or 0),
                    "shares": int(item.get("shares") or 0),
                    "sentiment": str(item.get("sentiment", "neutral") or "neutral"),
                    "tags": item.get("tags") if isinstance(item.get("tags"), list) else [],
                    "metadata": item.get("metadata") if isinstance(item.get("metadata"), dict) else {},
                })
            self.db.bulk_collect_posts(topic_id, posts)
            viral_post_alerts = self._check_single_post_viral_alerts(topic_id, search_results)

        snapshot = self._generate_snapshot(topic_id)
        agg_alerts = self._check_alerts(topic_id, snapshot)
        return {"snapshot": snapshot, "alerts": viral_post_alerts + agg_alerts}

    def _generate_snapshot(
        self,
        topic_id: str,
        since: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        since = since or (_utcnow() - timedelta(hours=self.config.snapshot_interval_hours))
        posts = self.db.get_collected_posts(topic_id, limit=500, since=since)

        if not posts:
            return self.db.create_snapshot(topic_id, {
                "post_count": 0,
                "engagement_sum": 0,
                "avg_sentiment": 0.0,
                "top_keywords": [],
                "volume_trend": "stable",
                "summary": "无新数据",
            })

        post_count = len(posts)
        engagement_sum = sum(
            int(p.get("likes") or 0) + int(p.get("comments") or 0) + int(p.get("shares") or 0)
            for p in posts
        )

        sentiment_scores = {"positive": 1, "neutral": 0, "negative": -1}
        avg_sentiment = (
            sum(sentiment_scores.get(str(p.get("sentiment") or "neutral").lower(), 0) for p in posts)
            / len(posts)
        )

        last_snapshot = self.db.get_latest_snapshot(topic_id)
        if last_snapshot:
            last_count = int(last_snapshot.get("post_count") or 0)
            if last_count <= 0:
                volume_trend = "up"
            elif post_count > last_count * 1.5:
                volume_trend = "up"
            elif post_count < last_count * 0.5:
                volume_trend = "down"
            else:
                volume_trend = "stable"
        else:
            volume_trend = "stable"

        all_content = " ".join(
            f"{p.get('title','')} {p.get('content','')}" for p in posts
        )
        top_keywords = self._extract_keywords(all_content, top_n=12)

        snapshot_data = {
            "post_count": post_count,
            "engagement_sum": engagement_sum,
            "avg_sentiment": round(avg_sentiment, 3),
            "top_keywords": top_keywords,
            "volume_trend": volume_trend,
            "summary": f"近 {len(posts)} 条帖子，总互动 {engagement_sum}，情感 {round(avg_sentiment,3)}",
        }
        return self.db.create_snapshot(topic_id, snapshot_data)

    def _extract_keywords(self, text: str, top_n: int = 10) -> List[str]:
        if not text or not text.strip():
            return []
        raw = re.sub(r"[^\w\u4e00-\u9fff]+", " ", text)
        words = [w.strip() for w in jieba.lcut(raw) if len(w.strip()) > 1]
        freq: Dict[str, int] = {}
        for word in words:
            if word.isdigit():
                continue
            freq[word] = freq.get(word, 0) + 1
        sorted_words = sorted(freq.items(), key=lambda item: (-item[1], item[0]))
        return [word for word, _ in sorted_words[:top_n]]

    def _check_single_post_viral_alerts(
        self,
        topic_id: str,
        batch: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """本批新抓数据中：单帖互动爆发预警（与聚合 snapshot 告警互补）。"""
        out: List[Dict[str, Any]] = []
        if not batch:
            return out
        thr = int(self.config.single_post_viral_threshold or 0)
        if thr <= 0:
            return out
        existing = self.db.list_alerts(topic_id=topic_id, is_resolved=False, limit=80)
        seen_post_ids: set[str] = set()
        for a in existing:
            if str(a.get("alert_type") or "") != "viral_post":
                continue
            meta = a.get("metadata") if isinstance(a.get("metadata"), dict) else {}
            pid = str(meta.get("post_id") or "").strip()
            if pid:
                seen_post_ids.add(pid)
        for item in batch:
            eng = int(item.get("likes") or 0) + int(item.get("comments") or 0) + int(item.get("shares") or 0)
            if eng < thr:
                continue
            pid = str(item.get("id", "") or "").strip()
            if pid and pid in seen_post_ids:
                continue
            title = str(item.get("title", "") or "")[:120]
            out.append(
                self.db.create_alert(
                    topic_id=topic_id,
                    alert_type="viral_post",
                    title="单帖互动量异常偏高",
                    message=f"单帖总互动 {eng}（阈值 {thr}）。标题摘要：{title}",
                    severity="warning",
                    metadata={
                        "post_id": pid,
                        "url": str(item.get("url", "") or ""),
                        "platform": str(item.get("platform", "") or ""),
                        "engagement": eng,
                    },
                )
            )
            if pid:
                seen_post_ids.add(pid)
        return out

    def _check_alerts(self, topic_id: str, snapshot: Dict[str, Any]) -> List[Dict[str, Any]]:
        alerts: List[Dict[str, Any]] = []
        existing = self.db.list_alerts(topic_id=topic_id, is_resolved=False, limit=50)

        def can_emit(alert_type: str) -> bool:
            if not existing:
                return True
            cutoff = _utcnow() - timedelta(hours=self.config.alert_cooldown_hours)
            for alert in existing:
                if str(alert.get("alert_type") or "") != alert_type:
                    continue
                created_at = alert.get("created_at")
                if isinstance(created_at, str):
                    try:
                        created_at = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
                        if created_at.tzinfo is not None:
                            created_at = created_at.replace(tzinfo=None)
                    except Exception:
                        created_at = None
                if isinstance(created_at, datetime) and created_at >= cutoff:
                    return False
            return True

        if snapshot.get("volume_trend") == "up" and can_emit("volume_spike"):
            alerts.append(self.db.create_alert(
                topic_id=topic_id,
                alert_type="volume_spike",
                title="话题热度上升",
                message=f"当前帖子数量显著增加：{snapshot.get('post_count')} 条，趋势：{snapshot.get('volume_trend')}",
                severity="warning",
            ))

        if int(snapshot.get("engagement_sum") or 0) >= self.config.viral_threshold and can_emit("viral_content"):
            alerts.append(self.db.create_alert(
                topic_id=topic_id,
                alert_type="viral_content",
                title="发现高互动内容",
                message=f"当前总互动量达到 {snapshot.get('engagement_sum')}，可能出现热点传播。",
                severity="info",
            ))

        if float(snapshot.get("avg_sentiment") or 0.0) < -0.4 and can_emit("negative_sentiment"):
            alerts.append(self.db.create_alert(
                topic_id=topic_id,
                alert_type="negative_sentiment",
                title="舆情负面情绪偏高",
                message=f"平均情感分数 {snapshot.get('avg_sentiment')}，建议关注传播节奏与响应策略。",
                severity="warning",
            ))

        return alerts

    def get_topic_status(self, topic_id: str) -> Dict[str, Any]:
        topic = self.db.get_topic_by_id(topic_id)
        if not topic:
            return {"error": "话题不存在"}
        keywords = self.db.get_topic_keywords(topic_id)
        latest_snapshot = self.db.get_latest_snapshot(topic_id)
        alerts = self.db.list_alerts(topic_id=topic_id, is_resolved=False)
        linked_cases = self.db.get_linked_cases(topic_id=topic_id, min_score=0.1)
        return {
            "topic": topic,
            "keywords": keywords,
            "latest_snapshot": latest_snapshot,
            "active_alerts": alerts,
            "linked_cases": linked_cases,
        }

    def run_monitoring_cycle(
        self,
        topic_ids: List[str],
        search_func: Optional[SearchFunc] = None,
    ) -> Dict[str, Any]:
        results: List[Dict[str, Any]] = []
        for topic_id in topic_ids:
            keyword_records = self.db.get_topic_keywords(topic_id)
            keyword_list = [str(k.get("keyword") or "") for k in keyword_records if str(k.get("keyword") or "").strip()]
            if search_func:
                search_results = search_func(keyword_list, topic_id, len(results))
            else:
                search_results = []
            result = self.scan_topic(topic_id, search_results)
            results.append({"topic_id": topic_id, "snapshot": result["snapshot"], "alerts": result["alerts"]})
        return {"results": results}

    def generate_periodic_report(
        self,
        topic_id: str,
        period: str = "daily",
        output_dir: Optional[Path] = None,
    ) -> Dict[str, Any]:
        status = self.get_topic_status(topic_id)
        if status.get("error"):
            raise ValueError(status["error"])
        topic = status["topic"]
        snapshots = self.db.get_snapshots(topic_id, limit=20)
        alerts = self.db.list_alerts(topic_id=topic_id, limit=20)
        cases = self.db.get_linked_cases(topic_id=topic_id, min_score=0.1)

        period_label = "日报" if period.lower() in ("daily", "day") else "周报" if period.lower() in ("weekly", "week") else period
        output_dir = output_dir or (Path(__file__).resolve().parents[1] / "topic_monitoring_reports")
        output_dir.mkdir(parents=True, exist_ok=True)
        now = _utcnow()
        report_path = output_dir / f"{self._safe_filename(topic.get('name','topic'))}_{period_label}_{now.strftime('%Y%m%d_%H%M%S')}.md"

        p = str(period or "").lower()
        if p in ("daily", "day"):
            since = now - timedelta(days=1)
            window_note = "最近 24 小时"
        elif p in ("weekly", "week"):
            since = now - timedelta(days=7)
            window_note = "最近 7 天"
        else:
            since = now - timedelta(days=1)
            window_note = "最近 24 小时（默认）"

        tid = str(topic.get("id") or "")
        period_posts: List[Dict[str, Any]] = []
        if tid and hasattr(self.db, "get_collected_posts"):
            period_posts = self.db.get_collected_posts(tid, limit=8000, since=since)

        content = self._build_report_markdown(
            topic,
            snapshots,
            alerts,
            cases,
            period_label,
            period_posts=period_posts,
            period_window_note=window_note,
        )
        report_path.write_text(content, encoding="utf-8")
        return {
            "topic_id": topic_id,
            "topic_name": topic.get("name"),
            "period": period_label,
            "report_path": str(report_path),
            "generated_at": now.isoformat(),
        }

    def _safe_filename(self, text: str) -> str:
        safe = re.sub(r"[^\w\u4e00-\u9fff-]+", "_", str(text or "")).strip("_")
        return safe[:64] or "topic_report"

    def _build_report_markdown(
        self,
        topic: Dict[str, Any],
        snapshots: List[Dict[str, Any]],
        alerts: List[Dict[str, Any]],
        cases: List[Dict[str, Any]],
        period_label: str,
        *,
        period_posts: Optional[List[Dict[str, Any]]] = None,
        period_window_note: str = "",
    ) -> str:
        title = topic.get("name", "专题")
        lines: List[str] = [f"# {title} {period_label}报告", "", f"生成时间：{_utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')}", ""]
        lines.append(f"- 专题领域：{topic.get('domain', '')}")
        lines.append(f"- 话题描述：{topic.get('description', '')}")
        lines.append(f"- 关键词：{', '.join(str(k.get('keyword') or '') for k in self.db.get_topic_keywords(topic.get('id')) if str(k.get('keyword') or '').strip())}")
        lines.append(
            "- 分析维度：与事件分析对齐（声量结构、情感、平台分布、关键节点等）；"
            "专题下可出现**多个时间峰值/多条子事件线**，需结合快照序列阅读。"
        )
        cfg = topic.get("config") if isinstance(topic.get("config"), dict) else {}
        if cfg:
            lines.append(
                f"- 监测配置：间隔≈{cfg.get('collect_interval_hours', '—')}h；"
                f"NetInsight 条数提示上限≈{cfg.get('netinsight_max_rows_hint', '—')}。"
            )
        lines.append("")

        posts = period_posts or []
        note = period_window_note or "本周期"
        lines.extend([f"## {period_label}数据增量（{note}）", ""])
        if posts:
            eng = sum(
                int(p.get("likes") or 0) + int(p.get("comments") or 0) + int(p.get("shares") or 0)
                for p in posts
            )
            lines.append(f"- 窗口内抓取帖数：**{len(posts)}**")
            lines.append(f"- 窗口内总互动（粗）：**{eng}**")
            lines.append("- 说明：增量口径按 ``collected_at`` 落在窗口内统计；跨周期去重需在入库层按 post_id/url 归并。")
        else:
            lines.append("- 本窗口内暂无增量帖子（或未注入外部采集 / 本轮未拉到新数据）。")
        lines.append("")

        if snapshots:
            latest = snapshots[0]
            lines.extend([
                "## 最新快照",
                "",
                f"- 采集时间：{latest.get('created_at', '')}",
                f"- 帖子数：{latest.get('post_count', 0)}",
                f"- 互动总量：{latest.get('engagement_sum', 0)}",
                f"- 平均情感：{latest.get('avg_sentiment', 0.0)}",
                f"- 趋势：{latest.get('volume_trend', 'stable')}",
                f"- 关键词：{', '.join(latest.get('top_keywords') or [])}",
                f"- 摘要：{latest.get('summary', '')}",
                "",
            ])
        else:
            lines.extend(["## 最新快照", "", "- 无快照数据", ""])

        if alerts:
            lines.extend(["## 活动告警", ""])
            for alert in alerts[:10]:
                lines.extend([
                    f"- [{alert.get('severity', '').upper()}] {alert.get('title', '')}",
                    f"  - 类型：{alert.get('alert_type', '')}",
                    f"  - 时间：{alert.get('created_at', '')}",
                    f"  - 内容：{alert.get('message', '')}",
                    "",
                ])
        else:
            lines.extend(["## 活动告警", "", "- 暂无未解决告警", ""])

        if cases:
            lines.extend(["## 关联案例", ""])
            for case in cases[:6]:
                lines.extend([
                    f"- {case.get('case_title', '')}",
                    f"  - 领域：{case.get('case_domain', '')}",
                    f"  - 相关度：{case.get('relevance_score', 0.0)}",
                    f"  - 链接：{case.get('case_url', '')}",
                    "",
                ])
        else:
            lines.extend(["## 关联案例", "", "- 暂无关联案例", ""])

        lines.extend([
            "## 近期趋势结构",
            "",
        ])
        for snapshot in snapshots[:6]:
            lines.append(f"- {snapshot.get('created_at', '')} | 帖子 {snapshot.get('post_count', 0)} | 互动 {snapshot.get('engagement_sum', 0)} | 情感 {snapshot.get('avg_sentiment', 0.0)} | 趋势 {snapshot.get('volume_trend', '')}")
        lines.append("")
        return "\n".join(lines)


def create_demo_topic() -> Dict[str, Any]:
    pipeline = TopicMonitoringPipeline()
    return pipeline.create_topic(
        name="高铁舆情",
        domain="交通",
        keywords=["高铁", "动车", "铁路服务", "乘客权益", "列车"] ,
        description="高铁舆情专题监测，覆盖客运服务、投诉与安全议题。",
    )


def _mock_high_speed_rail_search(keyword_list: List[str], topic_id: str, cycle: int = 0) -> List[Dict[str, Any]]:
    count = 4 + cycle * 3
    results: List[Dict[str, Any]] = []
    for idx in range(count):
        sentiment = "negative" if idx % 3 == 0 else "neutral"
        results.append({
            "id": f"{topic_id}-mock-{cycle}-{idx}",
            "url": f"https://example.com/highspeed/{cycle}/{idx}",
            "platform": "微博" if idx % 2 == 0 else "小红书",
            "author": f"用户{idx}",
            "title": f"高铁服务争议示例 {idx}",
            "content": f"高铁服务质量和候车环境收到大量讨论，关键词：{', '.join(keyword_list[:3])}",
            "likes": 18 + idx * 3,
            "comments": 5 + idx,
            "shares": 2 + idx,
            "sentiment": sentiment,
            "tags": ["投诉", "服务"],
            "metadata": {"cycle": cycle},
        })
    return results


def run_high_speed_rail_demo(
    search_func: Optional[SearchFunc] = None,
    cycles: int = 2,
    interval_minutes: int = 1,
    output_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    pipeline = TopicMonitoringPipeline()
    topic = create_demo_topic()
    search_func = search_func or _mock_high_speed_rail_search
    for cycle in range(cycles):
        _ = pipeline.scan_topic(topic["id"], search_func(["高铁", "动车"], topic["id"], cycle))
    report = pipeline.generate_periodic_report(topic["id"], period="daily", output_dir=output_dir)
    return {"topic": topic, "report": report}


if __name__ == "__main__":
    try:
        demo = run_high_speed_rail_demo()
        print("高铁舆情示例专题已部署，报告路径：", demo["report"]["report_path"])
    except Exception as exc:
        print("运行示例失败，请先配置 Supabase/Postgres：", exc)
