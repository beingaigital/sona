"""舆情监测命令入口。"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Optional

from rich.console import Console

console = Console()


def run_monitor_command(config_path: Optional[str] = None, task_id: Optional[str] = None) -> None:
    """执行一次监测任务并展示结果。"""
    try:
        module_path = Path(__file__).resolve().parents[1] / "tools" / "monitoring.py"
        spec = importlib.util.spec_from_file_location("sona_monitoring", module_path)
        if spec is None or spec.loader is None:
            raise RuntimeError("无法加载 monitoring 模块")
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        run_monitoring = module.run_monitoring

        normalized_config_path: Optional[str] = None
        if config_path:
            normalized_config_path = str(Path(config_path).expanduser().resolve())

        console.print()
        console.print("[bold cyan]启动舆情专题监测...[/bold cyan]")
        if normalized_config_path:
            console.print(f"[dim]配置文件: {normalized_config_path}[/dim]")
        if task_id:
            console.print(f"[dim]任务ID: {task_id}[/dim]")

        result = run_monitoring(config_path=normalized_config_path, task_id=task_id)
        if not result.get("ok"):
            console.print(f"[yellow]⚠️ {result.get('message', '监测未执行')}[/yellow]")
            console.print()
            return

        rows = result.get("results") or []
        if not rows:
            console.print("[yellow]本次没有监测结果。[/yellow]")
            console.print()
            return

        for row in rows:
            status = row.get("status")
            if status == "alert":
                console.print(
                    f"[red]🚨 {row.get('task_name')} ({row.get('task_id')}) 触发预警 {len(row.get('alerts') or [])} 条[/red]"
                )
                for msg in (row.get("alerts") or [])[:6]:
                    console.print(f"  [red]- {msg}[/red]")
            elif status == "skipped":
                console.print(
                    f"[blue]⏭️ {row.get('task_name')} ({row.get('task_id')}) 跳过：{row.get('reason', '')}[/blue]"
                )
            else:
                console.print(f"[green]✅ {row.get('task_name')} ({row.get('task_id')}) 正常[/green]")
            if row.get("artifact_path"):
                console.print(f"[dim]  artifact: {row.get('artifact_path')}[/dim]")

        console.print()
    except Exception as exc:
        console.print(f"[red]❌ 监测流程执行失败: {exc}[/red]")
