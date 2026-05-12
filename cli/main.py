"""CLI 主入口：交互式命令行界面与子命令。"""

from __future__ import annotations

import os
import sys
from typing import List, Optional

import typer
from cli.serve_cmd import run_serve

app = typer.Typer(
    help="Sona：舆情分析 Agent。默认进入交互式 CLI；使用 serve 启动 HTTP API。",
    add_completion=False,
)


def interactive() -> None:
    """进入交互式模式（默认命令）"""
    from rich.prompt import Prompt

    from cli.clear_utils import confirm_and_clear
    from cli.case_ui import run_case_command
    from cli.display import console, print_icon, print_welcome
    from cli.hot_ui import run_hot_command
    from cli.interactive import run_session_loop
    from cli.monitor_ui import run_monitor_command
    from cli.models_ui import show_models_list
    from cli.session_ui import show_session_selector
    from cli.tools_ui import show_tools_list
    from cli.wiki_ui import run_wiki_approve_command, run_wiki_command

    # 显示图标和欢迎信息
    print_icon()
    print_welcome()
    
    # 主循环：处理命令
    while True:
        try:
            # 在 user 提示前添加绿色横线作为对话区隔
            console.print("[green]────────────────────────────────────────────────────────────[/green]")
            user_input = Prompt.ask("[bold cyan]user[/bold cyan]")
            
            if not user_input:
                continue
            
            # 处理系统级命令（以 / 开头）
            if user_input.strip().startswith("/"):
                # 处理 /exit 命令
                if user_input.strip() == "/exit":
                    console.print(f"\n[cyan]感谢使用 [bold magenta]Sona[/bold magenta]，再见！[/cyan]\n")
                    break
                
                # 处理 /new 命令
                if user_input.strip() == "/new":
                    run_session_loop(task_id=None)
                    continue

                # 处理 /event 命令：强制事件分析工作流（可直接带 query）
                if user_input.strip().startswith("/event"):
                    parts = user_input.strip().split(maxsplit=1)
                    event_query = parts[1].strip() if len(parts) > 1 else None
                    run_session_loop(
                        task_id=None,
                        force_event_workflow=True,
                        preset_initial_query=event_query,
                    )
                    continue
                
                # 处理 /memory 命令（选择会话）
                if user_input.strip() == "/memory":
                    selected_task_id = show_session_selector(limit=5)
                    if selected_task_id:
                        run_session_loop(task_id=selected_task_id)
                    continue
                
                # 处理 /models 命令（显示模型配置）
                if user_input.strip() == "/models":
                    show_models_list()
                    continue
                
                # 处理 /tools 命令（显示工具列表）
                if user_input.strip() == "/tools":
                    show_tools_list()
                    continue
                
                # 处理 /clear 命令（清除 memory 和 sandbox）
                if user_input.strip() == "/clear":
                    confirm_and_clear()
                    continue

                # 处理 /hot 命令（独立热点抓取与态势感知）
                if user_input.strip().startswith("/hot"):
                    parts = user_input.strip().split(maxsplit=1)
                    custom_config_path = parts[1] if len(parts) > 1 else None
                    run_hot_command(custom_config_path)
                    continue

                # 处理 /case 命令（案例库专用检索）
                if user_input.strip().startswith("/case"):
                    parts = user_input.strip().split(maxsplit=1)
                    case_query = parts[1].strip() if len(parts) > 1 else None
                    run_case_command(case_query)
                    continue

                # 处理 /monitor 命令（专题监测）
                if user_input.strip().startswith("/monitor"):
                    parts = user_input.strip().split(maxsplit=1)
                    monitor_query = parts[1].strip() if len(parts) > 1 else None
                    run_monitor_command(monitor_query)
                    continue

                # 处理 /wiki-approve 命令（候选回流）
                if user_input.strip().startswith("/wiki-approve"):
                    parts = user_input.strip().split(maxsplit=1)
                    selector = parts[1].strip() if len(parts) > 1 else None
                    run_wiki_approve_command(selector)
                    continue

                # 处理 /wiki 命令（知识问答）
                if user_input.strip().startswith("/wiki"):
                    parts = user_input.strip().split(maxsplit=1)
                    wiki_query = parts[1].strip() if len(parts) > 1 else None
                    run_wiki_command(wiki_query)
                    continue
                
                # 处理其他未知命令
                console.print(f"[yellow]未知命令: {user_input}[/yellow]")
                console.print("[cyan]可用命令:[/cyan]")
                console.print("  [cyan]/new[/cyan]     - 开启新的分析会话")
                console.print("  [cyan]/event[/cyan]   - 强制进入事件分析工作流（可带 query）")
                console.print("  [dim]                 示例: /event 315晚会舆情分析[/dim]")
                console.print("  [cyan]/memory[/cyan]  - 查看并恢复之前的会话")
                console.print("  [cyan]/models[/cyan]  - 查看所有模型配置")
                console.print("  [cyan]/tools[/cyan]   - 查看所有可用工具")
                console.print("  [cyan]/hot[/cyan]     - 运行热点抓取与态势感知流程")
                console.print("  [dim]                 示例: /hot 或 /hot config/config.yaml[/dim]")
                console.print("  [cyan]/case[/cyan]    - 检索案例库并输出相似案例对照")
                console.print("  [dim]                 示例: /case 找几个高铁服务争议案例[/dim]")
                console.print("  [cyan]/monitor[/cyan] - 专题监测（创建专题/日报周报/演示）")
                console.print("  [dim]                 示例: /monitor demo 或 /monitor create 高铁舆情|交通|高铁,服务[/dim]")
                console.print("  [cyan]/wiki[/cyan]    - 知识库问答（answer + sources）")
                console.print("  [dim]                 示例: /wiki 什么是舆情反转？[/dim]")
                console.print("  [cyan]/wiki-approve[/cyan] - 审核并回流高价值候选到 output")
                console.print("  [dim]                 示例: /wiki-approve 或 /wiki-approve 罗永浩[/dim]")
                console.print("  [cyan]/clear[/cyan]   - 清除 memory 和 sandbox")
                console.print("  [cyan]/exit[/cyan]    - 退出程序")
                continue
            
            # 默认行为：只提示，不创建会话
            console.print(
                "[yellow]提示: 使用 '/new' 开启新会话，'/memory' 恢复会话，"
                "'/event' 事件分析，'/wiki' 知识问答，'/hot' 热点态势。[/yellow]"
            )
            
        except KeyboardInterrupt:
            console.print(f"\n\n[cyan]感谢使用 [bold magenta]Sona[/bold magenta]，再见！[/cyan]\n")
            sys.exit(0)
        except EOFError:
            console.print(f"\n\n[cyan]感谢使用 [bold magenta]Sona[/bold magenta]，再见！[/cyan]\n")
            sys.exit(0)
        except Exception as e:
            console.print(f"\n[red]❌[/red] [red]发生错误:[/red] [white]{str(e)}[/white]\n")
            import traceback
            traceback.print_exc()


