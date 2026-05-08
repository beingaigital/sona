"""专题 YAML 配置（轻量编辑，写入项目 config/topics.yaml）。"""

from __future__ import annotations

import streamlit as st
import yaml

from utils.path import get_project_root

st.set_page_config(page_title="专题配置", page_icon="⚙️", layout="wide")

CONFIG_FILE = get_project_root() / "config" / "topics.yaml"

DEFAULT_CONFIG = {
    "topics": [
        {
            "name": "品牌舆情",
            "keywords": ["品牌", "客服"],
            "sources": ["weibo", "zhihu"],
            "alert_threshold": 0.7,
        }
    ],
    "global_settings": {
        "default_depth": "standard",
        "max_concurrent_tasks": 3,
        "report_format": "html",
    },
}


def load_config() -> dict:
    if CONFIG_FILE.is_file():
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
            return data if isinstance(data, dict) else DEFAULT_CONFIG
    return DEFAULT_CONFIG.copy()


def save_config(config: dict) -> None:
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        yaml.dump(config, f, allow_unicode=True, sort_keys=False)


if "topics_gui_config" not in st.session_state:
    st.session_state.topics_gui_config = load_config()

config = st.session_state.topics_gui_config

st.title("专题配置")
st.caption(str(CONFIG_FILE))

gs = config.setdefault("global_settings", DEFAULT_CONFIG["global_settings"].copy())
topics = config.setdefault("topics", [])

with st.expander("全局设置", expanded=True):
    c1, c2, c3 = st.columns(3)
    depth_opts = ["quick", "standard", "deep"]
    gs["default_depth"] = c1.selectbox(
        "默认分析深度",
        depth_opts,
        index=depth_opts.index(gs.get("default_depth", "standard")),
    )
    gs["max_concurrent_tasks"] = int(
        c2.number_input("最大并发任务数", min_value=1, max_value=10, value=int(gs.get("max_concurrent_tasks", 3)))
    )
    fmt_opts = ["html", "markdown", "pdf"]
    gs["report_format"] = c3.selectbox(
        "报告格式",
        fmt_opts,
        index=fmt_opts.index(gs.get("report_format", "html")),
    )

st.divider()
st.subheader("专题列表")

for i, topic in enumerate(list(topics)):
    with st.container(border=True):
        topic["name"] = st.text_input(f"名称 #{i+1}", topic.get("name", ""), key=f"tn_{i}")
        kw = st.text_area(
            "关键词（逗号分隔）",
            ", ".join(topic.get("keywords", [])),
            key=f"tk_{i}",
            height=60,
        )
        topic["keywords"] = [k.strip() for k in kw.split(",") if k.strip()]
        topic["sources"] = st.multiselect(
            "数据源",
            ["weibo", "xiaohongshu", "zhihu", "news", "douyin", "bilibili"],
            default=topic.get("sources", []),
            key=f"ts_{i}",
        )
        topic["alert_threshold"] = st.slider("预警阈值", 0.0, 1.0, float(topic.get("alert_threshold", 0.5)), key=f"tt_{i}")
        if st.button("删除", key=f"td_{i}"):
            topics.pop(i)
            st.rerun()

if st.button("添加专题"):
    topics.append({"name": "新专题", "keywords": [], "sources": [], "alert_threshold": 0.5})
    st.rerun()

if st.button("保存到磁盘", type="primary"):
    save_config(config)
    st.success("已写入 config/topics.yaml")

with st.expander("YAML 预览"):
    st.code(yaml.dump(config, allow_unicode=True), language="yaml")
