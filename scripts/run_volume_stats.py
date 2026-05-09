"""测试脚本：调用 volume_stats 工具，统计发布时间按日聚合的声量趋势并输出 JSON。"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from tools.volume_stats import volume_stats  # noqa: E402
from utils.path import ensure_task_dirs, get_task_process_dir  # noqa: E402
from utils.task_context import set_task_id  # noqa: E402


DEFAULT_DATA_PATH = r"D:\sona\sandbox\测试\过程文件\测试.csv"


def _parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="运行 volume_stats 工具，统计每日声量趋势。")
    parser.add_argument("--data", default=DEFAULT_DATA_PATH, help="采集后的 CSV 文件路径。")
    parser.add_argument("--task-id", default="测试", help="任务 ID（默认：测试）。")
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> None:
    args = _parse_args(argv)

    # 编码探测辅助：用常见编码读取表头，便于快速发现编码问题
    def _read_headers(csv_path: Path) -> list[str]:
        encodings = ["utf-8-sig", "utf-8", "gb18030", "gbk"]
        for enc in encodings:
            try:
                with csv_path.open("r", encoding=enc, errors="strict") as f:
                    import csv as _csv  # 局部导入
                    reader = _csv.reader(f)
                    header = next(reader, [])
                    return [str(h) for h in header]
            except Exception:
                continue
        try:
            with csv_path.open("r", encoding="utf-8-sig", errors="replace") as f:
                import csv as _csv
                reader = _csv.reader(f)
                header = next(reader, [])
                return [str(h) for h in header]
        except Exception:
            return []
    data_path = Path(args.data)
    if not data_path.exists():
        raise FileNotFoundError(f"数据文件不存在: {data_path}")

    task_id: str = str(args.task_id).strip() or "测试"
    ensure_task_dirs(task_id)
    set_task_id(task_id)

    try:
        print("=" * 80)
        print("volume_stats 工具测试")
        print("=" * 80)
        print(f"任务ID: {task_id}")
        print(f"数据文件: {data_path}")
        print("-" * 80)

        headers = _read_headers(data_path)
        if headers:
            print(f"[表头预览] {headers}")
        else:
            print("[表头预览] 读取失败（可能是编码或文件问题）")

        # 若命中“发布时间”或“发布时间戳”，则显式指定，以提升识别稳定性
        normalized_headers = [str(h).strip() for h in headers]
        time_forced = None
        if "发布时间" in normalized_headers:
            time_forced = "发布时间"
        elif "发布时间戳" in normalized_headers:
            time_forced = "发布时间戳"

        invoke_params: Dict[str, Any] = {"dataFilePath": str(data_path)}
        if time_forced:
            print(f"[时间列] 使用指定列: {time_forced}")
            invoke_params["timeColumn"] = time_forced
        result = volume_stats.invoke(invoke_params)

        if isinstance(result, str):
            parsed = json.loads(result)
            out_name = f"volume_stats_result_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
            out_path = get_task_process_dir(task_id) / out_name
            with open(out_path, "w", encoding="utf-8") as f:
                json.dump(parsed, f, ensure_ascii=False, indent=2)
            print("\n[OK] 统计完成！")
            print("结果详情（summary）：")
            print(json.dumps(parsed, ensure_ascii=False, indent=2))
            print(f"\n[OK] 已写入过程目录结果文件：{out_path}")
            return

        print(result)
    finally:
        set_task_id(None)


if __name__ == "__main__":
    main()

