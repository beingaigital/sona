"""Sona Streamlit 外观层（借鉴 BettaFish「微舆」黑白高对比布局）。

仅用于 GUI：不得从 workflow / api / tools 等业务包反向引用本模块。
"""

from __future__ import annotations

import os
from html import escape

import requests
import streamlit as st

# BettaFish templates/index.html：白底、2px 黑框、按钮反色、硬阴影
SONA_BF_CSS = """
<style>
    html, body,
    .stApp, [data-testid="stAppViewContainer"],
    [data-testid="stMarkdownContainer"] p, .stSelectbox label, label {
        font-family: system-ui, -apple-system, "Segoe UI", "Helvetica Neue", Arial, sans-serif !important;
    }

    [data-testid="stAppViewContainer"] > .main {
        background: linear-gradient(165deg, #f4f4f1 0%, #fafafa 42%, #ececea 100%);
    }

    [data-testid="stHeader"] {
        background: rgba(255, 255, 255, 0.92) !important;
        border-bottom: 2px solid #111111 !important;
    }

    [data-testid="stSidebar"] {
        background: #ffffff !important;
        border-right: 2px solid #111111 !important;
    }
    [data-testid="stSidebar"] > div:first-child {
        padding-top: 0.5rem;
    }

    [data-testid="stMetric"] {
        background: #ffffff !important;
        border: 2px solid #111111 !important;
        border-radius: 0 !important;
        padding: 0.85rem 1rem 0.65rem !important;
        box-shadow: 5px 5px 0 #111111 !important;
    }
    [data-testid="stMetricLabel"] {
        font-size: 0.72rem !important;
        letter-spacing: 0.08em;
        text-transform: uppercase;
        color: #555555 !important;
    }
    [data-testid="stMetricValue"] {
        color: #111111 !important;
        font-weight: 800 !important;
    }

    div[data-testid="stVerticalBlockBorderWrapper"] {
        border: 2px solid #111111 !important;
        border-radius: 0 !important;
        background: #ffffff !important;
        box-shadow: 4px 4px 0 rgba(0, 0, 0, 0.12) !important;
        padding: 0.35rem 0.5rem !important;
    }

    [data-testid="stExpander"] {
        border: 2px solid #111111 !important;
        border-radius: 0 !important;
        background: #ffffff !important;
        box-shadow: 3px 3px 0 #cccccc !important;
    }

    [data-testid="stForm"] {
        border: 2px solid #111111 !important;
        border-radius: 0 !important;
        background: #ffffff !important;
        padding: 1rem 1.1rem 1.2rem !important;
        box-shadow: 7px 7px 0 rgba(17, 17, 17, 0.14) !important;
    }

    .stButton > button {
        border-radius: 0 !important;
        border: 2px solid #111111 !important;
        font-weight: 600 !important;
        transition: background 0.15s ease, color 0.15s ease, transform 0.1s ease !important;
    }
    .stButton > button:hover {
        transform: translate(-1px, -1px);
        box-shadow: 3px 3px 0 #111111 !important;
    }
    .stButton > button[kind="primary"],
    div[data-testid="stBaseButton-primary"] button {
        background-color: #111111 !important;
        color: #fafafa !important;
    }
    .stButton > button[kind="primary"]:hover,
    div[data-testid="stBaseButton-primary"] button:hover {
        background-color: #333333 !important;
        color: #ffffff !important;
    }
    .stButton > button[kind="secondary"],
    div[data-testid="stBaseButton-secondary"] button {
        background-color: #ffffff !important;
        color: #111111 !important;
    }
    .stButton > button[kind="secondary"]:hover,
    div[data-testid="stBaseButton-secondary"] button:hover {
        background-color: #111111 !important;
        color: #ffffff !important;
    }

    [data-baseweb="tab-list"] {
        border-bottom: 2px solid #111111 !important;
        gap: 0 !important;
    }
    [data-baseweb="tab"] {
        border: 2px solid #111111 !important;
        border-bottom: none !important;
        border-radius: 0 !important;
        font-weight: 600 !important;
    }

    [data-testid="stTextInput"] input,
    [data-testid="stTextArea"] textarea {
        border-radius: 0 !important;
        border: 2px solid #111111 !important;
    }

    div[data-testid="stDecoration"] { display: none; }
    footer { visibility: hidden; height: 0; }

    [data-testid="stAlert"] {
        border-radius: 0 !important;
        border: 2px solid #111111 !important;
    }
</style>
"""

_API_BASE = os.environ.get("API_BASE", "http://127.0.0.1:8765")


def get_api_base() -> str:
    """当前 Streamlit 使用的 API 基址（与侧栏展示一致）。"""
    return _API_BASE


