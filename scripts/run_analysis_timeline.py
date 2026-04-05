"""测试脚本：调用 analysis_timeline 工具分析事件时间线。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional

# 添加项目根目录到 Python 路径
project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

from tools.analysis_timeline import analysis_timeline
from utils.path import ensure_task_dirs
from utils.task_context import set_task_id


DEFAULT_DATA_PATH = r"D:\sona\sandbox\测试\过程文件\测试.csv"


def _parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="运行 analysis_timeline 工具分析事件时间线。")
    parser.add_argument("--data", default=DEFAULT_DATA_PATH, help="采集后的 CSV 文件路径。")
    parser.add_argument("--task-id", default="测试", help="任务 ID（默认：测试）。")
    parser.add_argument("--event-intro", default="美伊战争", help="事件介绍文本。")
    parser.add_argument("--retry-context", default=None, help="重试上下文 JSON 字符串（可选）。")
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> None:
    """主函数：执行时间线分析测试。"""
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

    # 设置任务上下文
    task_id = str(args.task_id).strip() or "测试"
    ensure_task_dirs(task_id)
    set_task_id(task_id)

    try:
        print("=" * 80)
        print("analysis_timeline 工具测试")
        print("=" * 80)
        data_file = Path(args.data)
        print(f"事件介绍: {args.event_intro}")
        print(f"数据文件: {data_file}")
        print(f"重试上下文: {'无' if not args.retry_context else '有'}")
        print("-" * 80)

        # 检查文件是否存在
        if not data_file.exists():
            print(f"⚠️  数据文件不存在: {data_file}")
            print("   请确保文件存在后再运行测试")
            return

        # 表头预览（多编码）
        headers = _read_headers(data_file)
        if headers:
            print(f"[表头预览] {headers}")
        else:
            print("[表头预览] 读取失败（可能是编码或文件问题）")

        # 调用工具
        # 若命中“内容”“发布时间/发布时间戳”，则显式指定列名，提升识别稳定性
        normalized_headers = [str(h).strip() for h in headers]
        forced_content = "内容" if "内容" in normalized_headers else None
        forced_time = None
        if "发布时间" in normalized_headers:
            forced_time = "发布时间"
        elif "发布时间戳" in normalized_headers:
            forced_time = "发布时间戳"

        invoke_params = {
            "eventIntroduction": args.event_intro,
            "dataFilePath": str(data_file),
        }
        if args.retry_context:
            invoke_params["retryContext"] = args.retry_context
        if forced_content:
            print(f"[内容列] 使用指定列: {forced_content}")
            invoke_params["contentColumn"] = forced_content
        if forced_time:
            print(f"[时间列] 使用指定列: {forced_time}")
            invoke_params["timeColumn"] = forced_time

        result = analysis_timeline.invoke(invoke_params)

        # 解析并打印结果
        if isinstance(result, str):
            try:
                parsed = json.loads(result)

                # 检查是否有错误
                if "error" in parsed:
                    print(f"❌ 错误: {parsed['error']}")
                    return

                print("\n✅ 分析成功！")
                print("\n结果详情:")
                print(json.dumps(parsed, ensure_ascii=False, indent=2))

                # 验证关键字段
                print("\n字段验证:")
                timeline = parsed.get("timeline", [])
                if timeline:
                    print(f"  - 时间线节点数量: {len(timeline)}")
                    print(f"  - 时间线节点:")
                    for idx, node in enumerate(timeline[:5], 1):  # 只显示前5个
                        time_str = node.get("time", "N/A")
                        event_str = node.get("event", "N/A")
                        print(f"    {idx}. [{time_str}] {event_str}")
                    if len(timeline) > 5:
                        print(f"    ... 还有 {len(timeline) - 5} 个节点")
                else:
                    print("  - 时间线节点数量: 0")

                summary = parsed.get("summary", "")
                print(f"  - 时间线摘要: {summary[:200]}..." if len(summary) > 200 else f"  - 时间线摘要: {summary}")

                # 验证结果文件路径
                result_file_path = parsed.get("result_file_path", "")
                if result_file_path:
                    print(f"  - 结果文件路径: {result_file_path}")
                    result_file = Path(result_file_path)
                    if result_file.exists():
                        print(f"  ✅ 结果文件已保存: {result_file_path}")
                        print(f"  - 文件大小: {result_file.stat().st_size} 字节")
                    else:
                        print(f"  ⚠️  结果文件路径存在但文件未找到: {result_file_path}")
                else:
                    print("  ⚠️  未返回结果文件路径")

            except json.JSONDecodeError:
                print("⚠️  返回结果不是有效的 JSON:")
                print(result)
        else:
            print("返回结果:")
            print(result)

        print("\n✅ 测试完成！")
    finally:
        # 清理任务上下文
        set_task_id(None)


if __name__ == "__main__":
    main()
