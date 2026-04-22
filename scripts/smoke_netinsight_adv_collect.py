#!/usr/bin/env python3
"""一次性烟测：高级检索式 -> data_num -> data_collect（需 NETINSIGHT 账号与网络）。"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

import importlib.util


def _load_tool_module(module_name: str, relative_path: str):
    path = project_root / relative_path
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"无法加载 {path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = mod
    spec.loader.exec_module(mod)
    return mod


# 避免 import tools 包（会拉全量依赖如 langchain_google_genai）
_data_num_mod = _load_tool_module("_smoke_data_num", "tools/data_num.py")
_data_collect_mod = _load_tool_module("_smoke_data_collect", "tools/data_collect.py")
data_num = _data_num_mod.data_num
data_collect = _data_collect_mod.data_collect

from utils.path import ensure_task_dirs
from utils.task_context import set_task_id
from workflow.netinsight_keywords import build_data_num_search_words


def _time_range_last_days(days: int) -> str:
    end = datetime.now().replace(hour=23, minute=59, second=59, microsecond=0) - timedelta(days=1)
    start = end - timedelta(days=days)
    return f"{start.strftime('%Y-%m-%d %H:%M:%S')};{end.strftime('%Y-%m-%d %H:%M:%S')}"


def main() -> int:
    parser = argparse.ArgumentParser(description="高级模式 data_num -> data_collect 烟测")
    parser.add_argument(
        "--topic",
        default="大学生高铁骂熊孩子",
        help="事件描述（用于普通分词兜底；高级式由 --advanced 决定）",
    )
    parser.add_argument(
        "--advanced",
        default="(大学生|大学校)+(高铁|动车)+(骂|熊孩子)-广告",
        help="NetInsight 高级检索式（+且 |或 -排除）",
    )
    parser.add_argument("--platform", default="微博", help="采集平台（单平台烟测）")
    parser.add_argument("--threshold", type=int, default=40, help="data_num 配额上限（不宜过大）")
    parser.add_argument("--days", type=int, default=14, help="时间窗天数（结束为昨日 23:59:59）")
    parser.add_argument(
        "--keyword-mode",
        choices=("advanced", "normal"),
        default="advanced",
        help="advanced=使用 --advanced 表达式；normal=分号 OR 合并 topic 分词",
    )
    args = parser.parse_args()

    task_id = f"smoke_adv_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    ensure_task_dirs(task_id)
    set_task_id(task_id)

    if args.keyword_mode == "normal":
        raw_parts = re.split(r"[;；,\s]+", str(args.topic).strip())
        sw = [p.strip() for p in raw_parts if p.strip()]
    else:
        sw = [w for w in str(args.topic).replace("，", " ").split() if w]

    search_plan: dict = {
        "netinsightKeywordMode": str(args.keyword_mode),
        "netinsightAdvancedQuery": str(args.advanced) if args.keyword_mode == "advanced" else "",
        "searchWords": sw,
    }
    if not search_plan["searchWords"]:
        search_plan["searchWords"] = [args.topic]

    sw_collect = search_plan["searchWords"]
    words_for_num, km = build_data_num_search_words(search_plan, sw_collect)
    time_range = _time_range_last_days(max(3, min(int(args.days), 365)))

    print("=== smoke: advanced data_num -> data_collect ===")
    print(f"task_id={task_id}")
    print(f"keywordMode={km} words_for_num={words_for_num!r}")
    print(f"timeRange={time_range}")
    print(f"platform={args.platform} threshold={args.threshold}")

    raw_num = data_num.invoke(
        {
            "searchWords": json.dumps(words_for_num, ensure_ascii=False),
            "timeRange": time_range,
            "threshold": int(args.threshold),
            "platform": str(args.platform),
            "keywordMode": km,
            "platforms": "",
            "allocateByPlatform": False,
        }
    )
    num = json.loads(raw_num) if isinstance(raw_num, str) else raw_num
    print("\n--- data_num ---")
    print(json.dumps(num, ensure_ascii=False, indent=2))
    if num.get("error"):
        set_task_id(None)
        return 2

    matrix = num.get("search_matrix") or {}
    if not matrix:
        print("search_matrix 为空，中止 data_collect")
        set_task_id(None)
        return 3

    raw_col = data_collect.invoke(
        {
            "searchMatrix": json.dumps(matrix, ensure_ascii=False),
            "timeRange": str(num.get("time_range") or time_range),
            "platform": str(args.platform),
        }
    )
    col = json.loads(raw_col) if isinstance(raw_col, str) else raw_col
    print("\n--- data_collect ---")
    print(json.dumps(col, ensure_ascii=False, indent=2))

    set_task_id(None)
    return 0 if not col.get("error") else 4


if __name__ == "__main__":
    raise SystemExit(main())
