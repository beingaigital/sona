import streamlit as st
import requests
import json

st.set_page_config(page_title="新建任务", page_icon="➕")

st.title("➕ 新建分析任务")

st.info("调用任务19 API: POST /v1/analyze-event", icon="🔗")

import os
API_BASE = os.getenv("API_BASE", "http://127.0.0.1:8765")

with st.form("new_task_form"):
    st.subheader("任务配置")
    
    event_name = st.text_input("任务名称 *", placeholder="例如：某某品牌315舆情分析")
    
    event_desc = st.text_area(
        "事件描述 *", 
        placeholder="描述需要分析的舆情事件...",
        height=100
    )
    
    col1, col2 = st.columns(2)
    with col1:
        analysis_depth = st.selectbox(
            "分析深度",
            ["standard", "deep", "quick"],
            format_func=lambda x: {"standard": "标准", "deep": "深度", "quick": "快速"}[x]
        )
    with col2:
        topic = st.selectbox(
            "所属专题",
            ["舆情分析", "竞品监控", "热点追踪", "危机预警"]
        )
    
    callback_url = st.text_input(
        "回调 URL（可选）",
        placeholder="https://your-system.com/webhook",
        help="任务完成后会发送通知到此地址"
    )
    
    st.divider()
    
    col_submit, col_cancel = st.columns([1, 1])
    
    with col_submit:
        submitted = st.form_submit_button("🚀 创建任务", type="primary", use_container_width=True)
    
    with col_cancel:
        if st.form_submit_button("❌ 取消", use_container_width=True):
            st.switch_page("01_任务状态.py")

if submitted:
    if not event_name or not event_desc:
        st.error("请填写任务名称和事件描述")
    else:
        # 调用任务19 API
        payload = {
            "event_description": event_desc,
            "depth": analysis_depth,
            "callback_url": callback_url if callback_url else None,
            "metadata": {"name": event_name, "topic": topic}
        }
        
        with st.spinner("正在创建任务..."):
            try:
                r = requests.post(
                    f"{API_BASE}/v1/analyze-event",
                    json=payload,
                    timeout=30
                )
                
                if r.status_code in (200, 201):
                    result = r.json()
                    task_id = result.get("task_id", "unknown")
                    
                    st.success(f"✅ 任务创建成功！")
                    st.code(f"任务ID: {task_id}", language="text")
                    
                    col1, col2 = st.columns(2)
                    with col1:
                        if st.button("📋 查看任务列表"):
                            st.switch_page("01_任务状态.py")
                    with col2:
                        if st.button("📄 查看报告"):
                            st.session_state.current_task_id = task_id
                            st.switch_page("03_报告查看.py")
                else:
                    st.error(f"创建失败: {r.status_code} - {r.text}")
            except Exception as e:
                st.error(f"请求异常: {e}")
                st.info("请确保任务19 API已启动: sona serve --port 8765")
