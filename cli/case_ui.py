"""CLI bridge for /case command（案例库专用检索）。"""

from __future__ import annotations

from pathlib import Path

from rich.prompt import Prompt

from cli.display import console
from workflow.wiki_cli import answer_case_query


def run_case_command(raw_query: str | None = None) -> None:
    """检索 Wiki cases：结构化列表 + 多案例对照摘要。"""
    query = str(raw_query or "").strip()
    if not query:
        query = Prompt.ask("请输入案例检索问题").strip()
    if not query:
        console.print("[yellow]/case 需要文本，例如：/case 找几个高铁服务争议案例[/yellow]")
        return

    root = Path(__file__).resolve().parents[1]
    result = answer_case_query(query, project_root=root)
    console.print("[bold]案例库检索结果[/bold]\n")
    console.print(result.get("answer") or "")
