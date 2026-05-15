"""固定 HTML 模板报告：从分析 JSON 抽取图表配置 + 模型填充叙事占位符。"""

from __future__ import annotations

import ast
import html
import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from langchain_core.messages import HumanMessage, SystemMessage

from model.factory import get_report_model
from utils.path import get_prompt_dir
from utils.prompt_loader import get_prompt_config, get_report_html_template_basename

# 模板内 JSON 锚点（整段合法 JSON 对象字面量）
REPORT_CONFIG_ANCHOR = "__REPORT_CONFIG_JSON__"
REPORT_DATA_ANCHOR = "__REPORT_JSON_DATA__"

# 情感色：正面蓝 / 中立(中性)绿 / 负面红
_SENTIMENT_COLORS = {
    "正面": "#1e90ff",
    "中立": "#22c55e",
    "中性": "#22c55e",
    "负面": "#ef4444",
}

_REPORT_GENERIC_KEYWORDS = {
    "中国",
    "国家",
    "发展",
    "工作",
    "生活",
    "社会",
    "全球",
    "世界",
    "时间",
    "市场",
    "企业",
    "公司",
    "服务",
    "项目",
    "技术",
    "人员",
    "数据",
    "国际",
    "提供",
    "家庭",
    "相关",
    "经济",
    "能力",
    "信息",
    "平台",
    "专业",
    "建设",
    "岗位",
    "管理",
    "行业",
    "方式",
    "快乐",
    "电话",
    "妈妈",
    "政策",
    "科技",
    "教育",
    "持续",
    "领域",
    "支持",
    "产业",
    "幸福",
    "地区",
    "历史",
    "增长",
    "万事兴",
    "家和万事兴",
}

_PLACEHOLDER_KEYS = frozenset(
    {
        # 旧模板键
        "REPORT_TITLE",
        "DATA_PERIOD",
        "SAMPLE_SIZE",
        "EFFECTIVE_VOLUME",
        "OBJECT_NAME",
        "NATURE",
        "RISK_LEVEL",
        "EVENT_BACKGROUND",
        "5W_WHO",
        "5W_WHAT",
        "5W_WHERE",
        "5W_WHEN",
        "5W_WHY",
        "SENTIMENT_ANALYSIS",
        "TREND_ANALYSIS",
        "THEORY_AGENDA",
        "THEORY_SILENCE",
        "THEORY_RISK",
        "STRATEGY_RISK",
        "STRATEGY_SHORT",
        "STRATEGY_GUIDE",
        "STRATEGY_LONG",
        "AUTHOR",
        "DEPARTMENT",
        "GEN_TIME",
        # 新模板键
        "REPORT_SUBTITLE",
        "EVENT_TYPE",
        "PHASE_STATUS",
        "KPI_TOTAL",
        "KPI_EFFECTIVE",
        "KPI_POS_RATIO",
        "KPI_NEG_RATIO",
        "INTRO_BACKGROUND",
        "INTRO_TRIGGERS",
        "SUMMARY_BULLETS",
        "CHART_SENTIMENT_ANALYSIS",
        "CHART_TIMELINE_ANALYSIS",
        "CHART_VOLUME_ANALYSIS",
        "CHART_REGION_ANALYSIS",
        "CHART_AUTHOR_ANALYSIS",
        "CHART_KEYWORD_ANALYSIS",
        "CHART_CHANNEL_ANALYSIS",
        "CHART_RADAR_ANALYSIS",
        "CHART_LIFECYCLE_ANALYSIS",
        "THEORY_BUTTERFLY",
        "RESPONSE_ANALYSIS_BULLETS",
        "RESPONSE_ACTION_PLAN",
        "RECAP_DISCOURSE",
        "RECAP_TRENDS",
        "RECAP_DRIVERS_BULLETS",
        "DATA_SOURCE",
    }
)
# 叙事模型优先区中「微博智搜 → structured.report_bridge」的 template_hooks 与此处键名对齐，便于将外部讨论线索汇入对应段落。

_LIST_PLACEHOLDER_KEYS: Set[str] = {
    "SUMMARY_BULLETS",
    "CHART_SENTIMENT_ANALYSIS",
    "CHART_TIMELINE_ANALYSIS",
    "CHART_VOLUME_ANALYSIS",
    "CHART_REGION_ANALYSIS",
    "CHART_AUTHOR_ANALYSIS",
    "CHART_KEYWORD_ANALYSIS",
    "CHART_CHANNEL_ANALYSIS",
    "CHART_RADAR_ANALYSIS",
    "CHART_LIFECYCLE_ANALYSIS",
    "RESPONSE_ANALYSIS_BULLETS",
    "RECAP_DRIVERS_BULLETS",
}


def get_report_html_template_path() -> Optional[Path]:
    """若 prompt.yaml 配置了 report_html_template 且文件存在，返回路径。"""
    name = get_report_html_template_basename()
    if not name:
        return None
    p = (get_prompt_dir() / name).resolve()
    if p.is_file():
        return p
    return None


def _get_json_by_name(json_files: List[Dict[str, Any]], *candidates: str) -> Optional[Dict[str, Any]]:
    for item in json_files:
        fn = str(item.get("filename", "") or "").strip()
        if fn in candidates:
            c = item.get("content")
            if isinstance(c, dict):
                return c
    for item in json_files:
        fn = str(item.get("filename", "") or "").strip()
        for cand in candidates:
            if fn.startswith(cand) or fn.endswith(cand):
                c = item.get("content")
                if isinstance(c, dict):
                    return c
    return None


