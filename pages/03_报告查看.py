"""查看 HTML 报告：GET /v1/tasks/{id} + GET /v1/tasks/{id}/report。"""

from __future__ import annotations

import os

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

st.set_page_config(page_title="报告查看", page_icon="◼", layout="wide")

inject_ui_theme()
render_nav_sidebar("report")

API_BASE = os.environ.get("API_BASE", "http://127.0.0.1:8765")

page_header("报告查看", f"API · {API_BASE}")

task_id = str(st.session_state.get("current_task_id", "") or "").strip()
if not task_id:
    task_id = str(st.text_input("任务 ID", placeholder="粘贴 UUID") or "").strip()

if not task_id:
    callout_neutral(
        "未选择任务",
        "请从「任务状态」点「查看报告」，或在「新建任务」完成后自动跳转；也可在下方粘贴 task_id。",
    )
    if st.button("前往任务列表"):
        st.switch_page("pages/01_任务状态.py")
    st.stop()

if not is_api_reachable(API_BASE):
    callout_error(
        "API 未连通",
        "请先启动 `sona serve`，或检查 `API_BASE` 是否与 API 实际地址一致。",
    )
    st.stop()

task_info = None
with st.spinner("加载任务…"):
    try:
        r = requests.get(f"{API_BASE}/v1/tasks/{task_id}", timeout=30)
        task_info = r.json() if r.status_code == 200 else None
    except Exception as exc:
        task_info = None
        st.error(str(exc))

if not task_info:
    callout_error("无法获取任务详情", "API 返回异常或 task_id 无效；请核对 ID 或到「任务状态」重新选择。")
    st.stop()

status = str(task_info.get("status", ""))
col1, col2 = st.columns(2)
col1.metric("状态", status)
arts = task_info.get("artifacts") if isinstance(task_info.get("artifacts"), dict) else {}
rp = str(arts.get("report_path", "—"))
col2.metric("报告路径", (rp[:40] + "…") if len(rp) > 40 else rp)

st.divider()

if status == "succeeded":
    try:
        r2 = requests.get(f"{API_BASE}/v1/tasks/{task_id}/report", timeout=60)
        if r2.status_code == 200 and r2.text.strip():
            report_html = r2.text
            tab1, tab2 = st.tabs(["渲染", "原始 HTML"])
            with tab1:
                st.components.v1.html(report_html, height=720, scrolling=True)
            with tab2:
                st.code(report_html[:50000], language="html")
            st.download_button(
                "下载 HTML",
                data=report_html.encode("utf-8"),
                file_name=f"report_{task_id[:8]}.html",
                mime="text/html",
            )
        else:
            st.warning(f"报告不可用: HTTP {r2.status_code}")
    except Exception as exc:
        st.error(str(exc))
elif status == "failed":
    err = task_info.get("error")
    st.error("任务失败")
    if isinstance(err, dict):
        st.code(f"{err.get('error_code')}: {err.get('error_message')}")
    else:
        st.json(task_info)
else:
    st.info(f"当前状态 `{status}`，尚无报告或仍在排队。")
