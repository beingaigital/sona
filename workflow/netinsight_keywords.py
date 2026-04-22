"""NetInsight 检索词模式与 data_num 入参构建（从 CLI 迁移，供 workflow / CLI 共用）。"""

from __future__ import annotations

import os
import re
from typing import Any, Dict, List, Tuple

NETINSIGHT_PLATFORMS: List[str] = [
    "新闻网站",
    "新闻app",
    "视频",
    "微博",
    "微信",
    "自媒体号",
    "论坛",
    "电子报",
    "境外新闻",
    "Twitter",
    "Facebook",
]


def looks_like_netinsight_advanced_expression(text: str) -> bool:
    """
    启发式：是否像 NetInsight 高级表达式（含 + | ( ) 或「-词」排除）。
    在确认 data_collect 对 keyWords 高级语法完全兼容前，仅作自动切换参考。
    """
    t = str(text or "").strip()
    if not t:
        return False
    if re.search(r"[+|()]", t):
        return True
    if re.search(r"-\s*[\u4e00-\u9fffA-Za-z0-9#·]", t):
        return True
    return False


def effective_keyword_mode(search_plan: Dict[str, Any], *, joined_query: str) -> str:
    """
    解析最终 keywordMode（advanced / normal）。

    优先级：
    1) search_plan[\"netinsightKeywordMode\"] 显式 advanced|normal
    2) 环境变量 SONA_NETINSIGHT_KEYWORD_MODE（兼容旧名）
    3) SONA_NETINSIGHT_DEFAULT_KEYWORD_MODE=advanced：仅在显式开启时才默认高级（不做自动推断）
    4) 默认 normal
    """
    explicit = str(search_plan.get("netinsightKeywordMode", "")).strip().lower()
    if explicit in ("advanced", "adv", "expr", "expression"):
        return "advanced"
    if explicit in ("normal", "default"):
        return "normal"

    env_legacy = str(os.environ.get("SONA_NETINSIGHT_KEYWORD_MODE", "")).strip().lower()
    if env_legacy in ("advanced", "adv", "expr"):
        return "advanced"
    if env_legacy in ("normal", "default"):
        return "normal"

    default_mode = str(os.environ.get("SONA_NETINSIGHT_DEFAULT_KEYWORD_MODE", "normal")).strip().lower()
    if default_mode in ("advanced", "adv"):
        return "advanced"

    return "normal"


def build_data_num_search_words(
    search_plan: Dict[str, Any],
    search_words_for_collect: List[str],
) -> Tuple[List[str], str]:
    """
    返回 (传给 data_num 的 JSON 列表, keywordMode)。

    - advanced：优先 netinsightAdvancedQuery；否则在 effective 为 advanced 且合并串像高级式时用合并串。
    - normal：多词合并为单串，分号为「或」（与 NetInsight 普通模式一致）。
    """
    parts = [str(x).strip() for x in (search_words_for_collect or []) if str(x).strip()]
    # normal 模式：为了稳定拿到数据，允许把单个“长串”按常见分隔符切分成多词，再用分号 OR 合并。
    # 例如：["大学校 高铁 骂 熊孩子"] -> ["大学校", "高铁", "骂", "熊孩子"]
    if len(parts) == 1:
        maybe = parts[0]
        if re.search(r"[;；,\s]+", maybe):
            split_parts = [p.strip() for p in re.split(r"[;；,\s]+", maybe) if p.strip()]
            if split_parts:
                parts = split_parts
    joined = ";".join(parts) if parts else ""
    km = effective_keyword_mode(search_plan, joined_query=joined)
    adv = str(search_plan.get("netinsightAdvancedQuery") or "").strip()

    if km == "advanced" and adv:
        return [adv], "advanced"
    if km == "advanced" and not adv and looks_like_netinsight_advanced_expression(joined):
        return [joined], "advanced"
    if not parts:
        return [], "normal"
    return [joined], km
