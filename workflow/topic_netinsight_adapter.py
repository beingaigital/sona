"""专题监测 NetInsight 采集桥接：复用 opinion-system 的 NetInsight 工作流。

`opinion-system`（`/Users/biaowenhuang/Documents/opinion-system`）中的
``backend/src/netinsight/client.py`` 与 ``worker.py`` 实现了与 Sona 同源的
网察登录、分平台计数、按比例配额与列表拉取、正文去重等逻辑。
本模块**不拷贝**大段实现，而是在运行时把 ``opinion-system/backend`` 加入
``sys.path`` 后 import 其 ``src.netinsight.client``，再把其
``normalize_record`` 产出行映射为 ``TopicMonitoringPipeline.scan_topic`` 所需结构。

环境变量：

- ``SONA_OPINION_SYSTEM_ROOT``：opinion-system 仓库根目录；未设置时默认使用
  本机常见路径 ``/Users/biaowenhuang/Documents/opinion-system``（其他机器请显式配置）。
- ``SONA_TOPIC_MONITOR_USE_OPINION_NETINSIGHT``：设为 ``1``/``true`` 时，
  ``scripts/run_topic_monitor_tick.py`` 会对活跃专题注入本模块提供的 ``search_func``。
- 网察账号与 Sona 现有工具一致：``NETINSIGHT_USER`` / ``NETINSIGHT_PASS``（及
  ``NETINSIGHT_HEADLESS``、``NETINSIGHT_NO_PROXY`` 等，见 opinion-system 与 Sona README）。
- ``SONA_TOPIC_MONITOR_NETINSIGHT_WINDOW_HOURS``：单次拉取时间窗（小时），默认 ``24``。
"""

from __future__ import annotations

import logging
import os
import sys
import uuid
from datetime import timedelta
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from workflow.netinsight_keywords import NETINSIGHT_PLATFORMS
from workflow.topic_monitoring_pipeline import TopicMonitoringPipeline, _utcnow

LOGGER = logging.getLogger(__name__)

SearchFunc = Callable[[List[str], str, int], List[Dict[str, Any]]]

_CACHED_CTX: Any = None
_CACHED_USER: str = ""


def opinion_system_root() -> Path:
    raw = os.environ.get("SONA_OPINION_SYSTEM_ROOT", "/Users/biaowenhuang/Documents/opinion-system")
    return Path(raw).expanduser().resolve()


def _bootstrap_opinion_import_path(root: Path) -> None:
    backend = root / "backend"
    src = backend / "src"
    if not (src / "netinsight" / "client.py").is_file():
        raise FileNotFoundError(f"未找到 opinion-system NetInsight 客户端: {src / 'netinsight' / 'client.py'}")
    for p in (str(backend), str(src)):
        if p not in sys.path:
            sys.path.insert(0, p)


def load_opinion_netinsight_client() -> Any:
    """加载 ``src.netinsight.client``（来自 opinion-system）。"""
    root = opinion_system_root()
    _bootstrap_opinion_import_path(root)
    import importlib

    return importlib.import_module("src.netinsight.client")


def _sona_netinsight_credentials() -> Tuple[str, str]:
    user = str(os.environ.get("NETINSIGHT_USER") or os.environ.get("NETINSIGHT_USERNAME") or "").strip()
    password = str(os.environ.get("NETINSIGHT_PASS") or os.environ.get("NETINSIGHT_PASSWORD") or "").strip()
    return user, password


def format_time_range_hours(hours_back: float) -> str:
    end = _utcnow()
    start = end - timedelta(hours=max(0.25, float(hours_back)))
    fmt = "%Y-%m-%d %H:%M:%S"
    return f"{start.strftime(fmt)};{end.strftime(fmt)}"


