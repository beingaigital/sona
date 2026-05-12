"""案例检索（演示数据；后续可接 wiki / 案例库 API）。"""

from __future__ import annotations

import streamlit as st

from streamlit_ui_theme import inject_ui_theme, page_header, render_nav_sidebar

st.set_page_config(page_title="案例检索", page_icon="◼", layout="wide")

inject_ui_theme()
render_nav_sidebar("cases")

page_header("案例检索", "当前为演示数据；与升级计划案例库对接后可改为真实检索。")

st.text_input("关键词（演示未过滤）", placeholder="事件名、品牌…")

if st.button("搜索", type="primary"):
    mock_cases = [
        {
            "id": "case_001",
            "title": "某品牌 315 曝光事件分析",
            "date": "2024-03-15",
            "topic": "危机公关",
            "sentiment": "负面",
            "summary": "315 曝光后的舆情演变示例…",
            "tags": ["315", "食品安全"],
        },
        {
            "id": "case_002",
            "title": "竞品新品发布监测",
            "date": "2024-01-10",
            "topic": "竞品分析",
            "sentiment": "中性",
            "summary": "新品上市期间社媒反应示例…",
            "tags": ["竞品", "新品"],
        },
    ]
    st.session_state.search_results = mock_cases
    st.success(f"找到 {len(mock_cases)} 条（演示）")

if "search_results" in st.session_state:
    for case in st.session_state.search_results:
        with st.container(border=True):
            st.markdown(f"**{case['title']}**")
            st.caption(f"{case['date']} | {case['topic']} | {case['sentiment']}")
            st.write(case["summary"])
            if st.button("以此为模板", key=f"use_{case['id']}"):
                st.session_state.template_hint = case.get("title", "")
                st.switch_page("pages/02_新建任务.py")
