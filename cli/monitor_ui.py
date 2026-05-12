"""CLI bridge for /monitor command（专题监测）。"""

from __future__ import annotations

from pathlib import Path

from rich.prompt import Prompt

from cli.display import console
from workflow.topic_monitoring_pipeline import TopicMonitoringPipeline, run_high_speed_rail_demo


def _print_help() -> None:
    console.print("[cyan]/monitor[/cyan]  - 专题监测命令")
    console.print("  [cyan]/monitor help[/cyan]                 - 显示命令帮助")
    console.print("  [cyan]/monitor demo[/cyan]                 - 运行高铁舆情内存演示")
    console.print("  [cyan]/monitor list[/cyan]                 - 列出当前进程内/外部库专题")
    console.print("  [cyan]/monitor create 名称|领域|关键词1,关键词2[/cyan]")
    console.print("  [cyan]/monitor status <topic_id>[/cyan]    - 查询专题状态")
    console.print("  [cyan]/monitor report <topic_id> [daily|weekly][/cyan] - 生成日报/周报")


def _parse_create_args(args: str) -> tuple[str, str, list[str]]:
    parts = [p.strip() for p in args.split("|")]
    name = parts[0] if len(parts) > 0 else ""
    domain = parts[1] if len(parts) > 1 else ""
    keywords = [k.strip() for k in parts[2].split(",")] if len(parts) > 2 else []
    return name, domain, [k for k in keywords if k]


def run_monitor_command(raw_query: str | None = None) -> None:
    query = str(raw_query or "").strip()
    if not query:
        query = Prompt.ask("请输入 monitor 子命令").strip()
    if not query:
        _print_help()
        return

    parts = query.split(maxsplit=1)
    command = parts[0].lower()
    rest = parts[1].strip() if len(parts) > 1 else ""

    try:
        pipeline = TopicMonitoringPipeline()

        if command in ("help", "h", "?"):
            _print_help()
            return

        if command == "demo":
            console.print("[cyan]开始运行高铁舆情专题监测演示...[/cyan]")
            result = run_high_speed_rail_demo()
            console.print("[green]完成[/green]")
            console.print(f"专题 ID: [cyan]{result['topic'].get('id')}[/cyan]")
            console.print(f"报告路径: [cyan]{result['report']['report_path']}[/cyan]")
            return

        if command == "list":
            topics = pipeline.db.list_monitor_topics()
            if not topics:
                console.print("[yellow]当前暂无专题。可先运行 /monitor demo 或 /monitor create。[/yellow]")
                return
            console.print(f"[bold]已创建专题 ({len(topics)})[/bold]")
            for topic in topics:
                console.print(f"- {topic.get('id', '')} | {topic.get('name', '')} | {topic.get('domain', '')}")
            return

        if command == "create":
            name, domain, keywords = _parse_create_args(rest)
            if not name:
                name = Prompt.ask("请输入专题名称").strip()
            if not domain:
                domain = Prompt.ask("请输入专题领域").strip()
            if not keywords:
                raw_keywords = Prompt.ask("请输入关键词，逗号分隔").strip()
                keywords = [k.strip() for k in raw_keywords.split(",") if k.strip()]
            if not name or not domain or not keywords:
                console.print("[red]专题名称、领域和关键词都不能为空。[/red]")
                return
            topic = pipeline.create_topic(name=name, domain=domain, keywords=keywords, description="由 /monitor 创建")
            console.print("[green]已创建专题[/green]")
            console.print(f"ID: [cyan]{topic.get('id')}[/cyan]")
            return

        if command == "status":
            topic_id = rest or Prompt.ask("请输入专题 ID").strip()
            if not topic_id:
                console.print("[red]需要指定专题 ID。[/red]")
                return
            status = pipeline.get_topic_status(topic_id)
            if status.get("error"):
                console.print(f"[red]{status['error']}[/red]")
                return
            topic = status.get("topic") or {}
            console.print(f"[bold]专题状态：{topic.get('name','')} ({topic_id})[/bold]")
            console.print(f"领域：{topic.get('domain','')}  描述：{topic.get('description','')}")
            console.print(f"关键词：{', '.join(k.get('keyword','') for k in status.get('keywords') or [])}")
            console.print(f"最新快照：{status.get('latest_snapshot')}")
            console.print(f"未解决告警：{len(status.get('active_alerts') or [])}")
            return

        if command == "report":
            if not rest:
                console.print("[red]/monitor report 需要专题 ID，可选 daily 或 weekly。[/red]")
                return
            args = rest.split()
            topic_id = args[0]
            period = args[1] if len(args) > 1 else "daily"
            output_dir = Path(__file__).resolve().parents[1] / "topic_monitoring_reports"
            result = pipeline.generate_periodic_report(topic_id, period=period, output_dir=output_dir)
            console.print("[green]已生成报告[/green]")
            console.print(f"报告路径: [cyan]{result['report_path']}[/cyan]")
            return

        console.print(f"[yellow]未知 /monitor 子命令: {command}[/yellow]")
        _print_help()
    except Exception as exc:
        console.print(f"[red]/monitor 执行失败: {exc}[/red]")
        console.print(
            "[yellow]若要接 Supabase/Postgres，请配置 SUPABASE_URL/SUPABASE_KEY "
            "或 DATABASE_URL/POSTGRES_URL，并先执行 workflow/topic_monitoring_schema.sql。[/yellow]"
        )
