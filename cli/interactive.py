"""交互式运行：支持会话管理和 token 追踪。"""

from __future__ import annotations

import json
import os
import sys
import time
import re
import traceback
import warnings
from typing import Any, Optional, List, Dict
import webbrowser
from langchain_core.messages import AIMessage, ToolMessage, HumanMessage, BaseMessage, ToolCall
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.prompt import Prompt

from agent.reactagent import stream
from utils.path import ensure_task_dirs
from utils.session_manager import get_session_manager
from utils.token_tracker import TokenUsageTracker
from cli.display import (
    print_status,
    print_tool_call,
    print_tool_result,
    print_agent_message,
    print_separator,
    console,
    print_token_usage,
)
from cli.session_ui import show_session_selector
from utils.message_utils import messages_from_session_data
from cli.event_analysis_workflow import run_event_analysis_workflow
from cli.router import route_query, get_router
from cli.hot_ui import run_hot_command
from cli.wiki_ui import run_wiki_command, run_wiki_approve_command
from tools.extract_search_terms import extract_search_terms
from rich.prompt import Confirm

LOG_PATH = "/Users/biaowenhuang/Documents/sona-master/.cursor/debug.log"


def _full_report_workflow_options(
    route_policy: Optional[Dict[str, Any]] = None,
    *,
    existing_data_path: Optional[str] = None,
    skip_data_collect: bool = False,
    force_fresh_start: Optional[bool] = False,
) -> Dict[str, Any]:
    """合并路由策略中的 report_length，并传递数据复用相关选项到 full_report 模式。"""
    rl = ""
    if isinstance(route_policy, dict):
        rl = str(route_policy.get("report_length") or "").strip()
    if not rl:
        rl = str(get_router().policy_loader.get_policy().report_length or "").strip()
    return {
        "existing_data_path": existing_data_path,
        "skip_data_collect": skip_data_collect,
        "force_fresh_start": force_fresh_start,
        "report_length": rl or "中篇",
    }


_PT_SESSION = None


def _should_use_prompt_toolkit() -> bool:
    v = os.environ.get("SONA_CLI_PROMPT_TOOLKIT", "true").strip().lower()
    if v in ("0", "false", "no", "n", "off"):
        return False
    try:
        return bool(sys.stdin.isatty() and sys.stdout.isatty())
    except Exception:
        return False


def _ask_user(prompt_text: str) -> str:
    """
    统一用户输入读取：
    - 优先 prompt-toolkit：支持左右移动/历史/编辑
    - 失败回退 rich Prompt.ask
    """
    global _PT_SESSION

    if _should_use_prompt_toolkit():
        try:
            from prompt_toolkit import PromptSession
            from prompt_toolkit.formatted_text import ANSI

            if _PT_SESSION is None:
                _PT_SESSION = PromptSession()
            return str(_PT_SESSION.prompt(ANSI(prompt_text))).strip()
        except Exception:
            pass

    plain = re.sub(r"\[[^\]]+\]", "", str(prompt_text))
    return str(Prompt.ask(plain)).strip()


def _append_ndjson_log(
    *,
    run_id: str,
    hypothesis_id: str,
    location: str,
    message: str,
    data: Optional[Dict[str, Any]] = None,
) -> None:
    """追加 NDJSON 调试日志（DEBUG MODE 运行证据）。"""
    payload: Dict[str, Any] = {
        "id": f"log_{int(time.time() * 1000)}_{abs(hash((hypothesis_id, location, message))) % 10_000_000}",
        "timestamp": int(time.time() * 1000),
        "runId": run_id,
        "hypothesisId": hypothesis_id,
        "location": location,
        "message": message,
        "data": data or {},
    }
    try:
        with open(LOG_PATH, "a", encoding="utf-8", errors="replace") as f:
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except Exception:
        return


def _run_event_brief_workflow(query: str, task_id: str) -> None:
    """
    轻量事件概述流程：
    - 仅调用 extract_search_terms
    - 输出事件简介 / 关键词 / 时间范围
    """
    session_manager = get_session_manager()
    session_manager.add_message(task_id, "user", query)
    try:
        raw = extract_search_terms.invoke({"query": query})
        if not isinstance(raw, str):
            raw = str(raw)
        data = json.loads(raw)
    except Exception as e:
        msg = f"轻量概述流程失败：{str(e)}"
        console.print(f"[red]{msg}[/red]")
        session_manager.add_message(task_id, "assistant", msg)
        return

    event_intro = str(data.get("eventIntroduction", "") or "").strip() or "证据不足"
    words = data.get("searchWords")
    if isinstance(words, list):
        keywords = [str(x).strip() for x in words if str(x).strip()]
    else:
        keywords = []
    time_range = str(data.get("timeRange", "") or "").strip() or "证据不足"

    lines = [
        "### 事件经过概述（轻量模式）",
        f"- 事件简介：{event_intro}",
        f"- 关键词：{'、'.join(keywords[:12]) if keywords else '证据不足'}",
        f"- 建议时间范围：{time_range}",
        "",
        "如需完整报告（采集+多维分析+HTML），请继续输入同一事件并强调“舆情分析/生成报告”，或直接使用 `/event`。",
    ]
    output = "\n".join(lines)
    console.print(Markdown(output))
    session_manager.add_message(task_id, "assistant", output)


def _build_execution_plan(route_decision: str, route_data: Dict[str, Any]) -> List[str]:
    """
    根据路由结果给出可解释执行计划（仅用于展示，不影响真实执行）。
    """
    intent_result = route_data.get("intent_result")
    data_result = route_data.get("data_result")
    has_data = bool(getattr(data_result, "has_data", False)) if data_result is not None else False

    steps: List[str] = []
    if route_decision in ("event_analysis_workflow", "event_analysis_with_existing_data"):
        if has_data and route_decision == "event_analysis_with_existing_data":
            steps = [
                "extract_search_terms（确认事件与关键词）",
                "复用已有数据（跳过 data_collect）",
                "analysis_timeline（时间线）",
                "analysis_sentiment（情感）",
                "keyword/region/author/volume 统计",
                "report_html（生成报告）",
            ]
        else:
            steps = [
                "extract_search_terms（提取事件与检索参数）",
                "data_num（关键词数量分配）",
                "data_collect（采集数据）",
                "analysis_timeline（时间线）",
                "analysis_sentiment（情感）",
                "keyword/region/author/volume 统计",
                "report_html（生成报告）",
            ]
    elif route_decision == "event_brief_workflow":
        steps = [
            "extract_search_terms（提取事件简介/关键词/时间范围）",
            "输出轻量事件概述（不采集、不生成长报告）",
        ]
    elif route_decision == "hottopics_workflow":
        steps = [
            "run_hot_command（热点聚合与态势感知）",
            "输出热点分析结果",
        ]
    else:
        steps = [
            "reactagent（按 ReAct 决策按需调用工具）",
            "返回问答结果",
        ]

    # 若有意图说明，放在第一行便于理解“为什么走这条路”
    reason = str(getattr(intent_result, "reasoning", "") or "").strip()
    if reason:
        steps.insert(0, f"路由依据：{reason}")
    return steps


