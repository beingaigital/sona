"""原根目录完整 Streamlit 会话（聊天 / 事件 / 热点）。"""

from __future__ import annotations

import streamlit as st

from streamlit_ui_theme import inject_ui_theme, render_nav_sidebar
from streamlit_legacy_chat import run_legacy_chat

st.set_page_config(page_title="经典会话", page_icon="◼", layout="wide")

inject_ui_theme()
render_nav_sidebar("legacy")

run_legacy_chat()
