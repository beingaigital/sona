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
        for row in kw["top_keywords"][:10]:
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


def _build_lifecycle_series_from_volume(vol: Optional[Dict[str, Any]], dates: List[str], values: List[int]) -> Dict[str, Any]:
    """优先使用 volume_stats 内置生命周期结果，否则生成兜底 one-hot。"""
    phase_names = ["潜伏期", "成长期", "成熟期", "衰退期"]
    if isinstance(vol, dict):
        lifecycle = vol.get("lifecycle")
        if isinstance(lifecycle, dict):
            stages_raw = lifecycle.get("stages")
            series_raw = lifecycle.get("series")
            if isinstance(stages_raw, list) and isinstance(series_raw, list):
                stages = []
                for row in stages_raw:
                    if isinstance(row, dict):
                        stages.append(str(row.get("stage", "潜伏期")))
                    else:
                        stages.append(str(row))
                series = [x for x in series_raw if isinstance(x, dict)]
                if stages and series:
                    return {
                        "dates": dates or ["—"],
                        "stages": stages,
                        "series": series,
                    }

    # 兜底：按阈值简单分段
    stages_fallback: List[str] = []
    for v in values:
        if v < 15:
            stages_fallback.append("潜伏期")
        elif v < 80:
            stages_fallback.append("成长期")
        elif v >= 50:
            stages_fallback.append("成熟期")
        else:
            stages_fallback.append("衰退期")

    phase_data: Dict[str, List[int]] = {p: [] for p in phase_names}
    for idx, val in enumerate(values):
        stage = stages_fallback[idx] if idx < len(stages_fallback) else "潜伏期"
        for p in phase_names:
            phase_data[p].append(val if p == stage else 0)

    return {
        "dates": dates or ["—"],
        "stages": stages_fallback or ["潜伏期"],
        "series": [{"name": p, "data": phase_data[p]} for p in phase_names],
    }


def _summarize_phase_status_from_volume(vol: Optional[Dict[str, Any]]) -> str:
    if not isinstance(vol, dict):
        return "待评估（证据不足）"
    lifecycle = vol.get("lifecycle")
    if not isinstance(lifecycle, dict):
        return "待评估（证据不足）"
    current = str(lifecycle.get("current_phase", "") or "").strip()
    if current:
        return f"{current}（规则判定）"
    return "待评估（证据不足）"


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

    keyword_names = [str(x.get("word", "") or "") for x in cfg.get("keywords", []) if isinstance(x, dict)]
    keyword_values = [_safe_int(x.get("count", 0), 0) for x in cfg.get("keywords", []) if isinstance(x, dict)]
    vol = _get_json_by_name(json_files, "volume_stats.json")
    trend_dates, trend_values, vol_obj = _extract_volume_series(vol)
    lifecycle = _build_lifecycle_series_from_volume(vol_obj, trend_dates, trend_values)

    return {
        "charts": {
            "sentiment": cfg.get("sentiment", []),
            "volume": {"dates": trend_dates or ["—"], "values": trend_values or [0]},
            "region": {
                "names": list(cfg.get("regions", {}).get("names", []) or ["—"]),
                "values": list(cfg.get("regions", {}).get("counts", []) or [0]),
            },
            "author": {"names": author_names or ["证据不足"], "values": author_values or [0]},
            "keyword": {"names": keyword_names or ["证据不足"], "values": keyword_values or [0]},
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
        "KPI_EFFECTIVE": "—",
        "KPI_POS_RATIO": "—",
        "KPI_NEG_RATIO": "—",
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
    text_map["KPI_TOTAL"] = sample
    text_map["KPI_EFFECTIVE"] = effective
    text_map.setdefault("DATA_SOURCE", "过程文件 JSON")
    vol_obj = _get_json_by_name(json_files, "volume_stats.json")
    text_map["PHASE_STATUS"] = _summarize_phase_status_from_volume(vol_obj)

    sent = _find_sentiment_json(json_files)
    if sent and isinstance(sent.get("statistics"), dict):
        st = sent["statistics"]
        pos = float(st.get("positive_ratio", 0.0) or 0.0)
        neg = float(st.get("negative_ratio", 0.0) or 0.0)
        text_map["KPI_POS_RATIO"] = f"{round(pos * 100, 1)}%"
        text_map["KPI_NEG_RATIO"] = f"{round(neg * 100, 1)}%"
    else:
        text_map.setdefault("KPI_POS_RATIO", "—")
        text_map.setdefault("KPI_NEG_RATIO", "—")

    text_map = _sanitize_narrative_language(text_map, defaults)

    return merge_morandi_template(template_html, text_map, report_config, report_data)