def _find_sentiment_json(json_files: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    for item in json_files:
        fn = str(item.get("filename", "") or "").strip().lower()
        if "sentiment" in fn and fn.endswith(".json"):
            c = item.get("content")
            if isinstance(c, dict):
                return c
    return None


def _existing_column_sentiment_counts(stats: dict[str, Any]) -> Optional[Tuple[int, int, int]]:
    """从 sampling.existing_distribution 读取数据源情感列分布（若存在）。"""
    samp = stats.get("sampling") if isinstance(stats.get("sampling"), dict) else {}
    ex = samp.get("existing_distribution") if isinstance(samp.get("existing_distribution"), dict) else {}
    cts = ex.get("counts") if isinstance(ex.get("counts"), dict) else {}
    if not cts:
        return None
    pos = int(cts.get("正面", 0) or 0)
    neu = int(cts.get("中立", 0) or 0) or int(cts.get("中性", 0) or 0)
    neg = int(cts.get("负面", 0) or 0)
    if pos + neu + neg <= 0:
        return None
    return pos, neu, neg


def _effective_sentiment_counts_from_statistics(stats: dict[str, Any]) -> tuple[int, int, int]:
    """
    统一情感三分类条数口径：当顶层 positive/negative/neutral_count 因抽样解析失败
    塌缩为「全中立」等退化形态时，优先采用 sampling.existing_distribution.counts
    （数据源自带情感列），保证 KPI、饼图与叙事引用同源。
    """
    pos = int(stats.get("positive_count", 0) or 0)
    neu = int(stats.get("neutral_count", 0) or 0)
    neg = int(stats.get("negative_count", 0) or 0)
    total = int(stats.get("total", 0) or 0)
    ex = _existing_column_sentiment_counts(stats)
    if not ex:
        return pos, neu, neg
    ep, em, en = ex
    ex_sum = ep + em + en
    if ex_sum <= 0:
        return pos, neu, neg
    top_sum = pos + neu + neg
    parse_failed = float(stats.get("parse_success_rate", 1.0) or 1.0) < 0.001
    collapsed_neutral = total > 0 and neg == 0 and pos == 0 and neu >= total - 1 and (ep > 0 or en > 0)
    top_mismatch = total > 0 and abs(top_sum - total) > max(5, int(total * 0.02))
    ex_matches_total = abs(ex_sum - total) <= max(5, int(total * 0.02))
    if (collapsed_neutral or top_mismatch or parse_failed) and ex_matches_total:
        return ep, em, en
    return pos, neu, neg


def _statistics_effective_ratios(stats: dict[str, Any]) -> dict[str, Any]:
    pos, neu, neg = _effective_sentiment_counts_from_statistics(stats)
    tot = max(1, pos + neu + neg)
    return {
        "positive_count": pos,
        "neutral_count": neu,
        "negative_count": neg,
        "positive_ratio": pos / tot,
        "neutral_ratio": neu / tot,
        "negative_ratio": neg / tot,
        "total": tot,
    }


def _find_timeline_json(json_files: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    best: Optional[Tuple[str, Dict[str, Any]]] = None
    for item in json_files:
        fn = str(item.get("filename", "") or "").strip()
        if "timeline" in fn.lower() and fn.endswith(".json"):
            c = item.get("content")
            if isinstance(c, dict):
                if best is None or fn > best[0]:
                    best = (fn, c)
    return best[1] if best else None


def _find_author_json(json_files: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    return _get_json_by_name(json_files, "author_stats.json")


def _find_channel_distribution_json(json_files: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """
    读取渠道分布 JSON（优先 channel_distribution.json）。

    兼容 shape：
    - {"distribution": [{"channel": "...", "count": 1}, ...]}
    - {"channels": [{"name": "...", "value": 1}, ...]}
    - {"weibo": 10, "zhihu": 2, ...}  # 顶层 key->count
    - {"distribution": {"weibo": 10, ...}}
    """
    # 1) 精确命中
    hit = _get_json_by_name(json_files, "channel_distribution.json")
    if hit:
        return hit
    # 2) 模糊兜底（文件名包含 channel & dist）
    for item in json_files:
        fn = str(item.get("filename", "") or "").strip().lower()
        if "channel" in fn and ("dist" in fn or "distribution" in fn) and fn.endswith(".json"):
            c = item.get("content")
            if isinstance(c, dict):
                return c
    return None


def _build_channel_pie_data(channel_obj: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    将 channel_distribution.json 转为 ECharts pie 所需的 [{name, value}]。
    """
    if not isinstance(channel_obj, dict) or not channel_obj:
        return []

    candidates: List[Tuple[str, int]] = []

    # list-shaped
    for key in ("distribution", "channels", "data", "items", "results"):
        rows = channel_obj.get(key)
        if isinstance(rows, list) and rows:
            for r in rows:
                if not isinstance(r, dict):
                    continue
                name = str(
                    r.get("channel")
                    or r.get("platform")
                    or r.get("name")
                    or r.get("source")
                    or ""
                ).strip()
                val = r.get("count", r.get("value", r.get("num", 0)))
                if not name:
                    continue
                candidates.append((name, _safe_int(val, 0)))
            break

    # dict-shaped mapping
    if not candidates:
        mapping = channel_obj.get("distribution")
        if isinstance(mapping, dict) and mapping:
            for k, v in mapping.items():
                name = str(k or "").strip()
                if not name:
                    continue
                candidates.append((name, _safe_int(v, 0)))
        else:
            # top-level mapping: filter out obviously non-channel keys
            for k, v in channel_obj.items():
                name = str(k or "").strip()
                if not name or name.startswith("_"):
                    continue
                if name.lower() in {
                    "status",
                    "meta",
                    "total",
                    "summary",
                    "date_range",
                    "total_count",
                    "items",
                    "chart_type",
                    "mermaid_pie",
                    "created_at",
                    "calculation_source",
                }:
                    continue
                if isinstance(v, (int, float, str)):
                    candidates.append((name, _safe_int(v, 0)))

    # clean + sort
    cleaned = [(n, c) for n, c in candidates if n and c > 0]
    if not cleaned:
        return []
    cleaned.sort(key=lambda x: x[1], reverse=True)

    # 聚合长尾，避免图例过长
    top = cleaned[:10]
    rest = cleaned[10:]
    rest_sum = sum(v for _, v in rest)
    out = [{"name": n, "value": v} for n, v in top]
    if rest_sum > 0:
        out.append({"name": "其他", "value": rest_sum})
    return out


def build_report_config_from_json_files(json_files: List[Dict[str, Any]]) -> Dict[str, Any]:
    """构造旧模板使用的 REPORT_CONFIG。"""
    sentiment: List[Dict[str, Any]] = []
    sent_obj = _find_sentiment_json(json_files)
    if sent_obj:
        stats = sent_obj.get("statistics") if isinstance(sent_obj.get("statistics"), dict) else {}
        pos, neu, neg = _effective_sentiment_counts_from_statistics(stats)
        if pos == 0 and neg == 0 and neu == 0:
            pass
        else:

            def _slice(name_cn: str, val: int) -> Dict[str, Any]:
                color = _SENTIMENT_COLORS.get(name_cn, "#95a5a6")
                return {"value": val, "name": name_cn, "itemStyle": {"color": color}}

            sentiment.append(_slice("正面", pos))
            # 统计里多为「中立」；模板图例使用「中立」
            label_mid = "中立" if neu else "中立"
            sentiment.append({"value": neu, "name": label_mid, "itemStyle": {"color": _SENTIMENT_COLORS["中立"]}})
            sentiment.append(_slice("负面", neg))

    trend_dates: List[str] = []
    trend_values: List[int] = []
    vol = _get_json_by_name(json_files, "volume_stats.json")
    if vol and isinstance(vol.get("data"), list):
        for pt in vol["data"][:60]:
            if not isinstance(pt, dict):
                continue
            nm = str(pt.get("name", "") or "").strip()
            trend_dates.append(nm)
            try:
                trend_values.append(int(pt.get("value", 0)))
            except Exception:
                trend_values.append(0)

    region_names: List[str] = []
    region_counts: List[int] = []
    reg = _get_json_by_name(json_files, "region_stats.json")
    if reg and isinstance(reg.get("top_provinces"), list):
        for row in reg["top_provinces"][:10]:
            if not isinstance(row, dict):
                continue
            pv = str(row.get("province", "") or "").strip()
            if pv:
                region_names.append(pv)
                try:
                    region_counts.append(int(row.get("count", 0)))
                except Exception:
                    region_counts.append(0)

    keywords_out: List[Dict[str, Any]] = []
    kw = _get_json_by_name(json_files, "keyword_stats.json")
    if kw and isinstance(kw.get("top_keywords"), list):
        # 关键词用于词云：取 Top200，提升信息量
        for row in kw["top_keywords"][:200]:
            if not isinstance(row, dict):
                continue
            w = str(row.get("word", "") or "").strip()
            if not w:
                continue
            try:
                c = int(row.get("count", 0))
            except Exception:
                c = 0
            if c >= 200:
                rel = "高频"
            elif c >= 50:
                rel = "中频"
            else:
                rel = "长尾"
            keywords_out.append({"word": w, "count": c, "rel": rel})

    timeline_out: List[Dict[str, str]] = []
    tl = _find_timeline_json(json_files)
    if tl and isinstance(tl.get("timeline"), list):
        for row in tl["timeline"][:25]:
            if not isinstance(row, dict):
                continue
            t = str(row.get("time", "") or "").strip()
            ev = str(row.get("event", "") or "").strip()
            if t or ev:
                timeline_out.append(
                    {
                        "time": t or "—",
                        "event": ev or "—",
                        "evidence": str(row.get("evidence", "") or "").strip(),
                        "impact": str(row.get("impact", "") or "").strip(),
                    }
                )

    keyword_diagnostics = _keyword_quality_diagnostics(keywords_out)
    keyword_display = _curate_report_keywords(keywords_out, keyword_diagnostics)

    return {
        "sentiment": sentiment or [{"value": 1, "name": "中立", "itemStyle": {"color": "#22c55e"}}],
        "trend": {"dates": trend_dates or ["—"], "values": trend_values or [0]},
        "regions": {"names": region_names or ["—"], "counts": region_counts or [0]},
        "keywords": keyword_display or keywords_out or [{"word": "暂无关键词", "count": 0, "rel": "—"}],
        "keywordDiagnostics": keyword_diagnostics,
        "timeline": timeline_out or [{"time": "—", "event": "暂无时间线数据"}],
    }


def _safe_int(v: Any, default: int = 0) -> int:
    try:
        return int(v)
    except Exception:
        return default


def _safe_int_from_text(v: Any, default: int = 0) -> int:
    s = str(v if v is not None else "").strip()
    if not s:
        return default
    s = s.replace(",", "").replace("，", "")
    m = re.search(r"-?\d+", s)
    if not m:
        return default
    try:
        return int(m.group(0))
    except Exception:
        return default


def _is_report_generic_keyword(word: str) -> bool:
    s = re.sub(r"\s+", "", str(word or "").strip())
    if not s:
        return True
    if s in _REPORT_GENERIC_KEYWORDS:
        return True
    if len(s) <= 1:
        return True
    if re.fullmatch(r"\d+(?:\.\d+)?", s):
        return True
    return False


def _keyword_quality_diagnostics(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    top = [str(x.get("word", "") or "").strip() for x in rows[:20] if isinstance(x, dict)]
    generic_terms = [w for w in top if _is_report_generic_keyword(w)]
    ratio = round(len(generic_terms) / float(max(1, len(top))), 4)
    return {
        "top_count": len(top),
        "generic_top_ratio": ratio,
        "generic_terms": generic_terms[:12],
        "pollution_suspected": ratio >= 0.35,
    }


def _curate_report_keywords(rows: List[Dict[str, Any]], diagnostics: Dict[str, Any]) -> List[Dict[str, Any]]:
    """When generic terms dominate top keywords, keep the cloud anchored to topic-bearing terms."""
    if not rows:
        return []
    suspected = bool(diagnostics.get("pollution_suspected", False))
    if not suspected:
        return rows
    curated = [x for x in rows if isinstance(x, dict) and not _is_report_generic_keyword(str(x.get("word", "") or ""))]
    return curated if len(curated) >= 5 else rows


def _infer_pos_label(word: str) -> str:
    """
    为词云提供轻量词性标签（用于前端着色）。
    返回值：名词 / 动词 / 形容词 / 人名 / 地名 / 机构 / 其他
    """
    w = str(word or "").strip()
    if not w:
        return "其他"
    try:
        import jieba.posseg as pseg  # type: ignore

        token = next(iter(pseg.cut(w)), None)
        flag = str(getattr(token, "flag", "") or "")
        if flag.startswith(("nr",)):
            return "人名"
        if flag.startswith(("ns",)):
            return "地名"
        if flag.startswith(("nt", "nz")):
            return "机构"
        if flag.startswith(("n",)):
            return "名词"
        if flag.startswith(("v",)):
            return "动词"
        if flag.startswith(("a",)):
            return "形容词"
    except Exception:
        pass
    return "其他"


def _build_radar_values(report_config: Dict[str, Any]) -> List[int]:
    sentiment_total = sum(_safe_int(x.get("value", 0), 0) for x in report_config.get("sentiment", []) if isinstance(x, dict))
    keyword_total = sum(_safe_int(x.get("count", 0), 0) for x in report_config.get("keywords", []) if isinstance(x, dict))
    timeline_len = len(report_config.get("timeline", []))
    trend_peak = max(report_config.get("trend", {}).get("values", [0]) or [0])
    region_len = len(report_config.get("regions", {}).get("names", []))

    def scale(v: int, div: float) -> int:
        if div <= 0:
            return 3
        return max(2, min(10, int(round(v / div))))

    return [
        scale(sentiment_total, 100.0),  # 量
        scale(keyword_total, 200.0),  # 质
        scale(region_len * 10, 20.0),  # 人(参与广度替代)
        scale(timeline_len * 10, 20.0),  # 场(阶段复杂度替代)
        scale(trend_peak, 80.0),  # 效(峰值冲击替代)
    ]


def _extract_volume_series(vol: Optional[Dict[str, Any]]) -> Tuple[List[str], List[int], List[int], Dict[str, Any]]:
    """
    从 volume_stats.json 中抽取趋势序列。

    Returns:
        dates: x 轴日期
        post_counts: 发文量（条数）
        heat_norm: 热度（0-100，若缺失则用 0 填充）
        raw: 原始 volume_stats 对象（用于其他元信息）
    """
    if not isinstance(vol, dict):
        return ["—"], [0], [0], {}

    post_series = vol.get("post_count_series") or vol.get("data")
    dates: List[str] = []
    post_counts: List[int] = []
    if isinstance(post_series, list) and post_series:
        dates = [str(x.get("name", "—")) for x in post_series if isinstance(x, dict)]
        post_counts = [_safe_int(x.get("value", 0), 0) for x in post_series if isinstance(x, dict)]

    heat_series = vol.get("heat_percentage_series") or vol.get("heat_percentage_smoothed")
    heat_norm: List[int] = []
    if isinstance(heat_series, list) and heat_series:
        heat_dates = [str(x.get("name", "—")) for x in heat_series if isinstance(x, dict)]
        heat_vals = [_safe_int(x.get("value", 0), 0) for x in heat_series if isinstance(x, dict)]
        if heat_dates and heat_vals and dates and heat_dates == dates:
            heat_norm = heat_vals
        elif heat_dates and heat_vals and not dates:
            dates = heat_dates
            heat_norm = heat_vals

    if not dates or not post_counts:
        return ["—"], [0], [0], vol

    if not heat_norm:
        heat_norm = [0 for _ in dates]
    if len(heat_norm) != len(dates):
        heat_norm = (heat_norm + [0 for _ in dates])[: len(dates)]
    return dates, post_counts, heat_norm, vol


def _classify_lifecycle_stage(values: List[int]) -> List[str]:
    """按时间点给出唯一生命周期阶段（潜伏/扩散/爆发/衰退/衍生/结束）。"""
    if not values:
        return []

    n = len(values)
    seed = [max(0, int(v)) for v in values]
    peak_idx = max(range(n), key=lambda i: seed[i])
    max_v = max(max(seed), 1)
    stages: List[str] = []
    trailing_window = max(2, min(4, n))
    trailing_sum = sum(seed[-trailing_window:])
    trailing_avg = trailing_sum / float(trailing_window)
    is_ending = trailing_avg <= max_v * 0.08 and seed[-1] <= max_v * 0.06

    # 次峰（衍生期）检测：主峰后出现明显回升
    derivative = [seed[i] - seed[i - 1] for i in range(1, n)]
    second_peak_idx = -1
    second_peak_val = 0
    for i in range(peak_idx + 2, n - 1):
        if seed[i] >= seed[i - 1] and seed[i] >= seed[i + 1] and seed[i] >= int(max_v * 0.45):
            if seed[i] > second_peak_val:
                second_peak_val = seed[i]
                second_peak_idx = i

    for i, v in enumerate(seed):
        # 峰值附近视为爆发期（主峰前后）
        if abs(i - peak_idx) <= 1 or v >= int(max_v * 0.82):
            stages.append("爆发")
            continue

        # 峰值之前：低基线为潜伏，斜率明显上升转扩散
        if i < peak_idx:
            slope = derivative[i - 1] if i - 1 >= 0 and i - 1 < len(derivative) else 0
            if v <= int(max_v * 0.18) and slope <= int(max_v * 0.08):
                stages.append("潜伏")
            else:
                stages.append("扩散")
            continue

        # 次峰附近判定为衍生期（第二轮小高潮）
        if second_peak_idx > 0 and abs(i - second_peak_idx) <= 1:
            stages.append("衍生")
            continue

        # 峰值之后：先扩散，再衰退；末端接近归零时标记结束
        if is_ending and i >= n - trailing_window:
            stages.append("结束")
        elif v >= int(max_v * 0.35):
            stages.append("扩散")
        else:
            stages.append("衰退")

    return stages


def _normalize_phase_status_text(value: Any) -> str:
    """Normalize lifecycle labels from different tools into report-facing stages."""
    raw = str(value or "").strip()
    if not raw or "待评估" in raw or raw in {"—", "-", "－"}:
        return ""
    raw = raw.replace("当前处于", "").replace("目前处于", "").strip()
    mapping = {
        "潜伏": "潜伏期",
        "潜伏期": "潜伏期",
        "扩散": "扩散期",
        "扩散期": "扩散期",
        "成长期": "扩散期",
        "成长": "扩散期",
        "爆发": "爆发期",
        "爆发期": "爆发期",
        "成熟期": "爆发期",
        "高潮期": "爆发期",
        "高峰期": "爆发期",
        "衰退": "衰退期",
        "衰退期": "衰退期",
        "结束": "结束期",
        "结束期": "结束期",
    }
    for key, val in mapping.items():
        if key in raw:
            return val
    return ""


def _build_lifecycle_series(dates: List[str], values: List[int]) -> Dict[str, Any]:
    """构造生命周期图数据：单曲线 + 阶段竖虚线（图内仅四阶段）。"""
    if not dates:
        dates = ["—"]
        values = [0]

    seed = [max(0, int(v)) for v in values]
    stages = _classify_lifecycle_stage(seed)
    # 图内展示四阶段：潜伏/扩散/爆发/衰退（衍生并入扩散，结束并入衰退）
    chart_stages = []
    for s in stages:
        if s == "潜伏":
            chart_stages.append("潜伏")
        elif s == "爆发":
            chart_stages.append("爆发")
        elif s in {"扩散", "衍生"}:
            chart_stages.append("扩散")
        else:
            chart_stages.append("衰退")
    boundaries: List[Dict[str, Any]] = []
    for i in range(1, len(chart_stages)):
        if chart_stages[i] != chart_stages[i - 1]:
            boundaries.append({"xAxis": dates[i], "name": f"{chart_stages[i - 1]}→{chart_stages[i]}"})

    return {
        "dates": dates,
        "stages": stages,
        "values": seed,
        "boundaries": boundaries,
    }


def _summarize_phase_status(values: List[int]) -> str:
    """给出当前周期阶段判定文案。"""
    if not values:
        return "待评估"
    stages = _classify_lifecycle_stage([max(0, int(v)) for v in values])
    if not stages:
        return "待评估"
    latest = stages[-1]
    return _normalize_phase_status_text(latest) or f"{latest}期"


def _phase_status_from_report_data(report_data: Dict[str, Any], json_files: List[Dict[str, Any]]) -> str:
    """Prefer the lifecycle chart's latest stage, then volume_stats.lifecycle.current_phase."""
    lifecycle = report_data.get("charts", {}).get("lifecycle", {})
    if isinstance(lifecycle, dict):
        stages = lifecycle.get("stages") if isinstance(lifecycle.get("stages"), list) else []
        if stages:
            hit = _normalize_phase_status_text(stages[-1])
            if hit:
                return hit
        values = [_safe_int(v, 0) for v in (lifecycle.get("values") or [])] if isinstance(lifecycle.get("values"), list) else []
        if values:
            hit = _summarize_phase_status(values)
            if hit and "待评估" not in hit:
                return hit
    vol = _get_json_by_name(json_files, "volume_stats.json")
    if isinstance(vol, dict):
        lifecycle_obj = vol.get("lifecycle") if isinstance(vol.get("lifecycle"), dict) else {}
        hit = _normalize_phase_status_text(lifecycle_obj.get("current_phase"))
        if hit:
            return hit
    return "待评估（缺少声量时间序列）"


def _compute_impact_index(
    *,
    sample_total: int,
    effective_total: int,
    trend_values: List[int],
    region_count: int,
    top_author_share: float,
    sentiment_balance: float,
) -> int:
    max_peak = max([0] + [max(0, int(v)) for v in trend_values])
    # 归一化到 0~100，再加权
    sample_score = min(100.0, sample_total / 10.0)
    effective_score = min(100.0, effective_total / 8.0)
    peak_score = min(100.0, max_peak / 5.0)
    spread_score = min(100.0, region_count * 10.0)
    concentration_penalty = max(0.0, min(20.0, (top_author_share - 0.25) * 80.0))
    sentiment_score = max(0.0, min(100.0, sentiment_balance))
    raw = (
        sample_score * 0.24
        + effective_score * 0.24
        + peak_score * 0.24
        + spread_score * 0.14
        + sentiment_score * 0.14
        - concentration_penalty
    )
    return max(0, min(100, int(round(raw))))


def _overall_attitude_label(stats: Dict[str, Any]) -> str:
    pos = float(stats.get("positive_ratio", 0.0) or 0.0)
    neg = float(stats.get("negative_ratio", 0.0) or 0.0)
    neu = float(stats.get("neutral_ratio", 0.0) or 0.0)
    pairs = [("正面", pos), ("负面", neg), ("中性", neu)]
    pairs.sort(key=lambda x: x[1], reverse=True)
    label, ratio = pairs[0]
    return f"{label}（{round(ratio * 100, 1)}%）"


def build_report_data_from_json_files(json_files: List[Dict[str, Any]]) -> Dict[str, Any]:
    """构造新模板使用的 REPORT_DATA。"""
    cfg = build_report_config_from_json_files(json_files)

    author_names: List[str] = []
    author_values: List[int] = []
    au = _find_author_json(json_files)
    if au and isinstance(au.get("top_authors"), list):
        for row in au["top_authors"][:10]:
            if not isinstance(row, dict):
                continue
            nm = str(row.get("author", "") or "").strip()
            if not nm:
                continue
            author_names.append(nm)
            author_values.append(_safe_int(row.get("count", 0), 0))

    vol = _get_json_by_name(json_files, "volume_stats.json")
    trend_dates, post_counts, heat_norm, _ = _extract_volume_series(vol)
    if trend_dates == ["—"] and post_counts == [0]:
        trend_dates = list(cfg.get("trend", {}).get("dates", []) or [])
        post_counts = [_safe_int(v, 0) for v in (cfg.get("trend", {}).get("values", []) or [])]
        heat_norm = [0 for _ in post_counts]

    # 关键词词云：输出 name/value/pos 列表供前端 DOM 词云渲染
    keyword_cloud = [
        {
            "name": str(x.get("word", "") or ""),
            "value": _safe_int(x.get("count", 0), 0),
            "pos": _infer_pos_label(str(x.get("word", "") or "")),
        }
        for x in (cfg.get("keywords", []) or [])
        if isinstance(x, dict) and str(x.get("word", "") or "").strip()
    ][:120]
    lifecycle = _build_lifecycle_series(trend_dates, post_counts)
    channel_obj = _find_channel_distribution_json(json_files)
    channel_pie = _build_channel_pie_data(channel_obj)
    sentiment_detail = _find_sentiment_json(json_files) or {}
    emotion_analysis = (
        sentiment_detail.get("emotion_analysis")
        if isinstance(sentiment_detail.get("emotion_analysis"), dict)
        else {}
    )
    negative_drivers = str(sentiment_detail.get("negative_drivers", "") or "")
    emotion_validation = (
        sentiment_detail.get("emotion_validation")
        if isinstance(sentiment_detail.get("emotion_validation"), dict)
        else {}
    )

    return {
        "charts": {
            "sentiment": cfg.get("sentiment", []),
            "volume": {
                "dates": trend_dates or ["—"],
                "postCounts": post_counts or [0],
                "heat": heat_norm or [0],
            },
            "region": {
                "names": list(cfg.get("regions", {}).get("names", []) or ["—"]),
                "values": list(cfg.get("regions", {}).get("counts", []) or [0]),
            },
            "author": {"names": author_names or ["暂无作者数据"], "values": author_values or [0]},
            "keyword": keyword_cloud or [{"name": "暂无关键词", "value": 0}],
            "channel": channel_pie,
            "radarValues": _build_radar_values(cfg),
            "lifecycle": lifecycle,
        },
        "timeline": list(cfg.get("timeline", []) or [{"time": "—", "event": "暂无时间线数据"}]),
        "diagnostics": {
            "keyword": cfg.get("keywordDiagnostics", {}),
        },
        "sentimentDetail": {
            "emotionAnalysis": emotion_analysis,
            "negativeDrivers": negative_drivers,
            "emotionValidation": emotion_validation,
        },
    }


def build_meta_placeholders(json_files: List[Dict[str, Any]], event_introduction: str) -> Dict[str, str]:
    """从 JSON 抽取可核验的数字类占位符。"""
    sample = ""
    effective = ""
    period = "—"

    sent = _find_sentiment_json(json_files)
    if sent and isinstance(sent.get("statistics"), dict):
        st = sent["statistics"]
        if st.get("total") is not None:
            sample = str(int(st.get("total", 0)))

    reg = _get_json_by_name(json_files, "region_stats.json")
    if reg:
        if reg.get("valid_rows_count") is not None:
            effective = str(int(reg.get("valid_rows_count", 0)))
        elif reg.get("total_rows") is not None:
            effective = str(int(reg.get("total_rows", 0)))

    vol = _get_json_by_name(json_files, "volume_stats.json")
    if vol and isinstance(vol.get("data"), list) and vol["data"]:
        names = []
        for pt in vol["data"]:
            if isinstance(pt, dict) and pt.get("name"):
                names.append(str(pt["name"]))
        if names:
            period = f"{min(names)} 至 {max(names)}"

    if not sample and reg and reg.get("total_rows") is not None:
        sample = str(int(reg.get("total_rows", 0)))

    return {
        "SAMPLE_SIZE": sample or "—",
        "EFFECTIVE_VOLUME": effective or sample or "—",
        "DATA_PERIOD": period,
        "GEN_TIME": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


def _load_template_fill_prompt() -> str:
    raw = get_prompt_config().get("report_html_template_fill", "").strip()
    if raw:
        return raw
    path = get_prompt_dir() / "report_html_template_fill.txt"
    if path.is_file():
        return path.read_text(encoding="utf-8")
    return ""


def _report_template_analysis_char_budget() -> int:
    raw = str(os.environ.get("SONA_REPORT_TEMPLATE_ANALYSIS_BUDGET_CHARS", "") or "").strip()
    try:
        n = int(raw)
    except Exception:
        n = 72_000
    return max(24_000, min(n, 220_000))


def _merge_kb_priority_and_analysis_budget(kb_priority_text: str, analysis_results_text: str) -> str:
    """
    叙事模型输入预算：优先完整保留 Wiki/微博/OPRAG 等「优先区」，再截断后续过程 JSON。
    修复原先仅取 analysis 前 14k 导致 wiki/微博从未进入模型上下文的缺陷。
    """
    kb = (kb_priority_text or "").strip()
    body = (analysis_results_text or "").strip()
    max_total = _report_template_analysis_char_budget()
    if not kb:
        return body[:max_total] + ("..." if len(body) > max_total else "")
    if len(kb) >= max_total:
        return kb[:max_total] + "\n...[KB 优先区过长已截断]..."
    room = max_total - len(kb) - 32
    if len(body) <= room:
        return kb + "\n\n" + body
    return (
        kb
        + "\n\n"
        + body[:room]
        + "\n\n...[后续监测材料已截断；完整内容仍保留在任务材料目录]..."
    )


def _parse_llm_json_object(text: str) -> Optional[Dict[str, Any]]:
    if not text:
        return None
    s = str(text).strip()
    if s.startswith("```"):
        s = re.sub(r"^```(?:json)?\s*", "", s, flags=re.IGNORECASE)
        s = re.sub(r"\s*```\s*$", "", s)
    start = s.find("{")
    end = s.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        return json.loads(s[start : end + 1])
    except Exception:
        return None


def normalize_report_length(value: Optional[str]) -> str:
    """将用户/路由配置中的篇幅偏好归一为 短篇|中篇|长篇。"""
    s = str(value or "").strip()
    if s in ("短篇", "中篇", "长篇"):
        return s
    low = s.lower()
    if low in ("short", "brief", "s"):
        return "短篇"
    if low in ("long", "full", "l"):
        return "长篇"
    if low in ("medium", "mid", "m", "normal"):
        return "中篇"
    default_length = str(os.environ.get("SONA_DEFAULT_REPORT_LENGTH", "") or "").strip()
    if default_length in ("短篇", "中篇", "长篇"):
        return default_length
    default_length_low = default_length.lower()
    if default_length_low in ("short", "brief", "s"):
        return "短篇"
    if default_length_low in ("medium", "mid", "m", "normal"):
        return "中篇"
    if default_length_low in ("long", "full", "l"):
        return "长篇"
    return "长篇"


def format_report_length_instruction(report_length: str) -> str:
    """供模板叙事与非模板 HTML 生成共用的篇幅指令（追加到模型提示词末尾）。"""
    key = normalize_report_length(report_length)
    guides = {
        "短篇": (
            "【篇幅目标：短篇】\n"
            "可见正文总篇幅控制在约 1200～1800 字当量；可合并小节、删减重复图表解读；"
            "每个核心小节至多 3～4 条要点，结论优先，避免堆砌套话。"
        ),
        "中篇": (
            "【篇幅目标：中篇】\n"
            "各核心维度均衡展开，可见正文约 3000～5200 字当量；"
            "事件概览、总结复盘、处置建议不宜过度压缩，在审慎前提下保持信息密度与可执行细节；"
            "图表/数据后的结论各 2～3 条要点即可，勿为凑字数空泛扩写。"
        ),
        "长篇": (
            "【篇幅目标：长篇】\n"
            "允许充分展开研判、机制解释与处置建议，可见正文约 4500～7000 字当量；"
            "仍须严格依据输入材料，禁止编造；明显重复段落应合并。"
        ),
    }
    return guides.get(key, guides["中篇"])


def call_llm_for_template_narrative(
    *,
    event_introduction: str,
    analysis_results_text: str,
    methodology_text: str,
    meta_json: str,
    kb_priority_text: str = "",
    report_length: str = "中篇",
) -> Dict[str, Any]:
    """调用模型，仅返回叙事占位符 JSON。"""
    tpl = _load_template_fill_prompt()
    if not tpl:
        return {}
    merged_analysis = _merge_kb_priority_and_analysis_budget(kb_priority_text, analysis_results_text)
    prompt = (
        tpl.replace("{event_introduction}", event_introduction or "")
        .replace("{analysis_results}", merged_analysis or "")
        .replace("{methodology}", methodology_text or "")
        .replace("{meta_json}", meta_json or "{}")
    )
    prompt += "\n\n" + format_report_length_instruction(report_length)
    model = get_report_model()
    messages = [
        SystemMessage(
            content="你只输出一个 JSON 对象，键名必须与用户要求完全一致，不要输出其它任何字符。"
        ),
        HumanMessage(content=prompt),
    ]
    try:
        resp = model.invoke(messages)
        raw = resp.content if hasattr(resp, "content") else str(resp)
        parsed = _parse_llm_json_object(raw)
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def _default_narrative(event_introduction: str) -> Dict[str, str]:
    intro = (event_introduction or "").strip()[:500]
    stub = "围绕事件事实、传播表现与公众关切展开结构化研判。"
    title = intro[:40] if intro else "舆情分析报告"
    return {
        # 旧模板键
        "REPORT_TITLE": title,
        "OBJECT_NAME": title[:30],
        "NATURE": "待评估",
        "RISK_LEVEL": "待评估",
        "EVENT_BACKGROUND": intro or stub,
        "5W_WHO": "涉事主体、公众与相关管理部门",
        "5W_WHAT": "围绕事件事实、责任边界与处置进展形成讨论",
        "5W_WHERE": "相关线上平台及事件发生地",
        "5W_WHEN": "事件发酵期",
        "5W_WHY": "信息供给不足、公众期待与处置节奏之间存在落差",
        "SENTIMENT_ANALYSIS": stub,
        "TREND_ANALYSIS": stub,
        "THEORY_AGENDA": stub,
        "THEORY_SILENCE": stub,
        "THEORY_RISK": stub,
        "STRATEGY_RISK": stub,
        "STRATEGY_SHORT": stub,
        "STRATEGY_GUIDE": stub,
        "STRATEGY_LONG": stub,
        "AUTHOR": "舆情智库",
        "DEPARTMENT": "专题分析",
        # 新模板键
        "REPORT_SUBTITLE": "基于公开数据和事件事实的结构化研判",
        "EVENT_TYPE": "网络舆情",
        "PHASE_STATUS": "待评估",
        "KPI_TOTAL": "—",
        "KPI_EFFECTIVE": "0",
        "KPI_POS_RATIO": "—",
        "KPI_NEG_RATIO": "—",
        "INTRO_BACKGROUND": intro or stub,
        "INTRO_TRIGGERS": "事件触发点通常来自事实不清、回应滞后与高互动账号持续追问的叠加。",
        "SUMMARY_BULLETS": "事件仍需以事实核验和权威回应收敛争议|情绪变化、渠道扩散和高互动样本应交叉研判|处置建议应优先回应公众最集中的疑问",
        "CHART_SENTIMENT_ANALYSIS": "情感结构需要结合高互动样本和关键事实同步判断。",
        "CHART_TIMELINE_ANALYSIS": "时间线用于识别触发、扩散、回应和二次发酵节点。",
        "CHART_VOLUME_ANALYSIS": "声量变化需要同时观察峰值、长尾和潜在回弹信号。",
        "CHART_REGION_ANALYSIS": "地域分布反映讨论活跃区域和传播承接路径。",
        "CHART_AUTHOR_ANALYSIS": "头部发布者对议题扩散和叙事框架具有放大作用。",
        "CHART_KEYWORD_ANALYSIS": "关键词结构用于判断讨论是否仍聚焦事件事实，或已外溢到情绪和责任追问。",
        "CHART_RADAR_ANALYSIS": "雷达图用于综合观察声量、质量、参与广度和风险强度。",
        "CHART_LIFECYCLE_ANALYSIS": "生命周期判断需要同时结合声量走势、疑点是否解答和关键账号是否再发声。",
        "THEORY_BUTTERFLY": stub,
        "RESPONSE_ANALYSIS_BULLETS": "回应节奏应与公众疑问强度匹配|事实说明越模糊，二次猜测越容易扩散|处置闭环需要包含时间表、责任主体和可核验依据",
        "RESPONSE_ACTION_PLAN": {
            "24小时内": [
                {
                    "主体": "责任部门",
                    "动作": "发布事实说明与数据口径",
                    "话术": "我们已关注相关讨论，将以可核验事实持续更新处置进展。",
                    "风险": "信息不足导致二次猜测",
                    "验证指标": "负面评论占比与核心质疑关键词是否回落",
                }
            ],
            "3天内": [
                {
                    "主体": "业务与舆情团队",
                    "动作": "补充解释争议点并回应高频问题",
                    "话术": "针对大家集中关心的问题，我们将逐项说明依据、流程和后续改进。",
                    "风险": "回应过慢导致情绪固化",
                    "验证指标": "高频质疑问题的回应覆盖率",
                }
            ],
            "7天内": [
                {
                    "主体": "管理团队",
                    "动作": "公布复核结果和改进安排",
                    "话术": "我们将把复核结果和改进计划向公众说明，并接受持续监督。",
                    "风险": "承诺无法兑现引发反弹",
                    "验证指标": "相关负面声量是否持续下降",
                }
            ],
            "复盘期": [
                {
                    "主体": "组织复盘小组",
                    "动作": "沉淀案例、更新预案与话术库",
                    "话术": "本次事件已纳入复盘，后续将优化流程并定期检查执行情况。",
                    "风险": "同类事件重复发生",
                    "验证指标": "同类投诉量和相似舆情复发率",
                }
            ],
        },
        "RECAP_DISCOURSE": stub,
        "RECAP_TRENDS": stub,
        "RECAP_DRIVERS_BULLETS": "事实缺口推动公众持续追问|平台高互动样本会放大质疑框架|权威回应质量决定议题能否转入平稳收束",
        "DATA_SOURCE": "平台公开数据与结构化监测",
    }


def _to_action_plan_html(value: Any) -> str:
    stages = ["24小时内", "3天内", "7天内", "复盘期"]
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except Exception:
            parsed = None
        value = parsed if isinstance(parsed, (dict, list)) else value
    if not isinstance(value, dict):
        fallback = html.escape(str(value or "暂无可展示行动清单"), quote=True)
        return f'<div class="action-plan-fallback">{fallback}</div>'

    parts: List[str] = ['<div class="action-plan-grid">']
    for stage in stages:
        items = value.get(stage) or value.get(stage.replace("小时", " 小时")) or []
        if isinstance(items, dict):
            items = [items]
        if not isinstance(items, list) or not items:
            items = [
                {
                    "主体": "责任主体",
                    "动作": "核验事实并同步处置依据",
                    "话术": "我们将补充核验依据，并在确认后同步更新。",
                    "风险": "信息不足导致误判",
                    "验证指标": "关键事实补齐率",
                }
            ]
        parts.append(f'<div class="action-plan-card"><h4>{html.escape(stage, quote=True)}</h4>')
        for item in items[:3]:
            if not isinstance(item, dict):
                parts.append(f"<p>{html.escape(str(item), quote=True)}</p>")
                continue
            rows = []
            fallbacks = {
                "主体": "责任主体",
                "动作": "核验事实并同步处置依据",
                "话术": "我们将以可核验事实持续更新处置进展。",
                "风险": "信息不足导致二次猜测",
                "验证指标": "核心质疑点回应覆盖率",
            }
            for key in ("主体", "动作", "话术", "风险", "验证指标"):
                text = str(item.get(key, fallbacks[key]) or fallbacks[key])
                rows.append(f"<li><strong>{html.escape(key, quote=True)}：</strong>{html.escape(text, quote=True)}</li>")
            parts.append("<ul>" + "".join(rows) + "</ul>")
        parts.append("</div>")
    parts.append("</div>")
    return "".join(parts)


def _coerce_response_action_plan(value: Any) -> Dict[str, Any]:
    """
    将模型输出的 RESPONSE_ACTION_PLAN 规范为四阶段 dict。
    兼容：合法 dict、JSON 字符串、Python repr 单引号 dict（literal_eval）。
    """
    stages = ["24小时内", "3天内", "7天内", "复盘期"]
    defaults = _default_narrative("").get("RESPONSE_ACTION_PLAN")
    if not isinstance(defaults, dict):
        defaults = {}

    parsed: Any = value
    if isinstance(parsed, str):
        s = parsed.strip()
        if s:
            try:
                parsed = json.loads(s)
            except Exception:
                if s.startswith("{") and "'" in s:
                    try:
                        parsed = ast.literal_eval(s)
                    except Exception:
                        parsed = None
                else:
                    parsed = None
        else:
            parsed = None

    if not isinstance(parsed, dict):
        parsed = {}

    out: dict[str, Any] = {}
    for st in stages:
        items = parsed.get(st)
        if not isinstance(items, list):
            items = []
        fallback = defaults.get(st) if isinstance(defaults.get(st), list) else []
        fixed: list[dict[str, str]] = []
        for it in items[:4]:
            if isinstance(it, dict):
                row = {
                    "主体": str(it.get("主体", "") or "").strip() or "责任部门",
                    "动作": str(it.get("动作", "") or "").strip() or "补充事实说明与处置进展",
                    "话术": str(it.get("话术", "") or "").strip() or "我们将以可核验事实持续更新处置进展。",
                    "风险": str(it.get("风险", "") or "").strip() or "信息不足导致二次猜测",
                    "验证指标": str(it.get("验证指标", "") or "").strip() or "核心质疑关键词与负面声量是否回落",
                }
                fixed.append(row)
            elif isinstance(it, str) and it.strip():
                fixed.append(
                    {
                        "主体": "责任部门",
                        "动作": it.strip()[:240],
                        "话术": "我们将结合公众关切逐项说明依据、流程与后续改进。",
                        "风险": "表述笼统导致不信任",
                        "验证指标": "高频质疑点的回应覆盖率",
                    }
                )
        out[st] = fixed if fixed else (list(fallback) if fallback else [])

    return out


def _inject_sentiment_data_anchor(text_map: Dict[str, Any], report_data: Dict[str, Any]) -> None:
    """强制情感分析首条与 charts.sentiment、KPI 同源，避免模型写出与饼图矛盾的条数。"""
    charts = report_data.get("charts") if isinstance(report_data.get("charts"), dict) else {}
    rows = list(charts.get("sentiment", []) or [])
    sm = {str(x.get("name", "")): _safe_int(x.get("value", 0), 0) for x in rows if isinstance(x, dict)}
    pos = sm.get("正面", 0)
    neu = sm.get("中立", sm.get("中性", 0))
    neg = sm.get("负面", 0)
    tot = max(1, pos + neu + neg)
    neg_ratio = round(100.0 * neg / tot, 1)
    anchor = (
        f"样本情感分布为正面{pos}条、中立{neu}条、负面{neg}条，负面占比约{neg_ratio}%；"
        f"本段与报告顶部 KPI「整体态度」及左侧饼图同源。"
    )
    cur = text_map.get("CHART_SENTIMENT_ANALYSIS")
    if not isinstance(cur, list):
        text_map["CHART_SENTIMENT_ANALYSIS"] = [anchor, "建议结合高互动样本核查讽刺、反话等是否被误判为中立。", "将情感分布与时间线、关键词交叉解读。"]
        return
    body = [_polish_report_prose(str(x).strip()) for x in cur if str(x).strip()]
    body2 = [b for b in body if not re.match(r"^样本情感分布为正面\s*\d+\s*条", b)]
    merged = [anchor] + body2
    text_map["CHART_SENTIMENT_ANALYSIS"] = merged[:3]


def _to_bulleted_list_html(value: Any) -> str:
    if isinstance(value, list):
        items = [str(x).strip() for x in value if str(x).strip()]
    else:
        raw = str(value or "").strip()
        if not raw:
            items = []
        elif "<li>" in raw.lower():
            return raw
        elif "\n" in raw:
            items = [x.strip("-• \t") for x in raw.splitlines() if x.strip()]
        elif "|" in raw:
            items = [x.strip() for x in raw.split("|") if x.strip()]
        else:
            items = [raw]
    if not items:
        items = ["暂无可展示结论"]
    return "".join(f"<li>{html.escape(it, quote=True)}</li>" for it in items)


def _contains_english_phrase(value: str) -> bool:
    s = str(value or "").strip()
    if not s:
        return False
    # 只在“几乎纯英文短语”时触发，避免中文句子含少量英文/API/GraphRAG 被误判。
    if re.search(r"[\u4e00-\u9fff]", s):
        return False
    return bool(re.search(r"^[\s\-_/A-Za-z0-9.,:;!?()]+$", s) and re.search(r"[A-Za-z]{3,}", s))


def _polish_report_prose(value: str) -> str:
    """Lightly remove source-path prefixes, process leakage and over-confident crisis wording."""
    s = str(value or "")
    if not s:
        return s
    s = re.sub(
        r"引用知识库路径《[^》]+》中的[“\"]?([^”\"。；，,]+)[”\"]?理论[。；，,]?",
        r"根据“\1”理论，",
        s,
    )
    s = re.sub(
        r"引用知识库路径《[^》]+》中的[“\"]?([^”\"。；，,]+)[”\"]?[。；，,]?",
        r"围绕“\1”，",
        s,
    )
    s = re.sub(r"引用知识库路径《[^》]+》[。；，,]?", "", s)
    replacements = {
        "精准踩中女性价值观与公序良俗红线": "恰好触发公众对饭圈化表达和品牌自我表达误区的反感",
        "精准踩中": "恰好触发",
        "完美契合": "较为符合",
        "瞬间激活": "触发",
        "转危为机": "转入可核验整改",
        "将此次危机转化为品牌价值观升级的契机，联合行业协会制定内容伦理标准": "把整改重心放在内部审核、营销边界和第三方复核机制上，避免过早拔高为行业标准制定",
        "将此次危机转化为品牌价值观升级的契机": "把此次事件作为内部审核与营销边界整改的起点",
        "制定内容伦理标准": "完善自身内容审核标准",
        "女性价值观与公序良俗红线": "饭圈化表达和品牌内容边界",
        "公序良俗与女性尊严底线": "饭圈化表达和品牌内容边界",
    }
    for old, new in replacements.items():
        s = s.replace(old, new)
    source_prefix = (
        r"(?:根据|结合|基于)?(?:用户补充(?:意见|材料|线索)?|用户反馈|用户研判|专家研判|专家人工研判|"
        r"补充专家研判|协同输入|研判输入|用户协同输入|填写的研判表单|外部参考)"
        r"(?:指出|认为|显示|补充|提供|提到|研判)?"
    )
    s = re.sub(rf"^\s*{source_prefix}[：:，,\s]*", "", s)
    s = re.sub(rf"[（(]\s*{source_prefix}\s*[）)]", "", s)
    s = re.sub(rf"{source_prefix}[：:]\s*", "", s)
    process_replacements = {
        "本次过程文件": "本次监测数据",
        "过程文件 JSON": "平台公开数据与结构化监测",
        "过程文件json": "平台公开数据与结构化监测",
        "过程文件显示": "监测数据显示",
        "过程文件": "监测数据",
        "用户补充意见": "公开补充线索",
        "用户补充": "公开补充线索",
        "补充专家研判": "专业研判",
        "专家人工研判": "专业研判",
        "协同输入": "综合信息",
        "填写的研判表单": "研判材料",
        "大模型生成": "系统研判",
        "机器生成": "系统研判",
    }
    for old, new in process_replacements.items():
        s = s.replace(old, new)
    s = re.sub(r"\s+", " ", s).strip() if re.search(r"[A-Za-z]{2,}", s) else s.strip()
    return s


def _sanitize_narrative_language(text_map: Dict[str, Any], defaults: Dict[str, str]) -> Dict[str, Any]:
    """过滤英文叙事，保证最终模板文本为中文。"""
    sanitized: Dict[str, Any] = dict(text_map)
    for key, value in list(sanitized.items()):
        if key not in _PLACEHOLDER_KEYS:
            continue
        if key in {"AUTHOR", "DEPARTMENT"}:
            continue
        if key in _LIST_PLACEHOLDER_KEYS:
            if isinstance(value, list):
                raw_items = [_polish_report_prose(str(x).strip()) for x in value if str(x).strip()]
                cleaned_items = [x for x in raw_items if not _contains_english_phrase(x)]
                if cleaned_items:
                    sanitized[key] = cleaned_items
                elif raw_items:
                    sanitized[key] = raw_items
                else:
                    sanitized[key] = defaults.get(key, "待补充")
            elif _contains_english_phrase(str(value)):
                sanitized[key] = defaults.get(key, "待补充")
            else:
                sanitized[key] = _polish_report_prose(str(value))
        else:
            if key == "RESPONSE_ACTION_PLAN":
                sanitized[key] = _coerce_response_action_plan(value)
                continue
            if _contains_english_phrase(str(value)):
                sanitized[key] = defaults.get(key, "待补充")
            else:
                sanitized[key] = _polish_report_prose(str(value))
    return sanitized


def _has_placeholder_text(value: Any) -> bool:
    s = str(value or "").strip()
    if not s:
        return True
    marks = (
        "证据不足",
        "待补充",
        "placeholder",
        "todo",
        "请补充分析",
        "请结合图表",
        "请结合传播链路",
        "请补充",
        "已生成结构化报告",
        "更细结论",
        "专题约束",
        "过程文件 json",
        "过程文件json",
        "—",
        "-",
        "－",
    )
    return any(m in s.lower() for m in [x.lower() for x in marks])


def _fill_missing_narrative_sections(text_map: Dict[str, Any], report_data: Dict[str, Any]) -> Dict[str, Any]:
    """Use structured report_data to fill weak placeholder sections."""
    out: Dict[str, Any] = dict(text_map)
    charts = report_data.get("charts") if isinstance(report_data.get("charts"), dict) else {}
    lifecycle = charts.get("lifecycle") if isinstance(charts.get("lifecycle"), dict) else {}
    stages = lifecycle.get("stages") if isinstance(lifecycle.get("stages"), list) else []
    values = lifecycle.get("values") if isinstance(lifecycle.get("values"), list) else []
    boundaries = lifecycle.get("boundaries") if isinstance(lifecycle.get("boundaries"), list) else []
    timeline = report_data.get("timeline") if isinstance(report_data.get("timeline"), list) else []

    def _top_names(block_name: str, *, limit: int = 3) -> List[str]:
        block = charts.get(block_name) if isinstance(charts.get(block_name), dict) else {}
        names = list(block.get("names", []) or [])
        vals = list(block.get("values", []) or [])
        pairs = [(str(n).strip(), _safe_int(v, 0)) for n, v in zip(names, vals) if str(n).strip()]
        pairs = [p for p in pairs if p[0] != "—" and not p[0].startswith("暂无")]
        pairs.sort(key=lambda x: x[1], reverse=True)
        return [n for n, _ in pairs[:limit]]

    def _sentiment_counts() -> tuple[int, int, int]:
        rows = list(charts.get("sentiment", []) or [])
        sent = {str(x.get("name", "")): _safe_int(x.get("value", 0), 0) for x in rows if isinstance(x, dict)}
        return sent.get("正面", 0), sent.get("中立", sent.get("中性", 0)), sent.get("负面", 0)

    if _has_placeholder_text(out.get("SUMMARY_BULLETS")):
        pos, neu, neg = _sentiment_counts()
        timeline_count = len([x for x in timeline if isinstance(x, dict) and str(x.get("event", "")).strip()])
        peak = max([_safe_int(v, 0) for v in values], default=0)
        out["SUMMARY_BULLETS"] = [
            f"已汇总情感分布：正面{pos}条、中立{neu}条、负面{neg}条。",
            f"已提取时间线节点{timeline_count}个，最高声量约为{peak}。",
            "后续判断应以最新权威回应、高互动样本和平台扩散变化交叉验证。",
        ]

    if _has_placeholder_text(out.get("CHART_SENTIMENT_ANALYSIS")):
        pos, neu, neg = _sentiment_counts()
        total = max(1, pos + neu + neg)
        dominant = max([("正面", pos), ("中立", neu), ("负面", neg)], key=lambda x: x[1])
        out["CHART_SENTIMENT_ANALYSIS"] = [
            f"情感分布中{dominant[0]}占比最高（{round(dominant[1] * 100.0 / total, 1)}%），可作为判断舆论基调的基础信号。",
            f"负面样本为{neg}条，建议结合高互动内容核查是否存在风险叙事集中扩散。",
            "情感图应与声量高峰、关键词变化交叉解读，避免只凭单一比例作确定性判断。",
        ]

    if _has_placeholder_text(out.get("CHART_TIMELINE_ANALYSIS")):
        timeline_count = len([x for x in timeline if isinstance(x, dict) and str(x.get("event", "")).strip()])
        out["CHART_TIMELINE_ANALYSIS"] = [
            f"时间线共整理{timeline_count}个节点，用于观察事件从触发到扩散的顺序关系。",
            "节点密集区通常对应媒体再传播、当事方回应或平台二次分发等关键阶段。",
            "建议将时间线与声量曲线叠加复核，识别真正推动热度变化的触发点。",
        ]

    if _has_placeholder_text(out.get("CHART_VOLUME_ANALYSIS")):
        volume = charts.get("volume") if isinstance(charts.get("volume"), dict) else {}
        post_counts = [_safe_int(v, 0) for v in list(volume.get("postCounts", []) or [])]
        peak = max(post_counts, default=0)
        out["CHART_VOLUME_ANALYSIS"] = [
            f"声量序列峰值约为{peak}，反映公众关注在特定窗口出现集中抬升。",
            "若峰值后快速回落，说明议题可能进入消退阶段；若长尾持续，应关注次生话题。",
            "建议重点复盘峰值前后的关键词和高互动样本，定位传播加速因素。",
        ]

    if _has_placeholder_text(out.get("CHART_AUTHOR_ANALYSIS")):
        top_authors = _top_names("author", limit=3)
        out["CHART_AUTHOR_ANALYSIS"] = [
            f"高频发布者主要包括：{'、'.join(top_authors) if top_authors else '暂无显著高频作者'}。",
            "头部账号会影响议题扩散速度，但仍需结合互动量和内容立场判断真实影响力。",
            "建议持续跟踪关键账号后续发文，识别是否存在二次放大或话题转向。",
        ]

    if _has_placeholder_text(out.get("CHART_RADAR_ANALYSIS")):
        radar_values = list(charts.get("radarValues", []) or [])
        radar_text = "、".join(str(_safe_int(v, 0)) for v in radar_values[:5]) if radar_values else "暂无雷达评分"
        out["CHART_RADAR_ANALYSIS"] = [
            f"五维雷达评分约为：{radar_text}。",
            "雷达图用于快速观察声量、质量、参与广度、场景复杂度和影响强度是否均衡。",
            "分值较低的维度应通过补采样、补证据或专项分析进一步核验。",
        ]

    # Lifecycle chart analysis fallback
    if _has_placeholder_text(out.get("CHART_LIFECYCLE_ANALYSIS")):
        peak = max([_safe_int(v, 0) for v in values], default=0)
        latest = str(stages[-1] if stages else "衰退")
        trans = "、".join(str(b.get("name", "")).strip() for b in boundaries[:3] if isinstance(b, dict) and str(b.get("name", "")).strip())
        recent = [_safe_int(v, 0) for v in values[-3:]]
        if "衰退" in latest or "结束" in latest:
            lead = (
                f"当前图表阶段显示为{latest}期，近期声量峰值约为{peak}；"
                "但声量回落不等于风险结束，若核心疑点未被权威回应，仍可能因新证据或关键账号再发声出现二次抬升。"
            )
        elif recent and recent[-1] >= max(recent[:1] or [0]):
            lead = f"当前阶段判定为{latest}期，近期声量仍有抬升迹象，需按扩散或再爆发窗口进行监测。"
        else:
            lead = f"当前阶段判定为{latest}期，近期声量峰值约为{peak}，仍需结合疑点回应情况判断是否真正收束。"
        out["CHART_LIFECYCLE_ANALYSIS"] = [
            lead,
            f"阶段迁移链路为：{trans or '潜伏→扩散→爆发→衰退'}，与常见公共议题生命周期基本一致。",
            "后续可重点观察是否出现二次抬升信号（新素材传播、关键账号再发声、媒体再聚焦）。",
        ]

    # Channel distribution fallback (when JSON exists but narrative missing)
    if _has_placeholder_text(out.get("CHART_CHANNEL_ANALYSIS")):
        channel = list(charts.get("channel", []) or [])
        channel = [x for x in channel if isinstance(x, dict) and str(x.get("name", "")).strip()]
        channel.sort(key=lambda x: _safe_int(x.get("value", 0), 0), reverse=True)
        if channel:
            top = channel[:3]
            total = sum(_safe_int(x.get("value", 0), 0) for x in channel) or 1
            top_sum = sum(_safe_int(x.get("value", 0), 0) for x in top)
            share = round(100.0 * top_sum / total, 1)
            top_names = [str(x.get("name", "")).strip() for x in top if str(x.get("name", "")).strip()]
            top_desc = "、".join(top_names) if top_names else "主要平台"
            out["CHART_CHANNEL_ANALYSIS"] = [
                f"渠道声量主要集中在「{top_desc}」（Top{len(top)} 合计约{share}%），呈现一定渠道集中度。",
                "建议结合不同渠道的内容形态差异（短视频/问答/资讯）调整回应载体与节奏，避免单点渠道失守引发跨平台扩散。",
                "如需精细化处置，可进一步下钻到各渠道的高互动样本与核心发布者，识别传播链关键节点。",
            ]

    # Theory slots fallback：避免长期出现“证据不足”与固定三件套复读
    def _pick_distinct_theory_texts() -> Dict[str, str]:
        sent_items = list(charts.get("sentiment", []) or [])
        sent_map = {str(x.get("name", "")): _safe_int(x.get("value", 0), 0) for x in sent_items if isinstance(x, dict)}
        pos = sent_map.get("正面", 0)
        neg = sent_map.get("负面", 0)
        neu = sent_map.get("中立", sent_map.get("中性", 0))
        total = max(1, pos + neg + neu)
        neg_ratio = round(100.0 * neg / total, 1)
        pos_ratio = round(100.0 * pos / total, 1)

        region_names = list(charts.get("region", {}).get("names", []) or [])
        region_vals = list(charts.get("region", {}).get("values", []) or [])
        region_pairs = [(str(n), _safe_int(v, 0)) for n, v in zip(region_names, region_vals) if str(n).strip() and str(n).strip() != "—"]
        region_pairs.sort(key=lambda x: x[1], reverse=True)
        region_hint = "、".join([n for n, _ in region_pairs[:2]]) if region_pairs else "部分地区"

        channel = list(charts.get("channel", []) or [])
        channel = [x for x in channel if isinstance(x, dict) and str(x.get("name", "")).strip()]
        channel.sort(key=lambda x: _safe_int(x.get("value", 0), 0), reverse=True)
        top_channel = str(channel[0].get("name", "")).strip() if channel else "头部渠道"

        lifecycle = charts.get("lifecycle") if isinstance(charts.get("lifecycle"), dict) else {}
        stage = str((lifecycle.get("stages") or ["衰退"])[-1]) if isinstance(lifecycle.get("stages"), list) else "衰退"

        candidates: List[tuple[str, str]] = [
            (
                "THEORY_SILENCE",
                f"情绪感染与群体极化：当前负面占比约{neg_ratio}%（正面约{pos_ratio}%），讨论容易在高互动样本中形成同温层放大；"
                "建议用“可执行信息 + 明确规则边界 + 同理表达”组合，降低对立叙事的情绪黏性。",
            ),
            (
                "THEORY_AGENDA",
                f"框架竞争与议程迁移：议题往往在“秩序维护/公共规则”与“个体权益/服务体验”之间来回切换；"
                f"当讨论进入{stage}期，更需要用稳定口径把争议点收敛到可复核的事实与处理标准，避免被二次切片带节奏。",
            ),
            (
                "THEORY_BUTTERFLY",
                f"风险感知放大（社会放大框架）：单点冲突经{top_channel}等平台二次传播后，容易被上升为公共治理/服务能力的象征性争论；"
                f"可结合{region_hint}等活跃地区的高互动样本做针对性解释与服务补位，降低次生扩散概率。",
            ),
        ]

        # 去重：避免三个槽位在关键词上高度重复
        picked: Dict[str, str] = {}
        used_signatures: set[str] = set()
        for k, txt in candidates:
            sig = "|".join(sorted(set(re.findall(r"[\u4e00-\u9fff]{2,6}", txt)))[0:10])
            if sig in used_signatures:
                continue
            used_signatures.add(sig)
            picked[k] = txt
        return picked

    theory_texts = _pick_distinct_theory_texts()
    if _has_placeholder_text(out.get("THEORY_SILENCE")):
        out["THEORY_SILENCE"] = theory_texts.get("THEORY_SILENCE", out.get("THEORY_SILENCE", ""))
    if _has_placeholder_text(out.get("THEORY_AGENDA")):
        out["THEORY_AGENDA"] = theory_texts.get("THEORY_AGENDA", out.get("THEORY_AGENDA", ""))
    if _has_placeholder_text(out.get("THEORY_BUTTERFLY")):
        out["THEORY_BUTTERFLY"] = theory_texts.get("THEORY_BUTTERFLY", out.get("THEORY_BUTTERFLY", ""))

    # Intro trigger fallback
    if _has_placeholder_text(out.get("INTRO_TRIGGERS")):
        out["INTRO_TRIGGERS"] = "高热触发通常来自“规则执行争议 + 视频化传播 + 媒体再放大”的叠加效应。"

    # Recap fallback
    if _has_placeholder_text(out.get("RECAP_DISCOURSE")):
        out["RECAP_DISCOURSE"] = "该议题的核心不是单点事实，而是公众对“规则执行是否一致、是否可感知”的持续关注。"
    if _has_placeholder_text(out.get("RECAP_TRENDS")):
        latest = str(stages[-1] if stages else "衰退")
        if "衰退" in latest or "结束" in latest:
            out["RECAP_TRENDS"] = (
                f"当前图表阶段显示为{latest}期，但若关键事实仍未公开、责任边界仍未说明，舆论并不会自然结束。"
                "建议把策略重心放在权威说明、证据公开和后续整改闭环上，防止长尾讨论重新聚合为新一轮爆发。"
            )
        else:
            out["RECAP_TRENDS"] = f"当前整体处于{latest}期，应优先补齐事实说明、回应高频质疑并监测跨平台扩散信号。"

    return out


def merge_morandi_template(
    template_html: str,
    text_map: Dict[str, Any],
    report_config: Dict[str, Any],
    report_data: Dict[str, Any],
) -> str:
    """替换 JSON 锚点与 {{KEY}} 占位符。"""
    cfg_json = json.dumps(report_config, ensure_ascii=False, separators=(",", ":"))
    data_json = json.dumps(report_data, ensure_ascii=False, separators=(",", ":"))
    out = template_html.replace(REPORT_CONFIG_ANCHOR, cfg_json).replace(REPORT_DATA_ANCHOR, data_json)

    for k in _PLACEHOLDER_KEYS:
        token = "{{" + k + "}}"
        val = text_map.get(k, "—")
        if k == "RESPONSE_ACTION_PLAN":
            out = out.replace(token, _to_action_plan_html(val))
        elif k in _LIST_PLACEHOLDER_KEYS:
            out = out.replace(token, _to_bulleted_list_html(val))
        else:
            out = out.replace(token, html.escape(str(val), quote=True))
    # 轻量措辞清洗：避免“知识库建议”类前缀污染主报告叙事（引用应在脚注/来源区体现）
    out = out.replace("知识库建议", "").replace("知识库提示", "")
    return out


def build_html_from_morandi_template(
    *,
    template_path: Path,
    json_files: List[Dict[str, Any]],
    event_introduction: str,
    analysis_results_text: str,
    methodology_text: str,
    kb_priority_text: str = "",
    report_length: str = "中篇",
) -> str:
    """读取模板、抽取数据、调用叙事模型、合并输出。"""
    template_html = template_path.read_text(encoding="utf-8")
    report_config = build_report_config_from_json_files(json_files)
    report_data = build_report_data_from_json_files(json_files)
    meta = build_meta_placeholders(json_files, event_introduction)
    meta_json = json.dumps(meta, ensure_ascii=False, indent=2)

    meth_budget = int(str(os.environ.get("SONA_REPORT_TEMPLATE_METHODOLOGY_CHARS", "20000") or "20000"))
    meth_budget = max(8000, min(meth_budget, 64_000))
    meth_trunc = (methodology_text or "")[:meth_budget]

    narrative = call_llm_for_template_narrative(
        event_introduction=event_introduction or "",
        analysis_results_text=analysis_results_text or "",
        methodology_text=meth_trunc,
        meta_json=meta_json,
        kb_priority_text=kb_priority_text or "",
        report_length=normalize_report_length(report_length),
    )

    # 合并：默认 → 模型叙事 → 程序元信息（后者覆盖数字类字段）
    defaults = _default_narrative(event_introduction)
    text_map: Dict[str, Any] = dict(defaults)
    if isinstance(narrative, dict):
        for k, v in narrative.items():
            ks = str(k)
            if ks in _PLACEHOLDER_KEYS and v is not None:
                if ks == "RESPONSE_ACTION_PLAN" and isinstance(v, dict):
                    text_map[ks] = v
                elif ks in _LIST_PLACEHOLDER_KEYS and isinstance(v, list):
                    text_map[ks] = [str(x).strip() for x in v if str(x).strip()]
                else:
                    text_map[ks] = str(v).strip()
    text_map.update(meta)
    # 明确口径：地域分析来自 IP 属地统计（region_stats），用于约束模型不要输出“未提取明确地域分布”类误导文案
    text_map.setdefault("REGION_SOURCE", "IP属地")
    sample = text_map.get("SAMPLE_SIZE", "—")
    effective = text_map.get("EFFECTIVE_VOLUME", "—")
    sample_int = _safe_int_from_text(sample, 0)
    effective_int = _safe_int_from_text(effective, 0)
    text_map["KPI_TOTAL"] = sample
    text_map["KPI_EFFECTIVE"] = "—"
    text_map.setdefault("DATA_SOURCE", "平台公开数据与结构化监测")
    volume_chart = report_data.get("charts", {}).get("volume", {})
    lifecycle_values = list(volume_chart.get("postCounts", []) or volume_chart.get("values", []) or [])
    text_map["PHASE_STATUS"] = _phase_status_from_report_data(report_data, json_files)
    text_map["KPI_NEG_RATIO"] = text_map["PHASE_STATUS"]

    sent = _find_sentiment_json(json_files)
    if sent and isinstance(sent.get("statistics"), dict):
        st_eff = _statistics_effective_ratios(sent["statistics"])
        text_map["KPI_POS_RATIO"] = _overall_attitude_label(st_eff)
        pos = float(st_eff.get("positive_ratio", 0.0) or 0.0)
        neg = float(st_eff.get("negative_ratio", 0.0) or 0.0)
        sentiment_balance = abs(pos - neg) * 100.0
    else:
        text_map.setdefault("KPI_POS_RATIO", "—")
        sentiment_balance = 30.0

    region_count = len(list(report_data.get("charts", {}).get("region", {}).get("names", []) or []))
    author_vals = list(report_data.get("charts", {}).get("author", {}).get("values", []) or [])
    author_total = sum(_safe_int(v, 0) for v in author_vals)
    top_author_share = (_safe_int(author_vals[0], 0) / float(author_total)) if author_total > 0 else 0.0
    impact_index = _compute_impact_index(
        sample_total=sample_int,
        effective_total=effective_int,
        trend_values=[_safe_int(v, 0) for v in lifecycle_values],
        region_count=region_count,
        top_author_share=top_author_share,
        sentiment_balance=sentiment_balance,
    )
    text_map["KPI_EFFECTIVE"] = str(impact_index)

    text_map = _sanitize_narrative_language(text_map, defaults)
    text_map = _fill_missing_narrative_sections(text_map, report_data)
    text_map["RESPONSE_ACTION_PLAN"] = _coerce_response_action_plan(text_map.get("RESPONSE_ACTION_PLAN"))
    _inject_sentiment_data_anchor(text_map, report_data)
    intro_val = str(text_map.get("INTRO_BACKGROUND", "") or "").strip()
    if len(intro_val) > 600:
        text_map["INTRO_BACKGROUND"] = intro_val[:600] + "..."

    # --------- 程序兜底：地域/关键词结论至少给出描述性分析 ---------
    def _is_weak_list(val: Any) -> bool:
        if not isinstance(val, list):
            return True
        items = [str(x).strip() for x in val if str(x).strip()]
        if not items:
            return True
        weak_hits = sum(1 for x in items if "证据不足" in x or "未提供" in x)
        return weak_hits >= max(1, len(items) // 2)

    region_text_raw = str(text_map.get("CHART_REGION_ANALYSIS", "") or "")
    # 若模型输出了误导句，且 region_stats 实际有结果，则强制清空并走程序兜底生成
    if "数据未提取明确地域分布统计，仅显示IP属地字段存在" in region_text_raw:
        names_probe = list(report_data.get("charts", {}).get("region", {}).get("names", []) or [])
        vals_probe = list(report_data.get("charts", {}).get("region", {}).get("values", []) or [])
        has_region_stats = any(str(n).strip() and str(n).strip() != "—" for n in names_probe) and any(
            _safe_int(v, 0) > 0 for v in vals_probe
        )
        if has_region_stats:
            text_map["CHART_REGION_ANALYSIS"] = []
    if _is_weak_list(text_map.get("CHART_REGION_ANALYSIS")):
        names = list(report_data.get("charts", {}).get("region", {}).get("names", []) or [])
        vals = list(report_data.get("charts", {}).get("region", {}).get("values", []) or [])
        pairs = [(str(n), _safe_int(v, 0)) for n, v in zip(names, vals) if str(n).strip()]
        pairs = [p for p in pairs if p[0] != "—"]
        pairs.sort(key=lambda x: x[1], reverse=True)
        if pairs:
            top = pairs[:3]
            total = sum(v for _, v in pairs) or 1
            top_sum = sum(v for _, v in top)
            share = round(100.0 * top_sum / total, 1)
            top_names = [n for n, _ in top if n]
            top_desc = "、".join(top_names) if top_names else "主要地区"
            text_map["CHART_REGION_ANALYSIS"] = [
                f"主要声量集中在「{top_desc}」等地（Top{len(top)}合计约{share}%），呈现明显区域聚集特征。",
                "地域分布显示讨论在部分省市更活跃，说明传播与本地社会经验、平台用户结构存在关联。",
                "若需进一步验证地域差异来源，可补充同城高互动样本与地方媒体链路进行交叉核验。",
            ]

    if _is_weak_list(text_map.get("CHART_KEYWORD_ANALYSIS")):
        kws = list(report_data.get("charts", {}).get("keyword", []) or [])
        kws = [x for x in kws if isinstance(x, dict) and str(x.get("name", "") or "").strip()]
        kws.sort(key=lambda x: _safe_int(x.get("value", 0), 0), reverse=True)
        if kws:
            topw = [str(x["name"]) for x in kws[:8]]
            keyword_diag = {}
            if isinstance(report_data.get("diagnostics"), dict):
                keyword_diag = report_data.get("diagnostics", {}).get("keyword", {})
            generic_terms = []
            if isinstance(keyword_diag, dict):
                generic_terms = [str(x) for x in (keyword_diag.get("generic_terms") or []) if str(x).strip()]
            pollution_note = ""
            if generic_terms and bool(keyword_diag.get("pollution_suspected", False)):
                pollution_note = (
                    f"原始热词中曾出现「{'、'.join(generic_terms[:5])}」等泛词，提示采集口径存在同名词或生活类内容混入风险；"
                    "关键词解读应优先回到事件专属主体、地点、诉求和处置进展。"
                )
            text_map["CHART_KEYWORD_ANALYSIS"] = [
                f"高频关键词集中在「{'、'.join(topw[:5])}」等，讨论焦点更偏向冲突场景与规则认知，而非单一事实复述。",
                pollution_note or "关键词结构中情绪词和评价词占比较高时，通常意味着讨论正在从事实层向立场层迁移。",
                "可持续跟踪 Top200 热词的主题簇变化，观察议题是否出现外溢和泛化。",
            ]

    recap_discourse = str(text_map.get("RECAP_DISCOURSE", "") or "").strip()
    recap_trends = str(text_map.get("RECAP_TRENDS", "") or "").strip()
    recap_drivers = text_map.get("RECAP_DRIVERS_BULLETS")
    recap_drivers_weak = _is_weak_list(recap_drivers)
    recap_text_weak = (not recap_discourse) or ("证据不足" in recap_discourse) or (not recap_trends) or ("证据不足" in recap_trends)
    if recap_text_weak or recap_drivers_weak:
        stats = list(report_data.get("charts", {}).get("sentiment", []) or [])
        sent_map = {str(x.get("name", "")): _safe_int(x.get("value", 0), 0) for x in stats if isinstance(x, dict)}
        pos = sent_map.get("正面", 0)
        neg = sent_map.get("负面", 0)
        neu = sent_map.get("中立", sent_map.get("中性", 0))
        total = max(1, pos + neg + neu)
        neg_ratio = round(100.0 * neg / total, 1)
        kws = list(report_data.get("charts", {}).get("keyword", []) or [])
        kws = [x for x in kws if isinstance(x, dict) and str(x.get("name", "")).strip()]
        kws.sort(key=lambda x: _safe_int(x.get("value", 0), 0), reverse=True)
        hot_words = [str(x.get("name", "")).strip() for x in kws[:6] if str(x.get("name", "")).strip()]
        stage = str(text_map.get("PHASE_STATUS", "") or "衰退期")
        if recap_text_weak:
            text_map["RECAP_DISCOURSE"] = (
                f"本次事件呈现“规则诉求与情绪宣泄并行”的典型公共空间舆情结构：一方面，围绕公共秩序、监护责任与文明乘车形成较强规范讨论；"
                f"另一方面，情绪化表达在高热节点集中释放（当前负面占比约{neg_ratio}%），推动议题从个体冲突外溢到群体价值争论。"
            )
            text_map["RECAP_TRENDS"] = (
                f"从阶段看已进入{stage}，但仍需警惕二次传播触发：若出现新视频切片、当事人后续发声或平台再分发，事件可能由长尾回弹为阶段性小高潮。"
                f"建议在节假日、晚高峰等高风险时段提前部署“规则说明+服务缓冲”组合策略，减少同类冲突复发。"
            )
        if recap_drivers_weak:
            word_hint = "、".join(hot_words[:4]) if hot_words else "公共秩序、家长责任、乘客体验"
            text_map["RECAP_DRIVERS_BULLETS"] = [
                f"驱动因素一：冲突场景具备高可代入性，关键词「{word_hint}」触发广泛自我投射，导致普通用户高强度参与评论与转发。",
                "驱动因素二：平台分发机制放大“短视频冲突瞬间”，情绪峰值内容更易获得二次曝光，从而延长议题寿命并加剧立场分化。",
                "驱动因素三：治理预期与现实体验存在落差，公众希望看到可执行的处置闭环；建议发布清晰规则口径、升级静音/提醒机制并持续复盘公开。",
            ]

    return merge_morandi_template(template_html, text_map, report_config, report_data)
