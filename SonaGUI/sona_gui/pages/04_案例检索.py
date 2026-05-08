import streamlit as st
import json
import yaml

st.set_page_config(page_title="案例检索", page_icon="🔍")

st.title("🔍 案例检索")

st.info("历史案例库查询，支持关键词搜索和标签筛选")

# 搜索栏
col1, col2 = st.columns([3, 1])
with col1:
    search_query = st.text_input("搜索关键词", placeholder="输入事件名称、品牌名或关键词...")
with col2:
    search_type = st.selectbox("搜索范围", ["全部", "标题", "内容", "标签"])

# 高级筛选
with st.expander("高级筛选"):
    col_a, col_b, col_c = st.columns(3)
    with col_a:
        date_range = st.date_input("时间范围", [])
    with col_b:
        sentiment = st.selectbox("情感倾向", ["全部", "正面", "负面", "中性"])
    with col_c:
        topic_filter = st.multiselect(
            "专题分类",
            ["品牌舆情", "竞品分析", "危机公关", "热点追踪", "政策解读"]
        )

if st.button("🔍 搜索", type="primary"):
    with st.spinner("检索中..."):
        # 演示数据
        mock_cases = [
            {
                "id": "case_001",
                "title": "某品牌315曝光事件分析",
                "date": "2024-03-15",
                "topic": "危机公关",
                "sentiment": "负面",
                "summary": "315晚会曝光后的24小时舆情演变分析...",
                "tags": ["315", "食品安全", "品牌危机"]
            },
            {
                "id": "case_002", 
                "title": "某明星代言争议分析",
                "date": "2024-02-20",
                "topic": "品牌舆情",
                "sentiment": "中性",
                "summary": "代言人风波对品牌影响的多维度评估...",
                "tags": ["明星代言", "品牌形象"]
            },
            {
                "id": "case_003",
                "title": "某竞品新品发布监测",
                "date": "2024-01-10", 
                "topic": "竞品分析",
                "sentiment": "正面",
                "summary": "竞品新品上市期间的社交媒体反应追踪...",
                "tags": ["竞品", "新品发布"]
            }
        ]
        
        st.session_state.search_results = mock_cases
        st.success(f"找到 {len(mock_cases)} 个相关案例")

# 显示结果
if "search_results" in st.session_state:
    for case in st.session_state.search_results:
        with st.container(border=True):
            col1, col2 = st.columns([4, 1])
            
            with col1:
                st.write(f"**{case['title']}**")
                st.caption(f"📅 {case['date']} | 🏷️ {case['topic']} | 😐 {case['sentiment']}")
                st.write(case['summary'])
                st.write(" ".join([f"`{tag}`" for tag in case['tags']]))
            
            with col2:
                if st.button("查看详情", key=f"detail_{case['id']}"):
                    st.info(f"查看案例: {case['id']}")
                
                if st.button("以此为模板", key=f"use_{case['id']}"):
                    st.session_state.template_case = case
                    st.switch_page("02_新建任务.py")

st.divider()
st.caption("💡 提示：选择「以此为模板」可快速创建相似分析任务")
