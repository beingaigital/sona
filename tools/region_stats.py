"""地域分析工具：统计数据中 IP属地 的省份 TopN 排名并输出为 JSON。"""

from __future__ import annotations

import json as json_module
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from langchain_core.tools import tool

from tools._csv_io import read_csv_rows_all
from utils.path import get_task_process_dir
from utils.task_context import get_task_id


_IP_LOCATION_COLUMN_EXACT: str = "IP属地"
_TOP_PROVINCES_DEFAULT_N: int = 10

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
}

_MUNICIPALITIES: Set[str] = {"北京市", "天津市", "上海市", "重庆市"}
_HK_MACAO_TAIWAN: Set[str] = {"香港", "澳门", "台湾"}


def _looks_like_province_level(raw_location: str) -> bool:
    """判断是否符合“省份/省级地域”口径（用于过滤国家名等非省级地域）。"""
    s = str(raw_location or "").strip().replace(" ", "")
    if not s:
        return False

    if s in _HK_MACAO_TAIWAN:
        return True
    if s in _MUNICIPALITIES:
        return True

    if s.endswith("省") or s.endswith("自治区") or s.endswith("特别行政区"):
        return True

    # 直辖市在数据里通常会带 “市”；普通地级市不计入
    if s.endswith("市"):
        return s in _MUNICIPALITIES

    return False


def _identify_ip_location_column(fieldnames: Sequence[str]) -> Optional[str]:
    """识别 IP属地 字段列名（兼容不同命名）。"""
    if _IP_LOCATION_COLUMN_EXACT in fieldnames:
        return _IP_LOCATION_COLUMN_EXACT

    # 兜底：包含 ip + location/属地 关键词
    candidates: List[str] = []
    for name in fieldnames:
        if not name:
            continue
        n = str(name).strip()
        n_lower = n.lower()
        has_ip = "ip" in n_lower
        has_loc = ("属地" in n) or ("location" in n_lower) or ("loc" == n_lower)
        if has_ip and has_loc:
            candidates.append(n)
            continue

    return candidates[0] if candidates else None


def _is_unknown_ip_location(value: str) -> bool:
    """判断是否为未知/无效 IP属地。"""
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


def _normalize_province_label(raw_location: str) -> str:
    """将 IP属地 归一化为“省份/直辖市/自治区/特别行政区”的名称（尽量去掉后缀）。"""
    s = str(raw_location or "").strip()
    if not s:
        return ""

    # 常见后缀：省/市/自治区/特别行政区
    s = s.replace(" ", "")

    # 直辖市：北京市/上海市/天津市/重庆市
    for suf in ("省", "自治区", "特别行政区"):
        if s.endswith(suf):
            return s[: -len(suf)]

    # 市：多数数据为“xx省”但重庆可能是“重庆市”。若是普通“xx市”，也做去后缀处理。
    if s.endswith("市"):
        return s[: -len("市")]

    # 兜底：保留原值（但去掉可能的括号噪声）
    return re.sub(r"[\(\（].*?[\)\）]", "", s).strip() or s


def _save_result_json(task_id: str, payload: Dict[str, Any]) -> str:
    """将结果保存到任务过程文件目录，返回保存路径。"""
    process_dir = get_task_process_dir(task_id)
    process_dir.mkdir(parents=True, exist_ok=True)
    out_path = process_dir / "region_stats.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json_module.dump(payload, f, ensure_ascii=False, indent=2)
    return str(out_path)


@dataclass(frozen=True)
class RegionStatsTopItem:
    province: str
    count: int
    ratio_of_valid: float
    ratio_of_total: float

    def to_dict(self) -> Dict[str, Any]:
        return {
            "province": self.province,
            "count": self.count,
            "ratio_of_valid": self.ratio_of_valid,
            "ratio_of_total": self.ratio_of_total,
        }