def _print_execution_plan(route_decision: str, route_data: Dict[str, Any]) -> None:
    plan = _build_execution_plan(route_decision, route_data)
    if not plan:
        return
    console.print("[blue]🧩 本次执行计划[/blue]")
    for idx, step in enumerate(plan, start=1):
        console.print(f"[dim]  {idx}. {step}[/dim]")


def run_session_query(
    query: str,
    task_id: str,
    previous_messages: Optional[List[BaseMessage]] = None,
    show_spinner: bool = False,
    task_mode: str = "qa",
    workflow_options: Optional[Dict[str, Any]] = None,
) -> dict[str, Any]:
    """
    在会话中运行查询，支持 token 追踪
    
    Args:
        query: 用户查询
        task_id: 任务 ID
        previous_messages: 之前的消息列表
        
    Returns:
        Agent 的最终状态
    """
    session_manager = get_session_manager()
    token_tracker = TokenUsageTracker()
    last_printed_total_usage = {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
    }
    
    # full_report 模式由事件工作流内部写入用户消息，避免重复
    if str(task_mode or "qa").strip().lower() != "full_report":
        session_manager.add_message(task_id, "user", query)
    
    final_state = None
    processed_message_ids = set()
    pending_tool_calls: List[Dict[str, Any]] = []
    
    # 流式显示相关变量
    live_display = None
    current_content = ""
    current_msg_id = None
    is_streaming = False
    
    # 如果显示转圈动效，创建进度条
    progress = None
    if show_spinner:
        progress = Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
            transient=True
        )
        progress.start()
        task = progress.add_task("思考中...", total=None)
    
    try:
        def _safe_for_console(text: str) -> str:
            """
            将文本降级为当前终端编码可输出的字符串，避免 Windows/GBK 下 Rich 渲染崩溃。
            仅用于显示，不影响保存到会话的数据。
            """
            if not text:
                return ""
            # 常见不可编码字符做显式替换（提升可读性）
            text = text.replace("•", "-").replace("👇", "↓")
            encoding = getattr(getattr(console, "file", None), "encoding", None) or "utf-8"
            try:
                return text.encode(encoding, errors="replace").decode(encoding, errors="replace")
            except Exception:
                return text.encode("utf-8", errors="replace").decode("utf-8", errors="replace")

        def _print_reply_token_delta(step_name: str = "本次回复") -> None:
            """
            打印「自上次打印以来」新增的 token 使用量。

            说明：
            - token_tracker.total_usage 是累计值
            - 这里用 delta 实现“每次回复完成后”打印本轮消耗
            """
            nonlocal last_printed_total_usage
            total = token_tracker.get_total_usage()
            delta_prompt = total["prompt_tokens"] - last_printed_total_usage.get("prompt_tokens", 0)
            delta_completion = total["completion_tokens"] - last_printed_total_usage.get("completion_tokens", 0)
            delta_total = total["total_tokens"] - last_printed_total_usage.get("total_tokens", 0)
            if delta_total <= 0:
                last_printed_total_usage = total
                return

            print_token_usage(step_name, delta_prompt, delta_completion, delta_total)
            session_manager.add_token_usage(task_id, step_name, delta_prompt, delta_completion, delta_total)
            last_printed_total_usage = total

        # 设置当前步骤
        token_tracker.set_step("agent_processing")
        
        has_output = False
        for chunk in stream(
            query,
            task_id=task_id,
            previous_messages=previous_messages,
            token_tracker=token_tracker,
            task_mode=task_mode,
            workflow_options=workflow_options,
        ):
            
            # 处理 token 级别的流式输出
            if isinstance(chunk, dict) and "type" in chunk:
                chunk_type = chunk.get("type")
                
                # 消息压缩信息
                if chunk_type == "compression":
                    summary = chunk.get("summary", "")
                    original_count = chunk.get("original_count", 0)
                    compressed_count = chunk.get("compressed_count", 0)
                    compressed_messages = chunk.get("compressed_messages", [])
                    
                    console.print()
                    console.print(f"[yellow]对话历史已压缩[/yellow]")
                    if summary:
                        # 截断过长的摘要
                        summary_display = summary[:200] + "..." if len(summary) > 200 else summary
                        console.print(f"[dim]{summary_display}[/dim]")
                    console.print()
                    
                    # 关键修复：将压缩后的消息写回 session，并重置 token_usage
                    # 因为被压缩掉的旧消息的 token 不再计入上下文，所以需要重置累计值
                    if compressed_messages and task_id:
                        session_manager.replace_messages(task_id, compressed_messages, reset_token_usage=True)
                    
                    continue
                
                # Token 级别的流式输出
                if chunk_type == "token":
                    token_content = chunk.get("content", "")
                    accumulated_content = chunk.get("accumulated", "")
                    
                    if token_content:
                        # 如果有转圈动效，先停止
                        if progress and not has_output:
                            progress.stop()
                            has_output = True
                        
                        # 检测是否是新的流式响应开始
                        if not is_streaming:
                            # 开始新的流式响应
                            is_streaming = True
                            current_content = accumulated_content
                            current_msg_id = chunk.get("message_id", "")
                            
                            # 显示 assistant 提示
                            task_id_display = task_id[:8] if task_id and len(task_id) > 8 else task_id if task_id else ""
                            if task_id_display:
                                console.print(f"[bold orange1]({task_id_display}) assistant:[/bold orange1]")
                            else:
                                console.print("[bold orange1]assistant:[/bold orange1]")
                            console.print()  # 换行
                            
                            # 创建 Live 显示
                            panel = Panel(
                                Markdown(_safe_for_console(current_content)),
                                border_style="orange1",
                                padding=(1, 2),
                                expand=False
                            )
                            live_display = Live(panel, console=console, refresh_per_second=30)
                            live_display.start()
                        else:
                            # 更新流式内容
                            current_content = accumulated_content
                            if live_display:
                                panel = Panel(
                                    Markdown(_safe_for_console(current_content)),
                                    border_style="orange1",
                                    padding=(1, 2),
                                    expand=False
                                )
                                live_display.update(panel)
                        continue
                
                # 完整的消息（流式输出完成）
                elif chunk_type == "message":
                    message = chunk.get("message")
                    if message and isinstance(message, AIMessage):
                        # 关闭流式显示
                        if is_streaming and live_display:
                            live_display.stop()
                            live_display = None
                            console.print()  # 换行
                            is_streaming = False
                        
                        # 处理消息
                        tool_calls = getattr(message, "tool_calls", None) or []
                        content = str(getattr(message, "content", "")).strip() if hasattr(message, "content") else ""
                        
                        if tool_calls:
                            # 工具调用会在 tool_call 类型中处理，这里只保存消息
                            tool_calls_data = []
                            for tc in tool_calls:
                                if isinstance(tc, dict):
                                    tool_calls_data.append(tc)
                                else:
                                    tool_calls_data.append({
                                        "name": getattr(tc, "name", ""),
                                        "args": getattr(tc, "args", {}),
                                        "id": getattr(tc, "id", "")
                                    })
                            session_manager.add_message(task_id, "assistant", content or "", tool_calls=tool_calls_data)
                            pending_tool_calls = tool_calls_data.copy()
                        elif content:
                            # 检查是否是工具结果的JSON（不应该保存为独立的assistant消息）
                            is_tool_result_json = False
                            try:
                                parsed = json.loads(content)
                                # 如果解析成功且是字典，且包含工具返回的典型字段，则认为是工具结果
                                if isinstance(parsed, dict):
                                    # 检查是否包含工具返回的典型字段
                                    tool_result_fields = ["eventIntroduction", "searchWords", "timeRange", 
                                                         "search_matrix", "result_file_path", "save_path", 
                                                         "html_file_path", "file_url"]
                                    if any(field in parsed for field in tool_result_fields):
                                        is_tool_result_json = True
                            except (json.JSONDecodeError, TypeError):
                                pass
                            
                            # 如果是工具结果的JSON，跳过保存（工具结果已经通过tool_result类型保存了）
                            if not is_tool_result_json:
                                # 保存最终消息
                                session_manager.add_message(task_id, "assistant", content)
                                # 每次回复完成后打印 token 消耗（本轮 delta）
                                _print_reply_token_delta("本次回复")
                            current_content = ""
                            current_msg_id = None
                        continue
                
                # 工具调用
                elif chunk_type == "tool_call":
                    tool_name = chunk.get("tool_name", "unknown")
                    tool_args = chunk.get("args", {})
                    
                    if progress and not has_output:
                        progress.stop()
                        has_output = True
                    
                    token_tracker.set_step(f"tool_{tool_name}")
                    print_tool_call(tool_name, tool_args)
                    continue
                
                # 工具结果
                elif chunk_type == "tool_result":
                    tool_name = chunk.get("tool_name", "unknown")
                    tool_result = chunk.get("result", "")
                    
                    # 确保结果是字符串格式
                    if tool_result is None:
                        tool_result = ""
                    elif not isinstance(tool_result, str):
                        # 如果是对象，尝试获取 content 属性
                        if hasattr(tool_result, "content"):
                            tool_result = str(tool_result.content) if tool_result.content else ""
                        else:
                            tool_result = str(tool_result)
                    
                    print_tool_result(tool_name, tool_result)
                    
                    # 如果是 HTML 报告生成工具，尝试自动在默认浏览器中打开报告
                    if tool_name == "report_html":
                        try:
                            parsed = json.loads(tool_result)
                            # 优先使用 file_url，其次使用本地路径
                            file_url = parsed.get("file_url") or ""
                            html_file_path = parsed.get("html_file_path") or ""
                            if not file_url and html_file_path:
                                # 回退：将本地路径转换为 file:// URL
                                from pathlib import Path
                                file_url = Path(html_file_path).resolve().as_uri()
                            if file_url:
                                webbrowser.open(file_url)
                                console.print(f"[green]✅ 已在默认浏览器中打开报告: {file_url}[/green]")
                        except Exception:
                            # 若解析或打开失败，只在 CLI 中忽略，不影响主流程
                            warnings.warn("自动打开 HTML 报告失败，但报告已生成。")
                    
                    # 打印 token 使用情况（工具执行完成后）
                    step_usage = token_tracker.get_step_usage(token_tracker.current_step or "unknown")
                    if step_usage["total_tokens"] > 0:
                        print_token_usage(
                            token_tracker.current_step or "unknown",
                            step_usage["prompt_tokens"],
                            step_usage["completion_tokens"],
                            step_usage["total_tokens"]
                        )
                        session_manager.add_token_usage(
                            task_id,
                            token_tracker.current_step or "unknown",
                            step_usage["prompt_tokens"],
                            step_usage["completion_tokens"],
                            step_usage["total_tokens"]
                        )
                        token_tracker.step_token_usage[token_tracker.current_step or "unknown"] = {
                            "prompt_tokens": 0,
                            "completion_tokens": 0,
                            "total_tokens": 0
                        }
                    
                    # 保存工具结果到会话
                    run_id = chunk.get("run_id", "")
                    session_manager.add_message(
                        task_id,
                        "tool",
                        str(tool_result),
                        tool_name=tool_name,
                        tool_call_id=run_id
                    )
                    continue
                
                # 状态更新（兼容旧格式）
                elif chunk_type == "state_update":
                    state = chunk.get("state", {})
                    if isinstance(state, dict):
                        for node_name, state_update in state.items():
                            if isinstance(state_update, dict):
                                messages = state_update.get("messages", [])
                            else:
                                continue
                            
                            for msg in messages:
                                msg_id = getattr(msg, "id", None)
                                if not msg_id:
                                    content = str(getattr(msg, "content", ""))
                                    msg_type = type(msg).__name__
                                    if isinstance(msg, AIMessage) and hasattr(msg, "tool_calls") and msg.tool_calls:
                                        tool_names = [str(tc.get("name", "") if isinstance(tc, dict) else getattr(tc, "name", "")) for tc in msg.tool_calls]
                                        msg_id = f"{msg_type}:{hash((content, tuple(tool_names)))}"
                                    else:
                                        # 对于流式内容，使用消息类型和内容前缀作为临时 ID
                                        # 这样可以识别同一个消息的增量更新
                                        content_prefix = content[:50] if len(content) > 50 else content
                                        msg_id = f"{msg_type}:{hash(content_prefix)}"
                                
                                # 对于流式 AIMessage，如果正在流式输出且是同一个消息的更新，允许处理
                                is_streaming_update = False
                                if isinstance(msg, AIMessage) and is_streaming and current_msg_id:
                                    content_str = str(getattr(msg, "content", "")).strip()
                                    # 检查是否是同一个消息的增量更新（新内容包含旧内容，或长度更长）
                                    if content_str and current_content:
                                        if content_str.startswith(current_content) or len(content_str) > len(current_content):
                                            is_streaming_update = True
                                            msg_id = current_msg_id  # 使用当前消息 ID
                                
                                # 如果不是流式更新，且消息已处理过，则跳过
                                if not is_streaming_update and msg_id in processed_message_ids:
                                    continue
                                
                                # 如果不是流式更新，标记消息为已处理
                                if not is_streaming_update:
                                    processed_message_ids.add(msg_id)
                                
                                if isinstance(msg, AIMessage):
                                    tool_calls = getattr(msg, "tool_calls", None) or []
                                    content = str(getattr(msg, "content", "")).strip() if hasattr(msg, "content") else ""
                                    
                                    if tool_calls:
                                        # 如果有流式显示，先关闭它
                                        if is_streaming and live_display:
                                            live_display.stop()
                                            live_display = None
                                            console.print()  # 换行
                                            # 保存流式内容（如果有）
                                            if current_content:
                                                session_manager.add_message(task_id, "assistant", current_content)
                                            is_streaming = False
                                            current_content = ""
                                            current_msg_id = None
                                        
                                        if progress and not has_output:
                                            progress.stop()
                                            has_output = True
                                        
                                        for tc in tool_calls:
                                            if isinstance(tc, dict):
                                                tool_name = tc.get("name", "unknown")
                                                tool_args = tc.get("args", {})
                                            else:
                                                tool_name = getattr(tc, "name", "unknown")
                                                tool_args = getattr(tc, "args", {})
                                            
                                            token_tracker.set_step(f"tool_{tool_name}")
                                            print_tool_call(tool_name, tool_args)
                                        
                                        tool_calls_data = []
                                        for tc in tool_calls:
                                            if isinstance(tc, dict):
                                                tool_calls_data.append(tc)
                                            else:
                                                tool_calls_data.append({
                                                    "name": getattr(tc, "name", ""),
                                                    "args": getattr(tc, "args", {}),
                                                    "id": getattr(tc, "id", "")
                                                })
                                        session_manager.add_message(task_id, "assistant", content or "", tool_calls=tool_calls_data)
                                        pending_tool_calls = tool_calls_data.copy()
                                
                                elif isinstance(msg, AIMessage) and content:
                                    # 检查是否是工具结果的中间表示（JSON格式的内容，且没有tool_calls）
                                    # 这种情况不应该保存为独立的assistant消息
                                    content_str = str(msg.content).strip()
                                    is_tool_result_json = False
                                    if content_str:
                                        # 检查是否是JSON格式（工具返回的结果）
                                        try:
                                            parsed = json.loads(content_str)
                                            # 如果解析成功且是字典，且包含工具返回的典型字段，则认为是工具结果
                                            if isinstance(parsed, dict):
                                                # 检查是否包含工具返回的典型字段
                                                tool_result_fields = ["eventIntroduction", "searchWords", "timeRange", 
                                                                     "search_matrix", "result_file_path", "save_path", 
                                                                     "html_file_path", "file_url"]
                                                if any(field in parsed for field in tool_result_fields):
                                                    is_tool_result_json = True
                                        except (json.JSONDecodeError, TypeError):
                                            pass
                                    
                                    # 如果是工具结果的JSON，跳过保存（工具结果已经通过tool_result类型保存了）
                                    if is_tool_result_json:
                                        continue
                                    
                                    # 否则正常处理
                                    if content_str:
                                        # 如果有转圈动效，先停止
                                        if progress and not has_output:
                                            progress.stop()
                                            has_output = True
                                        
                                        # 检测是否是新的流式响应开始
                                        if not is_streaming:
                                            # 开始新的流式响应
                                            is_streaming = True
                                            current_content = content_str
                                            current_msg_id = msg_id
                                            
                                            # 显示 assistant 提示
                                            task_id_display = task_id[:8] if task_id and len(task_id) > 8 else task_id if task_id else ""
                                            if task_id_display:
                                                console.print(f"[bold orange1]({task_id_display}) assistant:[/bold orange1]")
                                            else:
                                                console.print("[bold orange1]assistant:[/bold orange1]")
                                            console.print()  # 换行
                                            
                                            # 创建 Live 显示
                                            panel = Panel(
                                                Markdown(_safe_for_console(current_content)),
                                                border_style="orange1",
                                                padding=(1, 2),
                                                expand=False
                                            )
                                            live_display = Live(panel, console=console, refresh_per_second=10)
                                            live_display.start()
                                        else:
                                            # 更新流式内容（检查是否是同一个消息的增量更新）
                                            if content_str.startswith(current_content) or len(content_str) > len(current_content):
                                                current_content = content_str
                                                if live_display:
                                                    panel = Panel(
                                                        Markdown(_safe_for_console(current_content)),
                                                        border_style="orange1",
                                                        padding=(1, 2),
                                                        expand=False
                                                    )
                                                    live_display.update(panel)
                                        
                                        # 检查是否有 tool_calls（如果有，说明消息完整）
                                        tool_calls_data = None
                                        if hasattr(msg, "tool_calls") and msg.tool_calls:
                                            tool_calls_data = []
                                            for tc in msg.tool_calls:
                                                if isinstance(tc, dict):
                                                    tool_calls_data.append(tc)
                                                else:
                                                    tool_calls_data.append({
                                                        "name": getattr(tc, "name", ""),
                                                        "args": getattr(tc, "args", {}),
                                                        "id": getattr(tc, "id", "")
                                                    })
                                        
                                        # 注意：这里不立即保存消息，等流式输出完成后再保存
                                
                                # 处理工具执行结果
                                elif isinstance(msg, ToolMessage):
                                    tool_name = getattr(msg, "name", "unknown")
                                    tool_call_id = getattr(msg, "tool_call_id", "")
                                    content = getattr(msg, "content", "")
                                    if content:
                                        print_tool_result(tool_name, content)
                                    session_manager.add_message(
                                        task_id, 
                                        "tool", 
                                        content, 
                                        tool_name=tool_name,
                                        tool_call_id=tool_call_id
                                    )
                                    pending_tool_calls = [
                                        tc for tc in pending_tool_calls 
                                        if tc.get("id") != tool_call_id
                                    ]
                            
                            step_usage = token_tracker.get_step_usage(token_tracker.current_step or "unknown")
                            if step_usage["total_tokens"] > 0:
                                print_token_usage(
                                    token_tracker.current_step or "unknown",
                                    step_usage["prompt_tokens"],
                                    step_usage["completion_tokens"],
                                    step_usage["total_tokens"]
                                )
                                session_manager.add_token_usage(
                                    task_id,
                                    token_tracker.current_step or "unknown",
                                    step_usage["prompt_tokens"],
                                    step_usage["completion_tokens"],
                                    step_usage["total_tokens"]
                                )
                                token_tracker.step_token_usage[token_tracker.current_step or "unknown"] = {
                                    "prompt_tokens": 0,
                                    "completion_tokens": 0,
                                    "total_tokens": 0
                                }
                    
                    if final_state is None:
                        final_state = {}
                    final_state.update(chunk)
        
        # 流式输出完成，关闭 Live 显示并保存消息
        if is_streaming and live_display:
            live_display.stop()
            live_display = None
            console.print()  # 换行
            
            # 保存最终消息
            tool_calls_data = None
            # 从 final_state 中查找对应的 AIMessage 来获取 tool_calls
            if final_state:
                for node_name, state_update in final_state.items():
                    if isinstance(state_update, dict):
                        messages = state_update.get("messages", [])
                        for msg in messages:
                            if isinstance(msg, AIMessage):
                                msg_content = str(msg.content).strip()
                                # 匹配当前内容（允许内容完全匹配或当前内容是消息内容的前缀）
                                if msg_content == current_content or (current_content and msg_content.startswith(current_content)):
                                    if hasattr(msg, "tool_calls") and msg.tool_calls:
                                        tool_calls_data = []
                                        for tc in msg.tool_calls:
                                            if isinstance(tc, dict):
                                                tool_calls_data.append(tc)
                                            else:
                                                tool_calls_data.append({
                                                    "name": getattr(tc, "name", ""),
                                                    "args": getattr(tc, "args", {}),
                                                    "id": getattr(tc, "id", "")
                                                })
                                    # 使用最终的消息内容（可能比 current_content 更完整）
                                    if msg_content != current_content:
                                        current_content = msg_content
                                    break
            
            if current_content:
                session_manager.add_message(task_id, "assistant", current_content, tool_calls=tool_calls_data)
                # 每次回复完成后打印 token 消耗（本轮 delta）
                _print_reply_token_delta("本次回复")
            
            is_streaming = False
            current_content = ""
            current_msg_id = None
        
        if progress and not has_output:
            progress.stop()
        
    except KeyboardInterrupt:
        if progress:
            progress.stop()
        # 关闭流式显示
        if is_streaming and live_display:
            live_display.stop()
            console.print()  # 换行
            # 保存已生成的内容
            if current_content:
                session_manager.add_message(task_id, "assistant", current_content)
        print_status("用户中断了执行", "warning")
        for tc in pending_tool_calls:
            tool_call_id = tc.get("id", "")
            tool_name = tc.get("name", "unknown")
            if tool_call_id:
                session_manager.add_message(
                    task_id,
                    "tool",
                    "",  # content为空
                    tool_name=tool_name,
                    tool_call_id=tool_call_id
                )
        raise
    except Exception as e:
        if progress:
            progress.stop()
        # 关闭流式显示
        if is_streaming and live_display:
            live_display.stop()
            console.print()  # 换行
            # 保存已生成的内容
            if current_content:
                session_manager.add_message(task_id, "assistant", current_content)
        print_status(f"执行出错: {str(e)}", "error")
        
        for tc in pending_tool_calls:
            tool_call_id = tc.get("id", "")
            tool_name = tc.get("name", "unknown")
            if tool_call_id:
                session_manager.add_message(
                    task_id,
                    "tool",
                    "",  # content为空
                    tool_name=tool_name,
                    tool_call_id=tool_call_id
                )
        traceback.print_exc()
        raise
    
    # 确保每次回复结束后至少打印一次 token
    _print_reply_token_delta("本次回复")

    # 打印总 token 使用（每次回复完成后都显示）
    total_usage = token_tracker.get_total_usage()
    # 若已在“本次回复”处打印过 delta，则此处避免重复打印同样的总计
    if total_usage["total_tokens"] > 0 and total_usage != last_printed_total_usage:
        print_separator()
        print_token_usage(
            "总计",
            total_usage["prompt_tokens"],
            total_usage["completion_tokens"],
            total_usage["total_tokens"]
        )
    
    if not show_spinner:
        print_separator()
        print_status("处理完成", "success")
    
    return final_state or {}


