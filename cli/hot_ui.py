"""热点态势命令入口：执行独立的热点抓取与分析流程。"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from rich.console import Console

console = Console()


def run_hot_command(config_path: Optional[str] = None) -> None:
    """执行热点抓取与态势感知流程，并输出结果路径。"""
    import os

    try:
        from utils.hot_topics_env import ensure_hot_topics_cwd, prepare_hot_topics_environment

        prepare_hot_topics_environment()
        ensure_hot_topics_cwd()

        # 惰性导入，避免 CLI 启动时引入重依赖（在 .env 与 cwd 就绪之后）
        from tools.hottopics import run as run_hot_topics

        normalized_config_path: Optional[str] = None
        if config_path:
            normalized_config_path = str(Path(config_path).expanduser().resolve())

        console.print()
        console.print("[bold cyan]启动热点抓取与态势感知流程...[/bold cyan]")
        if normalized_config_path:
            console.print(f"[dim]配置文件: {normalized_config_path}[/dim]")

        if not os.environ.get("INSIGHT_ENGINE_API_KEY"):
            console.print(
                "[yellow]提示: 未检测到 INSIGHT_ENGINE_API_KEY。[/yellow] "
                "请在 .env 中至少配置其一：QWEN_APIKEY、OPENAI_APIKEY、DEEPSEEK_APIKEY、KIMI_APIKEY，"
                "或显式设置 INSIGHT_ENGINE_API_KEY / QUERY_ENGINE_API_KEY。"
            )
            console.print()

        # 展示当前热点流程实际使用的 base_url（便于确认走 coding plan）
        base_url = os.environ.get("INSIGHT_ENGINE_BASE_URL") or ""
        if base_url:
            console.print(f"[dim]hot LLM base_url: {base_url}[/dim]")

        report_path = run_hot_topics(config_path=normalized_config_path)
        if report_path:
            console.print(f"[green]✅ 热点报告已生成: {report_path}[/green]")
        else:
            console.print("[yellow]流程已执行，但未返回报告路径。[/yellow]")
        console.print()
    except Exception as exc:
        console.print(f"[red]❌ 热点流程执行失败: {exc}[/red]")
