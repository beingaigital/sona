import streamlit as st
import requests
import json
from datetime import datetime

st.set_page_config(page_title="任务状态", page_icon="📋")

st.title("📋 任务状态")

import os
API_BASE = os.getenv("API_BASE", "http://127.0.0.1:8765")

def get_tasks():
    # 从任务19 API获取任务列表
    try:
        # 任务19已实现: GET /v1/tasks/{task_id}
        # 这里模拟多个任务（实际需要列表端点或遍历已知ID）
        r = requests.get(f"{API_BASE}/health", timeout=5)
        if r.status_code == 200:
            # 演示数据
            return [
                {"id": "task_001", "name": "某某品牌舆情分析", "status": "running", "created_at": "2026-05-07 21:00", "progress": 65},
                {"id": "task_002", "name": "竞品事件追踪", "status": "completed", "created_at": "2026-05-07 20:30", "progress": 100},
                {"id": "task_003", "name": "热点话题监测", "status": "pending", "created_at": "2026-05-07 22:00", "progress": 0},
                {"id": "task_004", "name": "负面舆情预警", "status": "failed", "created_at": "2026-05-07 19:00", "progress": 30},
            ]
    except:
        pass
    return []

# 筛选
filter_status = st.selectbox("状态筛选", ["全部", "运行中", "已完成", "等待中", "失败"])

# 刷新按钮
col1, col2 = st.columns([1, 6])
with col1:
    if st.button("🔄 刷新", type="primary"):
        st.rerun()
with col2:
    if st.button("➕ 新建任务"):
        st.switch_page("02_新建任务.py")

tasks = get_tasks()

if not tasks:
    st.info("暂无任务，点击上方「新建任务」开始")
else:
    # 统计卡片
    cols = st.columns(4)
    status_count = {"running": 0, "completed": 0, "pending": 0, "failed": 0}
    for t in tasks:
        status_count[t.get("status", ""), 0] += 1
    
    cols[0].metric("运行中", sum(1 for t in tasks if t["status"] == "running"), "⚡")
    cols[1].metric("已完成", sum(1 for t in tasks if t["status"] == "completed"), "✅")
    cols[2].metric("等待中", sum(1 for t in tasks if t["status"] == "pending"), "⏳")
    cols[3].metric("失败", sum(1 for t in tasks if t["status"] == "failed"), "❌")
    
    st.divider()
    
    # 任务列表
    for task in tasks:
        if filter_status != "全部":
            status_map = {"运行中": "running", "已完成": "completed", "等待中": "pending", "失败": "failed"}
            if task["status"] != status_map.get(filter_status):
                continue
        
        with st.container(border=True):
            col1, col2, col3, col4 = st.columns([3, 1, 1, 1])
            
            with col1:
                st.write(f"**{task['name']}**")
                st.caption(f"ID: {task['id']} | 创建: {task['created_at']}")
            
            with col2:
                status_emoji = {
                    "running": "⚡ 运行中",
                    "completed": "✅ 已完成", 
                    "pending": "⏳ 等待中",
                    "failed": "❌ 失败"
                }.get(task["status"], task["status"])
                st.write(status_emoji)
            
            with col3:
                if task["status"] == "running":
                    st.progress(task.get("progress", 0) / 100, text=f"{task.get('progress', 0)}%")
            
            with col4:
                if st.button("查看", key=f"view_{task['id']}"):
                    st.session_state.current_task = task
                    st.switch_page("03_报告查看.py")
