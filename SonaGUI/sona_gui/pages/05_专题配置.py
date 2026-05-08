import streamlit as st
import json
import yaml
import os

st.set_page_config(page_title="专题配置", page_icon="⚙️")

st.title("⚙️ 专题配置")

st.info("配置分析专题参数、关键词库、数据源等")

CONFIG_FILE = "config/topics.yaml"

# 默认配置
DEFAULT_CONFIG = {
    "topics": [
        {
            "name": "品牌舆情",
            "keywords": ["品牌名", "产品质量", "客服"],
            "sources": ["weibo", "xiaohongshu", "zhihu"],
            "alert_threshold": 0.7
        },
        {
            "name": "竞品监控", 
            "keywords": ["竞品A", "竞品B"],
            "sources": ["news", "weibo"],
            "alert_threshold": 0.5
        }
    ],
    "global_settings": {
        "default_depth": "standard",
        "max_concurrent_tasks": 3,
        "report_format": "html"
    }
}

# 加载配置
def load_config():
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            return yaml.safe_load(f)
    return DEFAULT_CONFIG

# 保存配置
def save_config(config):
    os.makedirs(os.path.dirname(CONFIG_FILE), exist_ok=True)
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        yaml.dump(config, f, allow_unicode=True, sort_keys=False)

# 初始化配置
if "config" not in st.session_state:
    st.session_state.config = load_config()

config = st.session_state.config

# 全局设置
with st.expander("全局设置", expanded=True):
    col1, col2, col3 = st.columns(3)
    with col1:
        config["global_settings"]["default_depth"] = st.selectbox(
            "默认分析深度",
            ["quick", "standard", "deep"],
            index=["quick", "standard", "deep"].index(config["global_settings"]["default_depth"])
        )
    with col2:
        config["global_settings"]["max_concurrent_tasks"] = st.number_input(
            "最大并发任务数",
            min_value=1,
            max_value=10,
            value=config["global_settings"]["max_concurrent_tasks"]
        )
    with col3:
        config["global_settings"]["report_format"] = st.selectbox(
            "报告格式",
            ["html", "markdown", "pdf"],
            index=["html", "markdown", "pdf"].index(config["global_settings"]["report_format"])
        )

st.divider()

# 专题列表
st.subheader("📂 专题列表")

for i, topic in enumerate(config["topics"]):
    with st.container(border=True):
        col1, col2 = st.columns([4, 1])
        
        with col1:
            topic["name"] = st.text_input(f"专题名称 #{i+1}", topic["name"], key=f"topic_name_{i}")
            
            keywords_str = st.text_area(
                f"关键词 #{i+1}",
                ", ".join(topic["keywords"]),
                key=f"topic_kw_{i}",
                height=60
            )
            topic["keywords"] = [k.strip() for k in keywords_str.split(",") if k.strip()]
            
            col_a, col_b = st.columns(2)
            with col_a:
                topic["sources"] = st.multiselect(
                    f"数据源 #{i+1}",
                    ["weibo", "xiaohongshu", "zhihu", "news", "douyin", "bilibili"],
                    default=topic.get("sources", []),
                    key=f"topic_src_{i}"
                )
            with col_b:
                topic["alert_threshold"] = st.slider(
                    f"预警阈值 #{i+1}",
                    0.0, 1.0, topic.get("alert_threshold", 0.5),
                    key=f"topic_thresh_{i}"
                )
        
        with col2:
            if st.button("🗑️ 删除", key=f"del_{i}"):
                config["topics"].pop(i)
                st.rerun()

# 添加新专题
if st.button("➕ 添加专题"):
    config["topics"].append({
        "name": "新专题",
        "keywords": [],
        "sources": [],
        "alert_threshold": 0.5
    })
    st.rerun()

st.divider()

# 保存按钮
if st.button("💾 保存配置", type="primary"):
    save_config(config)
    st.success("✅ 配置已保存到 config/topics.yaml")

# 预览配置
with st.expander("预览配置 YAML"):
    st.code(yaml.dump(config, allow_unicode=True), language="yaml")
