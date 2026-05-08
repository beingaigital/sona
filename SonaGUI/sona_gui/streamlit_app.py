import streamlit as st
import requests
import json
from datetime import datetime

st.set_page_config(page_title="Sona 舆情分析", layout="wide", page_icon="📊")

import os
API_BASE = os.getenv("API_BASE", "http://127.0.0.1:8765")

def api_get(path: str):
    try:
        r = requests.get(f"{API_BASE}{path}", timeout=2)
        return r.json() if r.status_code == 200 else None
    except Exception as e:
        return None  # 静默失败，使用演示模式

def api_post(path: str, data: dict):
    try:
        r = requests.post(f"{API_BASE}{path}", json=data, timeout=5)
        return r.json() if r.status_code in (200, 201) else None
    except Exception as e:
        st.error(f"API 请求失败: {e}")
        return None

# 悬浮聊天助手（简化版）
if "chat_open" not in st.session_state:
    st.session_state.chat_open = False

with st.sidebar:
    st.title("💬 助手")
    st.caption("快速命令：")
    if st.button("📋 查看所有任务"):
        st.switch_page("pages/01_任务状态.py")
    if st.button("➕ 新建分析任务"):
        st.switch_page("pages/02_新建任务.py")
    if st.button("📄 查看最新报告"):
        st.switch_page("pages/03_报告查看.py")

st.title("📊 Sona 舆情分析系统")

# 健康检查
col1, col2, col3 = st.columns(3)

health = api_get("/health")
if health:
    col1.metric("API 状态", "✅ 正常", health.get("version", "unknown"))
else:
    col1.metric("API 状态", "❌ 离线", "请启动 sona serve")

# 模拟统计数据（实际应从API获取）
col2.metric("今日任务", "12", "+3")
col3.metric("运行中", "2", "1 个新增")

st.divider()

st.subheader("🚀 快速开始")

col_a, col_b, col_c, col_d, col_e = st.columns(5)

with col_a:
    if st.button("📋 任务状态", use_container_width=True):
        st.switch_page("pages/01_任务状态.py")

with col_b:
    if st.button("➕ 新建任务", use_container_width=True):
        st.switch_page("pages/02_新建任务.py")

with col_c:
    if st.button("📄 报告查看", use_container_width=True):
        st.switch_page("pages/03_报告查看.py")

with col_d:
    if st.button("🔍 案例检索", use_container_width=True):
        st.switch_page("pages/04_案例检索.py")

with col_e:
    if st.button("⚙️ 专题配置", use_container_width=True):
        st.switch_page("pages/05_专题配置.py")

st.divider()

st.subheader("📈 最近活动")

# 示例数据
activities = [
    {"time": "22:30", "action": "任务 #task_abc 完成", "type": "success"},
    {"time": "22:15", "action": "新建任务 #task_def", "type": "info"},
    {"time": "21:50", "action": "任务 #task_xyz 失败", "type": "error"},
]

for act in activities:
    emoji = {"success": "✅", "info": "ℹ️", "error": "❌"}.get(act["type"], "•")
    st.text(f"{emoji} [{act['time']}] {act['action']}")

st.caption("---")
st.caption(f"🧢 Sona GUI | API: {API_BASE} | 任务20")
