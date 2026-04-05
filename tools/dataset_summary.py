"""数据集摘要工具：为 data_collect 产出的 CSV 生成 dataset_summary.json。"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional, List

from langchain_core.tools import tool

from utils.path import ensure_task_dirs
from utils.task_context import get_task_id


@dataclass(frozen=True)
class CsvSummary:
    """CSV 摘要信息。"""

    row_count: int
    fieldnames: List[str]
    time_coverage: Dict[str, Optional[str]]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "row_count": self.row_count,
            "fieldnames": self.fieldnames,
            "time_coverage": self.time_coverage,
        }


def _try_parse_time(value: str) -> Optional[str]:
    """
    尝试把时间字段解析为 ISO 字符串（失败则返回 None）。
    该步骤用于生成 time_coverage，不强求完全准确。
    """

    if not value:
        return None
    v = value.strip()
    if not v:
        return None

    # 常见格式：YYYY-MM-DD HH:MM:SS
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(v, fmt)
            return dt.isoformat(sep=" ")
        except Exception:
            continue

    # 可能是毫秒时间戳
    try:
        ms = int(float(v))
        # 猜测：若是13位，通常是毫秒
        if ms > 10_000_000_000:
            dt = datetime.fromtimestamp(ms / 1000.0)
        else:
            dt = datetime.fromtimestamp(ms)
        return dt.isoformat(sep=" ")
    except Exception:
        return None


def _extract_time_coverage(rows: List[Dict[str, str]]) -> Dict[str, Optional[str]]:
    """
    从可能的时间列中估算覆盖范围（最小值/最大值）。
    """

    if not rows:
        return {"min_time": None, "max_time": None, "time_column": None}

    time_columns = []
    first_row = rows[0]
    for key in first_row.keys():
        if any(s in key for s in ["发布时间", "time", "timeBak", "time_time", "发布时间戳"]):
            time_columns.append(key)

    if not time_columns:
        return {"min_time": None, "max_time": None, "time_column": None}

    # 优先使用发布时间类字段
    chosen_col = time_columns[0]

    parsed_times: List[str] = []
    for r in rows:
        t_raw = str(r.get(chosen_col, "") or "").strip()
        t = _try_parse_time(t_raw)
        if t:
            parsed_times.append(t)

    if not parsed_times:
        return {"min_time": None, "max_time": None, "time_column": chosen_col}

    # ISO字符串可做字典序比较近似排序
    min_time = min(parsed_times)
    max_time = max(parsed_times)
    return {"min_time": min_time, "max_time": max_time, "time_column": chosen_col}


def _read_csv_header_and_sample(save_path: str, sample_limit: int = 200) -> tuple[List[str], List[Dict[str, str]], int]:
    """
    读取 CSV 的字段名、样本行与行数（尽量避免把大文件全部读入内存）。
    """

    csv_path = Path(save_path)
    if not csv_path.exists():
        raise FileNotFoundError(f"CSV 文件不存在: {save_path}")

    with open(csv_path, "r", encoding="utf-8-sig", errors="replace") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            return [], [], 0

        fieldnames = list(reader.fieldnames)
        rows: List[Dict[str, str]] = []
        row_count = 0
        for i, row in enumerate(reader):
            row_count += 1
            if len(rows) < sample_limit:
                rows.append({k: (v or "") for k, v in row.items()})

    return fieldnames, rows, row_count


@tool
def dataset_summary(save_path: str) -> str:
    """
    描述：为指定 CSV 生成 dataset_summary.json，并返回其内容 JSON 字符串。
    使用时机：在数据采集与清洗去重完成后，对 CSV 进行摘要统计，以便后续解释与报告。
    输入：
    - save_path（必填）：data_collect 生成的 CSV 路径
    输出：JSON 字符串，包含 dataset_summary 与 result_file_path。
    """

    task_id = get_task_id()
    if not task_id:
        return json.dumps(
            {
                "error": "未找到任务ID，请确保在任务上下文中调用",
                "result_file_path": "",
                "dataset_summary": {},
            },
            ensure_ascii=False,
        )

    try:
        fieldnames, sample_rows, row_count = _read_csv_header_and_sample(save_path)
        time_coverage = _extract_time_coverage(sample_rows)
        summary = CsvSummary(
            row_count=row_count,
            fieldnames=fieldnames,
            time_coverage=time_coverage,
        )
        summary_dict = summary.to_dict()
    except Exception as e:
        return json.dumps(
            {
                "error": f"生成 dataset_summary 失败: {str(e)}",
                "result_file_path": "",
                "dataset_summary": {},
            },
            ensure_ascii=False,
        )

    # 保存到过程文件夹（写入固定文件名，便于工作流/报告稳定引用）
    try:
        process_dir = ensure_task_dirs(task_id)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_path_ts = process_dir / f"dataset_summary_{timestamp}.json"
        out_path = process_dir / "dataset_summary.json"
        payload = {
            "save_path": str(save_path),
            "dataset_summary": summary_dict,
            "generated_at": datetime.now().isoformat(sep=" "),
        }
        with open(out_path_ts, "w", encoding="utf-8", errors="replace") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
    except Exception as e:
        return json.dumps(
            {
                "error": f"保存 dataset_summary.json 失败: {str(e)}",
                "result_file_path": "",
                "dataset_summary": summary_dict,
            },
            ensure_ascii=False,
        )

    # 写入固定文件名（覆盖），用于后续节点稳定读取
    try:
        with open(out_path, "w", encoding="utf-8", errors="replace") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
    except Exception:
        # 固定文件名失败不影响主流程，返回 timestamp 文件路径
        out_path = out_path_ts

    return json.dumps(
        {
            "result_file_path": str(out_path),
            "dataset_summary": summary_dict,
        },
        ensure_ascii=False,
    )

