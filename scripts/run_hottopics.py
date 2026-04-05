"""测试脚本：运行热点抓取与态势感知流程。"""

from __future__ import annotations

import sys
from pathlib import Path

# 添加项目根目录到 Python 路径
project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

from tools.hottopics import run as run_hot_topics


def main() -> None:
    """主函数：执行热点抓取与态势感知流程。"""
    report_path = run_hot_topics()
    if report_path:
        print(f"热点报告已生成: {report_path}")
    else:
        print("流程已执行，但未返回报告路径。")


if __name__ == "__main__":
    main()
