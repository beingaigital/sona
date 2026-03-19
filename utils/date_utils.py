"""日期工具：获取当前日期、时间等。"""

from __future__ import annotations

from datetime import date, datetime, timedelta


def get_today_str(date_format: str = "%Y-%m-%d") -> str:
    """
    获取今天的日期字符串。
    
    Args:
        date_format: 日期格式字符串，默认为 "%Y-%m-%d"（如 "2025-01-15"）
    
    Returns:
        格式化后的日期字符串
    """
    return date.today().strftime(date_format)


def get_yesterday_end() -> datetime:
    """
    获取昨天最后一刻的时间（23:59:59）。
    
    Returns:
        昨天 23:59:59 的 datetime 对象
    """
    yesterday = date.today() - timedelta(days=1)
    return datetime.combine(yesterday, datetime.max.time())
