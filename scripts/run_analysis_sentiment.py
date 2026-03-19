"""测试脚本：调用 analysis_sentiment 工具分析情感倾向。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# 添加项目根目录到 Python 路径
project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

from tools.analysis_sentiment import analysis_sentiment
from utils.path import ensure_task_dirs
from utils.task_context import set_task_id


def main() -> None:
    """主函数：执行情感倾向分析测试。"""
    # 设置任务上下文
    task_id = "测试"
    ensure_task_dirs(task_id)
    set_task_id(task_id)
    
    try:
        # 测试配置示例
        test_configs = [
            {
                "eventIntroduction": "美伊战争相关舆情事件，关注军事行动、外交谈判破裂、地区外溢及国际反应。",
                "dataFilePath": "sandbox/测试/过程文件/测试.csv",
                "retryContext": None
            }
        ]
        
        print("=" * 80)
        print("analysis_sentiment 工具测试")
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
                
                result = analysis_sentiment.invoke(invoke_params)
                
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
                        statistics = parsed.get("statistics", {})
                        if statistics:
                            print(f"  - 总数据量: {statistics.get('total', 0)}")
                            print(f"  - 正面数量: {statistics.get('positive_count', 0)} ({statistics.get('positive_ratio', 0)*100:.2f}%)")
                            print(f"  - 负面数量: {statistics.get('negative_count', 0)} ({statistics.get('negative_ratio', 0)*100:.2f}%)")
                            print(f"  - 中性数量: {statistics.get('neutral_count', 0)} ({statistics.get('neutral_ratio', 0)*100:.2f}%)")
                        else:
                            print("  - 统计信息: 无")
                        
                        positive_summary = parsed.get("positive_summary", [])
                        if positive_summary:
                            print(f"  - 正面观点数量: {len(positive_summary)}")
                            print(f"  - 正面观点:")
                            for idx, view in enumerate(positive_summary[:3], 1):
                                print(f"    {idx}. {view}")
                        else:
                            print("  - 正面观点: 无")
                        
                        negative_summary = parsed.get("negative_summary", [])
                        if negative_summary:
                            print(f"  - 负面观点数量: {len(negative_summary)}")
                            print(f"  - 负面观点:")
                            for idx, view in enumerate(negative_summary[:3], 1):
                                print(f"    {idx}. {view}")
                        else:
                            print("  - 负面观点: 无")
                        
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
