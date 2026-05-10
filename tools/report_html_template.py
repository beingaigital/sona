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
        "keywords": keywords_out or [{"word": "暂无关键词", "count": 0, "rel": "—"}],
        "timeline": timeline_out or [{"time": "—", "event": "暂无时间线数据"}],
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
        return "待评估"
    lifecycle = vol.get("lifecycle")
    if not isinstance(lifecycle, dict):
        return "待评估"
    current = str(lifecycle.get("current_phase", "") or "").strip()
    if current:
        return f"{current}（规则判定）"
    return "待评估"


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
            "author": {"names": author_names or ["暂无作者数据"], "values": author_values or [0]},
            "keyword": {"names": keyword_names or ["暂无关键词"], "values": keyword_values or [0]},
            "radarValues": _build_radar_values(cfg),
            "lifecycle": lifecycle,
        },
        "timeline": list(cfg.get("timeline", []) or [{"time": "—", "event": "暂无时间线数据"}]),
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

    # ============ 注入历史反馈提醒 ============
    try:
        from feedback.retriever import retrieve_and_format

        # 内联领域检测（避免导入 cli 模块触发依赖链）
        _domain_keywords = {
            "health": ["健康", "医疗", "医院", "疫情", "病毒", "感染", "疾病", "药品",
                        "食品", "诺如", "流感", "新冠", "疫苗", "卫生", "疾控"],
            "traffic": ["交通", "出行", "高铁", "地铁", "航班", "高速", "事故", "拥堵",
                         "春运", "航空", "铁路", "道路"],
            "education": ["教育", "学校", "高考", "大学", "考试", "招生", "学生", "教师",
                          "校园", "学费", "双减"],
            "government": ["政务", "政府", "政策", "法规", "监管", "执法", "官员", "反腐",
                           "信访", "民生", "社保"],
            "consumption": ["消费", "价格", "购物", "电商", "直播", "品牌", "质量", "投诉",
                            "维权", "退货", "预制菜", "315"],
            "tourism": ["旅游", "景区", "酒店", "民宿", "假期", "黄金周", "签证", "出境", "文旅"],
            "panda": ["大熊猫", "熊猫", "国宝", "动物园", "保护", "繁育"],
        }
        _domain = "general"
        _text = (event_introduction or "").lower()
        for d, kws in _domain_keywords.items():
            if any(kw in _text for kw in kws):
                _domain = d
                break

        feedback_reminder = retrieve_and_format(
            domain=_domain,
            query=event_introduction or "",
            top_k=5,
        )
        if feedback_reminder:
            prompt = feedback_reminder + "\n\n" + prompt
    except Exception:
        pass  # 反馈召回失败不影响报告生成

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
    stub = "待补充：请结合图表与统计结果完善表述。"
    title = intro[:40] if intro else "舆情分析报告"
    return {
        # 旧模板键
        "REPORT_TITLE": title,
        "OBJECT_NAME": title[:30],
        "NATURE": "待评估",
        "RISK_LEVEL": "待评估",
        "EVENT_BACKGROUND": intro or stub,
        "5W_WHO": "待补充",
        "5W_WHAT": "待补充",
        "5W_WHERE": "待补充",
        "5W_WHEN": "待补充",
        "5W_WHY": "待补充",
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
        "INTRO_TRIGGERS": "待补充",
        "SUMMARY_BULLETS": "已生成结构化报告|请结合图表查看核心结论|如需更细结论请补充专题约束",
        "CHART_SENTIMENT_ANALYSIS": "待补充",
        "CHART_TIMELINE_ANALYSIS": "待补充",
        "CHART_VOLUME_ANALYSIS": "待补充",
        "CHART_REGION_ANALYSIS": "待补充",
        "CHART_AUTHOR_ANALYSIS": "待补充",
        "CHART_KEYWORD_ANALYSIS": "待补充",
        "CHART_RADAR_ANALYSIS": "待补充",
        "CHART_LIFECYCLE_ANALYSIS": "待补充",
        "THEORY_BUTTERFLY": stub,
        "RESPONSE_ANALYSIS_BULLETS": "待补充|请结合传播链路与响应时间评估处置效果",
        "RECAP_DISCOURSE": stub,
        "RECAP_TRENDS": stub,
        "RECAP_DRIVERS_BULLETS": "待补充|建议补充作者与传播路径数据",
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
        items = ["暂无可展示结论"]
    return "".join(f"<li>{html.escape(it, quote=True)}</li>" for it in items)


def _contains_english_phrase(value: str) -> bool:
    s = str(value or "").strip()
    if not s:
        return False
    # 只在“几乎纯英文短语”时触发，避免中文句子含少量英文被误判
    if re.search(r"[\u4e00-\u9fff]", s):
        return False
    return bool(re.search(r"^[\s\-_/A-Za-z0-9.,:;!?()]+$", s) and re.search(r"[A-Za-z]{3,}", s))


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
                raw_items = [str(x).strip() for x in value if str(x).strip()]
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
            if _contains_english_phrase(str(value)):
                sanitized[key] = defaults.get(key, "待补充")
    return sanitized


_PLACEHOLDER_MARKERS = ("证据不足", "待补充", "待评估", "暂无可展示结论")


def _is_placeholder_like(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, list):
        if not value:
            return True
        return all(_is_placeholder_like(v) for v in value)
    s = str(value).strip()
    if not s or s == "—":
        return True
    return any(marker in s for marker in _PLACEHOLDER_MARKERS)


def _take_valid_names(items: List[Any], limit: int = 3) -> List[str]:
    out: List[str] = []
    for it in items:
        s = str(it or "").strip()
        if not s or s == "—" or "暂无" in s:
            continue
        out.append(s)
        if len(out) >= limit:
            break
    return out


def _backfill_narrative_from_data(
    text_map: Dict[str, Any],
    report_config: Dict[str, Any],
    report_data: Dict[str, Any],
    meta: Dict[str, str],
) -> Dict[str, Any]:
    """当模型输出偏弱时，用已有统计数据回填关键叙事，避免占位文案泄漏。"""
    out: Dict[str, Any] = dict(text_map)

    charts = report_data.get("charts", {}) if isinstance(report_data, dict) else {}
    volume = charts.get("volume", {}) if isinstance(charts, dict) else {}
    region = charts.get("region", {}) if isinstance(charts, dict) else {}
    author = charts.get("author", {}) if isinstance(charts, dict) else {}
    keyword = charts.get("keyword", {}) if isinstance(charts, dict) else {}
    radar_values = charts.get("radarValues", []) if isinstance(charts, dict) else []
    lifecycle = charts.get("lifecycle", {}) if isinstance(charts, dict) else {}

    timeline = report_data.get("timeline", []) if isinstance(report_data, dict) else []
    timeline_count = len([x for x in timeline if isinstance(x, dict) and (str(x.get("time", "")).strip() or str(x.get("event", "")).strip())])

    sentiment_rows = report_config.get("sentiment", []) if isinstance(report_config, dict) else []
    sentiment_map: Dict[str, int] = {}
    for row in sentiment_rows:
        if isinstance(row, dict):
            nm = str(row.get("name", "") or "").strip()
            sentiment_map[nm] = _safe_int(row.get("value", 0), 0)

    total_sample = str(meta.get("SAMPLE_SIZE", "—") or "—")
    effective_sample = str(meta.get("EFFECTIVE_VOLUME", "—") or "—")
    period = str(meta.get("DATA_PERIOD", "—") or "—")

    region_names = _take_valid_names(list(region.get("names", []) if isinstance(region, dict) else []), 3)
    author_names = _take_valid_names(list(author.get("names", []) if isinstance(author, dict) else []), 3)
    keyword_names = _take_valid_names(list(keyword.get("names", []) if isinstance(keyword, dict) else []), 4)

    volume_values = list(volume.get("values", []) if isinstance(volume, dict) else [])
    try:
        peak_volume = max([_safe_int(v, 0) for v in volume_values], default=0)
    except Exception:
        peak_volume = 0

    phase_status = str(out.get("PHASE_STATUS", "") or "").strip()
    if _is_placeholder_like(phase_status):
        stages = lifecycle.get("stages", []) if isinstance(lifecycle, dict) else []
        stage_name = str(stages[-1]).strip() if isinstance(stages, list) and stages else "待评估"
        out["PHASE_STATUS"] = stage_name

    if _is_placeholder_like(out.get("SUMMARY_BULLETS")):
        out["SUMMARY_BULLETS"] = [
            f"样本规模为{total_sample}，有效声量为{effective_sample}。",
            f"监测时间范围：{period}。",
            f"已提取时间线节点{timeline_count}个，报告结论基于过程文件统计结果。",
        ]

    if _is_placeholder_like(out.get("INTRO_TRIGGERS")) and keyword_names:
        out["INTRO_TRIGGERS"] = f"讨论触发点主要集中在：{'、'.join(keyword_names[:3])}。"

    if _is_placeholder_like(out.get("CHART_SENTIMENT_ANALYSIS")):
        pos = sentiment_map.get("正面", 0)
        neg = sentiment_map.get("负面", 0)
        neu = sentiment_map.get("中立", sentiment_map.get("中性", 0))
        out["CHART_SENTIMENT_ANALYSIS"] = [
            f"情感分布显示正面{pos}条、中立{neu}条、负面{neg}条。",
            "情绪结构可用于识别舆论风险与传播方向。",
            "建议持续跟踪负面占比变化并结合热点节点复核。",
        ]

    if _is_placeholder_like(out.get("CHART_TIMELINE_ANALYSIS")):
        out["CHART_TIMELINE_ANALYSIS"] = [
            f"时间线共提取{timeline_count}个关键节点。",
            "关键节点展示了事件从触发到扩散的演进过程。",
            "建议结合节点附近内容核查风险拐点。",
        ]

    if _is_placeholder_like(out.get("CHART_VOLUME_ANALYSIS")):
        out["CHART_VOLUME_ANALYSIS"] = [
            f"声量序列峰值约为{peak_volume}。",
            "声量波动反映关注度变化与外部刺激节奏。",
            "建议在高峰期同步加强回应与事实澄清。",
        ]

    if _is_placeholder_like(out.get("CHART_REGION_ANALYSIS")):
        region_text = "、".join(region_names) if region_names else "暂无显著地域集中"
        out["CHART_REGION_ANALYSIS"] = [
            f"地域分布主要集中在：{region_text}。",
            "区域集中度可辅助判断传播扩散路径。",
            "建议对高占比地区开展定向监测。",
        ]

    if _is_placeholder_like(out.get("CHART_AUTHOR_ANALYSIS")):
        author_text = "、".join(author_names) if author_names else "暂无显著高频作者"
        out["CHART_AUTHOR_ANALYSIS"] = [
            f"高频发布者包括：{author_text}。",
            "头部发布者对议题扩散具有放大效应。",
            "建议跟踪关键账号后续发文与互动变化。",
        ]

    if _is_placeholder_like(out.get("CHART_KEYWORD_ANALYSIS")):
        keyword_text = "、".join(keyword_names) if keyword_names else "暂无高频关键词"
        out["CHART_KEYWORD_ANALYSIS"] = [
            f"关键词热度集中在：{keyword_text}。",
            "关键词聚类反映当前议题焦点结构。",
            "建议按关键词簇更新监测词库。",
        ]

    if _is_placeholder_like(out.get("CHART_RADAR_ANALYSIS")):
        radar_text = "、".join(str(_safe_int(v, 0)) for v in radar_values[:5]) if isinstance(radar_values, list) else "—"
        out["CHART_RADAR_ANALYSIS"] = [
            f"五维雷达评分（量/质/人/场/效）约为：{radar_text}。",
            "雷达图用于观察多维能力是否均衡。",
            "建议针对低分维度补充采样与研判。",
        ]

    if _is_placeholder_like(out.get("CHART_LIFECYCLE_ANALYSIS")):
        phase_now = str(out.get("PHASE_STATUS", "待评估") or "待评估")
        out["CHART_LIFECYCLE_ANALYSIS"] = [
            f"生命周期阶段判定为：{phase_now}。",
            "阶段变化可用于指导响应节奏与资源分配。",
            "建议结合后续声量与情绪数据动态复核阶段。",
        ]

    if _is_placeholder_like(out.get("RESPONSE_ANALYSIS_BULLETS")):
        out["RESPONSE_ANALYSIS_BULLETS"] = [
            "现有数据可用于评估回应时点与传播反馈。",
            "建议建立“高峰时段-回应动作-情绪变化”对照表。",
            "后续需持续跟踪关键节点后的风险变化。",
        ]

    return out


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
    text_map = _backfill_narrative_from_data(text_map, report_config, report_data, meta)

    return merge_morandi_template(template_html, text_map, report_config, report_data)
