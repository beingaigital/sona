"""Report meta extraction (v1).

This module provides lightweight heuristics to produce machine-checkable
`report_meta` from HTML content without introducing heavy parsing deps.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Dict, List


_THEORY_KEYWORDS: List[str] = [
    "议程设置",
    "沉默的螺旋",
    "框架理论",
    "两级传播",
    "信息茧房",
    "群体极化",
]


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _unique_urls(html_content: str) -> List[str]:
    if not html_content:
        return []
    urls = re.findall(r"https?://[^\s\"'<>]+", html_content, flags=re.IGNORECASE)
    # normalize a bit
    cleaned: List[str] = []
    seen = set()
    for u in urls:
        u = u.rstrip(").,;，。；")
        if u in seen:
            continue
        seen.add(u)
        cleaned.append(u)
    return cleaned


def _contains_any(text: str, keywords: List[str]) -> bool:
    return any(k in text for k in keywords)


def _count_bullets_near(text: str, heading_keywords: List[str], window: int = 800) -> int:
    """Heuristic count: number of list items near a heading."""
    if not text:
        return 0
    for kw in heading_keywords:
        idx = text.find(kw)
        if idx < 0:
            continue
        segment = text[idx : idx + max(200, window)]
        # count common bullet markers
        return len(re.findall(r"(^|\n)\s*[-*]\s+", segment))
    return 0


def build_report_meta_from_html(html_content: str) -> Dict[str, Any]:
    """Build `report_meta` v1 from HTML content (heuristic-based)."""
    text = html_content or ""

    sections: List[str] = []
    # Minimal section normalization: keyword-based.
    if _contains_any(text, ["摘要", "概览", "结论摘要", "Summary"]):
        sections.append("summary")
    if _contains_any(text, ["时间线", "时间轴", "timeline", "生命周期"]):
        sections.append("timeline")
    if _contains_any(text, ["分析", "研判", "洞察", "规律"]):
        sections.append("analysis")
    if _contains_any(text, ["建议", "处置建议", "应对建议", "recommendation"]):
        sections.append("recommendations")

    # De-dup while preserving order
    seen = set()
    sections = [s for s in sections if not (s in seen or seen.add(s))]

    urls = _unique_urls(text)
    references_count = len(urls)
    if references_count == 0:
        # fallback heuristic
        references_count = len(re.findall(r"来源[:：]|参考(资料|文献)|引用", text))

    has_summary = "summary" in sections
    has_timeline = "timeline" in sections
    has_recommendations = "recommendations" in sections

    analogous_keywords = ["相似案例", "同类事件", "历史案例", "案例对比"]
    has_analogous_cases = _contains_any(text, analogous_keywords)
    analogous_cases_count = _count_bullets_near(text, analogous_keywords)
    if has_analogous_cases and analogous_cases_count == 0:
        analogous_cases_count = 1

    pattern_keywords = ["规律", "综合研判", "演化路径", "舆情生命周期", "传播规律"]
    has_public_opinion_patterns = _contains_any(text, pattern_keywords)
    pattern_points_count = _count_bullets_near(text, pattern_keywords)
    if has_public_opinion_patterns and pattern_points_count == 0:
        # weak but deterministic
        pattern_points_count = 1

    theory_frameworks = [k for k in _THEORY_KEYWORDS if k in text]
    has_theory_analysis = bool(theory_frameworks) or _contains_any(text, ["传播理论", "理论规律"])

    return {
        "version": "v1",
        "generated_at": _utc_now_iso(),
        "sections": sections,
        "references_count": int(references_count),
        "has_summary": bool(has_summary),
        "has_timeline": bool(has_timeline),
        "has_recommendations": bool(has_recommendations),
        "has_analogous_cases": bool(has_analogous_cases),
        "analogous_cases_count": int(analogous_cases_count),
        "has_public_opinion_patterns": bool(has_public_opinion_patterns),
        "pattern_points_count": int(pattern_points_count),
        "has_theory_analysis": bool(has_theory_analysis),
        "theory_frameworks": theory_frameworks,
    }

