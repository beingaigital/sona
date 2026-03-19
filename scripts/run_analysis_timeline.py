"""测试脚本：调用 analysis_timeline 工具分析事件时间线。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# 添加项目根目录到 Python 路径
project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

from tools.analysis_timeline import analysis_timeline
from utils.path import ensure_task_dirs
from utils.task_context import set_task_id


def main() -> None:
    """主函数：执行时间线分析测试。"""
    # 设置任务上下文
    task_id = "测试"
    ensure_task_dirs(task_id)
    set_task_id(task_id)
    
    try:
        # 测试配置示例
        test_configs = [
            {
                "eventIntroduction": "美伊战争",
                "dataFilePath": "sandbox/测试/过程文件/测试.csv",
                "retryContext": None
            }
        ]
        
        print("=" * 80)
        print("analysis_timeline 工具测试")
        print("=" * 80)
        
        for i, config in enumerate(test_configs, 1):
            print(f"\n[测试 {i}/{len(test_configs)}]")
            print(f"事件介绍: {config['eventIntroduction']}")
            print(f"数据文件: {config['dataFilePath']}")
            print(f"重试上下文: {'无' if not config.get('retryContext') else '有'}")
            print("-" * 80)
            
            # 检查文件是否存在
            data_file = Path(config['dataFilePath'])
            if not data_file.exists():
                print(f"⚠️  数据文件不存在: {config['dataFilePath']}")
                print("   请确保文件存在后再运行测试")
                continue
            
            try:
                # 调用工具
                invoke_params = {
                    "eventIntroduction": config["eventIntroduction"],
                    "dataFilePath": config["dataFilePath"]
                }
                if config.get("retryContext"):
                    invoke_params["retryContext"] = config["retryContext"]
                
                result = analysis_timeline.invoke(invoke_params)
                
                # 解析并打印结果
                if isinstance(result, str):
                    try:
                        parsed = json.loads(result)
                        
                        # 检查是否有错误
                        if "error" in parsed:
                            print(f"❌ 错误: {parsed['error']}")
                            continue
                        
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
                    
            except Exception as e:
                print(f"❌ 错误: {str(e)}")
                import traceback
                traceback.print_exc()
            
            print("\n" + "=" * 80)
        
        print("\n✅ 测试完成！")
    finally:
        # 清理任务上下文
        set_task_id(None)


if __name__ == "__main__":
    main()
