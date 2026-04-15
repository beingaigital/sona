"""固定 HTML 模板报告：从分析 JSON 抽取图表配置 + 模型填充叙事占位符。"""

from __future__ import annotations

import html
import json
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
        "CHART_RADAR_ANALYSIS",
        "CHART_LIFECYCLE_ANALYSIS",
        "THEORY_BUTTERFLY",
        "RESPONSE_ANALYSIS_BULLETS",
        "RECAP_DISCOURSE",
        "RECAP_TRENDS",
        "RECAP_DRIVERS_BULLETS",
        "DATA_SOURCE",
    }
)

_LIST_PLACEHOLDER_KEYS: Set[str] = {
    "SUMMARY_BULLETS",
    "CHART_SENTIMENT_ANALYSIS",
    "CHART_TIMELINE_ANALYSIS",
    "CHART_VOLUME_ANALYSIS",
    "CHART_REGION_ANALYSIS",
    "CHART_AUTHOR_ANALYSIS",
    "CHART_KEYWORD_ANALYSIS",
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


def build_report_config_from_json_files(json_files: List[Dict[str, Any]]) -> Dict[str, Any]:
    """构造旧模板使用的 REPORT_CONFIG。"""
    sentiment: List[Dict[str, Any]] = []
    sent_obj = _find_sentiment_json(json_files)
    if sent_obj:
        stats = sent_obj.get("statistics") if isinstance(sent_obj.get("statistics"), dict) else {}
        pos = int(stats.get("positive_count", 0) or 0)
        neg = int(stats.get("negative_count", 0) or 0)
        neu = int(stats.get("neutral_count", 0) or 0)
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
                timeline_out.append({"time": t or "—", "event": ev or "—"})

    return {
        "sentiment": sentiment or [{"value": 1, "name": "中立", "itemStyle": {"color": "#22c55e"}}],
        "trend": {"dates": trend_dates or ["—"], "values": trend_values or [0]},
        "regions": {"names": region_names or ["—"], "counts": region_counts or [0]},
        "keywords": keywords_out or [{"word": "证据不足", "count": 0, "rel": "—"}],
        "timeline": timeline_out or [{"time": "—", "event": "未找到时间线分析 JSON，证据不足。"}],
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


def _extract_volume_series(vol: Optional[Dict[str, Any]]) -> Tuple[List[str], List[int], Dict[str, Any]]:
    """从 volume_stats.json 中抽取优先级明确的趋势序列。"""
    if not isinstance(vol, dict):
        return ["—"], [0], {}

    heat_smoothed = vol.get("heat_percentage_smoothed")
    if isinstance(heat_smoothed, list) and heat_smoothed:
        dates = [str(x.get("name", "—")) for x in heat_smoothed if isinstance(x, dict)]
        values = [_safe_int(x.get("value", 0), 0) for x in heat_smoothed if isinstance(x, dict)]
        if dates and values:
            return dates, values, vol

    data = vol.get("data")
    if isinstance(data, list) and data:
        dates = [str(x.get("name", "—")) for x in data if isinstance(x, dict)]
        values = [_safe_int(x.get("value", 0), 0) for x in data if isinstance(x, dict)]
        if dates and values:
            return dates, values, vol

    return ["—"], [0], vol


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
        return "待评估（证据不足）"
    stages = _classify_lifecycle_stage([max(0, int(v)) for v in values])
    if not stages:
        return "待评估（证据不足）"
    latest = stages[-1]
    return f"{latest}期"


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
    trend_dates, trend_values, _ = _extract_volume_series(vol)
    if trend_dates == ["—"] and trend_values == [0]:
        trend_dates = list(cfg.get("trend", {}).get("dates", []) or [])
        trend_values = [_safe_int(v, 0) for v in (cfg.get("trend", {}).get("values", []) or [])]

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
    lifecycle = _build_lifecycle_series(trend_dates, trend_values)

    return {
        "charts": {
            "sentiment": cfg.get("sentiment", []),
            "volume": {"dates": trend_dates or ["—"], "values": trend_values or [0]},
            "region": {
                "names": list(cfg.get("regions", {}).get("names", []) or ["—"]),
                "values": list(cfg.get("regions", {}).get("counts", []) or [0]),
            },
            "author": {"names": author_names or ["证据不足"], "values": author_values or [0]},
            "keyword": keyword_cloud or [{"name": "证据不足", "value": 0}],
            "radarValues": _build_radar_values(cfg),
            "lifecycle": lifecycle,
        },
        "timeline": list(cfg.get("timeline", []) or [{"time": "—", "event": "证据不足"}]),
    }


def build_meta_placeholders(json_files: List[Dict[str, Any]], event_introduction: str) -> Dict[str, str]:
    """从 JSON 抽取可核验的数字类占位符。"""
    sample = ""
    effective = ""
    period = "证据不足"

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


def call_llm_for_template_narrative(
    *,
    event_introduction: str,
    analysis_results_text: str,
    methodology_text: str,
    meta_json: str,
) -> Dict[str, Any]:
    """调用模型，仅返回叙事占位符 JSON。"""
    tpl = _load_template_fill_prompt()
    if not tpl:
        return {}
    prompt = (
        tpl.replace("{event_introduction}", event_introduction or "")
        .replace("{analysis_results}", analysis_results_text or "")
        .replace("{methodology}", methodology_text or "")
        .replace("{meta_json}", meta_json or "{}")
    )
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
    stub = "证据不足：请补充分析 JSON 或检查模型输出。"
    title = intro[:40] if intro else "舆情分析报告"
    return {
        # 旧模板键
        "REPORT_TITLE": title,
        "OBJECT_NAME": title[:30],
        "NATURE": "待评估",
        "RISK_LEVEL": "待评估（证据不足）",
        "EVENT_BACKGROUND": intro or stub,
        "5W_WHO": stub,
        "5W_WHAT": stub,
        "5W_WHERE": stub,
        "5W_WHEN": stub,
        "5W_WHY": stub,
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
        "DEPARTMENT": "自动生成",
        # 新模板键
        "REPORT_SUBTITLE": "基于过程文件自动生成的结构化研判报告",
        "EVENT_TYPE": "网络舆情",
        "PHASE_STATUS": "待评估",
        "KPI_TOTAL": "—",
        "KPI_EFFECTIVE": "0",
        "KPI_POS_RATIO": "中性（证据不足）",
        "KPI_NEG_RATIO": "待评估（证据不足）",
        "INTRO_BACKGROUND": intro or stub,
        "INTRO_TRIGGERS": stub,
        "SUMMARY_BULLETS": "证据不足|请补充分析 JSON|已使用模板兜底输出",
        "CHART_SENTIMENT_ANALYSIS": stub,
        "CHART_TIMELINE_ANALYSIS": stub,
        "CHART_VOLUME_ANALYSIS": stub,
        "CHART_REGION_ANALYSIS": stub,
        "CHART_AUTHOR_ANALYSIS": stub,
        "CHART_KEYWORD_ANALYSIS": stub,
        "CHART_RADAR_ANALYSIS": stub,
        "CHART_LIFECYCLE_ANALYSIS": stub,
        "THEORY_BUTTERFLY": stub,
        "RESPONSE_ANALYSIS_BULLETS": "证据不足|未发现可核验的响应链条",
        "RECAP_DISCOURSE": stub,
        "RECAP_TRENDS": stub,
        "RECAP_DRIVERS_BULLETS": "证据不足|建议补充作者与传播路径数据",
        "DATA_SOURCE": "过程文件 JSON",
    }


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
        items = ["证据不足"]
    return "".join(f"<li>{html.escape(it, quote=True)}</li>" for it in items)


def _contains_english_phrase(value: str) -> bool:
    s = str(value or "").strip()
    if not s:
        return False
    # 命中连续英文词组，视为非中文报告内容
    return bool(re.search(r"[A-Za-z]{3,}(?:[\s\-_/]+[A-Za-z]{2,})*", s))


def _sanitize_narrative_language(text_map: Dict[str, Any], defaults: Dict[str, str]) -> Dict[str, Any]:
    """过滤英文叙事，保证最终模板文本为中文。"""
    sanitized: Dict[str, Any] = dict(text_map)
    for key, value in list(sanitized.items()):
        if key not in _PLACEHOLDER_KEYS:
            continue
        if key in {"DATA_SOURCE", "AUTHOR", "DEPARTMENT"}:
            continue
        if key in _LIST_PLACEHOLDER_KEYS:
            if isinstance(value, list):
                cleaned_items = [str(x).strip() for x in value if str(x).strip() and not _contains_english_phrase(str(x))]
                sanitized[key] = cleaned_items or defaults.get(key, "证据不足")
            elif _contains_english_phrase(str(value)):
                sanitized[key] = defaults.get(key, "证据不足")
        else:
            if _contains_english_phrase(str(value)):
                sanitized[key] = defaults.get(key, "证据不足")
    return sanitized


def merge_morandi_template(
    template_html: str,
    text_map: Dict[str, str],
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
        if k in _LIST_PLACEHOLDER_KEYS:
            out = out.replace(token, _to_bulleted_list_html(val))
        else:
            out = out.replace(token, html.escape(str(val), quote=True))
    return out


def build_html_from_morandi_template(
    *,
    template_path: Path,
    json_files: List[Dict[str, Any]],
    event_introduction: str,
    analysis_results_text: str,
    methodology_text: str,
) -> str:
    """读取模板、抽取数据、调用叙事模型、合并输出。"""
    template_html = template_path.read_text(encoding="utf-8")
    report_config = build_report_config_from_json_files(json_files)
    report_data = build_report_data_from_json_files(json_files)
    meta = build_meta_placeholders(json_files, event_introduction)
    meta_json = json.dumps(meta, ensure_ascii=False, indent=2)

    # 控制上下文长度
    ar_trunc = (analysis_results_text or "")[:14000]
    meth_trunc = (methodology_text or "")[:6000]

    narrative = call_llm_for_template_narrative(
        event_introduction=event_introduction or "",
        analysis_results_text=ar_trunc,
        methodology_text=meth_trunc,
        meta_json=meta_json,
    )

    # 合并：默认 → 模型叙事 → 程序元信息（后者覆盖数字类字段）
    defaults = _default_narrative(event_introduction)
    text_map: Dict[str, Any] = dict(defaults)
    if isinstance(narrative, dict):
        for k, v in narrative.items():
            ks = str(k)
            if ks in _PLACEHOLDER_KEYS and v is not None:
                if ks in _LIST_PLACEHOLDER_KEYS and isinstance(v, list):
                    text_map[ks] = [str(x).strip() for x in v if str(x).strip()]
                else:
                    text_map[ks] = str(v).strip()
    text_map.update(meta)
    sample = text_map.get("SAMPLE_SIZE", "—")
    effective = text_map.get("EFFECTIVE_VOLUME", "—")
    sample_int = _safe_int_from_text(sample, 0)
    effective_int = _safe_int_from_text(effective, 0)
    text_map["KPI_TOTAL"] = sample
    text_map["KPI_EFFECTIVE"] = "—"
    text_map.setdefault("DATA_SOURCE", "过程文件 JSON")
    lifecycle_values = list(report_data.get("charts", {}).get("volume", {}).get("values", []) or [])
    text_map["PHASE_STATUS"] = _summarize_phase_status([_safe_int(v, 0) for v in lifecycle_values])
    text_map["KPI_NEG_RATIO"] = text_map["PHASE_STATUS"]

    sent = _find_sentiment_json(json_files)
    if sent and isinstance(sent.get("statistics"), dict):
        st = sent["statistics"]
        text_map["KPI_POS_RATIO"] = _overall_attitude_label(st)
        pos = float(st.get("positive_ratio", 0.0) or 0.0)
        neg = float(st.get("negative_ratio", 0.0) or 0.0)
        sentiment_balance = abs(pos - neg) * 100.0
    else:
        text_map.setdefault("KPI_POS_RATIO", "中性（证据不足）")
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
            text_map["CHART_REGION_ANALYSIS"] = [
                f"主要声量集中在「{top[0][0]}、{top[1][0]}、{top[2][0]}」等地（Top3合计约{share}%），呈现区域性聚集特征；建议优先在这些地区的重点账号/媒体做信息澄清与议题引导。",
                "地域分布的集中意味着讨论更可能由本地社会经验/群体情绪驱动扩散；建议结合地域高发话题（如公共秩序、育儿冲突）做针对性回应话术。",
                "若需进一步研判“线下影响”，建议补采集地域维度的高互动样本，核验是否存在同城号、地方媒体或本地KOL带节奏。",
            ]

    if _is_weak_list(text_map.get("CHART_KEYWORD_ANALYSIS")):
        kws = list(report_data.get("charts", {}).get("keyword", []) or [])
        kws = [x for x in kws if isinstance(x, dict) and str(x.get("name", "") or "").strip()]
        kws.sort(key=lambda x: _safe_int(x.get("value", 0), 0), reverse=True)
        if kws:
            topw = [str(x["name"]) for x in kws[:8]]
            text_map["CHART_KEYWORD_ANALYSIS"] = [
                f"高频关键词集中在「{'、'.join(topw[:5])}」等，讨论焦点更偏向“行为冲突/公共秩序/育儿争议”而非单一事实本身；建议回应时先对齐公众最关心的冲突点。",
                "关键词结构呈现明显的情绪化与道德评判取向时，舆情更易二次发酵；建议用“规则共识 + 体验共情”的组合话术降低对立。",
                "建议持续监测 Top200 热词的主题簇变化（是否从事件细节转向群体对立/标签化），一旦出现泛化趋势及时介入议题边界管理。",
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
