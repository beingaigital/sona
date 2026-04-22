"""ReAct Agent：将实例化的LLM封装为Agent"""

from __future__ import annotations

import json
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage, BaseMessage, ToolMessage
from typing import Optional, List, Dict, Any
import asyncio
import queue
import threading
from datetime import datetime

from langchain.agents import create_agent
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.runnables.history import RunnableWithMessageHistory
from langchain_core.chat_history import BaseChatMessageHistory
from model.factory import get_react_model
from tools import (
    extract_search_terms,
    data_collect,
    data_num,
    analysis_timeline,
    analysis_sentiment,
    keyword_stats,
    region_stats,
    author_stats,
    volume_stats,
    user_portrait,
    report_html,
    graph_rag_query,
    weibo_aisearch,
    search_reference_insights,
    load_sentiment_knowledge,
)
from utils.message_utils import messages_from_session_data
from utils.prompt_loader import get_system_prompt_with_tools
from utils.task_context import set_task_id, get_task_id
from utils.token_tracker import TokenUsageTracker
from utils.message_utils import compress_messages
from utils.session_manager import get_session_manager
from utils.harness_memory import normalize_session_pref_patch, set_session_prefs
import warnings

# 当前注册的工具列表
AGENT_TOOLS = [
    extract_search_terms,
    data_collect,
    data_num,
    analysis_timeline,
    analysis_sentiment,
    keyword_stats,
    region_stats,
    author_stats,
    volume_stats,
    user_portrait,
    report_html,
    graph_rag_query,
    weibo_aisearch,
    search_reference_insights,
    load_sentiment_knowledge,
]

# QA 模式使用轻量工具集，避免在开放问答中触发重型采集/报告链路反复试探
QA_TOOLS = [
    extract_search_terms,
    graph_rag_query,
    weibo_aisearch,
    search_reference_insights,
    load_sentiment_knowledge,
]

