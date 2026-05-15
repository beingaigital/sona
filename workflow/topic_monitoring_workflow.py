"""专题监测编排：与事件分析共用工具，独立工作流。

与 ``event_analysis_pipeline`` 的差异（体会）：

- **时间结构**：事件分析围绕单一事件窗口；专题监测是滚动时间轴，下面会有**多条声量脊线 / 多个子事件节点**。
- **数据形态**：专题数据是**增量追加 + 去重归并**（同一帖多平台、多次抓取），不同于一次性全量 CSV 跑完即分析。
- **调度**：默认每 ``collect_interval_hours``（如 6h）拉一次增量；常驻定时由 **cron / systemd / K8s CronJob** 调用
  ``scripts/run_topic_monitor_tick.py``（或自建调度）——本库不内置系统级守护进程。
- **NetInsight**：单专题/单次拉取常见上限约 **1 万条**；若预估窗口内总量逼近上限，应**缩短间隔**、**收窄检索式**或**分页按时间切片**（见 ``suggest_interval_for_netinsight``）。
- **工具复用**：关键词阶段与事件分析一致，可调用 ``extract_search_terms``；词表 / 普通-or-高级模式与 ``build_data_num_search_words`` 对齐，便于后续接 ``data_num`` / ``data_collect``。
- **opinion-system 桥接**：若本地克隆了 ``opinion-system``，可用 ``workflow/topic_netinsight_adapter`` 在运行时加载其 ``src.netinsight.client``，将 opinion-system worker 的多平台计数 + 配额拉取 + 去重接到 ``run_monitoring_cycle`` 的 ``search_func``（见 ``SONA_TOPIC_MONITOR_USE_OPINION_NETINSIGHT``）。

环境变量（可选）：

- ``SONA_MONITOR_SKIP_EXTRACT``：设为 ``1`` 跳过 ``extract_search_terms``（离线/无密钥时）。
- ``SONA_TOPIC_MONITOR_INTERVAL_HOURS``：默认采集间隔建议（写入专题 config）。
- ``SONA_TOPIC_MONITOR_USE_OPINION_NETINSIGHT``：``1`` 时 ``run_topic_monitor_tick`` 使用 opinion-system NetInsight 客户端拉数（需 ``SONA_OPINION_SYSTEM_ROOT`` + 网察账号）。
- ``SONA_OPINION_SYSTEM_ROOT``：opinion-system 仓库根路径。
- ``SONA_TOPIC_MONITOR_NETINSIGHT_WINDOW_HOURS``：单次拉取时间窗（小时），默认 ``24``。

from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional, Sequence

from tools.extract_search_terms import extract_search_terms
from workflow.netinsight_keywords import NETINSIGHT_PLATFORMS


def _parse_tool_json(raw: str) -> Dict[str, Any]:
    t = (raw or "").strip()
    if not t:
        return {}
    try:
        out = json.loads(t)
    except json.JSONDecodeError:
        return {"_parse_error": "invalid_json", "raw_preview": t[:400]}
    return out if isinstance(out, dict) else {"_parse_error": "not_object", "value": out}


def invoke_extract_search_terms(*, query: str) -> Dict[str, Any]:
    """调用与事件分析 Step1 相同的 ``extract_search_terms``，返回 dict。"""
    raw = extract_search_terms.invoke({"query": query})
    if not isinstance(raw, str):
        raw = str(raw)
    return _parse_tool_json(raw)


def _normalize_search_words(plan: Dict[str, Any]) -> List[str]:
    sw = plan.get("searchWords")
    if sw is None:
        sw = plan.get("search_words")
    if isinstance(sw, str) and sw.strip():
        return [sw.strip()]
    if isinstance(sw, list):
        return [str(x).strip() for x in sw if str(x).strip()]
    return []


def dedupe_keywords(items: Sequence[str], *, max_items: int = 24) -> List[str]:
    out: List[str] = []
    seen: set[str] = set()
    for it in items:
        s = str(it or "").strip()
        if len(s) < 2:
            continue
        key = s.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(s)
        if len(out) >= max_items:
            break
    return out


def merge_seed_with_extract_plan(*, seed_keywords: List[str], plan: Dict[str, Any]) -> List[str]:
    """种子词 + extract_search_terms 产出的 searchWords 合并（种子优先）。"""
    extracted = _normalize_search_words(plan)
    return dedupe_keywords(list(seed_keywords) + extracted)


def refine_monitor_keywords(
    *,
    user_text: str,
    seed_keywords: List[str],
) -> Dict[str, Any]:
    """
    专题创建前：用自然语言 + 种子词走一遍 extract，得到更稳的监测词表。

    Returns:
        ``merged_keywords``, ``search_plan``（可能含 error 字段）, ``used_extract`` 等。
    """
    seeds = [str(x).strip() for x in (seed_keywords or []) if str(x).strip()]
    q = str(user_text or "").strip()
    if not q:
        q = "、".join(seeds) if seeds else "舆情专题监测"
    plan: Dict[str, Any] = {}
    err: Optional[str] = None
    try:
        plan = invoke_extract_search_terms(query=q)
        if plan.get("_parse_error"):
            err = str(plan.get("_parse_error"))
    except Exception as exc:  # noqa: BLE001
        err = str(exc)
        plan = {"error": err}

    merged = merge_seed_with_extract_plan(seed_keywords=seeds, plan=plan) if not err else dedupe_keywords(seeds)
    if not merged:
        merged = dedupe_keywords(seeds) or ["舆情"]

    return {
        "search_plan": plan,
        "merged_keywords": merged,
        "used_extract": not bool(err),
        "extract_error": err,
    }


def _env_float(name: str, default: float) -> float:
    raw = str(os.environ.get(name, "") or "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    raw = str(os.environ.get(name, "") or "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def build_default_topic_config(
    *,
    merged_search_plan: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """写入 ``monitor_topics.config`` 的默认工作流元数据（不含密钥）。"""
    interval = _env_float("SONA_TOPIC_MONITOR_INTERVAL_HOURS", 6.0)
    cap = _env_int("SONA_MONITOR_NETINSIGHT_ROW_CAP", 10_000)
    viral_post = _env_int("SONA_MONITOR_VIRAL_POST_THRESHOLD", 5000)
    viral_agg = _env_int("SONA_MONITOR_VIRAL_AGG_THRESHOLD", 1000)
    return {
        "workflow": "topic_monitoring_v1",
        "rolling_monitoring": True,
        "monitoring_started_at": None,
        "platforms": "ALL",
        "platform_list": list(NETINSIGHT_PLATFORMS),
        "collect_interval_hours": max(0.5, min(interval, 168.0)),
        "netinsight_max_rows_hint": max(1000, min(cap, 50_000)),
        "analysis_profile": "event_similar_multi_peak",
        "data_notes": (
            "专题数据建议：按 collected_at 增量入库；跨平台去重可按 content_hash / url 归并；"
            "大流量下优先时间切片拉取，避免单次超过平台条数上限。"
        ),
        "alert_thresholds": {
            "single_post_engagement": viral_post,
            "aggregate_engagement_snapshot": viral_agg,
        },
        "extract_search_plan_snapshot": merged_search_plan or {},
    }


def suggest_interval_for_netinsight(
    *,
    estimated_rows_in_window: int,
    row_cap: int = 10_000,
    desired_coverage: float = 0.85,
) -> Dict[str, Any]:
    """
    在 NetInsight 单次约 ``row_cap`` 条上限下，粗算建议采集间隔（小时）。

    假设：每个间隔窗口内新增帖子近似均匀，希望单次拉取不超过 ``row_cap * desired_coverage``。
    """
    cap = max(500, int(row_cap))
    est = max(0, int(estimated_rows_in_window))
    if est <= 0:
        return {
            "suggested_interval_hours": _env_float("SONA_TOPIC_MONITOR_INTERVAL_HOURS", 6.0),
            "rationale": "无预估条数，使用默认间隔。",
        }
    per_hour = est / max(_env_float("SONA_TOPIC_MONITOR_INTERVAL_HOURS", 6.0), 0.5)
    if per_hour <= 0:
        return {"suggested_interval_hours": 6.0, "rationale": "无法估算速率，使用默认 6h。"}
    budget = cap * max(0.1, min(desired_coverage, 0.99))
    hours = max(0.5, budget / per_hour)
    hours = min(hours, 168.0)
    return {
        "suggested_interval_hours": round(hours, 2),
        "rationale": (
            f"按预估窗口内 {est} 条、折算约 {per_hour:.1f} 条/小时，"
            f"为使单次拉取低于约 {int(budget)} 条，建议间隔 ≥ {hours:.2f} 小时（需结合实际 API 行为调参）。"
        ),
    }


def format_monitor_workflow_hints(config: Dict[str, Any]) -> str:
    """给人看的调度与 NetInsight 提示（Rich 外层可自行加颜色）。"""
    interval = config.get("collect_interval_hours", 6)
    cap = config.get("netinsight_max_rows_hint", 10_000)
    lines = [
        f"- 默认全平台列表已写入 config（共 {len(config.get('platform_list') or [])} 项，采集侧仍可用 ALL）。",
        f"- 建议采集间隔：每 **{interval}** 小时（可用环境变量 SONA_TOPIC_MONITOR_INTERVAL_HOURS 调整）。",
        f"- NetInsight 单次拉取常见上限约 **{cap}** 条：大流量专题请缩短间隔或拆分时间窗。",
        "- 定时执行示例：``0 */6 * * * cd /path/to/sona-master && python3 scripts/run_topic_monitor_tick.py``",
    ]
    return "\n".join(lines)
