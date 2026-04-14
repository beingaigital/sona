"""声量分析工具：根据采集数据的发布时间按日聚合统计并输出折线图数据。"""

from __future__ import annotations

import csv
import json as json_module
import re
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from langchain_core.tools import tool

from utils.path import get_task_process_dir
from utils.task_context import get_task_id


_TIME_COLUMN_PREFER: Tuple[str, ...] = ("发布时间", "timeBak", "time")
_TOP_DAILY_DEFAULT_N: int = 1000  # 日粒度一般不需要截断


_UNKNOWN_TOKENS: Tuple[str, ...] = ("", "未知", "none", "null", "-", "—", "不详", "暂无", "NaN")


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


def _try_parse_to_date(value: Any) -> Optional[str]:
    """将发布时间字段解析为 `YYYY-MM-DD`。解析失败返回 None。"""
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
            return dt.strftime("%Y-%m-%d")
        except Exception:
            return None
    if re.fullmatch(r"\d{13}", s):
        try:
            dt = datetime.fromtimestamp(int(s) / 1000.0)
            return dt.strftime("%Y-%m-%d")
        except Exception:
            return None

    # 查找日期子串（适配：YYYY-MM-DD / YYYY/MM/DD / 包含时分秒的字符串）
    m = re.search(r"(\d{4}-\d{2}-\d{2})", s)
    if m:
        return m.group(1)
    m2 = re.search(r"(\d{4}-\d{1}-\d{1})", s)
    if m2:
        # 宽松纠错：补齐月日
        raw = m2.group(1)
        parts = raw.split("-")
        if len(parts) == 3:
            y = parts[0]
            mm = parts[1].zfill(2)
            dd = parts[2].zfill(2)
            return f"{y}-{mm}-{dd}"

    # 尝试 isoformat/parse
    try:
        # 仅截取前 10 位作为兜底
        if len(s) >= 10 and re.fullmatch(r"\d{4}-\d{2}-\d{2}", s[:10]):
            return s[:10]
    except Exception:
        return None

    return None


@dataclass(frozen=True)
class DailyVolumePoint:
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


@tool
def volume_stats(
    dataFilePath: str,
    timeColumn: Optional[str] = None,
) -> str:
    """
    描述：声量分析。对采集数据中的发布时间按日粒度聚合统计（发帖/条目数）。

    使用时机：时间线分析（analysis_timeline）之后，用于补充“声量随时间变化”维度。

    输入：
    - dataFilePath（必填）：数据文件位置（CSV 路径，通常来自 data_collect 的 save_path）。

    输出：
    - JSON 字符串，包含 `data`: [{"name":"YYYY-MM-DD","value":int(count)}, ...]；
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
        saved_payload = {"data": []}
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
        saved_payload = {"data": []}
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

    date_counter: Counter[str] = Counter()
    skipped = 0
    for row in rows:
        raw_time = row.get(time_col, "")
        date = _try_parse_to_date(raw_time)
        if not date:
            skipped += 1
            continue
        date_counter[date] += 1

    sorted_dates = sorted(date_counter.keys())
    data_points: List[DailyVolumePoint] = [
        DailyVolumePoint(name=d, value=date_counter[d]) for d in sorted_dates
    ]
    data = [p.to_dict() for p in data_points]

    parsed_rows_count = sum(p.value for p in data_points)
    total_rows = len(rows)
    saved_payload = {"data": data}
    out_path = _save_result_json(task_id, saved_payload)

    preview = data[:5]
    return json_module.dumps(
        {
            "message": f"声量统计完成：共 {total_rows} 行，解析成功 {parsed_rows_count} 行；已写入过程文件。",
            "data_preview": preview,
            "data": data,
            "result_file_path": out_path,
            "time_column_detected": time_col,
            "total_rows": total_rows,
            "parsed_rows_count": parsed_rows_count,
            "skipped_rows_count": skipped,
        },
        ensure_ascii=False,
    )

