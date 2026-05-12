"""新建事件分析：调用 POST /v1/analyze-event（与 api/schema 一致）。"""

from __future__ import annotations

import os

import requests
import streamlit as st

from streamlit_ui_theme import inject_ui_theme, page_header, render_nav_sidebar

st.set_page_config(page_title="新建任务", page_icon="◼", layout="wide")

inject_ui_theme()
render_nav_sidebar("new")

API_BASE = os.environ.get("API_BASE", "http://127.0.0.1:8765")

page_header("新建分析任务", "调用任务 19：`POST /v1/analyze-event`（同步执行，可能耗时很长）")
st.info("请确认已启动 `sona serve`。")

with st.form("new_task_form"):
    event_name = st.text_input("任务名称（可选，会写入分析 query 前缀）", placeholder="例如：315 舆情分析")
    event_desc = st.text_area(
        "事件描述 *",
        placeholder="需要分析的舆情事件说明…",
        height=120,
    )
    prefer_existing = st.checkbox("优先复用历史数据", value=True)
    no_block = st.checkbox("禁用阻塞式交互提示（推荐 API 调用）", value=True)

    submitted = st.form_submit_button("创建并运行", type="primary", use_container_width=True)

if submitted:
    if not str(event_desc or "").strip():
        st.error("请填写事件描述")
    else:
        query = str(event_desc).strip()
        if str(event_name or "").strip():
            query = f"{event_name.strip()}：{query}"
        payload = {
            "query": query,
            "prefer_existing_data": prefer_existing,
            "disable_blocking_prompts": no_block,
        }
        with st.spinner("正在执行事件分析（请勿关闭页面）…"):
            try:
                r = requests.post(
                    f"{API_BASE}/v1/analyze-event",
                    json=payload,
                    timeout=None,
                )
                if r.status_code == 200:
                    result = r.json()
                    task_id = result.get("task_id", "")
                    st.session_state.current_task_id = task_id
                    st.success("任务已完成（同步），正在打开报告页…")
                    st.switch_page("pages/03_报告查看.py")
                else:
                    st.error(f"HTTP {r.status_code}: {r.text[:2000]}")
            except Exception as exc:
                st.error(f"请求异常: {exc}")
                st.caption("请先启动：`sona serve --port 8765`")
