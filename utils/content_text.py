"""与关键词统计一致的文本清洗（正则保留中英数字，其余置空格）。"""

from __future__ import annotations

import re

# 与 tools.keyword_stats._tokenize_fallback 中逻辑一致
_NON_WORD_RE = re.compile(r"[^\u4e00-\u9fffA-Za-z0-9]+", flags=re.UNICODE)


def clean_text_like_keyword_stats(text: str | None) -> str:
    """
    对单段文本做与关键词分析 fallback 分词前相同的清洗。

    - 去除首尾空白
    - 非中英数字字符替换为单个空格
    - 连续空白压缩为单个空格
    """
    if text is None:
        return ""
    s = str(text).strip()
    if not s:
        return ""
    s = _NON_WORD_RE.sub(" ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s
