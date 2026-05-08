import streamlit as st
import requests
import json

st.set_page_config(page_title="报告查看", page_icon="📄")

st.title("📄 报告查看")

import os
API_BASE = os.getenv("API_BASE", "http://127.0.0.1:8765")

# 任务选择
task_id = st.session_state.get("current_task_id", "")

if not task_id:
    # 手动输入或选择
    task_id = st.text_input("输入任务ID", placeholder="task_xxx")

if not task_id:
    st.info("请在「任务状态」页面选择一个任务查看，或输入任务ID")
    if st.button("📋 前往任务列表"):
        st.switch_page("01_任务状态.py")
    st.stop()

# 获取任务详情
with st.spinner("加载中..."):
    try:
        r = requests.get(f"{API_BASE}/v1/tasks/{task_id}", timeout=10)
        if r.status_code == 200:
            task_info = r.json()
        else:
            task_info = None
            st.error(f"获取任务失败: {r.status_code}")
    except Exception as e:
        task_info = None
        st.error(f"请求异常: {e}")

if task_info:
    # 任务信息卡片
    col1, col2, col3 = st.columns(3)
    col1.metric("状态", task_info.get("status", "unknown"))
    col2.metric("创建时间", task_info.get("created_at", "-")[:16] if task_info.get("created_at") else "-")
    col3.metric("耗时", f"{task_info.get('duration_seconds', 0)//60} 分钟")
    
    st.divider()
    
    # 报告内容
    if task_info.get("status") == "completed":
        st.subheader("📑 分析报告")
        
        # 尝试获取HTML报告
        try:
            r = requests.get(f"{API_BASE}/v1/tasks/{task_id}/report", timeout=10)
            if r.status_code == 200:
                report_html = r.text
                
                tab1, tab2 = st.tabs(["📊 渲染视图", "📝 原始HTML"])
                
                with tab1:
                    st.components.v1.html(report_html, height=600, scrolling=True)
                
                with tab2:
                    st.code(report_html, language="html")
                
                # 下载按钮
                st.download_button(
                    label="⬇️ 下载报告 HTML",
                    data=report_html,
                    file_name=f"report_{task_id}.html",
                    mime="text/html"
                )
            else:
                st.warning("报告尚未生成或获取失败")
        except Exception as e:
            st.error(f"获取报告异常: {e}")
    
    elif task_info.get("status") == "running":
        st.info("⏳ 任务正在运行中，请稍后刷新查看报告")
        progress = task_info.get("progress", 50)
        st.progress(progress / 100, text=f"进度: {progress}%")
        if st.button("🔄 刷新状态"):
            st.rerun()
    
    elif task_info.get("status") == "failed":
        st.error("❌ 任务执行失败")
        st.code(task_info.get("error", "未知错误"))
    
    else:
        st.info(f"当前状态: {task_info.get('status')}")
