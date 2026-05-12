"""Sona 轻量首页：导航到各子页，并探测任务 19 API（与 ``pages/`` 多页 GUI 对齐）。"""

from __future__ import annotations

import os

import requests
import streamlit as st

from streamlit_ui_theme import hero_panel, inject_ui_theme, render_nav_sidebar

st.set_page_config(page_title="Sona 舆情分析", layout="wide", page_icon="◼")

inject_ui_theme()
render_nav_sidebar("home")

API_BASE = os.environ.get("API_BASE", "http://127.0.0.1:8765")


def api_get(path: str, timeout: float = 3.0) -> dict | list | None:
    try:
        r = requests.get(f"{API_BASE}{path}", timeout=timeout)
        if r.status_code == 200:
            return r.json()
        return None
    except Exception:
        return None


hero_panel(
    kicker="Sona · Opinion intelligence",
    title="舆情分析工作台",
    subtitle="轻量监视与事件分析入口；执行仍由既有 FastAPI 与工作流承担，本页仅做状态展示与路由。",
)

health = api_get("/health")
task_payload = api_get("/v1/tasks", timeout=3.0)
n_tasks = 0
if isinstance(task_payload, dict) and isinstance(task_payload.get("tasks"), list):
    n_tasks = len(task_payload["tasks"])

col1, col2, col3 = st.columns(3)
if health:
    col1.metric("API", "在线", health.get("version", "—"))
else:
    col1.metric("API", "离线", "请先 sona serve")
col2.metric("本进程任务数", str(n_tasks), "GET /v1/tasks")
col3.metric("GUI", "Streamlit", "默认 :8501")

st.divider()
st.subheader("快速入口")
c1, c2, c3, c4, c5 = st.columns(5)
with c1:
    if st.button("任务状态", use_container_width=True):
        st.switch_page("pages/01_任务状态.py")
with c2:
    if st.button("新建任务", use_container_width=True):
        st.switch_page("pages/02_新建任务.py")
with c3:
    if st.button("报告查看", use_container_width=True):
        st.switch_page("pages/03_报告查看.py")
with c4:
    if st.button("案例检索", use_container_width=True):
        st.switch_page("pages/04_案例检索.py")
with c5:
    if st.button("专题配置", use_container_width=True):
        st.switch_page("pages/05_专题配置.py")

st.info(
    "完整对话式事件分析（会话、热点等）请从侧栏进入 **经典会话**。新建分析也可在「新建任务」中调用 "
    "`POST /v1/analyze-event`（需 API 在线）。",
)
