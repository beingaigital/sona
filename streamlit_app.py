"""Sona 分析员控制台 · 仪表盘：API 状态、最近任务、工作流速查（多页 Streamlit 入口）。"""

from __future__ import annotations

import os
from typing import Any, Dict, List

import requests
import streamlit as st

from streamlit_ui_theme import (
    callout_error,
    callout_neutral,
    callout_success,
    hero_panel,
    inject_ui_theme,
    render_nav_sidebar,
)

st.set_page_config(page_title="Sona · 分析员控制台", layout="wide", page_icon="◼")

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


def _fetch_tasks() -> List[Dict[str, Any]]:
    data = api_get("/v1/tasks", timeout=5.0)
    if not isinstance(data, dict):
        return []
    raw = data.get("tasks")
    if not isinstance(raw, list):
        return []
    out: List[Dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        tid = str(item.get("task_id", "") or "")
        status = str(item.get("status", "") or "")
        arts = item.get("artifacts") if isinstance(item.get("artifacts"), dict) else {}
        report_path = str(arts.get("report_path", "") or "")
        name = report_path.split("/")[-1].split("\\")[-1] or (tid[:12] + "…" if len(tid) > 12 else tid)
        out.append({"id": tid, "name": name, "status": status})
    return out


_STATUS_CN = {
    "queued": "等待中",
    "running": "运行中",
    "succeeded": "已完成",
    "failed": "失败",
}

hero_panel(
    kicker="Sona · Analyst console",
    title="分析员控制台",
    subtitle=(
        "面向舆情分析的重操作入口：配置与探活、发起事件分析、查看报告与任务、案例与专题、"
        "以及经典会话中的 /hot、/wiki 等。界面不替代完整监测平台，与 HTTP API 分工协作。"
    ),
)

health = api_get("/health")
tasks = _fetch_tasks() if health else []

if not health:
    callout_error(
        "API 离线：本控制台无法发起「新建任务」或拉取报告",
        "请先在本机另开终端启动 HTTP 服务，再刷新本页。\n\n"
        f"终端 1：sona serve --host 127.0.0.1 --port 8765\n"
        f"当前探测地址：{API_BASE}\n\n"
        "若 API 在其他端口，请设置环境变量 API_BASE 后重启 Streamlit。",
    )
else:
    callout_success(
        "API 在线",
        f"已连接 {API_BASE}；版本信息：{health.get('version', '—')}",
    )

col1, col2, col3 = st.columns(3)
col1.metric("API", "在线" if health else "离线", health.get("version", "—") if health else "sona serve")
col2.metric("本进程任务数", str(len(tasks)), "内存列表，重启 API 会清空")
col3.metric("Streamlit", ":8501", "与 API 可同时运行")

st.divider()
st.subheader("最近任务（本 API 进程）")
if not health:
    callout_neutral("无任务列表", "API 离线时无法获取 /v1/tasks。")
elif not tasks:
    callout_neutral(
        "暂无任务",
        "在「新建任务」提交一次事件分析，或从「经典会话」走完整链路后，此处会列出 task_id 与状态。",
    )
else:
    preview = tasks[:12]
    st.dataframe(
        [
            {
                "状态": _STATUS_CN.get(r["status"], r["status"]),
                "摘要": r["name"][:60] + ("…" if len(r["name"]) > 60 else ""),
                "task_id": r["id"],
            }
            for r in preview
        ],
        use_container_width=True,
        hide_index=True,
    )
    if len(tasks) > 12:
        st.caption(f"仅展示最近 {len(preview)} 条，共 {len(tasks)} 条；完整列表见「任务状态」。")

st.divider()
st.subheader("工作流速查")
st.markdown(
    """
| 目标 | 推荐入口 | 说明 |
|------|----------|------|
| **事件分析报告** | 「新建任务」→ 自动跳转「报告查看」 | 调用 `POST /v1/analyze-event`，同步耗时可能很长 |
| **对话里跑 /event、/hot、/wiki** | 侧栏「经典会话」 | 与历史 CLI 会话能力一致 |
| **案例演示检索** | 「案例检索」 | 当前为演示数据，可后续接真实案例库 |
| **专题 YAML** | 「专题配置」 | 写入 `config/topics.yaml` |
"""
)

with st.expander("环境变量与配置（常用）", expanded=False):
    st.markdown(
        f"""
- **`API_BASE`**：当前 `{API_BASE}`（Streamlit 探测 API 用）
- **模型与采集**：见项目根目录 **`.env`**、**`config/config.yaml`**；缺 key 时任务会失败
- **API CORS**：`SONA_API_CORS_ORIGINS`（多源逗号分隔；详见 `docs/api_design.md`）
- **Neo4j（可选）**：`SONA_NEO4J_*`、`SONA_ENABLE_GRAPH_RAG`
"""
    )

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
    "**经典会话**：侧栏进入后可使用 `/hot`、`/wiki`、事件路由等完整能力；与「新建任务」HTTP 路径并行，按习惯二选一即可。"
)
