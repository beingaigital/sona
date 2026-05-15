"""从用户自然语言中推断热点相关回溯小时数，并写入环境变量供热点流程读取。

``streamlit_legacy_chat`` 在跑 /hot 或路由到热点前会调用本模块；若未匹配到显式时间，
则使用 ``SONA_HOT_DEFAULT_LOOKBACK_HOURS``（默认 24）或保持已有 ``SONA_HOT_INFERRED_LOOKBACK_HOURS``。
"""

from __future__ import annotations

import os
import re
from typing import Optional

_DEFAULT_HOURS = 24
_MAX_HOURS = 24 * 30  # 约一个月上限，避免误解析出极端值


def _env_int(name: str, default: int, low: int, high: int) -> int:
    raw = str(os.environ.get(name, str(default))).strip()
    try:
        v = int(raw)
    except ValueError:
        v = default
    return max(low, min(high, v))


def infer_hot_lookback_hours(user_input: str) -> int:
    """
    从用户输入中推断「热点/历史快照」相关的小时数。

    支持示例：「48小时」「24h」「三天」「一周」「7天」「两周」「最近72小时」。
    未匹配时返回 ``SONA_HOT_DEFAULT_LOOKBACK_HOURS``（默认 24）。
    """
    text = (user_input or "").strip().lower()
    if not text:
        return _env_int("SONA_HOT_DEFAULT_LOOKBACK_HOURS", _DEFAULT_HOURS, 1, _MAX_HOURS)

    # 阿拉伯数字 + 小时 / h
    m = re.search(r"(\d+)\s*(小时|个小时|h|hr|hrs|hours?)", text, re.I)
    if m:
        return max(1, min(_MAX_HOURS, int(m.group(1))))

    m = re.search(r"(\d+)\s*天", text)
    if m:
        return max(1, min(_MAX_HOURS, int(m.group(1)) * 24))

    if "两周" in text or "2周" in text or "十四天" in text:
        return min(_MAX_HOURS, 14 * 24)
    if "一周" in text or "1周" in text or "七天" in text or "7天" in text:
        return min(_MAX_HOURS, 7 * 24)
    if "三天" in text or "3天" in text:
        return min(_MAX_HOURS, 3 * 24)
    if "两天" in text or "2天" in text:
        return min(_MAX_HOURS, 2 * 24)

    m = re.search(r"最近\s*(\d+)\s*小时", text)
    if m:
        return max(1, min(_MAX_HOURS, int(m.group(1))))

    return _env_int("SONA_HOT_DEFAULT_LOOKBACK_HOURS", _DEFAULT_HOURS, 1, _MAX_HOURS)


def apply_hot_lookback_hours(hours: int) -> None:
    """
    将推断小时数写入环境变量。

    - ``SONA_HOT_INFERRED_LOOKBACK_HOURS``：供调试与未来 ``hottopics`` 扩展读取。
    - ``HOT_FALLBACK_LOOKBACK_HOURS``：与 ``tools/hottopics.py`` 中 fallback 逻辑对齐，
      仅在需要拉长历史窗口时使用（取 max(24, hours) 与现有实现一致的下界）。
    """
    h = max(1, min(_MAX_HOURS, int(hours)))
    os.environ["SONA_HOT_INFERRED_LOOKBACK_HOURS"] = str(h)
    # hottopics 使用 max(24, env)，故用户若推断 12h 仍会得到 24 的下限，与现网行为一致
    os.environ["HOT_FALLBACK_LOOKBACK_HOURS"] = str(max(24, h))


def read_applied_lookback_hours() -> Optional[int]:
    """读取最近一次 ``apply_hot_lookback_hours`` 写入的推断值（若存在）。"""
    raw = os.environ.get("SONA_HOT_INFERRED_LOOKBACK_HOURS", "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        return None