@tool
def region_stats(
    dataFilePath: str,
    top_n: int = _TOP_PROVINCES_DEFAULT_N,
    ipLocationColumn: Optional[str] = None,
) -> str:
    """
    描述：地域分析。统计采集数据中的 `IP属地` 省份出现频次，过滤未知后输出省份 TopN 排名。

    使用时机：数据采集完成、关键词统计（keyword_stats）之后，用于补充“地域分布”维度。

    输入：
    - dataFilePath（必填）：数据文件位置（CSV 路径，通常来自 data_collect 的 save_path）。
    - top_n（可选，默认10）：输出 TopN。

    输出：
    - JSON 字符串（summary），并在过程文件夹中保存 `region_stats.json`（用于报表可视化）。
    """
    task_id = get_task_id()
    if not task_id:
        return json_module.dumps(
            {
                "error": "未找到任务ID，请确保在Agent上下文中调用",
                "top_provinces": [],
                "result_file_path": "",
            },
            ensure_ascii=False,
        )

    try:
        rows = read_csv_rows_all(dataFilePath)
    except Exception as e:
        return json_module.dumps(
            {
                "error": f"读取数据文件失败: {str(e)}",
                "top_provinces": [],
                "result_file_path": "",
            },
            ensure_ascii=False,
        )

    if not rows:
        payload = {
            "total_rows": 0,
            "valid_rows_count": 0,
            "unknown_filtered_count": 0,
            "non_province_filtered_count": 0,
            "ip_location_column_detected": None,
            "top_provinces": [],
            "province_counts": {},
        }
        out_path = _save_result_json(task_id, payload)
        payload["result_file_path"] = out_path
        return json_module.dumps(
            {"message": "数据文件为空，已生成空的地域统计结果", **payload},
            ensure_ascii=False,
        )

    fieldnames = list(rows[0].keys())
    ip_col: Optional[str] = None
    if ipLocationColumn:
        normalized = str(ipLocationColumn).strip()
        header_set = {str(h).strip() for h in fieldnames}
        if normalized in header_set:
            ip_col = normalized
    if not ip_col:
        ip_col = _identify_ip_location_column(fieldnames)
    if not ip_col:
        payload = {
            "total_rows": len(rows),
            "valid_rows_count": 0,
            "unknown_filtered_count": len(rows),
            "non_province_filtered_count": 0,
            "ip_location_column_detected": None,
            "top_provinces": [],
            "province_counts": {},
            "error": f"无法识别 IP属地 列（检测字段名不包含: {_IP_LOCATION_COLUMN_EXACT}）",
        }
        out_path = _save_result_json(task_id, payload)
        payload["result_file_path"] = out_path
        return json_module.dumps(
            {"error": payload["error"], "top_provinces": [], "result_file_path": out_path},
            ensure_ascii=False,
        )

    total_rows = len(rows)
    unknown_filtered_count = 0
    non_province_filtered_count = 0
    province_counter: Counter[str] = Counter()

    for row in rows:
        raw_location = row.get(ip_col, "")
        s = str(raw_location or "").strip()
        if _is_unknown_ip_location(s):
            unknown_filtered_count += 1
            continue

        if not _looks_like_province_level(s):
            non_province_filtered_count += 1
            continue

        normalized = _normalize_province_label(s)
        if not normalized or _is_unknown_ip_location(normalized):
            unknown_filtered_count += 1
            continue

        province_counter[normalized] += 1

    valid_rows_count = total_rows - unknown_filtered_count
    valid_rows_count = total_rows - unknown_filtered_count - non_province_filtered_count
    if valid_rows_count <= 0:
        top_items: List[RegionStatsTopItem] = []
    else:
        top_items = []
        for prov, cnt in province_counter.most_common(int(top_n)):
            ratio_of_valid = round(cnt / valid_rows_count, 6) if valid_rows_count else 0.0
            ratio_of_total = round(cnt / total_rows, 6) if total_rows else 0.0
            top_items.append(
                RegionStatsTopItem(
                    province=prov,
                    count=cnt,
                    ratio_of_valid=ratio_of_valid,
                    ratio_of_total=ratio_of_total,
                )
            )

    province_counts: Dict[str, int] = dict(province_counter)
    top_provinces = [it.to_dict() for it in top_items]

    payload = {
        "ip_location_column_detected": ip_col,
        "total_rows": total_rows,
        "valid_rows_count": valid_rows_count,
        "unknown_filtered_count": unknown_filtered_count,
        "non_province_filtered_count": non_province_filtered_count,
        "top_provinces": top_provinces,
        "province_counts": province_counts,
        "top_n": int(top_n),
    }

    out_path = _save_result_json(task_id, payload)
    payload["result_file_path"] = out_path

    preview = top_provinces[: min(5, len(top_provinces))]
    return json_module.dumps(
        {
            "message": (
                f"地域统计完成：共 {total_rows} 行；"
                f"过滤未知 {unknown_filtered_count} 行，过滤非省级地域 {non_province_filtered_count} 行；"
                f"Top{int(top_n)} 省份已写入过程文件。"
            ),
            "top_provinces_preview": preview,
            "result_file_path": out_path,
            "ip_location_column_detected": ip_col,
            "total_rows": total_rows,
            "valid_rows_count": valid_rows_count,
            "unknown_filtered_count": unknown_filtered_count,
            "non_province_filtered_count": non_province_filtered_count,
        },
        ensure_ascii=False,
    )

