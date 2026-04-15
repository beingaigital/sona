"""声量分析工具：按时间窗口聚合发文量与互动热度，并输出生命周期分段。"""

from __future__ import annotations

import csv
import json as json_module
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from langchain_core.tools import tool

from utils.path import get_task_process_dir
from utils.task_context import get_task_id


_TIME_COLUMN_PREFER: Tuple[str, ...] = ("发布时间", "timeBak", "time")
_UNKNOWN_TOKENS: Tuple[str, ...] = ("", "未知", "none", "null", "-", "—", "不详", "暂无", "NaN")
_LIFECYCLE_STAGES: Tuple[str, ...] = ("潜伏期", "成长期", "成熟期", "衰退期")

_LIKE_COLUMN_CANDIDATES: Tuple[str, ...] = ("点赞数", "like_count", "likes")
_COMMENT_COLUMN_CANDIDATES: Tuple[str, ...] = ("评论数", "comment_count", "comments")
_REPOST_COLUMN_CANDIDATES: Tuple[str, ...] = ("转发数", "repost_count", "reposts", "share_count", "shares")


def _read_csv_rows(file_path: str) -> List[Dict[str, Any]]:
    """读取 CSV 文件为 DictReader 行列表（自适应编码）。"""
    file = Path(file_path)
    if not file.exists():
        raise FileNotFoundError(f"数据文件不存在: {file_path}")

    rows: List[Dict[str, Any]] = []
    encodings_to_try: List[str] = ["utf-8-sig", "utf-8", "gb18030", "gbk"]
    last_error: Optional[Exception] = None
    for enc in encodings_to_try:
        try:
            with open(file, "r", encoding=enc, errors="strict") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    rows.append(row)
            break
        except Exception as e:
            rows = []
            last_error = e
            continue
    if not rows and last_error is not None:
        with open(file, "r", encoding="utf-8-sig", errors="replace") as f:
            reader = csv.DictReader(f)
            for row in reader:
                rows.append(row)
    return rows


def _identify_time_column(fieldnames: Sequence[str]) -> Optional[str]:
    """识别发布时间列。优先选择“发布时间/发布时间戳”，其次选择包含 time/timeBak 的列。"""
    if not fieldnames:
        return None

    # 1) 精确优先
    for p in _TIME_COLUMN_PREFER:
        if p in fieldnames:
            return p

    # 2) 模糊兜底
    # 优先“发布时间”相关，其次时间戳相关
    scored: List[Tuple[int, str]] = []
    for name in fieldnames:
        n = str(name or "").strip()
        if not n:
            continue
        lower = n.lower()
        score = 0
        if "发布时间" in n:
            score += 100
        if "timebak" in lower:
            score += 70
        if "timestamp" in lower or "time" in lower and "戳" in n:
            score += 50
        if lower == "time":
            score += 40
        if lower.startswith("time"):
            score += 20
        scored.append((score, n))

    scored.sort(key=lambda x: x[0], reverse=True)
    return scored[0][1] if scored and scored[0][0] > 0 else None


def _try_parse_to_datetime(value: Any) -> Optional[datetime]:
    """将发布时间字段解析为 datetime。解析失败返回 None。"""
    if value is None:
        return None
    s = str(value).strip()
    if not s or s in _UNKNOWN_TOKENS:
        return None

    s = s.replace("T", " ").replace("/", "-")

    # 时间戳：10/13 位数字
    if re.fullmatch(r"\d{10}(\.\d+)?", s):
        try:
            dt = datetime.fromtimestamp(float(s))
            return dt
        except Exception:
            return None
    if re.fullmatch(r"\d{13}", s):
        try:
            dt = datetime.fromtimestamp(int(s) / 1000.0)
            return dt
        except Exception:
            return None

    # 常见完整时间
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(s[: len(fmt)], fmt)
        except Exception:
            pass

    # 查找日期子串并补默认时间
    m = re.search(r"(\d{4}-\d{1,2}-\d{1,2})", s)
    if m:
        raw = m.group(1)
        parts = raw.split("-")
        if len(parts) == 3:
            y = parts[0]
            mm = parts[1].zfill(2)
            dd = parts[2].zfill(2)
            try:
                return datetime.strptime(f"{y}-{mm}-{dd} 00:00:00", "%Y-%m-%d %H:%M:%S")
            except Exception:
                return None

    try:
        if len(s) >= 19:
            return datetime.strptime(s[:19], "%Y-%m-%d %H:%M:%S")
        if len(s) >= 10:
            return datetime.strptime(s[:10], "%Y-%m-%d")
    except Exception:
        return None

    return None


@dataclass(frozen=True)
class VolumePoint:
    name: str
    value: int

    def to_dict(self) -> Dict[str, Any]:
        return {"name": self.name, "value": self.value}


def _save_result_json(task_id: str, payload: Dict[str, Any]) -> str:
    """将结果保存到任务过程文件目录，返回保存路径。"""
    process_dir = get_task_process_dir(task_id)
    process_dir.mkdir(parents=True, exist_ok=True)
    out_path = process_dir / "volume_stats.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json_module.dump(payload, f, ensure_ascii=False, indent=2)
    return str(out_path)


