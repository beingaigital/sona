"""测试脚本：调用 keyword_stats 工具做关键词 TopN 统计。"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

# 添加项目根目录到 Python 路径（便于从 scripts 直接运行）
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from tools.keyword_stats import keyword_stats  # noqa: E402
from utils.path import ensure_task_dirs, get_task_process_dir  # noqa: E402
from utils.task_context import set_task_id  # noqa: E402


DEFAULT_DATA_PATH = r"D:\sona\sandbox\测试\过程文件\测试.csv"


def _parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="运行 keyword_stats 工具，输出 TopN 关键词统计结果。")
    parser.add_argument(
        "--data",
        default=DEFAULT_DATA_PATH,
        help="采集后的 CSV 文件路径（通常为 data_collect 返回的 save_path）。",
    )
    parser.add_argument("--top-n", type=int, default=20, help="TopN 数量（默认 20）。")
    parser.add_argument("--min-len", type=int, default=2, help="最小词长度（默认 2）。")
    parser.add_argument("--task-id", default="测试", help="任务 ID（默认：测试）。")
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> None:
    """主函数：执行关键词统计测试。"""
    if argv is None and len(sys.argv) <= 1:
        print("未传参数，默认读取：")
        print(f"  {DEFAULT_DATA_PATH}")
        print("\n如需指定其它文件：")
        print('  python .\\scripts\\run_keyword_stats.py --data "D:\\path\\to\\your.csv" --top-n 20 --min-len 2 --task-id 测试')
        print("-" * 80)

    args = _parse_args(argv)

    data_path = Path(args.data)
    if not data_path.exists():
        raise FileNotFoundError(f"数据文件不存在: {data_path}")

    task_id: str = str(args.task_id).strip() or "测试"
    ensure_task_dirs(task_id)
    set_task_id(task_id)

    try:
        print("=" * 80)
        print("keyword_stats 工具测试")
        print("=" * 80)
        print(f"任务ID: {task_id}")
        print(f"数据文件: {data_path}")
        print(f"top_n: {int(args.top_n)}")
        print(f"min_len: {int(args.min_len)}")
        print("-" * 80)

        invoke_params: Dict[str, Any] = {
            "dataFilePath": str(data_path),
            "top_n": int(args.top_n),
            "min_len": int(args.min_len),
        }
        result = keyword_stats.invoke(invoke_params)

        if isinstance(result, str):
            try:
                parsed = json.loads(result)
            except json.JSONDecodeError:
                print(result)
                return

            # 将结果额外输出到任务过程目录（便于与主流程产物统一管理）
            now = datetime.now().strftime("%Y%m%d_%H%M%S")
            out_name = f"keyword_stats_result_{now}.json"
            out_path = get_task_process_dir(task_id) / out_name
            with open(out_path, "w", encoding="utf-8") as f:
                json.dump(parsed, f, ensure_ascii=False, indent=2)

            print("\n[OK] 统计完成！")
            print("\n结果详情:")
            print(json.dumps(parsed, ensure_ascii=False, indent=2))
            print("\n字段验证:")
            content_cols = parsed.get("content_columns", [])
            top_keywords = parsed.get("top_keywords", [])
            top_keywords_preview = parsed.get("top_keywords_preview", [])
            total_rows = parsed.get("total_rows", 0)
            result_file_path = parsed.get("result_file_path", "")
            print(f"  - 识别内容列数量: {len(content_cols)}")
            if content_cols:
                print(f"  - 内容列: {', '.join(content_cols)}")
            print(f"  - 数据行数: {total_rows}")
            if top_keywords:
                print(f"  - Top关键词数量: {len(top_keywords)}")
                print("  - Top关键词(前10):")
                for idx, item in enumerate(top_keywords[:10], 1):
                    word = item.get("word", "N/A")
                    count = item.get("count", "N/A")
                    print(f"    {idx}. {word} ({count})")
            else:
                print(f"  - Top关键词数量: 详见过程文件（当前返回为简洁预览）")
                print(f"  - Top关键词预览数量: {len(top_keywords_preview)}")
                if top_keywords_preview:
                    print("  - Top关键词预览(前5):")
                    for idx, item in enumerate(top_keywords_preview[:5], 1):
                        word = item.get("word", "N/A")
                        count = item.get("count", "N/A")
                        print(f"    {idx}. {word} ({count})")
            if result_file_path:
                rf = Path(str(result_file_path))
                print(f"  - 工具保存结果文件: {result_file_path} {'(存在)' if rf.exists() else '(未找到)'}")

            print(f"\n[OK] 已写入过程目录结果文件: {out_path}")
            return

        print(result)
    finally:
        set_task_id(None)


if __name__ == "__main__":
    main()

