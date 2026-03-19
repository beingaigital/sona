"""测试脚本：调用 BoCha AI Search API 进行网页搜索。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# 添加项目根目录到 Python 路径
project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

from mcps.web_search import bocha_ai_search


def main() -> None:
    """主函数：执行网页搜索测试。"""
    # 测试查询
    query = "小米新概念车"
    
    try:
        # 调用搜索 API
        result = bocha_ai_search(
            query=query,
            summary=True,
            count=10,
            freshness= "oneWeek"
        )
        
        # 打印结果
        print("搜索结果:")
        print(json.dumps(result, ensure_ascii=False, indent=2))
        
    except Exception as e:
        print(f"❌ 错误: {str(e)}")
        sys.exit(1)


if __name__ == "__main__":
    main()
