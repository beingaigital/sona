"""作者分析工具：统计采集数据中的作者 TopN 排名并输出为 JSON。"""

from __future__ import annotations

import json as json_module
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from langchain_core.tools import tool

from tools._csv_io import read_csv_rows_all
from utils.path import get_task_process_dir
from utils.task_context import get_task_id


_AUTHOR_COLUMN_EXACT: str = "作者"
_TOP_AUTHORS_DEFAULT_N: int = 10

_UNKNOWN_TOKENS: Set[str] = {
    "",
    "未知",
    "其他",
    "其它",
    "null",
    "none",
    "n/a",
    "na",
    "-",
    "—",
    "未填写",
    "不详",
    "暂无",
}

_AUTHOR_SPLIT_SEPARATORS: Tuple[str, ...] = (";", "；", ",", "，", "|")


def _identify_author_column(fieldnames: Sequence[str]) -> Optional[str]:
    """识别作者列名（兼容不同命名）。"""
    if _AUTHOR_COLUMN_EXACT in fieldnames:
        return _AUTHOR_COLUMN_EXACT

    candidates: List[str] = []
    for name in fieldnames:
        if not name:
            continue
        n = str(name).strip()
        n_lower = n.lower()
        # 常见：作者/author/user_name/screenName/发布者
        if ("作者" in n) or ("author" in n_lower) or ("user_name" in n_lower) or ("screenname" in n_lower) or (
            "发布者" in n
        ):
            candidates.append(n)

    return candidates[0] if candidates else None


def _is_unknown_author(value: str) -> bool:
    """判断是否为未知/无效作者。"""
    s = str(value or "").strip()
    if not s:
        return True
    if s in _UNKNOWN_TOKENS:
        return True
    if any(token in s for token in ("未知", "其他", "其它", "暂无", "不详")):
        return True
    s_lower = s.lower()
    if s_lower in _UNKNOWN_TOKENS:
        return True
    return False


def _normalize_author_label(raw_author: str) -> str:
    """作者归一化：去除首尾空白并去掉常见分隔噪声。"""
    s = str(raw_author or "").strip()
    s = s.replace(" ", "")
    # 去掉两端可能的标点噪声
    s = s.strip("，,;；|｜/\\")
    return s


def _iter_author_labels(raw_author: str) -> Iterable[str]:
    """
    将作者字段转为可计数的 author labels。

    如果作者字段内部包含分隔符（如 ';'），则尽可能拆分并统计每一段。
    """
    s = str(raw_author or "").strip()
    if not s:
        return []

    # 优先处理全角/半角分隔符
    if any(sep in s for sep in _AUTHOR_SPLIT_SEPARATORS):
        parts: List[str] = [s]
        for sep in _AUTHOR_SPLIT_SEPARATORS:
            if sep in s:
                # 逐步切分；由于分隔符集合较小，循环次数有限
                new_parts: List[str] = []
                for p in parts:
                    new_parts.extend(p.split(sep))
                parts = new_parts
        return [p for p in (_normalize_author_label(p) for p in parts) if p]

    normalized = _normalize_author_label(s)
    return [normalized] if normalized else []


@dataclass(frozen=True)
class AuthorStatsTopItem:
    author: str
    count: int
    ratio_of_valid: float
    ratio_of_total: float

    def to_dict(self) -> Dict[str, Any]:
        return {
            "author": self.author,
            "count": self.count,
            "ratio_of_valid": self.ratio_of_valid,
            "ratio_of_total": self.ratio_of_total,
        }


def _save_result_json(task_id: str, payload: Dict[str, Any]) -> str:
    """将结果保存到任务过程文件目录，返回保存路径。"""
    process_dir = get_task_process_dir(task_id)
    process_dir.mkdir(parents=True, exist_ok=True)
    out_path = process_dir / "author_stats.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json_module.dump(payload, f, ensure_ascii=False, indent=2)
    return str(out_path)


