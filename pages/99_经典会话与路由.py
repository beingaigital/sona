"""原根目录完整 Streamlit 会话（聊天 / 事件 / 热点）。"""

from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

st.set_page_config(page_title="经典会话", page_icon="◼", layout="wide")

# 多页应用下保证项目根在 sys.path（须早于 streamlit_legacy_chat 等包内导入）
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from streamlit_ui_theme import inject_ui_theme, render_nav_sidebar
from streamlit_legacy_chat import run_legacy_chat

inject_ui_theme()
render_nav_sidebar("legacy")

run_legacy_chat()
