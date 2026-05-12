"""
Supabase 数据库客户端模块
用于话题监控数据的存储和管理
"""
import os
from pathlib import Path
from typing import Any, Dict, List, Optional
from dataclasses import dataclass
from datetime import date, datetime
from uuid import UUID
from dotenv import load_dotenv

try:
    from supabase import create_client
except ImportError:  # Optional dependency: topic monitoring must not break event analysis.
    create_client = None  # type: ignore[assignment]

Client = Any

# 强制加载项目根目录下的 .env，避免模块导入前后环境不一致
_project_root = Path(__file__).resolve().parents[1]
_env_path = _project_root / ".env"
if _env_path.is_file():
    load_dotenv(_env_path, override=True)
else:
    load_dotenv()

try:
    import psycopg2
    from psycopg2.extras import DictCursor, Json
    _has_psycopg = True
except ImportError:
    _has_psycopg = False


def _sanitize_for_supabase_json(obj: Any) -> Any:
    """将请求体递归转为 JSON 可序列化类型（PostgREST 使用标准 json 序列化）。"""
    if obj is None or isinstance(obj, (bool, str, int, float)):
        return obj
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, date):
        return obj.isoformat()
    if isinstance(obj, UUID):
        return str(obj)
    try:
        from decimal import Decimal

        if isinstance(obj, Decimal):
            return float(obj)
    except ImportError:
        pass
    if isinstance(obj, dict):
        return {str(k): _sanitize_for_supabase_json(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_sanitize_for_supabase_json(v) for v in obj]
    if isinstance(obj, set):
        return [_sanitize_for_supabase_json(v) for v in obj]
    return obj


@dataclass
class SupabaseConfig:
    """Supabase/Postgres 配置"""
    url: str = ""
    key: str = ""
    database_url: str = ""

    @classmethod
    def from_env(cls) -> "SupabaseConfig":
        url = os.environ.get("SUPABASE_URL", "")
        key = os.environ.get("SUPABASE_KEY", "")
        database_url = os.environ.get("DATABASE_URL") or os.environ.get("POSTGRES_URL") or ""
        if url and key:
            return cls(url=url, key=key, database_url="")
        if database_url:
            return cls(url="", key="", database_url=database_url)
        raise ValueError("请在 .env 中配置 SUPABASE_URL/SUPABASE_KEY 或 DATABASE_URL/POSTGRES_URL")


def topic_store_configured() -> bool:
    """是否配置了外部 Supabase/Postgres；未配置时专题监测可使用内存演示模式。"""
    return bool(
        (os.environ.get("SUPABASE_URL") and os.environ.get("SUPABASE_KEY"))
        or os.environ.get("DATABASE_URL")
        or os.environ.get("POSTGRES_URL")
    )


class SupabaseDB:
    """Supabase/Postgres 数据库操作类"""

    def __init__(self, config: Optional[SupabaseConfig] = None):
        self.config = config or SupabaseConfig.from_env()
        self.is_postgres = bool(self.config.database_url)
        self.is_supabase = bool(self.config.url and self.config.key)
        self.client: Optional[Client] = None
        self.conn = None

        if self.is_supabase:
            if create_client is None:
                raise ImportError(
                    "未安装 supabase，可先运行 `pip install supabase`，"
                    "或不配置 SUPABASE_URL/SUPABASE_KEY 使用专题监测内存演示模式。"
                )
            try:
                self.client = create_client(self.config.url, self.config.key)
            except Exception as exc:
                raise ConnectionError(
                    "Supabase 连接失败，请检查 SUPABASE_URL 和 SUPABASE_KEY 是否正确，或数据库服务是否可达。"
                ) from exc
        elif self.is_postgres:
            if not _has_psycopg:
                raise ImportError(
                    "未安装 psycopg2，请先在 requirements.txt 中添加 psycopg2-binary 并安装依赖。"
                )
            try:
                self.conn = psycopg2.connect(self.config.database_url, cursor_factory=DictCursor)
                self.conn.autocommit = True
            except Exception as exc:
                raise ConnectionError(
                    "Postgres 连接失败，请检查 DATABASE_URL/POSTGRES_URL 是否正确，或数据库服务是否可达。"
                ) from exc
        else:
            raise ValueError("请在 .env 中配置 SUPABASE_URL/SUPABASE_KEY 或 DATABASE_URL/POSTGRES_URL")

    def _timestamp_for_write(self) -> Any:
        """写入时间戳：Supabase HTTP JSON 须可序列化；直连 Postgres 使用 datetime。"""
        if self.is_supabase:
            return datetime.utcnow().isoformat()
        return datetime.utcnow()

    def _fetch_one(self, sql: str, params: Optional[tuple] = None) -> Optional[Dict[str, Any]]:
        assert self.conn is not None
        with self.conn.cursor() as cursor:
            cursor.execute(sql, params or ())
            row = cursor.fetchone()
            return dict(row) if row is not None else None

    def _fetch_all(self, sql: str, params: Optional[tuple] = None) -> List[Dict[str, Any]]:
        assert self.conn is not None
        with self.conn.cursor() as cursor:
            cursor.execute(sql, params or ())
            rows = cursor.fetchall()
            return [dict(row) for row in rows]

    def _execute(self, sql: str, params: Optional[tuple] = None) -> None:
        assert self.conn is not None
        with self.conn.cursor() as cursor:
            cursor.execute(sql, params or ())

    def _normalize_row(self, row: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(row, dict):
            return {}
        return {k: (list(v) if isinstance(v, tuple) else v) for k, v in row.items()}

    # ============ Monitor Topics 表 ============

    def create_monitor_topic(
        self,
        name: str,
        domain: str,
        description: str = "",
        owner: str = "system"
    ) -> Dict[str, Any]:
        now_ts = self._timestamp_for_write()
        data = {
            "name": name,
            "domain": domain,
            "description": description,
            "owner": owner,
            "is_active": True,
            "config": {},
            "created_at": now_ts,
            "updated_at": now_ts,
        }
        if self.is_supabase:
            return (
                self.client.table("monitor_topics")
                .insert(_sanitize_for_supabase_json(data))
                .execute()
                .data[0]
            )
        sql = """
            INSERT INTO monitor_topics (name, domain, description, owner, is_active, config, created_at, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING *
        """
        row = self._fetch_one(sql, (
            data["name"],
            data["domain"],
            data["description"],
            data["owner"],
            data["is_active"],
            Json(data["config"]),
            data["created_at"],
            data["updated_at"],
        ))
        return self._normalize_row(row) if row else {}

    def get_topic_by_id(self, topic_id: str) -> Optional[Dict[str, Any]]:
        if self.is_supabase:
            result = self.client.table("monitor_topics").select("*").eq("id", topic_id).limit(1).execute()
            return (result.data or [None])[0]
        sql = "SELECT * FROM monitor_topics WHERE id = %s LIMIT 1"
        row = self._fetch_one(sql, (topic_id,))
        return self._normalize_row(row) if row else None

    def list_monitor_topics(
        self,
        is_active: Optional[bool] = None,
        domain: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        if self.is_supabase:
            query = self.client.table("monitor_topics").select("*")
            if is_active is not None:
                query = query.eq("is_active", is_active)
            if domain:
                query = query.eq("domain", domain)
            return query.execute().data
        sql = "SELECT * FROM monitor_topics WHERE 1=1"
        params: List[Any] = []
        if is_active is not None:
            sql += " AND is_active = %s"
            params.append(is_active)
        if domain:
            sql += " AND domain = %s"
            params.append(domain)
        return [self._normalize_row(r) for r in self._fetch_all(sql, tuple(params))]

    def update_monitor_topic(
        self,
        topic_id: str,
        updates: Dict[str, Any]
    ) -> Dict[str, Any]:
        updates["updated_at"] = self._timestamp_for_write()
        if self.is_supabase:
            return (
                self.client.table("monitor_topics")
                .update(_sanitize_for_supabase_json(dict(updates)))
                .eq("id", topic_id)
                .execute()
                .data[0]
            )
        fields = [f"{k} = %s" for k in updates.keys()]
        sql = f"UPDATE monitor_topics SET {', '.join(fields)} WHERE id = %s RETURNING *"
        params = tuple(
            Json(v) if isinstance(v, dict) else v
            for v in updates.values()
        ) + (topic_id,)
        row = self._fetch_one(sql, params)
        return self._normalize_row(row) if row else {}

    # ============ Topic Keywords 表 ============

    def add_topic_keywords(
        self,
        topic_id: str,
        keywords: List[Dict[str, str]]
    ) -> List[Dict[str, Any]]:
        now = self._timestamp_for_write()
        for kw in keywords:
            kw["topic_id"] = topic_id
            kw["created_at"] = now
        if self.is_supabase:
            return (
                self.client.table("topic_keywords")
                .insert(_sanitize_for_supabase_json(keywords))
                .execute()
                .data
            )
        rows = []
        for kw in keywords:
            sql = """
                INSERT INTO topic_keywords (topic_id, keyword, keyword_type, weight, created_at)
                VALUES (%s, %s, %s, %s, %s)
                RETURNING *
            """
            row = self._fetch_one(sql, (
                kw["topic_id"],
                kw["keyword"],
                kw.get("keyword_type", "include"),
                kw.get("weight", 1.0),
                kw["created_at"],
            ))
            if row:
                rows.append(self._normalize_row(row))
        return rows

    def get_topic_keywords(self, topic_id: str) -> List[Dict[str, Any]]:
        if self.is_supabase:
            return self.client.table("topic_keywords").select("*").eq("topic_id", topic_id).execute().data
        sql = "SELECT * FROM topic_keywords WHERE topic_id = %s ORDER BY created_at DESC"
        return [self._normalize_row(r) for r in self._fetch_all(sql, (topic_id,))]

    def delete_topic_keyword(self, keyword_id: str) -> None:
        if self.is_supabase:
            self.client.table("topic_keywords").delete().eq("id", keyword_id).execute()
            return
        sql = "DELETE FROM topic_keywords WHERE id = %s"
        self._execute(sql, (keyword_id,))

    # ============ Collected Posts 表 ============

    def collect_post(
        self,
        topic_id: str,
        post_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        data = {
            "topic_id": topic_id,
            **post_data,
            "collected_at": self._timestamp_for_write(),
        }
        if self.is_supabase:
            return (
                self.client.table("collected_posts")
                .insert(_sanitize_for_supabase_json(data))
                .execute()
                .data[0]
            )
        sql = """
            INSERT INTO collected_posts (
                topic_id, post_id, post_url, platform, author, title, content,
                likes, comments, shares, sentiment, tags, metadata, collected_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING *
        """
        row = self._fetch_one(sql, (
            data["topic_id"],
            data.get("post_id"),
            data.get("post_url"),
            data.get("platform"),
            data.get("author"),
            data.get("title"),
            data.get("content"),
            data.get("likes"),
            data.get("comments"),
            data.get("shares"),
            data.get("sentiment"),
            data.get("tags"),
            Json(data.get("metadata", {})),
            data["collected_at"],
        ))
        return self._normalize_row(row) if row else {}

    def bulk_collect_posts(
        self,
        topic_id: str,
        posts: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        if self.is_supabase:
            now = self._timestamp_for_write()
            for post in posts:
                post["topic_id"] = topic_id
                post["collected_at"] = now
            return (
                self.client.table("collected_posts")
                .insert(_sanitize_for_supabase_json(posts))
                .execute()
                .data
            )
        rows = []
        for post in posts:
            rows.append(self.collect_post(topic_id, post))
        return rows

    def get_collected_posts(
        self,
        topic_id: str,
        limit: int = 100,
        since: Optional[datetime] = None
    ) -> List[Dict[str, Any]]:
        if self.is_supabase:
            query = self.client.table("collected_posts").select("*").eq("topic_id", topic_id)
            if since:
                query = query.gte("collected_at", since.isoformat())
            return query.order("collected_at", desc=True).limit(limit).execute().data
        sql = "SELECT * FROM collected_posts WHERE topic_id = %s"
        params: List[Any] = [topic_id]
        if since:
            sql += " AND collected_at >= %s"
            params.append(since)
        sql += " ORDER BY collected_at DESC LIMIT %s"
        params.append(limit)
        return [self._normalize_row(r) for r in self._fetch_all(sql, tuple(params))]

    # ============ Topic Snapshots 表 ============

    def create_snapshot(
        self,
        topic_id: str,
        snapshot_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        data = {
            "topic_id": topic_id,
            **snapshot_data,
            "created_at": self._timestamp_for_write(),
        }
        if self.is_supabase:
            return (
                self.client.table("topic_snapshots")
                .insert(_sanitize_for_supabase_json(data))
                .execute()
                .data[0]
            )
        sql = """
            INSERT INTO topic_snapshots (
                topic_id, post_count, engagement_sum, avg_sentiment, top_keywords,
                volume_trend, summary, created_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING *
        """
        row = self._fetch_one(sql, (
            data["topic_id"],
            data.get("post_count"),
            data.get("engagement_sum"),
            data.get("avg_sentiment"),
            data.get("top_keywords"),
            data.get("volume_trend"),
            data.get("summary"),
            data["created_at"],
        ))
        return self._normalize_row(row) if row else {}

    def get_latest_snapshot(self, topic_id: str) -> Optional[Dict[str, Any]]:
        if self.is_supabase:
            result = self.client.table("topic_snapshots").select("*").eq("topic_id", topic_id).order("created_at", desc=True).limit(1).execute()
            return (result.data or [None])[0]
        sql = "SELECT * FROM topic_snapshots WHERE topic_id = %s ORDER BY created_at DESC LIMIT 1"
        row = self._fetch_one(sql, (topic_id,))
        return self._normalize_row(row) if row else None

    def get_snapshots(
        self,
        topic_id: str,
        limit: int = 10
    ) -> List[Dict[str, Any]]:
        if self.is_supabase:
            return self.client.table("topic_snapshots").select("*").eq("topic_id", topic_id).order("created_at", desc=True).limit(limit).execute().data
        sql = "SELECT * FROM topic_snapshots WHERE topic_id = %s ORDER BY created_at DESC LIMIT %s"
        return [self._normalize_row(r) for r in self._fetch_all(sql, (topic_id, limit))]

    # ============ Alerts 表 ============

    def create_alert(
        self,
        topic_id: str,
        alert_type: str,
        title: str,
        message: str,
        severity: str = "info",  # info, warning, critical
        metadata: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        data = {
            "topic_id": topic_id,
            "alert_type": alert_type,
            "title": title,
            "message": message,
            "severity": severity,
            "metadata": metadata or {},
            "is_resolved": False,
            "created_at": self._timestamp_for_write(),
        }
        if self.is_supabase:
            return (
                self.client.table("alerts")
                .insert(_sanitize_for_supabase_json(data))
                .execute()
                .data[0]
            )
        sql = """
            INSERT INTO alerts (
                topic_id, alert_type, title, message, severity, metadata,
                is_resolved, created_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING *
        """
        row = self._fetch_one(sql, (
            data["topic_id"],
            data["alert_type"],
            data["title"],
            data["message"],
            data["severity"],
            Json(data["metadata"]),
            data["is_resolved"],
            data["created_at"],
        ))
        return self._normalize_row(row) if row else {}

    def list_alerts(
        self,
        topic_id: Optional[str] = None,
        is_resolved: Optional[bool] = None,
        severity: Optional[str] = None,
        limit: int = 50
    ) -> List[Dict[str, Any]]:
        if self.is_supabase:
            query = self.client.table("alerts").select("*")
            if topic_id:
                query = query.eq("topic_id", topic_id)
            if is_resolved is not None:
                query = query.eq("is_resolved", is_resolved)
            if severity:
                query = query.eq("severity", severity)
            return query.order("created_at", desc=True).limit(limit).execute().data
        sql = "SELECT * FROM alerts WHERE 1=1"
        params: List[Any] = []
        if topic_id:
            sql += " AND topic_id = %s"
            params.append(topic_id)
        if is_resolved is not None:
            sql += " AND is_resolved = %s"
            params.append(is_resolved)
        if severity:
            sql += " AND severity = %s"
            params.append(severity)
        sql += " ORDER BY created_at DESC LIMIT %s"
        params.append(limit)
        return [self._normalize_row(r) for r in self._fetch_all(sql, tuple(params))]

    def resolve_alert(self, alert_id: str) -> Dict[str, Any]:
        if self.is_supabase:
            payload = _sanitize_for_supabase_json({
                "is_resolved": True,
                "resolved_at": datetime.utcnow().isoformat(),
            })
            return self.client.table("alerts").update(payload).eq("id", alert_id).execute().data[0]
        sql = """
            UPDATE alerts SET is_resolved = TRUE, resolved_at = %s WHERE id = %s RETURNING *
        """
        row = self._fetch_one(sql, (datetime.utcnow(), alert_id))
        return self._normalize_row(row) if row else {}

    # ============ Case Links 表 ============

    def link_case(
        self,
        topic_id: str,
        case_title: str,
        case_domain: str,
        case_url: str,
        relevance_score: float = 1.0,
        evidence: str = ""
    ) -> Dict[str, Any]:
        data = {
            "topic_id": topic_id,
            "case_title": case_title,
            "case_domain": case_domain,
            "case_url": case_url,
            "relevance_score": relevance_score,
            "evidence": evidence,
            "linked_at": self._timestamp_for_write(),
        }
        if self.is_supabase:
            return (
                self.client.table("case_links")
                .insert(_sanitize_for_supabase_json(data))
                .execute()
                .data[0]
            )
        sql = """
            INSERT INTO case_links (
                topic_id, case_title, case_domain, case_url,
                relevance_score, evidence, linked_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s)
            RETURNING *
        """
        row = self._fetch_one(sql, (
            data["topic_id"],
            data["case_title"],
            data["case_domain"],
            data["case_url"],
            data["relevance_score"],
            data["evidence"],
            data["linked_at"],
        ))
        return self._normalize_row(row) if row else {}

    def get_linked_cases(
        self,
        topic_id: str,
        min_score: float = 0.5
    ) -> List[Dict[str, Any]]:
        if self.is_supabase:
            return self.client.table("case_links").select("*").eq("topic_id", topic_id).gte("relevance_score", min_score).order("relevance_score", desc=True).execute().data
        sql = "SELECT * FROM case_links WHERE topic_id = %s AND relevance_score >= %s ORDER BY relevance_score DESC"
        return [self._normalize_row(r) for r in self._fetch_all(sql, (topic_id, min_score))]


def get_db() -> SupabaseDB:
    """获取数据库实例"""
    return SupabaseDB()
