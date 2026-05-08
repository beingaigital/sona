"""经典 Streamlit 会话界面（原根目录 streamlit_app 逻辑）。

由 ``pages/99_经典会话与路由.py`` 加载；首页 ``streamlit_app.py`` 为轻量面板。
"""

from __future__ import annotations

import json
import os
import asyncio
import sys
import threading
import time
import uuid
import traceback
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests
import streamlit as st

from cli.event_analysis_workflow import run_event_analysis_workflow
from cli.hot_ui import run_hot_command
from cli.interactive import run_session_query
from cli.router import route_query
from utils.hot_time_parser import apply_hot_lookback_hours, infer_hot_lookback_hours
from utils.message_utils import messages_from_session_data
from utils.path import ensure_task_dirs
from utils.session_manager import SessionManager, get_session_manager


def _get_manager() -> SessionManager:
    return get_session_manager()


def _session_label(session: Dict[str, Any]) -> str:
    task_id = str(session.get("task_id", ""))[:8]
    desc = str(session.get("description", "") or "无描述")
    updated_at = str(session.get("updated_at", "") or "")
    return f"{task_id} | {updated_at[:19]} | {desc[:40]}"


def _ensure_current_session() -> str:
    manager = _get_manager()
    current = st.session_state.get("current_task_id")
    if current:
        return current
    task_id = manager.create_session("Streamlit 会话")
    ensure_task_dirs(task_id)
    st.session_state.current_task_id = task_id
    return task_id


def _extract_report_info(session_data: Dict[str, Any]) -> Optional[Tuple[str, str]]:
    messages = session_data.get("messages", [])
    for msg in reversed(messages):
        if msg.get("role") != "tool":
            continue
        if msg.get("tool_name") != "report_html":
            continue
        raw = str(msg.get("content", "") or "").strip()
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except Exception:
            continue
        file_url = str(data.get("file_url", "") or "")
        html_path = str(data.get("html_file_path", "") or "")
        if file_url or html_path:
            return file_url, html_path
    return None