def run_session_loop(
    task_id: Optional[str] = None,
    *,
    force_event_workflow: bool = False,
    preset_initial_query: Optional[str] = None,
) -> None:
    """
    运行会话循环
    
    Args:
        task_id: 任务 ID，如果为 None 则创建新会话
        force_event_workflow: 是否强制使用事件分析工作流（由 /event 触发）
        preset_initial_query: 预置首条查询（由主命令直接传入）
    """
    session_manager = get_session_manager()
    previous_messages = []
    event_workflow_debug = os.environ.get("SONA_EVENT_WORKFLOW_DEBUG", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "y",
        "debug",
    )
    # #region debug_log_H4_event_workflow_switch
    _append_ndjson_log(
        run_id="session_loop_entry",
        hypothesis_id="H4_event_workflow_switch",
        location="cli/interactive.py:run_session_loop_start",
        message="会话循环启动时读取事件工作流开关",
        data={"task_id": task_id or "", "event_workflow_debug": event_workflow_debug},
    )
    # #endregion debug_log_H4_event_workflow_switch
    
    # 如果没有提供 task_id，创建新会话
    if task_id is None:
        # 先创建会话，获取任务ID（使用临时查询）
        task_id = session_manager.create_session("新会话")
        ensure_task_dirs(task_id)
        print_status(f"会话已创建，任务 ID: {task_id}", "info")
        
        # 直接显示 user 输入提示（带任务ID格式）
        task_id_display = task_id[:8] if task_id and len(task_id) > 8 else task_id if task_id else ""
        if preset_initial_query is not None:
            initial_query = preset_initial_query.strip()
            console.print(f"[dim]使用预置初始查询: {initial_query}[/dim]")
        else:
            # 在 user 提示前添加绿色横线作为对话区隔
            console.print("[green]────────────────────────────────────────────────────────────[/green]")
            initial_query = _ask_user(f"\x1b[36m({task_id_display}) user>\x1b[0m ")
        if not initial_query:
            console.print("[yellow]未输入查询，退出。[/yellow]")
            return

        # 新会话首条输入也支持运行时参数设置：SONA_XXX=YYY
        env_assign_match = re.match(r"^\s*(SONA_[A-Z0-9_]+)\s*=\s*(.+?)\s*$", initial_query)
        if env_assign_match:
            env_key = env_assign_match.group(1).strip()
            env_val = env_assign_match.group(2).strip()
            os.environ[env_key] = env_val
            console.print(f"[green]已设置运行时参数: {env_key}={env_val}[/green]")
            # #region debug_log_H21_runtime_env_set_in_initial_assignment
            _append_ndjson_log(
                run_id="runtime_env_update",
                hypothesis_id="H21_runtime_env_set_in_initial_assignment",
                location="cli/interactive.py:initial_runtime_env_assignment",
                message="新会话首条输入通过 KEY=VALUE 设置运行时环境变量",
                data={"key": env_key, "value": env_val},
            )
            # #endregion debug_log_H21_runtime_env_set_in_initial_assignment
            console.print("[yellow]参数已生效，请继续输入分析 query。[/yellow]")
            console.print("[green]────────────────────────────────────────────────────────────[/green]")
            initial_query = _ask_user(f"\x1b[36m({task_id_display}) user>\x1b[0m ")
            if not initial_query:
                console.print("[yellow]未输入查询，退出。[/yellow]")
                return

        # 新会话首条输入也支持 /set KEY VALUE
        if initial_query.strip().startswith("/set"):
            parts = initial_query.strip().split(maxsplit=2)
            if len(parts) >= 3:
                env_key = parts[1].strip()
                env_val = parts[2].strip()
                os.environ[env_key] = env_val
                console.print(f"[green]已设置运行时参数: {env_key}={env_val}[/green]")
                # #region debug_log_H22_runtime_env_set_in_initial_set_command
                _append_ndjson_log(
                    run_id="runtime_env_update",
                    hypothesis_id="H22_runtime_env_set_in_initial_set_command",
                    location="cli/interactive.py:initial_runtime_env_set_command",
                    message="新会话首条输入通过 /set 设置运行时环境变量",
                    data={"key": env_key, "value": env_val},
                )
                # #endregion debug_log_H22_runtime_env_set_in_initial_set_command
                console.print("[yellow]参数已生效，请继续输入分析 query。[/yellow]")
                console.print("[green]────────────────────────────────────────────────────────────[/green]")
                initial_query = _ask_user(f"\x1b[36m({task_id_display}) user>\x1b[0m ")
                if not initial_query:
                    console.print("[yellow]未输入查询，退出。[/yellow]")
                    return

        # 支持 /event 命令直接强制进入事件工作流
        initial_query_stripped = initial_query.strip()
        event_query = initial_query
        if initial_query_stripped.startswith("/event"):
            force_event_workflow = True
            parts = initial_query_stripped.split(maxsplit=1)
            event_query = parts[1] if len(parts) > 1 else ""
            if not event_query:
                event_query = _ask_user("请输入要执行事件分析的 query> ")
            initial_query_stripped = event_query.strip()

        # 意图路由决策：使用路由器分析用户 Query
        
        # 使用意图路由器进行智能路由
        router = get_router()
        route_decision, route_data = router.route(initial_query_stripped, task_id)
        
        intent_result = route_data.get("intent_result")
        data_result = route_data.get("data_result")
        
        console.print()
        console.print(f"[cyan]🎯 路由决策: {route_decision}[/cyan]")
        _print_execution_plan(route_decision, route_data)
        
        use_event_workflow = force_event_workflow or route_decision in (
            "event_analysis_workflow",
            "event_analysis_with_existing_data",
        )
        use_hot_workflow = (not force_event_workflow) and route_decision == "hottopics_workflow"
        use_event_brief_workflow = (not force_event_workflow) and route_decision == "event_brief_workflow"
        route_policy = route_data.get("route_policy", {}) or {}
        prefer_confirm = bool(route_policy.get("prefer_confirm", True))
        
        # 如果检测到现有数据，询问用户是否使用
        use_existing = False
        if use_event_workflow and data_result and data_result.has_data:
            console.print()
            console.print(f"[yellow]📊 检测到相关数据文件:[/yellow]")
            for i, (path, score) in enumerate(zip(data_result.data_paths[:3], data_result.relevance_scores[:3])):
                console.print(f"   {i+1}. {path} (相关度: {score:.2f})")
            console.print()
            default_use_existing = str(route_policy.get("preference", "")).strip() != "覆盖优先"
            if prefer_confirm:
                use_existing = Confirm.ask(
                    "是否使用已有数据进行分析？（选 n 会重新采集数据）",
                    default=default_use_existing,
                )
            else:
                use_existing = default_use_existing
                console.print(
                    f"[dim]根据路由策略自动选择: {'复用已有数据' if use_existing else '重新采集数据'}[/dim]"
                )
            if not use_existing:
                console.print("[cyan]好的，将重新采集数据...[/cyan]")
            else:
                console.print("[cyan]好的，将使用现有数据进行分析[/cyan]")
        
        # #region debug_log_H5_initial_route_decision
        _append_ndjson_log(
            run_id="initial_query_route",
            hypothesis_id="H5_initial_route_decision",
            location="cli/interactive.py:initial_query_route",
            message="意图路由器路由判定",
            data={
                "initial_query_prefix": initial_query_stripped[:32],
                "route_decision": route_decision,
                "intent": intent_result.intent if intent_result else "unknown",
                "confidence": intent_result.confidence if intent_result else 0,
                "has_existing_data": data_result.has_data if data_result else False,
            },
        )
        # #endregion debug_log_H5_initial_route_decision

        # 支持 /event_debug 强制进入事件工作流（保留调试入口）
        if initial_query_stripped.startswith("/event_debug"):
            parts = initial_query_stripped.split(maxsplit=1)
            event_query = parts[1] if len(parts) > 1 else ""
            if not event_query:
                event_query = _ask_user("请输入要执行事件分析的 query> ")
            use_event_workflow = True
            use_hot_workflow = False

        # 更新会话的初始查询
        session_data = session_manager.load_session(task_id)
        if session_data:
            session_data["initial_query"] = event_query
            # 关键修复：必须立即保存，否则会话列表/恢复时仍显示 create_session("新会话") 的占位描述
            session_manager.save_session(task_id, session_data, final_query=event_query)
        
        # 立即执行初始查询（带转圈动效）
        try:
            if use_event_workflow:
                # #region debug_log_H6_initial_event_workflow_called
                _append_ndjson_log(
                    run_id="initial_query_route",
                    hypothesis_id="H6_initial_event_workflow_called",
                    location="cli/interactive.py:initial_call_event_workflow",
                    message="初始输入进入事件工作流",
                    data={"query_len": len(event_query)},
                )
                # #endregion debug_log_H6_initial_event_workflow_called
                
                # 确定是否使用现有数据（跳过数据采集）
                existing_data_path = None
                skip_data_collect = False
                if data_result and data_result.has_data:
                    if use_existing and data_result.data_paths:
                        # 使用第一相关的数据文件
                        existing_data_path = data_result.data_paths[0]
                        skip_data_collect = True
                
                # 由 Agent 执行 full_report 模式（内部复用 event workflow）
                run_session_query(
                    event_query,
                    task_id,
                    previous_messages,
                    show_spinner=True,
                    task_mode="full_report",
                    workflow_options=_full_report_workflow_options(
                        route_policy,
                        existing_data_path=existing_data_path,
                        skip_data_collect=skip_data_collect,
                        force_fresh_start=False,
                    ),
                )
            elif use_event_brief_workflow:
                run_session_query(
                    event_query,
                    task_id,
                    previous_messages,
                    show_spinner=True,
                    task_mode="brief",
                )
            elif use_hot_workflow:
                run_hot_command()
                session_manager.add_message(task_id, "assistant", "已执行热点发现流程（/hot）。")
            else:
                run_session_query(event_query, task_id, previous_messages, show_spinner=True, task_mode="qa")
            # 执行完后更新 previous_messages
            session_data = session_manager.load_session(task_id)
            if session_data:
                previous_messages = messages_from_session_data(session_data)
        except Exception as e:
            console.print()  # 换行
            console.print(f"\n[red]执行初始查询失败: {str(e)}[/red]\n")
            traceback.print_exc()
    else:
        # 加载会话
        session_data = session_manager.load_session(task_id)
        if not session_data:
            console.print(f"[red]无法加载会话: {task_id}[/red]")
            return
        
        ensure_task_dirs(task_id)
        print_status(f"已恢复会话: {task_id}", "info")
        print_status(f"描述: {session_data.get('description', 'N/A')}", "info")
        
        # 恢复消息
        previous_messages = messages_from_session_data(session_data)
    
    # 会话循环（等待后续查询）
    while True:
        try:
            # 在agent运行时也显示user:提示，便于输入/exit等
            task_id_display = task_id[:8] if task_id and len(task_id) > 8 else task_id if task_id else ""
            # 在 user 提示前添加绿色横线作为对话区隔
            console.print("[green]────────────────────────────────────────────────────────────[/green]")
            user_input = _ask_user(f"\x1b[36m({task_id_display}) user>\x1b[0m ")
            
            if not user_input:
                continue

            # 处理运行时环境变量设置：支持 SONA_XXX=YYY（无需重启进程）
            env_assign_match = re.match(r"^\s*(SONA_[A-Z0-9_]+)\s*=\s*(.+?)\s*$", user_input)
            if env_assign_match:
                env_key = env_assign_match.group(1).strip()
                env_val = env_assign_match.group(2).strip()
                os.environ[env_key] = env_val
                console.print(f"[green]已设置运行时参数: {env_key}={env_val}[/green]")
                # #region debug_log_H19_runtime_env_set_by_assignment
                _append_ndjson_log(
                    run_id="runtime_env_update",
                    hypothesis_id="H19_runtime_env_set_by_assignment",
                    location="cli/interactive.py:runtime_env_assignment",
                    message="通过 KEY=VALUE 设置运行时环境变量",
                    data={"key": env_key, "value": env_val},
                )
                # #endregion debug_log_H19_runtime_env_set_by_assignment
                continue
            
            # 处理系统级命令（以 / 开头）
            if user_input.strip().startswith("/"):
                # 处理 /set 命令：/set SONA_DATA_COLLECT_MAX_WORKERS 5
                if user_input.strip().startswith("/set"):
                    parts = user_input.strip().split(maxsplit=2)
                    if len(parts) < 3:
                        console.print("[yellow]用法: /set KEY VALUE（仅建议用于 SONA_* 运行时参数）[/yellow]")
                        continue
                    env_key = parts[1].strip()
                    env_val = parts[2].strip()
                    os.environ[env_key] = env_val
                    console.print(f"[green]已设置运行时参数: {env_key}={env_val}[/green]")
                    # #region debug_log_H20_runtime_env_set_by_set_command
                    _append_ndjson_log(
                        run_id="runtime_env_update",
                        hypothesis_id="H20_runtime_env_set_by_set_command",
                        location="cli/interactive.py:runtime_env_set_command",
                        message="通过 /set 命令设置运行时环境变量",
                        data={"key": env_key, "value": env_val},
                    )
                    # #endregion debug_log_H20_runtime_env_set_by_set_command
                    continue

                # 处理 /exit 命令（退出到主页面）
                if user_input.strip() == "/exit":
                    # 保存会话
                    session_data = session_manager.load_session(task_id)
                    if session_data:
                        session_manager.save_session(task_id, session_data)
                    # 显示任务目录信息（只在退出时显示）
                    print_status(f"任务目录: sandbox/{task_id}/", "info")
                    console.print(f"\n[cyan]会话已保存，已退出到主页面[/cyan]\n")
                    return  # 返回到主页面

                # 处理 /event 与 /event_debug 命令：显式触发事件分析工作流
                if user_input.strip().startswith("/event"):
                    parts = user_input.strip().split(maxsplit=1)
                    event_query = parts[1] if len(parts) > 1 else ""
                    if not event_query:
                        event_query = _ask_user("请输入要执行事件分析的 query> ")
                    try:
                        # #region debug_log_H7_command_event_workflow_called
                        _append_ndjson_log(
                            run_id="loop_query_route",
                            hypothesis_id="H7_command_event_workflow_called",
                            location="cli/interactive.py:loop_call_event_workflow_command",
                            message="循环中通过 /event 命令进入事件工作流",
                            data={"query_len": len(event_query)},
                        )
                        # #endregion debug_log_H7_command_event_workflow_called
                        run_session_query(
                            event_query,
                            task_id,
                            previous_messages,
                            show_spinner=True,
                            task_mode="full_report",
                            workflow_options=_full_report_workflow_options(
                                None,
                                force_fresh_start=False,
                            ),
                        )
                        session_data = session_manager.load_session(task_id)
                        if session_data:
                            previous_messages = messages_from_session_data(session_data)
                    except Exception as e:
                        console.print(f"[red]事件分析工作流执行失败: {str(e)}[/red]")
                        traceback.print_exc()
                    continue

                # 处理 /hot 命令：态势感知与热点发现
                if user_input.strip().startswith("/hot"):
                    parts = user_input.strip().split(maxsplit=1)
                    custom_config_path = parts[1] if len(parts) > 1 else None
                    try:
                        run_hot_command(custom_config_path)
                        session_manager.add_message(task_id, "assistant", "已执行热点态势感知流程。")
                    except Exception as e:
                        console.print(f"[red]/hot 执行失败: {str(e)}[/red]")
                        traceback.print_exc()
                    continue

                # 处理 /wiki-approve 命令（候选审批回流）
                if user_input.strip().startswith("/wiki-approve"):
                    parts = user_input.strip().split(maxsplit=1)
                    selector = parts[1].strip() if len(parts) > 1 else None
                    try:
                        run_wiki_approve_command(selector)
                        session_manager.add_message(task_id, "assistant", "已执行 /wiki-approve 候选回流。")
                    except Exception as e:
                        console.print(f"[red]/wiki-approve 执行失败: {str(e)}[/red]")
                        traceback.print_exc()
                    continue

                # 处理 /wiki 命令（知识问答，与会话并行、不写长上下文）
                if user_input.strip().startswith("/wiki"):
                    parts = user_input.strip().split(maxsplit=1)
                    wiki_query = parts[1].strip() if len(parts) > 1 else None
                    try:
                        run_wiki_command(wiki_query)
                        session_manager.add_message(task_id, "assistant", "已执行 /wiki 知识库问答。")
                    except Exception as e:
                        console.print(f"[red]/wiki 执行失败: {str(e)}[/red]")
                        traceback.print_exc()
                    continue

                # 处理 /compress 命令（手动压缩上下文）
                if user_input.strip() == "/compress":
                    session_data = session_manager.load_session(task_id)
                    if not session_data:
                        console.print("[red]无法加载会话数据，压缩失败[/red]")
                        continue

                    # 将当前会话消息转换为 BaseMessage 列表
                    current_messages = messages_from_session_data(session_data)

                    # 读取当前累计 completion_tokens（用于与 auto-compress 一致的阈值逻辑）
                    current_completion_tokens = 0
                    if isinstance(session_data, dict) and "token_usage" in session_data:
                        current_completion_tokens = session_data["token_usage"].get("completion_tokens", 0) or 0

                    # 执行压缩
                    compressed_messages, was_compressed, compression_summary = compress_messages(
                        current_messages,
                        max_completion_tokens=20000,
                        current_completion_tokens=current_completion_tokens,
                    )

                    if not was_compressed:
                        console.print("[yellow]当前上下文未达到压缩条件（或无可压缩内容）。[/yellow]")
                        continue

                    # 转换为 session 可存储的 dict 列表（与 run_session_query 的 compression 分支一致）
                    compressed_messages_dict = []
                    for msg in compressed_messages:
                        if isinstance(msg, SystemMessage):
                            continue
                        elif isinstance(msg, HumanMessage):
                            compressed_messages_dict.append(
                                {
                                    "role": "user",
                                    "content": msg.content,
                                    "timestamp": datetime.now().isoformat(),
                                }
                            )
                        elif isinstance(msg, AIMessage):
                            tool_calls_data = []
                            if hasattr(msg, "tool_calls") and msg.tool_calls:
                                for tc in msg.tool_calls:
                                    if isinstance(tc, dict):
                                        tool_calls_data.append(tc)
                                    else:
                                        tool_calls_data.append(
                                            {
                                                "name": getattr(tc, "name", ""),
                                                "args": getattr(tc, "args", {}),
                                                "id": getattr(tc, "id", ""),
                                            }
                                        )
                            compressed_messages_dict.append(
                                {
                                    "role": "assistant",
                                    "content": msg.content or "",
                                    "tool_calls": tool_calls_data if tool_calls_data else None,
                                    "timestamp": datetime.now().isoformat(),
                                }
                            )
                        elif isinstance(msg, ToolMessage):
                            compressed_messages_dict.append(
                                {
                                    "role": "tool",
                                    "content": msg.content,
                                    "tool_name": getattr(msg, "name", "unknown"),
                                    "tool_call_id": msg.tool_call_id,
                                    "timestamp": datetime.now().isoformat(),
                                }
                            )

                    # 写回 session，并重置 token_usage（因为压缩后的上下文变短）
                    session_manager.replace_messages(task_id, compressed_messages_dict, reset_token_usage=True)

                    # 同步更新内存中的 previous_messages，保证后续提问基于压缩后的上下文
                    previous_messages = messages_from_session_data(session_manager.load_session(task_id) or {})

                    console.print("[green]✅ 上下文已压缩并写回会话记忆（token 统计已重置）。[/green]")
                    if compression_summary:
                        summary_display = compression_summary[:400] + "..." if len(compression_summary) > 400 else compression_summary
                        console.print(f"[dim]{summary_display}[/dim]")
                    continue
                
                # 其他系统命令在main.py中处理，这里仅支持会话内命令
                console.print(
                    "[yellow]会话内可用命令: "
                    "/exit, /set, /event, /hot, /wiki, /wiki-approve, /compress[/yellow]"
                )
                continue

            # 若开启事件分析 debug 开关，则把普通输入也路由到事件分析工作流
            if event_workflow_debug:
                try:
                    # #region debug_log_H8_auto_event_workflow_called
                    _append_ndjson_log(
                        run_id="loop_query_route",
                        hypothesis_id="H8_auto_event_workflow_called",
                        location="cli/interactive.py:loop_call_event_workflow_auto",
                        message="循环中因环境开关进入事件工作流",
                        data={"query_len": len(user_input)},
                    )
                    # #endregion debug_log_H8_auto_event_workflow_called
                    run_session_query(
                        user_input,
                        task_id,
                        previous_messages,
                        show_spinner=True,
                        task_mode="full_report",
                        workflow_options=_full_report_workflow_options(None),
                    )
                    session_data = session_manager.load_session(task_id)
                    if session_data:
                        previous_messages = messages_from_session_data(session_data)
                except Exception as e:
                    console.print(f"[red]事件分析工作流执行失败: {str(e)}[/red]")
                    traceback.print_exc()
                continue

            # 每轮输入都进行智能路由（OpenClaw 风格）：优先高效路径
            route_decision, route_data = route_query(user_input, task_id)
            intent_result = route_data.get("intent_result")
            data_result = route_data.get("data_result")
            route_policy = route_data.get("route_policy", {}) or {}
            confidence = float(getattr(intent_result, "confidence", 0.0) or 0.0)
            _print_execution_plan(route_decision, route_data)

            # 事件/热点类意图即使置信度中等也优先走模式化流程，避免掉回 qa 触发 ReAct 试探循环
            should_apply_route = confidence >= 0.65 or route_decision in (
                "event_analysis_workflow",
                "event_analysis_with_existing_data",
                "event_brief_workflow",
                "hottopics_workflow",
            )
            if should_apply_route and route_decision in ("event_analysis_workflow", "event_analysis_with_existing_data"):
                use_existing = False
                if data_result and data_result.has_data:
                    prefer_confirm = bool(route_policy.get("prefer_confirm", True))
                    default_use_existing = str(route_policy.get("preference", "")).strip() != "覆盖优先"
                    if prefer_confirm:
                        console.print()
                        console.print(f"[yellow]📊 检测到相关数据文件:[/yellow]")
                        for i, (path, score) in enumerate(zip(data_result.data_paths[:3], data_result.relevance_scores[:3])):
                            console.print(f"   {i+1}. {path} (相关度: {score:.2f})")
                        console.print()
                        use_existing = Confirm.ask(
                            "是否使用已有数据进行分析？（选 n 会重新采集数据）",
                            default=default_use_existing,
                        )
                    else:
                        use_existing = default_use_existing
                        console.print(
                            f"[dim]根据路由策略自动选择: {'复用已有数据' if use_existing else '重新采集数据'}[/dim]"
                        )

                existing_data_path = None
                skip_data_collect = False
                if data_result and data_result.has_data and use_existing and data_result.data_paths:
                    existing_data_path = data_result.data_paths[0]
                    skip_data_collect = True

                run_session_query(
                    user_input,
                    task_id,
                    previous_messages,
                    show_spinner=True,
                    task_mode="full_report",
                    workflow_options=_full_report_workflow_options(
                        route_policy,
                        existing_data_path=existing_data_path,
                        skip_data_collect=skip_data_collect,
                        force_fresh_start=False,
                    ),
                )
            elif should_apply_route and route_decision == "event_brief_workflow":
                run_session_query(
                    user_input,
                    task_id,
                    previous_messages,
                    show_spinner=True,
                    task_mode="brief",
                )
            elif should_apply_route and route_decision == "hottopics_workflow":
                run_hot_command()
                session_manager.add_message(task_id, "assistant", "已执行热点态势感知流程。")
            else:
                run_session_query(user_input, task_id, previous_messages, show_spinner=True, task_mode="qa")
            
            # 更新 previous_messages（查询完成后重新加载会话以获取最新消息）
            session_data = session_manager.load_session(task_id)
            if session_data:
                previous_messages = messages_from_session_data(session_data)
            
        except KeyboardInterrupt:
            # 保存会话
            session_data = session_manager.load_session(task_id)
            if session_data:
                session_manager.save_session(task_id, session_data)
            console.print(f"\n\n[cyan]会话已保存，感谢使用 [bold magenta]Sona[/bold magenta]！[/cyan]\n")
            sys.exit(0)
        except EOFError:
            session_data = session_manager.load_session(task_id)
            if session_data:
                session_manager.save_session(task_id, session_data)
            console.print(f"\n\n[cyan]会话已保存，感谢使用 [bold magenta]Sona[/bold magenta]！[/cyan]\n")
            sys.exit(0)
        except Exception as e:
            console.print(f"\n[red]发生错误: {str(e)}[/red]\n")
            traceback.print_exc()
