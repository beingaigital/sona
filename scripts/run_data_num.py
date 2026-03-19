"""测试脚本：调用 data_num 工具查询微博渠道数据数量。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# 添加项目根目录到 Python 路径
project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

from tools.data_num import data_num


def main() -> None:
    """主函数：执行数据数量查询测试。"""
    # 测试配置示例
    test_configs = [
        {
            "searchWords": '["元宝", "小米"]',
            "timeRange": "2026-02-20 00:00:00;2026-03-01 23:59:59",
            "threshold": 2000
        }
    ]
    
    print("=" * 80)
    print("data_num 工具测试")
    print("=" * 80)
    
    for i, config in enumerate(test_configs, 1):
        print(f"\n[测试 {i}/{len(test_configs)}]")
        print(f"搜索词: {config['searchWords']}")
        print(f"时间范围: {config['timeRange']}")
        print(f"数量阈值: {config.get('threshold', 2000)}")
        print("-" * 80)
        
        try:
            # 调用工具
            result = data_num.invoke({
                "searchWords": config["searchWords"],
                "timeRange": config["timeRange"],
                "threshold": config.get("threshold", 2000)
            })
            
            # 解析并打印结果
            if isinstance(result, str):
                try:
                    parsed = json.loads(result)
                    
                    # 检查是否有错误
                    if "error" in parsed:
                        print(f"❌ 错误: {parsed['error']}")
                        continue
                    
                    print("\n✅ 查询成功！")
                    print("\n结果详情:")
                    print(json.dumps(parsed, ensure_ascii=False, indent=2))
                    
                    # 验证关键字段
                    print("\n字段验证:")
                    search_matrix = parsed.get("search_matrix", {})
                    if search_matrix:
                        print(f"  - 搜索矩阵:")
                        for keyword, count in search_matrix.items():
                            print(f"    • {keyword}: {count} 条")
                    
                    total_count = parsed.get("total_count", 0)
                    print(f"  - 总数量: {total_count} 条")
                    
                    time_range = parsed.get("time_range", "")
                    print(f"  - 时间范围: {time_range}")
                    
                    threshold = parsed.get("threshold", 2000)
                    print(f"  - 数量阈值: {threshold} 条")
                    
                    # 验证比例分配逻辑
                    if total_count > 0:
                        print(f"\n  比例分配验证:")
                        print(f"    - 总和是否接近阈值({threshold}): {'是' if total_count <= threshold else f'否（{total_count}条）'}")
                        if total_count > threshold:
                            print(f"    - 已按比例分配，总和: {total_count} 条")
                    
                    # 显示警告（如果有）
                    if "warnings" in parsed:
                        print(f"\n  ⚠️  警告:")
                        for warning in parsed["warnings"]:
                            print(f"    - {warning}")
                    
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


if __name__ == "__main__":
    main()