def opinion_row_to_scan_post(row: Dict[str, Any]) -> Dict[str, Any]:
    """将 opinion-system ``normalize_record`` 的中文字段行转为 ``scan_topic`` 帖子字典。"""
    pid = str(row.get("原始ID") or row.get("id") or "").strip()
    if not pid:
        pid = f"ni-{uuid.uuid4().hex[:12]}"
    title = str(row.get("标题") or "")
    content = str(row.get("内容") or "")
    em = str(row.get("情感") or "").strip().lower()
    if any(x in em for x in ("负", "消极", "neg")):
        sentiment = "negative"
    elif any(x in em for x in ("正", "积极", "pos")):
        sentiment = "positive"
    else:
        sentiment = "neutral"
    return {
        "id": pid,
        "url": str(row.get("URL") or ""),
        "platform": str(row.get("平台") or "unknown"),
        "author": str(row.get("作者") or ""),
        "title": title,
        "content": content,
        "likes": int(row.get("点赞数") or 0),
        "comments": int(row.get("评论数") or 0),
        "shares": int(row.get("转发数") or 0),
        "sentiment": sentiment,
        "tags": [],
        "metadata": {
            "source": "opinion-system-netinsight",
            "检索词": row.get("检索词"),
            "发布时间": row.get("发布时间"),
        },
    }


def _is_login_expired(exc: BaseException) -> bool:
    return "515" in str(exc) or "登录" in str(exc)


def _get_or_login_context(client: Any) -> Any:
    global _CACHED_CTX, _CACHED_USER
    user, password = _sona_netinsight_credentials()
    if not user or not password:
        raise RuntimeError("未配置 NETINSIGHT_USER / NETINSIGHT_PASS，无法使用 opinion-system NetInsight 采集。")
    if _CACHED_CTX is not None and _CACHED_USER == user:
        return _CACHED_CTX

    headless = str(os.environ.get("NETINSIGHT_HEADLESS", "true")).strip().lower() in ("1", "true", "yes")
    no_proxy = str(os.environ.get("SONA_NETINSIGHT_NO_PROXY", os.environ.get("NETINSIGHT_NO_PROXY", ""))).strip().lower() in (
        "1",
        "true",
        "yes",
    )
    browser_channel = str(os.environ.get("NETINSIGHT_BROWSER_CHANNEL", "") or "").strip()

    LOGGER.info("NetInsight login via opinion-system client (user=%s)", user[:3] + "***")
    ctx = client.login_and_capture(
        user,
        password,
        headless=headless,
        no_proxy=no_proxy,
        browser_channel=browser_channel,
    )
    _CACHED_CTX = ctx
    _CACHED_USER = user
    return ctx