def _load_hot_topics_summary() -> Optional[Dict[str, Any]]:
    """加载热点结构化摘要（由 tools/hottopics.py 写入）。"""
    summary_path = Path("output_langgraph") / "hot_topics_latest.json"
    if not summary_path.exists():
        return None
    try:
        with open(summary_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
    except Exception:
        return None
    return None


def _mask_key(value: str) -> str:
    s = str(value or "")
    if len(s) <= 8:
        return "*" * len(s)
    return f"{s[:4]}...{s[-4:]}"


def _hot_llm_self_check() -> List[Dict[str, str]]:
    """Check each hot LLM provider connectivity and return status rows."""
    from utils.hot_topics_env import prepare_hot_topics_environment

    providers = ["deepseek", "qwen", "kimi", "openai"]
    original_provider = os.environ.get("HOT_LLM_PROVIDER")
    results: List[Dict[str, str]] = []

    try:
        for provider in providers:
            os.environ["HOT_LLM_PROVIDER"] = provider
            prepare_hot_topics_environment()

            api_key = str(os.environ.get("INSIGHT_ENGINE_API_KEY") or "")
            base_url = str(os.environ.get("INSIGHT_ENGINE_BASE_URL") or "").rstrip("/")
            model = str(os.environ.get("INSIGHT_ENGINE_MODEL_NAME") or "")

            row: Dict[str, str] = {
                "provider": provider,
                "base_url": base_url or "-",
                "model": model or "-",
                "key": _mask_key(api_key) if api_key else "(missing)",
                "status": "unknown",
                "detail": "",
            }

            if not api_key or not base_url or not model:
                row["status"] = "missing_config"
                row["detail"] = "缺少 key/base_url/model"
                results.append(row)
                continue

            check_url = f"{base_url}/chat/completions"
            payload = {
                "model": model,
                "messages": [{"role": "user", "content": "ping"}],
                "max_tokens": 4,
                "temperature": 0,
            }
            headers = {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            }
            try:
                resp = requests.post(check_url, headers=headers, json=payload, timeout=20)
                if resp.status_code == 200:
                    row["status"] = "ok"
                    row["detail"] = "连接成功"
                else:
                    row["status"] = f"http_{resp.status_code}"
                    body = resp.text[:180] if resp.text else ""
                    row["detail"] = body or "请求失败"
            except Exception as exc:
                row["status"] = "network_error"
                row["detail"] = str(exc)[:180]

            results.append(row)
    finally:
        if original_provider is None:
            os.environ.pop("HOT_LLM_PROVIDER", None)
        else:
            os.environ["HOT_LLM_PROVIDER"] = original_provider
        try:
            prepare_hot_topics_environment()
        except Exception:
            pass

    return results


def _render_hot_topics_panel() -> None:
    """渲染热点排序 + 点击展开详情。"""
    data = _load_hot_topics_summary()
    if not data:
        return

    top_topics = data.get("top_topics")
    if not isinstance(top_topics, list) or not top_topics:
        return

    # 按热度排序（降序）
    sorted_topics = sorted(
        [x for x in top_topics if isinstance(x, dict)],
        key=lambda x: float(x.get("heat_score") or 0.0),
        reverse=True,
    )
    if not sorted_topics:
        return

    # 情绪占比
    sentiment_counter = {"正面": 0, "中性": 0, "负面": 0}
    for topic in sorted_topics:
        sentiment = str(topic.get("sentiment", "中性") or "中性").strip()
        if sentiment not in sentiment_counter:
            sentiment = "中性"
        sentiment_counter[sentiment] += 1

    total = max(1, len(sorted_topics))
    pos_ratio = sentiment_counter["正面"] / total
    neu_ratio = sentiment_counter["中性"] / total
    neg_ratio = sentiment_counter["负面"] / total

    with st.container(border=True):
        st.markdown("### 热点排序与详情")
        summary = str(data.get("summary", "") or "").strip()
        if summary:
            st.caption(summary)

        c1, c2, c3 = st.columns(3)
        c1.metric("正面占比", f"{pos_ratio:.0%}")
        c2.metric("中性占比", f"{neu_ratio:.0%}")
        c3.metric("负面占比", f"{neg_ratio:.0%}")

        st.caption("点击每条热点可展开查看：情绪、类别、热度、主要观点")
        for idx, topic in enumerate(sorted_topics, start=1):
            title = str(topic.get("topic", "") or f"热点{idx}")
            sentiment = str(topic.get("sentiment", "中性") or "中性")
            category = str(topic.get("category", "其他") or "其他")
            heat = float(topic.get("heat_score") or 0.0)
            comment = str(topic.get("comment", "") or "暂无观点摘要")
            label = f"#{idx} {title} | 热度 {heat:.1f}"
            with st.expander(label, expanded=False):
                st.markdown(f"- 情绪倾向：`{sentiment}`")
                st.markdown(f"- 事件类别：`{category}`")
                st.markdown(f"- 热度得分：`{heat:.1f}`")
                st.markdown(f"- 主要观点：{comment}")


def _render_messages(session_data: Dict[str, Any]) -> None:
    for msg in session_data.get("messages", []):
        role = msg.get("role")
        content = str(msg.get("content", "") or "")
        if role == "user":
            with st.chat_message("user"):
                st.markdown(content)
        elif role == "assistant":
            with st.chat_message("assistant"):
                st.markdown(content or "_（无文本回复）_")
        elif role == "tool":
            tool_name = msg.get("tool_name", "tool")
            with st.chat_message("assistant"):
                with st.expander(f"工具输出: {tool_name}", expanded=False):
                    st.code(content[:5000] if content else "(empty)")


def _analyze_error(error: Exception) -> Tuple[str, List[str]]:
    """把底层异常转换为用户可理解的提示。"""
    msg = str(error or "").strip()
    suggestions: List[str] = []
    title = "执行失败"

    if "登录失败" in msg or "NetInsight" in msg or "IDAgent" in msg:
        title = "采集登录失败"
        suggestions.extend(
            [
                "切换到“纯对话”模式先继续，不依赖采集登录。",
                "在侧边栏开启“允许历史回退”，避免采集失败时直接中断。",
                "勾选“采集禁用代理（SONA_NETINSIGHT_NO_PROXY=true）”后重试。",
                "取消“采集无头模式（NETINSIGHT_HEADLESS=true）”，用可见浏览器重试登录。",
            ]
        )
    elif "token limit" in msg or "exceeded model token limit" in msg:
        title = "模型上下文超限"
        suggestions.extend(
            [
                "缩小本次分析范围（更短时间段/更少关键词）后重试。",
                "改用更长上下文模型（例如 report profile 切到更大上下文）。",
            ]
        )
    else:
        suggestions.append("可以先切到“纯对话”模式确认 UI 与模型本身是否正常。")

    return title, suggestions


class PromptBridge:
    """后台工作流与 Streamlit UI 的交互桥。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._cond = threading.Condition(self._lock)
        self.pending: Optional[Dict[str, Any]] = None
        self._responses: Dict[str, str] = {}

    def ask(self, question: str, timeout_sec: int, default_yes: bool, kind: str) -> str:
        prompt_id = str(uuid.uuid4())
        start_ts = time.time()
        with self._cond:
            self.pending = {
                "id": prompt_id,
                "question": question,
                "kind": kind,
                "timeout_sec": max(1, int(timeout_sec)),
                "default_yes": bool(default_yes),
                "start_ts": start_ts,
            }
            self._responses.pop(prompt_id, None)
            self._cond.notify_all()

        deadline = start_ts + max(1, int(timeout_sec))
        with self._cond:
            while True:
                if prompt_id in self._responses:
                    ans = self._responses.pop(prompt_id, "")
                    if self.pending and self.pending.get("id") == prompt_id:
                        self.pending = None
                    return ans
                remain = deadline - time.time()
                if remain <= 0:
                    if self.pending and self.pending.get("id") == prompt_id:
                        self.pending = None
                    return ""
                self._cond.wait(timeout=min(remain, 0.25))

    def snapshot(self) -> Optional[Dict[str, Any]]:
        with self._lock:
            if not self.pending:
                return None
            p = dict(self.pending)
            elapsed = max(0.0, time.time() - float(p.get("start_ts", 0.0)))
            p["remain_sec"] = max(0, int(float(p.get("timeout_sec", 0)) - elapsed))
            return p

    def submit(self, prompt_id: str, answer: str) -> None:
        with self._cond:
            if not self.pending or self.pending.get("id") != prompt_id:
                return
            self._responses[prompt_id] = answer
            self._cond.notify_all()


class StatusBridge:
    """后台工作流状态桥：记录步骤状态供前端轮询展示。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._events: List[Dict[str, Any]] = []

    def clear(self) -> None:
        with self._lock:
            self._events = []

    def push(self, step: str, status: str, detail: str) -> None:
        with self._lock:
            self._events.append(
                {
                    "ts": time.strftime("%H:%M:%S"),
                    "step": str(step or "").strip() or "未知步骤",
                    "status": str(status or "").strip() or "running",
                    "detail": str(detail or "").strip(),
                }
            )
            # 只保留最近 120 条，避免 UI 无限增长
            if len(self._events) > 120:
                self._events = self._events[-120:]

    def snapshot(self) -> List[Dict[str, Any]]:
        with self._lock:
            return [dict(x) for x in self._events]


def _ensure_runtime_state() -> Dict[str, Any]:
    if "runtime" not in st.session_state:
        st.session_state["runtime"] = {
            "running": False,
            "thread": None,
            "error": "",
            "traceback": "",
            "bridge": PromptBridge(),
            "status_bridge": StatusBridge(),
            "last_query": "",
        }
    return st.session_state["runtime"]


def _start_background_run(
    *,
    task_id: str,
    user_input: str,
    mode: str,
    prefer_existing_data: bool,
    disable_blocking_prompts: bool,
) -> None:
    runtime = _ensure_runtime_state()
    if runtime.get("running"):
        return

    runtime["running"] = True
    runtime["error"] = ""
    runtime["traceback"] = ""
    runtime["last_query"] = user_input

    bridge: PromptBridge = runtime["bridge"]
    status_bridge: StatusBridge = runtime["status_bridge"]
    status_bridge.clear()
    status_bridge.push("初始化", "running", "任务已启动，等待执行")

    def _runner() -> None:
        from cli import event_analysis_workflow as workflow_mod

        # Windows + 后台线程中运行 Playwright 时，需确保事件循环策略支持 subprocess。
        if sys.platform.startswith("win"):
            try:
                asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
            except Exception:
                pass

        workflow_mod.set_web_prompt_handler(bridge.ask)
        workflow_mod.set_web_status_handler(status_bridge.push)
        try:
            status_bridge.push("路由", "running", "正在判定执行路径")
            _run_query(
                task_id=task_id,
                user_input=user_input,
                mode=mode,
                prefer_existing_data=prefer_existing_data,
                disable_blocking_prompts=disable_blocking_prompts,
            )
            status_bridge.push("结束", "success", "任务执行完成")
        except Exception as e:
            runtime["error"] = str(e)
            runtime["traceback"] = traceback.format_exc()
            status_bridge.push("结束", "failed", str(e))
        finally:
            workflow_mod.set_web_prompt_handler(None)
            workflow_mod.set_web_status_handler(None)
            runtime["running"] = False

    t = threading.Thread(target=_runner, daemon=True)
    runtime["thread"] = t
    t.start()


@st.fragment(run_every="1s")
def _render_runtime_panel() -> None:
    runtime = _ensure_runtime_state()
    bridge: PromptBridge = runtime["bridge"]
    status_bridge: StatusBridge = runtime["status_bridge"]
    pending = bridge.snapshot()
    status_events = status_bridge.snapshot()

    if runtime.get("running"):
        st.info("任务执行中...")

    with st.container(border=True):
        st.markdown("### 流程状态栏")
        if not status_events:
            st.caption("尚未开始执行")
        else:
            icon_map = {"running": "🟡", "success": "🟢", "failed": "🔴", "skipped": "⚪"}
            show_events = status_events[-10:]
            for ev in show_events:
                status = str(ev.get("status", "running"))
                icon = icon_map.get(status, "🟡")
                ts = str(ev.get("ts", ""))
                step = str(ev.get("step", ""))
                detail = str(ev.get("detail", ""))
                st.markdown(f"{icon} `{ts}` **{step}** · {status}")
                if detail:
                    st.caption(detail)

    if pending:
        q = str(pending.get("question", "") or "")
        pid = str(pending.get("id", ""))
        kind = str(pending.get("kind", "yes_no"))
        remain = int(pending.get("remain_sec", 0))
        default_yes = bool(pending.get("default_yes", True))

        with st.container(border=True):
            st.markdown("### 需要你的输入")
            st.write(q)
            if kind == "yes_no":
                st.caption(f"{remain}s 后默认选择：{'y' if default_yes else 'n'}")
                c1, c2, c3 = st.columns(3)
                if c1.button("y", key=f"prompt_yes_{pid}", use_container_width=True):
                    bridge.submit(pid, "y")
                if c2.button("n", key=f"prompt_no_{pid}", use_container_width=True):
                    bridge.submit(pid, "n")
                if c3.button("使用默认", key=f"prompt_def_{pid}", use_container_width=True):
                    bridge.submit(pid, "")
            else:
                st.caption(f"{remain}s 后默认跳过")
                text_key = f"prompt_text_{pid}"
                answer = st.text_input("请输入", key=text_key)
                c1, c2 = st.columns(2)
                if c1.button("提交", key=f"prompt_submit_{pid}", use_container_width=True):
                    bridge.submit(pid, answer or "")
                if c2.button("跳过（默认）", key=f"prompt_skip_{pid}", use_container_width=True):
                    bridge.submit(pid, "")

    if (not runtime.get("running")) and runtime.get("error"):
        title, suggestions = _analyze_error(Exception(runtime["error"]))
        st.error(f"{title}: {runtime['error']}")
        for s in suggestions:
            st.markdown(f"- {s}")
        with st.expander("查看详细追溯", expanded=False):
            st.code(runtime.get("traceback", ""))
        if st.button("清除错误提示", key="clear_runtime_error"):
            runtime["error"] = ""
            runtime["traceback"] = ""

def _run_query(
    *,
    task_id: str,
    user_input: str,
    mode: str,
    prefer_existing_data: bool,
    disable_blocking_prompts: bool,
) -> None:
    manager = _get_manager()
    inferred_hot_hours = infer_hot_lookback_hours(user_input)
    apply_hot_lookback_hours(inferred_hot_hours)
    # Streamlit 无法在一次请求中处理中途阻塞输入，默认关闭 CLI 阻塞式提问
    os.environ["SONA_EVENT_COLLAB_MODE"] = "auto" if disable_blocking_prompts else "hybrid"
    mode = mode.lower()
    if mode == "hot":
        report_path = run_hot_command()
        manager.add_message(task_id, "user", user_input)
        if report_path:
            manager.add_message(task_id, "assistant", f"已执行热点态势感知流程，报告路径：{report_path}")
        else:
            manager.add_message(task_id, "assistant", "已执行热点态势感知流程。")
        return

    if mode == "event":
        route_decision, route_data = route_query(user_input, task_id)
        data_result = route_data.get("data_result")
        existing_data_path = None
        skip_data_collect = False
        if (
            prefer_existing_data
            and data_result
            and getattr(data_result, "has_data", False)
            and getattr(data_result, "data_paths", None)
        ):
            existing_data_path = data_result.data_paths[0]
            skip_data_collect = True
        run_event_analysis_workflow(
            user_input,
            task_id,
            manager,
            debug=True,
            existing_data_path=existing_data_path,
            skip_data_collect=skip_data_collect,
        )
        return

    if mode == "chat":
        session_data = manager.load_session(task_id) or {}
        previous_messages = messages_from_session_data(session_data)
        run_session_query(user_input, task_id, previous_messages, show_spinner=False)
        return

    # auto mode
    route_decision, route_data = route_query(user_input, task_id)
    data_result = route_data.get("data_result")

    if route_decision in ("event_analysis_workflow", "event_analysis_with_existing_data"):
        existing_data_path = None
        skip_data_collect = False
        if (
            prefer_existing_data
            and data_result
            and getattr(data_result, "has_data", False)
            and getattr(data_result, "data_paths", None)
        ):
            existing_data_path = data_result.data_paths[0]
            skip_data_collect = True

        run_event_analysis_workflow(
            user_input,
            task_id,
            manager,
            debug=True,
            existing_data_path=existing_data_path,
            skip_data_collect=skip_data_collect,
        )
    elif route_decision == "hottopics_workflow":
        report_path = run_hot_command()
        if report_path:
            manager.add_message(task_id, "assistant", f"已执行热点态势感知流程，报告路径：{report_path}")
        else:
            manager.add_message(task_id, "assistant", "已执行热点态势感知流程。")
    else:
        session_data = manager.load_session(task_id) or {}
        previous_messages = messages_from_session_data(session_data)
        run_session_query(user_input, task_id, previous_messages, show_spinner=False)


def main() -> None:
    st.title("Sona Web UI")
    st.caption("Cleaner chat interface with session management and workflow routing.")

    manager = _get_manager()
    _ensure_current_session()

    with st.sidebar:
        st.subheader("会话")
        if st.button("新建会话", use_container_width=True):
            task_id = manager.create_session("Streamlit 会话")
            ensure_task_dirs(task_id)
            st.session_state.current_task_id = task_id
            st.rerun()

        sessions = manager.list_sessions(limit=50)
        session_options = { _session_label(s): s.get("task_id", "") for s in sessions }
        labels = list(session_options.keys())
        if labels:
            current = st.session_state.get("current_task_id", "")
            current_label = next((k for k, v in session_options.items() if v == current), labels[0])
            selected_label = st.selectbox("选择历史会话", labels, index=labels.index(current_label))
            selected_task_id = session_options[selected_label]
            if selected_task_id != current:
                st.session_state.current_task_id = selected_task_id
                st.rerun()

        st.divider()
        st.subheader("执行模式")
        mode = st.radio(
            "模式",
            options=["auto", "chat", "event", "hot"],
            format_func=lambda x: {
                "auto": "自动路由",
                "chat": "纯对话",
                "event": "强制事件分析",
                "hot": "热点流程",
            }[x],
        )
        forced_mode = st.session_state.pop("force_mode", None)
        if forced_mode in {"auto", "chat", "event", "hot"}:
            mode = forced_mode
        prefer_existing_data = st.checkbox("自动路由时优先复用历史数据", value=True)

        st.divider()
        st.subheader("容错与采集")
        allow_history_fallback = st.checkbox(
            "允许历史回退（SONA_ALLOW_HISTORY_FALLBACK）",
            value=True,
            help="开启后，采集失败时可尝试复用历史 CSV 继续分析。",
        )
        netinsight_no_proxy = st.checkbox(
            "采集禁用代理（SONA_NETINSIGHT_NO_PROXY=true）",
            value=True,
        )
        netinsight_headless = st.checkbox(
            "采集无头模式（NETINSIGHT_HEADLESS=true）",
            value=False,
            help="关闭后会显示浏览器，常用于排查登录失败。",
        )
        keep_runtime_prompts = st.checkbox(
            "运行中实时提问（y/n、文本输入）",
            value=True,
            help="开启后，会在流程运行中弹出问题卡片，超时自动走默认值。",
        )

        st.divider()
        st.subheader("热点模型自检")
        if st.button("检测 deepseek/qwen/kimi/openai", use_container_width=True):
            with st.spinner("正在逐个检测模型连接..."):
                st.session_state["hot_self_check_rows"] = _hot_llm_self_check()
        check_rows = st.session_state.get("hot_self_check_rows", [])
        if check_rows:
            st.dataframe(check_rows, use_container_width=True, hide_index=True)

    task_id = st.session_state.current_task_id
    session_data = manager.load_session(task_id) or {"messages": []}

    col1, col2 = st.columns([3, 1])
    with col1:
        st.markdown(f"**当前会话**: `{task_id}`")
    with col2:
        if st.button("刷新", use_container_width=True):
            st.rerun()

    _render_messages(session_data)
    _render_runtime_panel()
    _render_hot_topics_panel()

    report_info = _extract_report_info(session_data)
    if report_info:
        file_url, html_path = report_info
        with st.container(border=True):
            st.markdown("**最新报告**")
            if file_url:
                st.link_button("打开报告", file_url, use_container_width=True)
            if html_path:
                st.code(html_path)

    runtime = _ensure_runtime_state()
    user_input = st.chat_input(
        "输入你的问题，例如：分析某事件舆情",
        disabled=bool(runtime.get("running")),
    )
    if user_input:
        with st.chat_message("user"):
            st.markdown(user_input)
        # 让当前 Streamlit 进程环境与 UI 选项一致
        os.environ["SONA_ALLOW_HISTORY_FALLBACK"] = "true" if allow_history_fallback else "false"
        os.environ["SONA_NETINSIGHT_NO_PROXY"] = "true" if netinsight_no_proxy else "false"
        os.environ["NETINSIGHT_HEADLESS"] = "true" if netinsight_headless else "false"
        _start_background_run(
            task_id=task_id,
            user_input=user_input,
            mode=mode,
            prefer_existing_data=prefer_existing_data,
            disable_blocking_prompts=not keep_runtime_prompts,
        )
        st.rerun()


def run_legacy_chat() -> None:
    """供多页应用子页面调用（子页面需先 ``st.set_page_config``）。"""
    main()

