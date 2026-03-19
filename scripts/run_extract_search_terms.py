"""测试脚本：调用 extract_search_terms 工具提取搜索关键词和配置。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# 添加项目根目录到 Python 路径
project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

from tools.extract_search_terms import extract_search_terms


def main() -> None:
    """主函数：执行搜索词提取测试。"""
    # 测试查询示例
    test_queries = [
        "小米最新跑车上线",
    ]
    
    print("=" * 80)
    print("extract_search_terms 工具测试")
    print("=" * 80)
    
    for i, query in enumerate(test_queries, 1):
        print(f"\n[测试 {i}/{len(test_queries)}]")
        print(f"查询: {query}")
        print("-" * 80)
        
        try:
            # 调用工具
            result = extract_search_terms.invoke({"query": query})
            
            # 解析并打印结果
            if isinstance(result, str):
                try:
                    parsed = json.loads(result)
                    print("提取结果:")
                    print(json.dumps(parsed, ensure_ascii=False, indent=2))
                    
                    # 验证关键字段
                    print("\n字段验证:")
                    print(f"  - 事件介绍: {parsed.get('eventIntroduction', 'N/A')[:100]}...")
                    print(f"  - 检索词: {parsed.get('searchWords', [])}")
                    print(f"  - 时间范围: {parsed.get('timeRange', 'N/A')}")
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