def is_api_reachable(base: str | None = None) -> bool:
    """探活 GET {base}/health，供子页在表单前提示。"""
    root = (base or _API_BASE).rstrip("/")
    try:
        r = requests.get(f"{root}/health", timeout=2.5)
        return r.status_code == 200
    except Exception:
        return False

_NAV: list[tuple[str, str, str]] = [
    ("home", "仪表盘", "streamlit_app.py"),
    ("tasks", "任务状态", "pages/01_任务状态.py"),
    ("new", "新建任务", "pages/02_新建任务.py"),
    ("report", "报告查看", "pages/03_报告查看.py"),
    ("cases", "案例检索", "pages/04_案例检索.py"),
    ("topics", "专题配置", "pages/05_专题配置.py"),
    ("legacy", "经典会话", "pages/99_经典会话与路由.py"),
]


def inject_ui_theme() -> None:
    """注入全局 CSS（每个页面在 set_page_config 之后调用一次）。"""
    st.markdown(SONA_BF_CSS, unsafe_allow_html=True)


def render_nav_sidebar(current: str) -> None:
    """自定义侧栏导航（需在 .streamlit/config.toml 关闭默认多页导航）。"""
    with st.sidebar:
        st.markdown(
            '<div style="border:2px solid #111;background:#fff;padding:12px 14px;margin-bottom:12px;'
            'box-shadow:4px 4px 0 #111;"><div style="font-weight:800;font-size:1.15rem;color:#111;">Sona</div>'
            '<div style="font-size:0.72rem;letter-spacing:0.14em;color:#555;margin-top:4px;">分析员控制台</div></div>',
            unsafe_allow_html=True,
        )
        st.caption(f"API · {escape(_API_BASE)}")
        st.divider()
        for key, label, page in _NAV:
            if key == current:
                st.markdown(f'<p style="margin:6px 0;font-weight:700;color:#111;">▸ {label}</p>', unsafe_allow_html=True)
            else:
                if st.button(label, key=f"sona_nav_{key}", use_container_width=True):
                    st.switch_page(page)
        st.divider()
        st.caption("面向分析员：重任务走 API + 工作流；本 GUI 不替代完整舆情监测系统。")


def hero_panel(*, kicker: str, title: str, subtitle: str) -> None:
    """首页 / 页头大色块。"""
    k, t, s = escape(kicker), escape(title), escape(subtitle)
    st.markdown(
        f"""
        <div style="border:2px solid #111;background:#fff;padding:22px 26px;margin-bottom:18px;
                    box-shadow:8px 8px 0 #111;">
            <div style="font-size:11px;letter-spacing:0.18em;text-transform:uppercase;color:#666;">{k}</div>
            <div style="font-size:1.75rem;font-weight:800;color:#111;margin-top:6px;line-height:1.2;">{t}</div>
            <div style="font-size:0.95rem;color:#444;margin-top:10px;max-width:820px;line-height:1.55;">{s}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def page_header(title: str, caption: str = "") -> None:
    """子页顶部窄条标题（与首页 hero 同系）。"""
    t = escape(title)
    cap = (
        f'<div style="font-size:0.85rem;color:#555;margin-top:6px;">{escape(caption)}</div>'
        if caption
        else ""
    )
    st.markdown(
        f"""
        <div style="border:2px solid #111;background:#fff;padding:14px 18px;margin-bottom:14px;
                    box-shadow:5px 5px 0 #111;">
            <div style="font-size:1.35rem;font-weight:800;color:#111;">{t}</div>
            {cap}
        </div>
        """,
        unsafe_allow_html=True,
    )


def _callout_box(*, border: str, bg: str, shadow: str, title: str, body: str) -> None:
    t, b = escape(title), escape(body).replace("\n", "<br/>")
    st.markdown(
        f"""
        <div style="border:2px solid {border};background:{bg};padding:14px 16px;margin:10px 0;
                    box-shadow:5px 5px 0 {shadow};max-width:920px;">
            <div style="font-weight:800;color:#111;font-size:0.95rem;">{t}</div>
            <div style="margin-top:8px;color:#333;font-size:0.9rem;line-height:1.6;">{b}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def callout_error(title: str, body: str) -> None:
    """API 离线、任务失败等强提示。"""
    _callout_box(border="#8b1538", bg="#fff5f7", shadow="#8b1538", title=title, body=body)


def callout_success(title: str, body: str) -> None:
    """API 正常等正向提示。"""
    _callout_box(border="#1e5f3f", bg="#f4faf6", shadow="#1e5f3f", title=title, body=body)


def callout_neutral(title: str, body: str) -> None:
    """空列表、说明性提示。"""
    _callout_box(border="#444444", bg="#ffffff", shadow="#cccccc", title=title, body=body)
