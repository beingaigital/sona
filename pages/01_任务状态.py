"""任务列表：从任务 19 API 的 GET /v1/tasks 读取（无数据时提示）。"""

from __future__ import annotations

import os
from typing import Any, Dict, List

import requests
import streamlit as st

from streamlit_ui_theme import (
    callout_error,
    callout_neutral,
    inject_ui_theme,
    is_api_reachable,
    page_header,
    render_nav_sidebar,
)

st.set_page_config(page_title="任务状态", page_icon="◼", layout="wide")

inject_ui_theme()
render_nav_sidebar("tasks")

API_BASE = os.environ.get("API_BASE", "http://127.0.0.1:8765")

_STATUS_CN = {
    "queued": "等待中",
    "running": "运行中",
    "succeeded": "已完成",
    "failed": "失败",
}


def _fetch_tasks() -> List[Dict[str, Any]]:
    try:
        r = requests.get(f"{API_BASE}/v1/tasks", timeout=5)
        if r.status_code != 200:
            return []
        data = r.json()
        raw = data.get("tasks") if isinstance(data, dict) else None
        if not isinstance(raw, list):
            return []
        out: List[Dict[str, Any]] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            tid = str(item.get("task_id", ""))
            status = str(item.get("status", ""))
            arts = item.get("artifacts") if isinstance(item.get("artifacts"), dict) else {}
            report_path = str(arts.get("report_path", "") or "")
            err = item.get("error")
            err_msg = ""
            if isinstance(err, dict):
                err_msg = str(err.get("error_message", "") or "")
            out.append(
                {
                    "id": tid,
                    "name": report_path.split("\\")[-1].split("/")[-1] or tid[:8] + "…",
                    "status": status,
                    "report_path": report_path,
                    "error_message": err_msg,
                }
            )
        return out
    except Exception:
        return []


page_header("任务状态", f"API · {API_BASE}")

if not is_api_reachable(API_BASE):
    callout_error(
        "无法连接 API",
        "请先启动：`sona serve --host 127.0.0.1 --port 8765`\n\n"
        f"当前探测：`{API_BASE}/health`\n\n"
        "若端口不同，请设置环境变量 `API_BASE` 后重启 Streamlit。",
    )
    st.stop()

filter_status = st.selectbox("状态筛选", ["全部", "运行中", "已完成", "等待中", "失败"])

c1, c2 = st.columns([1, 6])
with c1:
    if st.button("刷新", type="primary"):
        st.rerun()
with c2:
    if st.button("新建任务"):
        st.switch_page("pages/02_新建任务.py")

tasks = _fetch_tasks()

if not tasks:
    callout_neutral(
        "暂无任务记录",
        "请先在本页或「新建任务」提交一次事件分析；任务列表来自当前 API 进程内存，重启 `sona serve` 后会清空。",
    )
    st.stop()

rev_map = {"运行中": "running", "已完成": "succeeded", "等待中": "queued", "失败": "failed"}

cols = st.columns(4)
cols[0].metric("运行中", sum(1 for t in tasks if t["status"] == "running"))
cols[1].metric("已完成", sum(1 for t in tasks if t["status"] == "succeeded"))
cols[2].metric("等待中", sum(1 for t in tasks if t["status"] == "queued"))
cols[3].metric("失败", sum(1 for t in tasks if t["status"] == "failed"))

st.divider()

for task in tasks:
    if filter_status != "全部":
        want = rev_map.get(filter_status, "")
        if task["status"] != want:
            continue

    with st.container(border=True):
        col1, col2, col3 = st.columns([4, 2, 1])
        with col1:
            st.write(f"**{task['name']}**")
            st.caption(f"ID: `{task['id']}`")
            if task.get("error_message"):
                st.caption(f"错误: {task['error_message'][:200]}")
        with col2:
            st.write(_STATUS_CN.get(task["status"], task["status"]))
        with col3:
            if st.button("查看报告", key=f"view_{task['id']}"):
                st.session_state.current_task_id = task["id"]
                st.switch_page("pages/03_报告查看.py")
