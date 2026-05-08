"""Sona 轻量首页：导航到各子页，并探测任务 19 API（与 ``pages/`` 多页 GUI 对齐）。"""

from __future__ import annotations

import os

import requests
import streamlit as st

st.set_page_config(page_title="Sona 舆情分析", layout="wide", page_icon="📊")

API_BASE = os.environ.get("API_BASE", "http://127.0.0.1:8765")


def api_get(path: str, timeout: float = 3.0) -> dict | list | None:
    try:
        r = requests.get(f"{API_BASE}{path}", timeout=timeout)
        if r.status_code == 200:
            return r.json()
        return None
    except Exception:
        return None


with st.sidebar:
    st.title("导航")
    st.caption("多页面板（任务 20）与 API（任务 19）")
    st.markdown(f"- API：`{API_BASE}`")
    if st.button("📋 任务状态", use_container_width=True):
        st.switch_page("pages/01_任务状态.py")
    if st.button("➕ 新建任务", use_container_width=True):
        st.switch_page("pages/02_新建任务.py")
    if st.button("📄 报告查看", use_container_width=True):
        st.switch_page("pages/03_报告查看.py")
    if st.button("🔍 案例检索", use_container_width=True):
        st.switch_page("pages/04_案例检索.py")
    if st.button("⚙️ 专题配置", use_container_width=True):
        st.switch_page("pages/05_专题配置.py")
    st.divider()
    if st.button("🧠 经典会话（原完整界面）", use_container_width=True):
        st.switch_page("pages/99_经典会话与路由.py")

st.title("📊 Sona 舆情分析")
st.caption("轻量 Viewer：业务由 API / 工作流承担；详见 docs/gui_decision.md")

col1, col2, col3 = st.columns(3)
health = api_get("/health")
if health:
    col1.metric("API 状态", "正常", health.get("version", "—"))
else:
    col1.metric("API 状态", "离线", "请先 sona serve")

task_payload = api_get("/v1/tasks", timeout=3.0)
n_tasks = 0
if isinstance(task_payload, dict) and isinstance(task_payload.get("tasks"), list):
    n_tasks = len(task_payload["tasks"])
col2.metric("本进程已登记任务", str(n_tasks), "来自 GET /v1/tasks")
col3.metric("Streamlit", "8501 默认", "见 StartSonaWebUI.bat")

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

st.divider()
st.info(
    "完整对话式事件分析（会话、热点自检等）在侧边栏进入 **经典会话** 子页。"
    " 新建分析也可在「新建任务」中调用 POST /v1/analyze-event（需 API 在线）。",
    icon="💡",
)