def collect_posts_via_opinion_system(
    *,
    keyword_list: List[str],
    topic_id: str,
    platforms: List[str],
    time_range: str,
    total_limit: int,
    page_size: int = 50,
    sort: str = "comments_desc",
    info_type: str = "2",
    allocate_by_platform: bool = False,
) -> List[Dict[str, Any]]:
    """
    执行一轮与 opinion-system worker 等价的多平台计数 + 拉取 + 去重，并映射为 Sona 帖子结构。
    """
    global _CACHED_CTX
    client = load_opinion_netinsight_client()
    keywords = [str(x).strip() for x in keyword_list if str(x).strip()]
    if not keywords:
        return []

    context = _get_or_login_context(client)
    per_platform_limit = max(1, int(total_limit) // max(len(platforms), 1))

    aggregated_plan: Dict[str, Any] = {}
    all_warnings: List[str] = []
    planned_total = 0

    for platform in platforms:
        try:
            result = client.query_platform_counts(
                keywords=keywords,
                time_range=time_range,
                platform=platform,
                threshold=per_platform_limit,
                context=context,
                progress_callback=None,
            )
        except Exception as exc:  # noqa: BLE001
            if _is_login_expired(exc):
                _CACHED_CTX = None
                context = _get_or_login_context(client)
                result = client.query_platform_counts(
                    keywords=keywords,
                    time_range=time_range,
                    platform=platform,
                    threshold=per_platform_limit,
                    context=context,
                    progress_callback=None,
                )
            else:
                LOGGER.warning("NetInsight 计数失败 platform=%s err=%s", platform, exc)
                continue
        aggregated_plan[platform] = result
        planned_total += int(result.get("planned_total") or 0)
        all_warnings.extend(result.get("warnings") or [])

    if allocate_by_platform and len(platforms) > 1:
        platform_totals = {
            str(p): int((aggregated_plan.get(p) or {}).get("total_available") or 0) for p in platforms
        }
        platform_limits = client.allocate_platform_limits(platform_totals, int(total_limit))
        planned_total = 0
        for platform in platforms:
            plan = aggregated_plan.get(platform) or {}
            raw_counts = plan.get("raw_counts") or {}
            platform_limit = max(0, int(platform_limits.get(platform) or 0))
            search_matrix = client.allocate_platform_limits(raw_counts, platform_limit)
            plan["search_matrix"] = search_matrix
            plan["planned_total"] = sum(search_matrix.values())
            aggregated_plan[platform] = plan
            planned_total += int(plan["planned_total"] or 0)

    if planned_total <= 0:
        LOGGER.warning("NetInsight 无可采数据 topic=%s warnings=%s", topic_id, all_warnings[:3])
        return []

    all_records: List[Dict[str, Any]] = []
    for platform in platforms:
        platform_plan = aggregated_plan.get(platform) or {}
        search_matrix = platform_plan.get("search_matrix") or {}
        if not search_matrix:
            continue
        try:
            result = client.collect_platform_records(
                search_matrix=search_matrix,
                time_range=time_range,
                platform=platform,
                context=context,
                page_size=page_size,
                sort=sort,
                info_type=info_type,
                task_id=str(topic_id),
                progress_callback=None,
            )
        except Exception as exc:  # noqa: BLE001
            if _is_login_expired(exc):
                _CACHED_CTX = None
                context = _get_or_login_context(client)
                result = client.collect_platform_records(
                    search_matrix=search_matrix,
                    time_range=time_range,
                    platform=platform,
                    context=context,
                    page_size=page_size,
                    sort=sort,
                    info_type=info_type,
                    task_id=str(topic_id),
                    progress_callback=None,
                )
            else:
                LOGGER.warning("NetInsight 拉取失败 platform=%s err=%s", platform, exc)
                continue
        all_records.extend(result.get("records") or [])

    deduped, removed = client.deduplicate_records(all_records)
    LOGGER.info(
        "NetInsight opinion bridge topic=%s raw=%s deduped=%s removed=%s",
        topic_id,
        len(all_records),
        len(deduped),
        removed,
    )
    return [opinion_row_to_scan_post(r) for r in deduped]


def build_opinion_netinsight_search_func(
    pipeline: TopicMonitoringPipeline,
    *,
    window_hours: Optional[float] = None,
    total_limit: Optional[int] = None,
    allocate_by_platform: bool = False,
) -> SearchFunc:
    """
    构造 ``TopicMonitoringPipeline.run_monitoring_cycle`` 所需的 ``search_func``。

    每次调用按专题 ``config`` 中的 ``platform_list``（缺省为全平台列表）与
    ``netinsight_max_rows_hint``（缺省 8000）拉取最近 ``window_hours`` 小时数据。
    """

    def _search_func(keyword_list: List[str], topic_id: str, cycle_idx: int) -> List[Dict[str, Any]]:  # noqa: ARG001
        topic = pipeline.db.get_topic_by_id(topic_id) or {}
        cfg = topic.get("config") if isinstance(topic.get("config"), dict) else {}
        pl = cfg.get("platform_list")
        if isinstance(pl, list) and pl:
            platforms = [str(x).strip() for x in pl if str(x).strip()]
        else:
            platforms = list(NETINSIGHT_PLATFORMS)
        lim = int(total_limit or cfg.get("netinsight_max_rows_hint") or 8000)
        lim = max(100, min(lim, 50_000))
        if window_hours is not None:
            wh = float(window_hours)
        else:
            env_w = str(os.environ.get("SONA_TOPIC_MONITOR_NETINSIGHT_WINDOW_HOURS", "") or "").strip()
            if env_w:
                wh = float(env_w)
            else:
                cwh = cfg.get("netinsight_pull_window_hours")
                wh = float(cwh) if cwh not in (None, "") else 24.0
        try:
            wh = float(wh)
        except (TypeError, ValueError):
            wh = 24.0
        wh = max(1.0, min(wh, 720.0))
        tr = format_time_range_hours(wh)
        try:
            return collect_posts_via_opinion_system(
                keyword_list=keyword_list,
                topic_id=topic_id,
                platforms=platforms,
                time_range=tr,
                total_limit=lim,
                allocate_by_platform=allocate_by_platform,
            )
        except FileNotFoundError as exc:
            LOGGER.error("%s", exc)
            return []
        except Exception as exc:  # noqa: BLE001
            LOGGER.exception("opinion-system NetInsight 采集异常 topic=%s", topic_id)
            raise

    return _search_func


def topic_monitor_use_opinion_netinsight() -> bool:
    return str(os.environ.get("SONA_TOPIC_MONITOR_USE_OPINION_NETINSIGHT", "") or "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )
