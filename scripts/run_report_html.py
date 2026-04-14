"""测试脚本：调用 report_html 工具生成HTML报告。"""

from __future__ import annotations

import json
import sys
from pathlib import Path
import webbrowser

# 添加项目根目录到 Python 路径
project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

from tools.report_html import report_html
from utils.path import ensure_task_dirs
from utils.task_context import set_task_id


def main() -> None:
    """主函数：执行HTML报告生成测试。"""
    # 设置任务上下文
    task_id = "测试"
    ensure_task_dirs(task_id)
    set_task_id(task_id)
    
    try:
        # 测试配置示例
        test_configs = [
            {
                "eventIntroduction": "洛克王国世界相关舆情事件，关注游戏开服、玩家体验、游戏质量、玩家反馈",
                "analysisResultsDir": "sandbox/测试/过程文件"
            }
        ]
        
        print("=" * 80)
        print("report_html 工具测试")
        print("=" * 80)
        
        for i, config in enumerate(test_configs, 1):
            print(f"\n[测试 {i}/{len(test_configs)}]")
            print(f"事件介绍: {config['eventIntroduction']}")
            print(f"分析结果目录: {config['analysisResultsDir']}")
            print("-" * 80)
            
            # 检查目录是否存在
            results_dir = Path(config['analysisResultsDir'])
            if not results_dir.exists():
                print(f"分析结果目录不存在: {config['analysisResultsDir']}")
                print("   请确保目录存在后再运行测试")
                continue
            
            # 检查是否有JSON文件
            json_files = list(results_dir.glob("*.json"))
            if not json_files:
                print(f"分析结果目录中没有找到JSON文件: {config['analysisResultsDir']}")
                print("   请确保目录中有JSON文件后再运行测试")
                continue
            
            print(f"找到 {len(json_files)} 个JSON文件")
            
            try:
                # 调用工具
                invoke_params = {
                    "eventIntroduction": config["eventIntroduction"],
                    "analysisResultsDir": config["analysisResultsDir"]
                }
                
                result = report_html.invoke(invoke_params)
                
                # 解析并打印结果
                if isinstance(result, str):
                    try:
                        parsed = json.loads(result)
                        
                        # 检查是否有错误
                        if "error" in parsed:
                            print(f"错误: {parsed['error']}")
                            continue
                        
                        print("\n生成成功！")
                        print("\n结果详情:")
                        print(json.dumps(parsed, ensure_ascii=False, indent=2))
                        
                        # 验证关键字段
                        print("\n字段验证:")
                        html_file_path = parsed.get("html_file_path", "")
                        if html_file_path:
                            print(f"  - HTML文件路径: {html_file_path}")
                            html_file = Path(html_file_path)
                            if html_file.exists():
                                print(f"  HTML文件已保存: {html_file_path}")
                                print(f"  - 文件大小: {html_file.stat().st_size} 字节")
                            else:
                                print(f"  HTML文件路径存在但文件未找到: {html_file_path}")
                        else:
                            print("  未返回HTML文件路径")
                        
                        file_url = parsed.get("file_url", "")
                        if file_url:
                            print(f"  - 文件访问地址: {file_url}")
                            print(f"  - 访问方式: 在浏览器中打开 {file_url}")
                            
                            # 自动在默认浏览器中打开生成的报告
                            try:
                                webbrowser.open(file_url)
                                print(f"\n已在默认浏览器中打开报告: {file_url}")
                            except Exception as open_err:
                                print(f"\n警告：自动在浏览器中打开报告失败: {open_err}")
                        else:
                            print("  未返回文件访问地址")
                        
                    except json.JSONDecodeError:
                        print("返回结果不是有效的 JSON:")
                        print(result)
                else:
                    print("返回结果:")
                    print(result)
                    
            except Exception as e:
                print(f"错误: {str(e)}")
                import traceback
                traceback.print_exc()
            
            print("\n" + "=" * 80)
        
        print("\n测试完成！")
    finally:
        # 清理任务上下文
        set_task_id(None)


if __name__ == "__main__":
    main()