@app.callback(invoke_without_command=True)
def _typer_root(ctx: typer.Context) -> None:
    """无子命令时进入交互式 CLI。"""
    if ctx.invoked_subcommand is None:
        interactive()


@app.command("serve")
def serve_command(
    host: str = typer.Option(
        os.environ.get("SONA_API_HOST", "127.0.0.1"),
        "--host",
        help="监听地址（可用环境变量 SONA_API_HOST）。",
    ),
    port: int = typer.Option(
        int(os.environ.get("SONA_API_PORT", "8765")),
        "--port",
        help="监听端口（可用环境变量 SONA_API_PORT）。",
    ),
    reload: bool = typer.Option(
        False,
        "--reload",
        help="开发模式：代码变更自动重载。",
    ),
) -> None:
    """启动 FastAPI HTTP API。"""
    run_serve(host=host, port=port, reload=reload)


@app.command("case")
def case_command(query: Optional[List[str]] = typer.Argument(None, help="案例检索问题")) -> None:
    """检索本地案例库。"""
    from cli.case_ui import run_case_command

    run_case_command(" ".join(query or []))


@app.command("monitor")
def monitor_command(args: Optional[List[str]] = typer.Argument(None, help="专题监测子命令")) -> None:
    """运行专题监测命令。"""
    from cli.monitor_ui import run_monitor_command

    run_monitor_command(" ".join(args or []))


def main() -> None:
    """入口：交给 Typer 解析子命令；无参数时进入交互式模式。"""
    app()


if __name__ == "__main__":
    main()
