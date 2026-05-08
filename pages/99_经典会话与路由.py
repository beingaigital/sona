"""原根目录完整 Streamlit 会话（聊天 / 事件 / 热点）。"""

from __future__ import annotations

import streamlit as st

st.set_page_config(page_title="经典会话", page_icon="🧠", layout="wide")

from streamlit_legacy_chat import run_legacy_chat

run_legacy_chat()