# Callback 处理器：在工具执行时设置任务ID上下文
class TaskContextCallback(BaseCallbackHandler):
    """Callback 处理器：在工具执行时设置任务ID上下文"""
    
    def __init__(self, task_id: str | None = None):
        super().__init__()
        self.task_id = task_id
    
    def on_tool_start(
        self,
        serialized: dict[str, Any],
        input_str: str,
        *,
        run_id: str,
        parent_run_id: str | None = None,
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> Any:
        """工具开始执行时设置任务ID上下文"""
        if self.task_id:
            set_task_id(self.task_id)
        return super().on_tool_start(
            serialized, input_str, run_id=run_id, parent_run_id=parent_run_id, tags=tags, metadata=metadata, **kwargs
        )

# 消息记忆实现
class SessionChatMessageHistory(BaseChatMessageHistory):
    """基于 SessionManager 的聊天消息历史实现"""
    
    def __init__(self, task_id: str):
        super().__init__()
        self.task_id = task_id
        self.session_manager = get_session_manager()
    
    @property
    def messages(self) -> List[BaseMessage]:
        """从 SessionManager 加载消息"""
        session_data = self.session_manager.load_session(self.task_id)
        if not session_data:
            return []
        
        return messages_from_session_data(session_data)
    
    def add_message(self, message: BaseMessage) -> None:
        """添加消息到 SessionManager"""
        session_manager = get_session_manager()
        
        if isinstance(message, HumanMessage):
            session_manager.add_message(self.task_id, "user", message.content)
        elif isinstance(message, AIMessage):
            tool_calls = getattr(message, "tool_calls", None) or []
            tool_calls_data = []
            if tool_calls:
                for tc in tool_calls:
                    if isinstance(tc, dict):
                        tool_calls_data.append(tc)
                    else:
                        tool_calls_data.append({
                            "name": getattr(tc, "name", ""),
                            "args": getattr(tc, "args", {}),
                            "id": getattr(tc, "id", "")
                        })
            session_manager.add_message(
                self.task_id, 
                "assistant", 
                message.content or "", 
                tool_calls=tool_calls_data if tool_calls_data else None
            )
        elif isinstance(message, ToolMessage):
            session_manager.add_message(
                self.task_id,
                "tool",
                message.content,
                tool_name=getattr(message, "name", "unknown"),
                tool_call_id=message.tool_call_id
            )
    
    def clear(self) -> None:
        """清空消息历史"""
        session_data = self.session_manager.load_session(self.task_id)
        if session_data:
            session_data["messages"] = []
            self.session_manager.save_session(self.task_id, session_data)

# 获取会话历史
def _get_session_history(task_id: str) -> BaseChatMessageHistory:
    """获取会话历史"""
    return SessionChatMessageHistory(task_id)

# 实例化 system prompt、LLM、ReAct Agent
_system_prompt = get_system_prompt_with_tools(AGENT_TOOLS)
_llm = get_react_model()
react_agent = create_agent(
    model=_llm,
    tools=AGENT_TOOLS,
    system_prompt=_system_prompt,
)

_qa_system_prompt = (
    get_system_prompt_with_tools(QA_TOOLS)
    + "\n\n【QA 模式约束】\n"
      "1) 仅在确有必要时调用工具，优先直接回答。\n"
      "2) 禁止重复调用同一工具且输入语义高度相同（最多 1 次）。\n"
      "3) 若外部检索连续失败，应明确说明证据不足并停止继续试探。"
)
qa_agent = create_agent(
    model=_llm,
    tools=QA_TOOLS,
    system_prompt=_qa_system_prompt,
)


def _build_brief_summary_from_extract(raw_result: str) -> str:
    try:
        obj = json.loads(raw_result)
    except Exception:
        obj = {}
    intro = str(obj.get("eventIntroduction", "") or "").strip() if isinstance(obj, dict) else ""
    words = obj.get("searchWords") if isinstance(obj, dict) else []
    time_range = str(obj.get("timeRange", "") or "").strip() if isinstance(obj, dict) else ""
    keys = []
    if isinstance(words, list):
        keys = [str(x).strip() for x in words if str(x).strip()]

    lines = [
        "### 事件经过概述（Agent Brief 模式）",
        f"- 事件简介：{intro or '证据不足'}",
        f"- 关键词：{'、'.join(keys[:12]) if keys else '证据不足'}",
        f"- 建议时间范围：{time_range or '证据不足'}",
        "",
        "如需完整报告，请继续输入“做完整舆情分析并生成报告”。",
    ]
    return "\n".join(lines)


def _stream_mode_flow(
    user_input: str,
    task_id: Optional[str],
    task_mode: str,
    workflow_options: Optional[Dict[str, Any]] = None,
):
    """在 Agent 层执行模式化流程（brief/full_report）。"""
    mode = str(task_mode or "qa").strip().lower()
    if mode == "brief":
        from cli.event_analysis_workflow import run_brief_mode

        payload = {"query": user_input}
        yield {"type": "tool_call", "tool_name": "brief_mode_node", "args": payload, "run_id": f"mode_brief_{task_id or 'na'}"}
        brief_obj = run_brief_mode(user_input)
        raw_text = json.dumps(brief_obj, ensure_ascii=False)
        yield {"type": "tool_result", "tool_name": "brief_mode_node", "result": raw_text, "run_id": f"mode_brief_{task_id or 'na'}"}
        final_text = _build_brief_summary_from_extract(raw_text)
        yield {"type": "message", "message": AIMessage(content=final_text), "message_id": f"mode_brief_msg_{task_id or 'na'}"}
        return

    if mode == "full_report":
        from cli.event_analysis_workflow import run_full_report_mode

        session_manager = get_session_manager()
        opts: Dict[str, Any] = dict(workflow_options or {})
        existing_data_path = opts.get("existing_data_path")
        skip_data_collect = bool(opts.get("skip_data_collect", False))
        force_fresh_start = opts.get("force_fresh_start")
        yield {"type": "tool_call", "tool_name": "full_report_mode_node", "args": {"query": user_input}, "run_id": f"mode_full_{task_id or 'na'}"}
        report_length = str(opts.get("report_length") or "").strip() or None
        file_url_or_path = run_full_report_mode(
            user_query=user_input,
            task_id=task_id or "",
            session_manager=session_manager,
            debug=True,
            existing_data_path=existing_data_path,
            skip_data_collect=skip_data_collect,
            force_fresh_start=force_fresh_start,
            report_length=report_length,
        )
        yield {
            "type": "tool_result",
            "tool_name": "full_report_mode_node",
            "result": str(file_url_or_path or ""),
            "run_id": f"mode_full_{task_id or 'na'}",
        }
        final_text = (
            "完整舆情报告流程已完成。\n"
            f"- 报告地址：{str(file_url_or_path or '未返回')}\n"
            "- 已复用事件分析工作流节点（采集/分析/报告生成）。"
        )
        yield {"type": "message", "message": AIMessage(content=final_text), "message_id": f"mode_full_msg_{task_id or 'na'}"}
        return

# 创建带消息历史的 Agent
# 注意：此函数目前未使用，保留作为预留功能，用于未来可能需要直接使用带历史管理的 Agent 的场景
def _create_agent_with_history():
    """
    创建带消息历史管理的 Agent
    
    注意：此函数目前未被调用，保留作为预留功能。
    当前代码直接使用 react_agent 和 SessionChatMessageHistory 来实现消息历史管理。
    """
    return RunnableWithMessageHistory(
        react_agent,
        _get_session_history,
        input_messages_key="messages",
        history_messages_key="messages",
    )

# 流式运行
def stream(
    user_input: str,
    task_id: str | None = None,
    previous_messages: Optional[List] = None,
    token_tracker: Optional[TokenUsageTracker] = None,
    max_context_tokens: int = 20000,
    task_mode: str = "qa",
    workflow_options: Optional[Dict[str, Any]] = None,
):
    """
    流式运行 ReAct Agent（token 级别），支持自动消息压缩
    
    Args:
        user_input: 用户输入
        task_id: 任务 ID
        previous_messages: 之前的消息列表（短期记忆）
        token_tracker: Token 追踪器
        max_context_tokens: 最大上下文 token 数，超过此值将压缩旧消息
        
    """
    # 设置任务ID（使用辅助函数，同时设置 ContextVar 和全局存储）
    set_task_id(task_id)

    # Harness 会话记忆：把 workflow_options 中的偏好写入 session（可进化、自举）。
    # 约定：UI/调用方可通过 workflow_options 传入 wiki_style/wiki_topk/wiki_weibo_aux 等。
    if task_id and workflow_options:
        try:
            session_manager = get_session_manager()
            session_data = session_manager.load_session(task_id) or {}
            patch = normalize_session_pref_patch(dict(workflow_options))
            if patch:
                session_data = set_session_prefs(session_data, patch=patch)
                session_manager.save_session(task_id, session_data)
        except Exception:
            pass

    # 模式化执行：由 Agent 直接编排（复用工作流骨架）
    mode = str(task_mode or "qa").strip().lower()
    if mode in {"brief", "full_report"}:
        for item in _stream_mode_flow(
            user_input=user_input,
            task_id=task_id,
            task_mode=mode,
            workflow_options=workflow_options,
        ):
            yield item
        return

    selected_agent = qa_agent if mode == "qa" else react_agent
    selected_tools = QA_TOOLS if mode == "qa" else AGENT_TOOLS
    
    # 构建消息列表
    messages = []
    if previous_messages:
        messages.extend(previous_messages)
    messages.append(HumanMessage(content=user_input))
    
    # 获取当前的 completion_tokens 累计值
    current_completion_tokens = 0
    if task_id:
        session_manager = get_session_manager()
        session_data = session_manager.load_session(task_id)
        if session_data and "token_usage" in session_data:
            current_completion_tokens = session_data["token_usage"].get("completion_tokens", 0)
    
    # 应用消息压缩（如果 completion_tokens 超过限制）
    original_count = len(messages)
    compressed_messages, was_compressed, compression_summary = compress_messages(
        messages, 
        max_completion_tokens=max_context_tokens,
        current_completion_tokens=current_completion_tokens
    )
    messages = compressed_messages
    
    inputs = {"messages": messages}
    config = {"configurable": {"task_id": task_id}} if task_id else {}
    
    # 添加 callbacks：token tracker 和 task context callback
    callbacks = []
    if token_tracker:
        callbacks.append(token_tracker)
    if task_id:
        callbacks.append(TaskContextCallback(task_id=task_id))
    
    if callbacks:
        config["callbacks"] = callbacks
    
    # 使用 astream_events 获取 token 级别的流式输出
    result_queue = queue.Queue()
    exception_holder = [None]
    
    # 如果进行了压缩，先发送压缩信息和压缩后的消息列表
    if was_compressed:
        # 将压缩后的消息转换为 session 格式（dict 列表）
        compressed_messages_dict = []
        for msg in messages:
            if isinstance(msg, SystemMessage):
                # 系统消息通常不保存到 session
                continue
            elif isinstance(msg, HumanMessage):
                compressed_messages_dict.append({
                    "role": "user",
                    "content": msg.content,
                    "timestamp": datetime.now().isoformat()
                })
            elif isinstance(msg, AIMessage):
                tool_calls_data = []
                if hasattr(msg, "tool_calls") and msg.tool_calls:
                    for tc in msg.tool_calls:
                        if isinstance(tc, dict):
                            tool_calls_data.append(tc)
                        else:
                            tool_calls_data.append({
                                "name": getattr(tc, "name", ""),
                                "args": getattr(tc, "args", {}),
                                "id": getattr(tc, "id", "")
                            })
                compressed_messages_dict.append({
                    "role": "assistant",
                    "content": msg.content or "",
                    "tool_calls": tool_calls_data if tool_calls_data else None,
                    "timestamp": datetime.now().isoformat()
                })
            elif isinstance(msg, ToolMessage):
                compressed_messages_dict.append({
                    "role": "tool",
                    "content": msg.content,
                    "tool_name": getattr(msg, "name", "unknown"),
                    "tool_call_id": msg.tool_call_id,
                    "timestamp": datetime.now().isoformat()
                })
        
        result_queue.put({
            "type": "compression",
            "summary": compression_summary,
            "original_count": original_count,
            "compressed_count": len(messages),
            "compressed_messages": compressed_messages_dict
        })
    
    def run_async_stream():
        """在单独的线程中运行异步流"""
        try:
            # 获取当前任务ID（在主线程中）
            current_task_id = get_task_id()
            
            # 创建新的事件循环
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            
            async def _stream_events():
                # 在新线程中恢复任务ID上下文
                if current_task_id:
                    set_task_id(current_task_id)
                
                current_message_id = None
                current_content = ""
                # 追踪当前正在执行的工具 run_id，用于过滤工具内部 LLM 的流式输出
                active_tool_run_ids: set[str] = set()
                
                async for event in selected_agent.astream_events(
                    inputs,
                    version="v2",
                    include_names=["ChatOpenAI", "ChatTongyi", "ChatGoogleGenerativeAI"] + [tool.name for tool in selected_tools],
                    config=config
                ):
                    # 在每个事件处理前都确保任务ID上下文设置（防止上下文丢失）
                    if current_task_id:
                        set_task_id(current_task_id)
                    
                    event_type = event.get("event", "")
                    event_name = event.get("name", "")
                    parent_run_id = event.get("parent_run_id")
                    data = event.get("data", {})
                    
                    # Token 级别的流式输出
                    if event_type == "on_chat_model_stream":
                        # 过滤：工具执行期间的 chat_model_stream 多半来自工具内部 LLM
                        if active_tool_run_ids:
                            continue
                        chunk = data.get("chunk")
                        if chunk and hasattr(chunk, "content") and chunk.content:
                            if current_message_id is None:
                                current_message_id = event.get("run_id", "")
                            
                            current_content += chunk.content
                            result_queue.put({
                                "type": "token",
                                "content": chunk.content,
                                "message_id": current_message_id,
                                "accumulated": current_content
                            })
                    
                    # 完整的消息
                    elif event_type == "on_chat_model_end":
                        # 过滤：工具执行期间的 chat_model_end 多半来自工具内部 LLM
                        if active_tool_run_ids:
                            continue
                        output = data.get("output")
                        if output and hasattr(output, "content") and output.content:
                            result_queue.put({
                                "type": "message",
                                "message": output,
                                "message_id": event.get("run_id", "")
                            })
                            current_message_id = None
                            current_content = ""
                    
                    # 工具调用
                    elif event_type == "on_tool_start":
                        # 在工具执行前设置任务ID上下文
                        event_task_id = None
                        event_data_config = data.get("config", {})
                        if isinstance(event_data_config, dict):
                            event_configurable = event_data_config.get("configurable", {})
                            event_task_id = event_configurable.get("task_id")
                        
                        # 使用事件中的任务ID，否则使用当前任务ID
                        task_id_to_set = event_task_id or current_task_id
                        if task_id_to_set:
                            set_task_id(task_id_to_set)
                        
                        tool_name = event_name
                        tool_input = data.get("input", {})
                        run_id = event.get("run_id", "") or ""
                        if run_id:
                            active_tool_run_ids.add(run_id)
                        result_queue.put({
                            "type": "tool_call",
                            "tool_name": tool_name,
                            "args": tool_input,
                            "run_id": run_id
                        })
                    
                    # 工具结果
                    elif event_type == "on_tool_end":
                        # 确保任务ID上下文仍然设置（工具执行后）
                        if current_task_id:
                            set_task_id(current_task_id)
                        
                        tool_name = event_name
                        tool_output = data.get("output", "")
                        run_id = event.get("run_id", "") or ""
                        if run_id:
                            active_tool_run_ids.discard(run_id)
                        # 确保工具输出被转换为字符串
                        if tool_output is None:
                            tool_output = ""
                        elif not isinstance(tool_output, str):
                            tool_output = str(tool_output)
                        result_queue.put({
                            "type": "tool_result",
                            "tool_name": tool_name,
                            "result": tool_output,
                            "run_id": run_id
                        })
                    
                    # 捕获消息流中的工具消息
                    elif event_type == "on_chain_stream":
                        # 确保任务ID上下文设置
                        if current_task_id:
                            set_task_id(current_task_id)
                        
                        chunk = data.get("chunk", {})
                        if isinstance(chunk, dict) and "messages" in chunk:
                            messages = chunk.get("messages", [])
                            for msg in messages:
                                # 检查是否是 ToolMessage
                                if isinstance(msg, ToolMessage):
                                    tool_name = getattr(msg, "name", "unknown")
                                    tool_content = getattr(msg, "content", "")
                                    tool_call_id = getattr(msg, "tool_call_id", "")
                                    # 确保内容是字符串格式
                                    if tool_content is None:
                                        tool_content_str = ""
                                    elif isinstance(tool_content, str):
                                        tool_content_str = tool_content
                                    else:
                                        tool_content_str = str(tool_content)
                                    result_queue.put({
                                        "type": "tool_result",
                                        "tool_name": tool_name,
                                        "result": tool_content_str,
                                        "run_id": tool_call_id
                                    })
                    
                    # 状态更新（兼容旧格式）
                    elif event_type == "on_chain_end" and event_name == "AgentExecutor":
                        output = data.get("output")
                        if isinstance(output, dict) and "messages" in output:
                            result_queue.put({
                                "type": "state_update",
                                "state": output
                            })
                # 结束标记
                result_queue.put(None)  
            
            loop.run_until_complete(_stream_events())
            loop.close()
            
        except Exception as e:
            exception_holder[0] = e
            result_queue.put(None)
    
    # 启动异步流线程
    thread = threading.Thread(target=run_async_stream, daemon=True)
    thread.start()
    
    # 从队列中获取结果
    while True:
        try:
            item = result_queue.get(timeout=1.0)
            if item is None:
                break
            if exception_holder[0]:
                raise exception_holder[0]
            yield item
        except queue.Empty:
            # 检查线程是否还在运行
            if not thread.is_alive():
                if exception_holder[0]:
                    raise exception_holder[0]
                break
            continue
    
    # 等待线程完成
    thread.join(timeout=5.0)
    
    # 如果还有异常，抛出
    if exception_holder[0]:
        # 如果 astream_events 失败，回退到旧的 stream 方法
        warnings.warn(f"Token-level streaming failed, falling back to message-level: {exception_holder[0]}")
        for chunk in selected_agent.stream(inputs, stream_mode="updates", config=config):
            yield {"type": "state_update", "state": chunk}
