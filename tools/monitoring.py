"""舆情专题监测：长期任务、增量采集、指标计算、阈值预警。"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple
from zoneinfo import ZoneInfo

import requests
import yaml

_DEFAULT_CONFIG_PATH = Path("config/config.yaml")
_DEFAULT_STATE_DIR = Path("monitor_state")
_DEFAULT_RESULTS_DIR = Path("monitor_results")
_MAX_STATE_TITLES = 1500

_NEGATIVE_HINTS = (
    "塌房",
    "翻车",
    "投诉",
    "危机",
    "争议",
    "辟谣",
    "事故",
    "暴雷",
    "谣言",
    "违法",
    "处罚",
    "曝光",
    "失控",
    "维权",
)


def get_beijing_time() -> datetime:
    return datetime.now(ZoneInfo("Asia/Shanghai"))


@dataclass
class MonitorTask:
    task_id: str
    name: str
    keywords: List[str]
    platform_ids: List[str]
    interval_minutes: int
    thresholds: Dict[str, float]
    enabled: bool = True


def _load_config(config_path: Optional[str]) -> Dict[str, Any]:
    path = Path(config_path).expanduser().resolve() if config_path else _DEFAULT_CONFIG_PATH.resolve()
    if not path.exists():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except Exception:
        return {}


def _extract_tasks(config: Dict[str, Any]) -> List[MonitorTask]:
    monitor_cfg = config.get("monitor") if isinstance(config.get("monitor"), dict) else {}
    task_rows = monitor_cfg.get("tasks")
    if not isinstance(task_rows, list):
        return []
    tasks: List[MonitorTask] = []
    for row in task_rows:
        if not isinstance(row, dict):
            continue
        task_id = str(row.get("id") or "").strip()
        name = str(row.get("name") or task_id).strip()
        if not task_id:
            continue
        keywords = [str(k).strip() for k in (row.get("keywords") or []) if str(k).strip()]
        platform_ids = [str(p).strip() for p in (row.get("platform_ids") or []) if str(p).strip()]
        if not platform_ids:
            platform_ids = ["weibo", "zhihu", "toutiao", "baidu", "douyin"]
        thresholds = row.get("thresholds") if isinstance(row.get("thresholds"), dict) else {}
        interval_minutes = int(row.get("interval_minutes") or 60)
        tasks.append(
            MonitorTask(
                task_id=task_id,
                name=name or task_id,
                keywords=keywords,
                platform_ids=platform_ids,
                interval_minutes=max(5, interval_minutes),
                thresholds={
                    "volume_spike_ratio": float(thresholds.get("volume_spike_ratio", 1.8)),
                    "negative_ratio": float(thresholds.get("negative_ratio", 0.35)),
                    "new_topic_count": float(thresholds.get("new_topic_count", 8)),
                    "velocity_ratio": float(thresholds.get("velocity_ratio", 0.3)),
                },
                enabled=bool(row.get("enabled", True)),
            )
        )
    return tasks


def _state_file(state_root: Path, task_id: str) -> Path:
    return state_root / "tasks" / f"{task_id}.json"


def _read_state(state_root: Path, task_id: str) -> Dict[str, Any]:
    path = _state_file(state_root, task_id)
    if not path.exists():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f) or {}
    except Exception:
        return {}


def _write_state(state_root: Path, task_id: str, data: Dict[str, Any]) -> None:
    path = _state_file(state_root, task_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _fetch_platform_items(platform_id: str) -> List[Dict[str, Any]]:
    url = f"https://newsnow.busiyi.world/api/s?id={platform_id}&latest"
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Accept": "application/json, text/plain, */*",
        "Referer": "https://newsnow.busiyi.world/",
    }
    try:
        response = requests.get(url, headers=headers, timeout=15)
        response.raise_for_status()
        payload = response.json() or {}
    except Exception:
        return []
    items = payload.get("items")
    if not isinstance(items, list):
        return []
    top_items = items[:30]
    normalized: List[Dict[str, Any]] = []
    for i, item in enumerate(top_items, 1):
        if not isinstance(item, dict):
            continue
        normalized.append(
            {
                "platform_id": platform_id,
                "title": str(item.get("title") or "").strip(),
                "rank": i,
                "hot_value": item.get("hotValue", 0),
                "url": item.get("url") or item.get("mobileUrl") or "",
            }
        )
    return normalized


def _match_keywords(title: str, keywords: List[str]) -> bool:
    if not keywords:
        return True
    t = title.lower()
    for kw in keywords:
        if kw.lower() in t:
            return True
    return False


def _collect_incremental(task: MonitorTask, state: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], Set[str]]:
    seen_titles = {str(x).strip() for x in (state.get("seen_titles") or []) if str(x).strip()}
    all_items: List[Dict[str, Any]] = []
    for platform_id in task.platform_ids:
        all_items.extend(_fetch_platform_items(platform_id))
    filtered = [x for x in all_items if x.get("title") and _match_keywords(str(x.get("title")), task.keywords)]
    new_items = [x for x in filtered if str(x.get("title")) not in seen_titles]
    current_titles = {str(x.get("title")) for x in filtered}
    return filtered, new_items, current_titles


def _compute_metrics(filtered: List[Dict[str, Any]], new_items: List[Dict[str, Any]], state: Dict[str, Any]) -> Dict[str, float]:
    volume_total = float(len(filtered))
    new_topic_count = float(len(new_items))
    negative_count = 0.0
    for item in filtered:
        title = str(item.get("title") or "")
        if any(h in title for h in _NEGATIVE_HINTS):
            negative_count += 1.0
    negative_ratio = (negative_count / volume_total) if volume_total > 0 else 0.0

    prev = state.get("last_metrics") if isinstance(state.get("last_metrics"), dict) else {}
    prev_volume = float(prev.get("volume_total", 0.0) or 0.0)
    if prev_volume <= 0:
        volume_spike_ratio = 1.0 if volume_total > 0 else 0.0
    else:
        volume_spike_ratio = volume_total / prev_volume
    velocity_ratio = (new_topic_count / volume_total) if volume_total > 0 else 0.0
    return {
        "volume_total": volume_total,
        "new_topic_count": new_topic_count,
        "negative_ratio": round(negative_ratio, 4),
        "volume_spike_ratio": round(volume_spike_ratio, 4),
        "velocity_ratio": round(velocity_ratio, 4),
    }


def _evaluate_alerts(metrics: Dict[str, float], thresholds: Dict[str, float]) -> List[str]:
    alerts: List[str] = []
    if metrics.get("volume_spike_ratio", 0.0) >= float(thresholds.get("volume_spike_ratio", 1.8)):
        alerts.append(
            f"声量突增: ratio={metrics.get('volume_spike_ratio')} >= {thresholds.get('volume_spike_ratio')}"
        )
    if metrics.get("negative_ratio", 0.0) >= float(thresholds.get("negative_ratio", 0.35)):
        alerts.append(
            f"负面占比偏高: ratio={metrics.get('negative_ratio')} >= {thresholds.get('negative_ratio')}"
        )
    if metrics.get("new_topic_count", 0.0) >= float(thresholds.get("new_topic_count", 8)):
        alerts.append(
            f"新增话题过多: count={metrics.get('new_topic_count')} >= {thresholds.get('new_topic_count')}"
        )
    if metrics.get("velocity_ratio", 0.0) >= float(thresholds.get("velocity_ratio", 0.3)):
        alerts.append(
            f"传播速度偏快: ratio={metrics.get('velocity_ratio')} >= {thresholds.get('velocity_ratio')}"
        )
    return alerts


def _write_run_artifact(
    results_root: Path,
    task: MonitorTask,
    metrics: Dict[str, float],
    alerts: List[str],
    filtered: List[Dict[str, Any]],
    new_items: List[Dict[str, Any]],
) -> Path:
    now = get_beijing_time()
    day_dir = results_root / now.strftime("%Y%m%d")
    day_dir.mkdir(parents=True, exist_ok=True)
    output_path = day_dir / f"{task.task_id}_{now.strftime('%H%M%S')}.json"
    payload = {
        "task_id": task.task_id,
        "task_name": task.name,
        "timestamp": now.isoformat(),
        "metrics": metrics,
        "alerts": alerts,
        "new_items_preview": new_items[:20],
        "sample_size": len(filtered),
    }
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    return output_path


def _send_ntfy_if_configured(config: Dict[str, Any], task: MonitorTask, alerts: List[str], metrics: Dict[str, float]) -> None:
    notification = config.get("notification") if isinstance(config.get("notification"), dict) else {}
    if not bool(notification.get("enable_notification", False)):
        return
    webhooks = notification.get("webhooks") if isinstance(notification.get("webhooks"), dict) else {}
    topic = str(webhooks.get("ntfy_topic") or "").strip()
    if not topic:
        return
    server = str(webhooks.get("ntfy_server_url") or "https://ntfy.sh").strip().rstrip("/")
    token = str(webhooks.get("ntfy_token") or "").strip()
    url = f"{server}/{topic}"
    title = f"[Sona监测预警] {task.name}"
    body = (
        f"任务: {task.task_id}\n"
        f"告警条数: {len(alerts)}\n"
        f"指标: volume={metrics.get('volume_total')}, new={metrics.get('new_topic_count')}, "
        f"negative_ratio={metrics.get('negative_ratio')}\n"
        + "\n".join(f"- {x}" for x in alerts[:6])
    )
    headers = {"Title": title}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        requests.post(url, data=body.encode("utf-8"), headers=headers, timeout=10)
    except Exception:
        # 通知失败不影响主流程
        pass


def run_monitoring(config_path: Optional[str] = None, task_id: Optional[str] = None) -> Dict[str, Any]:
    """执行一次监测轮询，返回所有任务结果。"""
    config = _load_config(config_path)
    monitor_cfg = config.get("monitor") if isinstance(config.get("monitor"), dict) else {}
    if not bool(monitor_cfg.get("enabled", False)):
        return {"ok": False, "message": "monitor 未启用（config.monitor.enabled=false）", "results": []}

    state_dir = Path(str(monitor_cfg.get("state_dir") or _DEFAULT_STATE_DIR))
    results_dir = Path(str(monitor_cfg.get("results_dir") or _DEFAULT_RESULTS_DIR))
    tasks = _extract_tasks(config)
    if task_id:
        tasks = [t for t in tasks if t.task_id == task_id]
    tasks = [t for t in tasks if t.enabled]
    if not tasks:
        return {"ok": False, "message": "没有可执行的监测任务", "results": []}

    run_results: List[Dict[str, Any]] = []
    now = get_beijing_time()
    for task in tasks:
        state = _read_state(state_dir, task.task_id)
        last_run_at = str(state.get("last_run_at") or "").strip()
        if last_run_at:
            try:
                last_dt = datetime.fromisoformat(last_run_at)
                if (now - last_dt) < timedelta(minutes=task.interval_minutes):
                    run_results.append(
                        {
                            "task_id": task.task_id,
                            "task_name": task.name,
                            "status": "skipped",
                            "reason": f"未到执行间隔({task.interval_minutes}m)",
                            "last_run_at": last_run_at,
                        }
                    )
                    continue
            except Exception:
                pass
        filtered, new_items, current_titles = _collect_incremental(task, state)
        metrics = _compute_metrics(filtered, new_items, state)
        alerts = _evaluate_alerts(metrics, task.thresholds)
        artifact_path = _write_run_artifact(results_dir, task, metrics, alerts, filtered, new_items)

        merged_titles = list(
            ({str(x).strip() for x in (state.get("seen_titles") or []) if str(x).strip()} | current_titles)
        )
        if len(merged_titles) > _MAX_STATE_TITLES:
            merged_titles = merged_titles[-_MAX_STATE_TITLES:]
        _write_state(
            state_dir,
            task.task_id,
            {
                "task_id": task.task_id,
                "last_run_at": now.isoformat(),
                "last_metrics": metrics,
                "seen_titles": merged_titles,
                "last_alerts": alerts,
                "last_artifact_path": str(artifact_path),
            },
        )

        if alerts:
            _send_ntfy_if_configured(config, task, alerts, metrics)

        run_results.append(
            {
                "task_id": task.task_id,
                "task_name": task.name,
                "status": "alert" if alerts else "ok",
                "alerts": alerts,
                "metrics": metrics,
                "artifact_path": str(artifact_path),
                "new_items_count": len(new_items),
            }
        )

    return {"ok": True, "message": "monitor run completed", "results": run_results}
