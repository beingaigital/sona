"""测试脚本：调用 data_collect 工具抓取舆情数据。"""

from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path

# 添加项目根目录到 Python 路径
project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

from tools.data_collect import data_collect
from utils.path import ensure_task_dirs
from utils.task_context import set_task_id


def main() -> None:
    """主函数：执行数据抓取测试。"""
    # 测试配置示例
    test_configs = [
        {
            "searchMatrix": '{"元宝": 344, "小米": 41}',
            "timeRange": "2026-02-20 00:00:00;2026-03-01 23:59:59"
        },
    ]
    
    print("=" * 80)
    print("data_collect 工具测试")
    print("=" * 80)
    print("=" * 80)
    
    for i, config in enumerate(test_configs, 1):
        print(f"\n[测试 {i}/{len(test_configs)}]")
        print(f"搜索矩阵: {config['searchMatrix']}")
        print(f"时间范围: {config['timeRange']}")
        print(f"平台: 微博（固定）")
        print("-" * 80)
        
        # 生成任务ID并设置上下文
        task_id = str(uuid.uuid4())
        ensure_task_dirs(task_id)
        set_task_id(task_id)
        
        print(f"任务ID: {task_id}")
        print(f"数据将保存到: sandbox/{task_id}/过程文件/")
        
        try:
            # 调用工具
            result = data_collect.invoke({
                "searchMatrix": config["searchMatrix"],
                "timeRange": config["timeRange"]
            })
            
            # 解析并打印结果
            if isinstance(result, str):
                try:
                    parsed = json.loads(result)
                    
                    # 检查是否有错误
                    if "error" in parsed:
                        print(f"❌ 错误: {parsed['error']}")
                        continue
                    
                    print("\n✅ 抓取成功！")
                    print("\n结果详情:")
                    print(json.dumps(parsed, ensure_ascii=False, indent=2))
                    
                    # 验证关键字段
                    print("\n字段验证:")
                    save_path = parsed.get("save_path", "")
                    if save_path:
                        print(f"  - 文件保存路径: {save_path}")
                        # 检查文件是否存在
                        if Path(save_path).exists():
                            file_size = Path(save_path).stat().st_size
                            print(f"  - 文件大小: {file_size / 1024:.2f} KB")
                    
                    meta = parsed.get("meta", {})
                    if meta:
                        print(f"  - 平台: {meta.get('platform', 'N/A')}")
                        print(f"  - 爬取数量（去重后）: {meta.get('count', 0)}")
                        print(f"  - 字段数量: {len(meta.get('fields', []))}")
                        print(f"  - 字段列表: {', '.join(meta.get('fields', [])[:10])}")
                        if len(meta.get('fields', [])) > 10:
                            print(f"    ... 还有 {len(meta.get('fields', [])) - 10} 个字段")
                        
                        # 显示搜索摘要
                        search_summary = meta.get('search_summary', {})
                        if search_summary:
                            print("\n  搜索词爬取摘要:")
                            for keyword, summary in search_summary.items():
                                status_emoji = "✅" if summary.get('status') == 'success' else "⚠️" if summary.get('status') == 'no_data' else "❌"
                                print(f"    {status_emoji} {keyword}: 目标 {summary.get('target', 0)} 条, 实际 {summary.get('actual', 0)} 条")
                        
                        # 显示字段类型示例
                        field_types = meta.get('field_types', {})
                        if field_types:
                            print("\n  字段类型示例:")
                            for field, ftype in list(field_types.items())[:5]:
                                print(f"    - {field}: {ftype}")
                            if len(field_types) > 5:
                                print(f"    ... 还有 {len(field_types) - 5} 个字段类型")
                    
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
        
        finally:
            # 清理任务上下文
            set_task_id(None)
        
        print("\n" + "=" * 80)
    
    print("\n✅ 测试完成！")


if __name__ == "__main__":
    main()