def _identify_numeric_column(fieldnames: Sequence[str], candidates: Sequence[str]) -> Optional[str]:
    field_set = {str(x).strip() for x in fieldnames}
    for c in candidates:
        if c in field_set:
            return c
    lowered = {str(x).strip().lower(): str(x).strip() for x in fieldnames}
    for c in candidates:
        got = lowered.get(c.lower())
        if got:
            return got
    return None


def _safe_int(value: Any) -> int:
    if value is None:
        return 0
    s = str(value).strip().replace(",", "")
    if not s:
        return 0
    m = re.search(r"-?\d+", s)
    if not m:
        return 0
    try:
        return int(m.group())
    except Exception:
        return 0


def _bucket_start(dt: datetime, window_hours: int) -> datetime:
    hour = (dt.hour // window_hours) * window_hours
    return dt.replace(hour=hour, minute=0, second=0, microsecond=0)


def _moving_average(values: List[float], window: int) -> List[float]:
    if window <= 1:
        return values[:]
    out: List[float] = []
    for i in range(len(values)):
        left = max(0, i - window + 1)
        chunk = values[left : i + 1]
        out.append(sum(chunk) / max(1, len(chunk)))
    return out


def _classify_lifecycle_stages(smoothed_pct: List[float]) -> List[str]:
    if not smoothed_pct:
        return []
    stages: List[str] = []
    reached_15 = False
    reached_80 = False
    decline_triggered = False
    low_streak = 0

    for v in smoothed_pct:
        if not reached_15:
            if v >= 15:
                reached_15 = True
                stages.append("成长期")
            else:
                stages.append("潜伏期")
            continue

        if not reached_80:
            if v >= 80:
                reached_80 = True
                stages.append("成熟期")
            else:
                stages.append("成长期")
            continue

        if not decline_triggered:
            if v < 50:
                low_streak += 1
            else:
                low_streak = 0
            if low_streak >= 2:
                decline_triggered = True
                stages.append("衰退期")
            else:
                stages.append("成熟期")
            continue

        stages.append("衰退期")
    return stages


@tool
def volume_stats(
    dataFilePath: str,
    timeColumn: Optional[str] = None,
    windowHours: int = 2,
    metric: str = "post_count",
    smoothWindow: int = 3,
) -> str:
    """
    描述：声量分析。对采集数据按时间窗口聚合，输出发文量与互动热度生命周期结果。

    使用时机：时间线分析（analysis_timeline）之后，用于补充“声量随时间变化”维度。

    输入：
    - dataFilePath（必填）：数据文件位置（CSV 路径，通常来自 data_collect 的 save_path）。

    输出：
    - JSON 字符串，包含 `data`: [{"name":"时间窗口","value":int}, ...]；
    - 并在过程文件夹中保存 `volume_stats.json` 供可视化。
    """
    task_id = get_task_id()
    if not task_id:
        return json_module.dumps({"error": "未找到任务ID，请确保在Agent上下文中调用", "data": [], "result_file_path": ""}, ensure_ascii=False)

    try:
        rows = _read_csv_rows(dataFilePath)
    except Exception as e:
        return json_module.dumps({"error": f"读取数据文件失败: {str(e)}", "data": [], "result_file_path": ""}, ensure_ascii=False)

    if not rows:
        saved_payload = {"data": [], "lifecycle": {"stages": [], "current_phase": "待评估（证据不足）"}}
        out_path = _save_result_json(task_id, saved_payload)
        return json_module.dumps(
            {
                "message": "声量统计完成：数据文件为空",
                "data": [],
                "data_preview": [],
                "result_file_path": out_path,
                "time_column_detected": None,
                "total_rows": 0,
                "parsed_rows_count": 0,
                "skipped_rows_count": 0,
            },
            ensure_ascii=False,
        )

    fieldnames = list(rows[0].keys())
    time_col: Optional[str] = None
    if timeColumn:
        normalized = str(timeColumn).strip()
        header_set = {str(h).strip() for h in fieldnames}
        if normalized in header_set:
            time_col = normalized
    if not time_col:
        time_col = _identify_time_column(fieldnames)
    if not time_col:
        saved_payload = {"data": [], "lifecycle": {"stages": [], "current_phase": "待评估（证据不足）"}}
        out_path = _save_result_json(task_id, saved_payload)
        return json_module.dumps(
            {
                "message": "声量统计完成：无法识别发布时间列",
                "data": [],
                "data_preview": [],
                "result_file_path": out_path,
                "time_column_detected": None,
                "total_rows": len(rows),
                "parsed_rows_count": 0,
                "skipped_rows_count": len(rows),
            },
            ensure_ascii=False,
        )

    fieldnames = list(rows[0].keys()) if rows else []
    like_col = _identify_numeric_column(fieldnames, _LIKE_COLUMN_CANDIDATES)
    comment_col = _identify_numeric_column(fieldnames, _COMMENT_COLUMN_CANDIDATES)
    repost_col = _identify_numeric_column(fieldnames, _REPOST_COLUMN_CANDIDATES)
    missing_interaction_fields = [
        name
        for name, col in (("点赞数", like_col), ("评论数", comment_col), ("转发数", repost_col))
        if not col
    ]

    window_hours = max(1, min(24, int(windowHours or 2)))
    smooth_window = max(1, min(12, int(smoothWindow or 3)))
    metric_norm = str(metric or "post_count").strip().lower()
    if metric_norm not in {"post_count", "heat_index"}:
        metric_norm = "post_count"

    bucket_post_count: Dict[datetime, int] = defaultdict(int)
    bucket_heat_index: Dict[datetime, int] = defaultdict(int)
    skipped = 0
    for row in rows:
        raw_time = row.get(time_col, "")
        dt = _try_parse_to_datetime(raw_time)
        if not dt:
            skipped += 1
            continue
        b = _bucket_start(dt, window_hours)
        bucket_post_count[b] += 1
        likes = _safe_int(row.get(like_col, 0)) if like_col else 0
        comments = _safe_int(row.get(comment_col, 0)) if comment_col else 0
        reposts = _safe_int(row.get(repost_col, 0)) if repost_col else 0
        heat = 1 + likes + comments * 3 + reposts * 5
        bucket_heat_index[b] += max(0, heat)

    sorted_buckets = sorted(set(bucket_post_count.keys()) | set(bucket_heat_index.keys()))
    post_count_series: List[Dict[str, Any]] = []
    heat_index_series: List[Dict[str, Any]] = []
    pct_series: List[float] = []
    for b in sorted_buckets:
        label = b.strftime("%Y-%m-%d %H:%M")
        post_v = int(bucket_post_count.get(b, 0))
        heat_v = int(bucket_heat_index.get(b, 0))
        post_count_series.append(VolumePoint(name=label, value=post_v).to_dict())
        heat_index_series.append(VolumePoint(name=label, value=heat_v).to_dict())
        pct_series.append(float(heat_v))

    peak = max(pct_series) if pct_series else 0.0
    normalized_pct = [round((v / peak) * 100, 4) if peak > 0 else 0.0 for v in pct_series]
    smoothed_pct = [round(v, 4) for v in _moving_average(normalized_pct, smooth_window)]
    stages = _classify_lifecycle_stages(smoothed_pct)
    current_phase = stages[-1] if stages else "待评估（证据不足）"

    lifecycle_series: List[Dict[str, Any]] = []
    for stage_name in _LIFECYCLE_STAGES:
        data_stage: List[float] = []
        for idx, v in enumerate(smoothed_pct):
            data_stage.append(v if idx < len(stages) and stages[idx] == stage_name else 0.0)
        lifecycle_series.append({"name": stage_name, "data": data_stage})

    data = heat_index_series if metric_norm == "heat_index" else post_count_series
    parsed_rows_count = sum(int(x.get("value", 0) or 0) for x in post_count_series)
    total_rows = len(rows)
    saved_payload = {
        "data": data,
        "post_count_series": post_count_series,
        "heat_index_series": heat_index_series,
        "heat_percentage_series": [
            {"name": post_count_series[i]["name"], "value": normalized_pct[i]} for i in range(len(normalized_pct))
        ],
        "heat_percentage_smoothed": [
            {"name": post_count_series[i]["name"], "value": smoothed_pct[i]} for i in range(len(smoothed_pct))
        ],
        "lifecycle": {
            "stages": [
                {"name": post_count_series[i]["name"], "stage": stages[i]} for i in range(len(stages))
            ],
            "series": lifecycle_series,
            "current_phase": current_phase,
        },
        "window_hours": window_hours,
        "metric": metric_norm,
        "smooth_window": smooth_window,
        "time_column_detected": time_col,
        "like_column_detected": like_col,
        "comment_column_detected": comment_col,
        "repost_column_detected": repost_col,
        "missing_interaction_fields": missing_interaction_fields,
    }
    out_path = _save_result_json(task_id, saved_payload)

    preview = data[:5]
    return json_module.dumps(
        {
            "message": (
                f"热度统计完成：共 {total_rows} 行，解析成功 {parsed_rows_count} 行；"
                f"当前阶段={current_phase}；已写入过程文件。"
            ),
            "data_preview": preview,
            "data": data,
            "post_count_series_preview": post_count_series[:5],
            "heat_index_series_preview": heat_index_series[:5],
            "heat_percentage_smoothed_preview": saved_payload["heat_percentage_smoothed"][:5],
            "lifecycle_current_phase": current_phase,
            "result_file_path": out_path,
            "time_column_detected": time_col,
            "like_column_detected": like_col,
            "comment_column_detected": comment_col,
            "repost_column_detected": repost_col,
            "missing_interaction_fields": missing_interaction_fields,
            "window_hours": window_hours,
            "metric": metric_norm,
            "smooth_window": smooth_window,
            "total_rows": total_rows,
            "parsed_rows_count": parsed_rows_count,
            "skipped_rows_count": skipped,
        },
        ensure_ascii=False,
    )