@tool
def author_stats(
    dataFilePath: str,
    top_n: int = _TOP_AUTHORS_DEFAULT_N,
    authorColumn: Optional[str] = None,
) -> str:
    """
    描述：作者分析。统计采集数据中 `作者`（或兼容字段）出现频次，过滤未知后输出发布者 TopN 排名。

    使用时机：数据采集完成、地域分析（region_stats）之后，用于补充“作者分布”维度。

    输入：
    - dataFilePath（必填）：数据文件位置（CSV 路径，通常来自 data_collect 的 save_path）。
    - top_n（可选，默认10）：输出 TopN。

    输出：
    - JSON 字符串（summary），并在过程文件夹中保存 `author_stats.json`（用于报表可视化）。
    """
    task_id = get_task_id()
    if not task_id:
        return json_module.dumps(
            {"error": "未找到任务ID，请确保在Agent上下文中调用", "top_authors": [], "result_file_path": ""},
            ensure_ascii=False,
        )

    try:
        rows = read_csv_rows_all(dataFilePath)
    except Exception as e:
        return json_module.dumps(
            {"error": f"读取数据文件失败: {str(e)}", "top_authors": [], "result_file_path": ""},
            ensure_ascii=False,
        )

    if not rows:
        payload = {
            "total_rows": 0,
            "valid_rows_count": 0,
            "unknown_filtered_count": 0,
            "author_column_detected": None,
            "top_authors": [],
            "author_counts": {},
        }
        out_path = _save_result_json(task_id, payload)
        payload["result_file_path"] = out_path
        return json_module.dumps({"message": "数据文件为空，已生成空的作者统计结果", **payload}, ensure_ascii=False)

    fieldnames = list(rows[0].keys())
    author_col = None
    # 若外部显式指定列名，且确实存在于表头，则优先使用
    if authorColumn:
        normalized = str(authorColumn).strip()
        header_set = {str(h).strip() for h in fieldnames}
        if normalized in header_set:
            author_col = normalized
    if not author_col:
        author_col = _identify_author_column(fieldnames)
    if not author_col:
        payload = {
            "total_rows": len(rows),
            "valid_rows_count": 0,
            "unknown_filtered_count": len(rows),
            "author_column_detected": None,
            "top_authors": [],
            "author_counts": {},
            "error": f"无法识别 作者 列（检测字段名不包含: {_AUTHOR_COLUMN_EXACT}）",
        }
        out_path = _save_result_json(task_id, payload)
        payload["result_file_path"] = out_path
        return json_module.dumps({"error": payload["error"], "top_authors": [], "result_file_path": out_path}, ensure_ascii=False)

    total_rows = len(rows)
    unknown_filtered_count = 0
    author_counter: Counter[str] = Counter()

    # 这里的 valid_rows_count 口径：参与计数的“作者段”总数（若一个字段拆出多个 author，也会累加）
    valid_authors_total = 0
    for row in rows:
        raw_author = row.get(author_col, "")
        if _is_unknown_author(raw_author):
            unknown_filtered_count += 1
            continue

        labels = list(_iter_author_labels(str(raw_author)))
        if not labels:
            unknown_filtered_count += 1
            continue

        for lab in labels:
            if _is_unknown_author(lab):
                continue
            author_counter[lab] += 1
            valid_authors_total += 1

    valid_rows_count = valid_authors_total

    if valid_rows_count <= 0:
        top_items: List[AuthorStatsTopItem] = []
    else:
        top_items = []
        for au, cnt in author_counter.most_common(int(top_n)):
            ratio_of_valid = round(cnt / valid_rows_count, 6) if valid_rows_count else 0.0
            ratio_of_total = round(cnt / total_rows, 6) if total_rows else 0.0
            top_items.append(
                AuthorStatsTopItem(
                    author=au,
                    count=cnt,
                    ratio_of_valid=ratio_of_valid,
                    ratio_of_total=ratio_of_total,
                )
            )

    author_counts: Dict[str, int] = dict(author_counter)
    top_authors = [it.to_dict() for it in top_items]

    payload = {
        "author_column_detected": author_col,
        "total_rows": total_rows,
        "valid_rows_count": valid_rows_count,
        "unknown_filtered_count": unknown_filtered_count,
        "top_authors": top_authors,
        "author_counts": author_counts,
        "top_n": int(top_n),
    }
    out_path = _save_result_json(task_id, payload)
    payload["result_file_path"] = out_path

    preview = top_authors[: min(5, len(top_authors))]
    return json_module.dumps(
        {
            "message": (
                f"作者统计完成：共 {total_rows} 行；过滤未知 {unknown_filtered_count} 行；"
                f"Top{int(top_n)} 发布者已写入过程文件。"
            ),
            "top_authors_preview": preview,
            "result_file_path": out_path,
            "author_column_detected": author_col,
            "total_rows": total_rows,
            "valid_rows_count": valid_rows_count,
            "unknown_filtered_count": unknown_filtered_count,
        },
        ensure_ascii=False,
    )

