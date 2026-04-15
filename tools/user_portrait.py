"""用户画像工具：基于 CSV 与情感结果生成舆情参与群体画像。"""

from __future__ import annotations

import csv
import json as json_module
import re
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from langchain_core.tools import tool

from tools.analysis_sentiment import analysis_sentiment
from tools.keyword_stats import _identify_content_columns
from utils.content_text import clean_text_like_keyword_stats
from utils.path import get_task_process_dir
from utils.task_context import get_task_id

_UNKNOWN = {"", "未知", "其他", "其它", "null", "none", "n/a", "na", "-", "—", "未填写", "不详", "暂无"}
_AUTHOR_SEPS: Tuple[str, ...] = (";", "；", ",", "，", "|", "/")
_BEHAVIOR_PATTERNS: Dict[str, Tuple[str, ...]] = {
    "维权求助": ("维权", "投诉", "求助", "索赔", "退费", "退款", "举报"),
    "追问求证": ("求证", "真相", "到底", "为何", "回应", "说明", "调查"),
    "围观转发": ("转发", "扩散", "关注", "围观", "吃瓜", "热搜"),
    "玩梗调侃": ("笑死", "离谱", "抽象", "逆天", "玩梗", "段子"),
}
_GROUP_RULES: Tuple[Tuple[str, Tuple[str, ...]], ...] = (
    ("普通网民", ("网友", "网民", "公众", "社会")),
    ("消费者", ("消费者", "顾客", "用户", "下单", "购买", "退款")),
    ("家长群体", ("家长", "孩子", "学生", "学校", "老师", "教育")),
    ("粉丝群体", ("粉丝", "饭圈", "应援", "明星", "爱豆")),
    ("从业者", ("商家", "品牌", "企业", "平台", "员工", "从业者")),
    ("媒体与自媒体", ("媒体", "记者", "博主", "大V", "主播", "自媒体")),
    ("病患及家属", ("患者", "家属", "医院", "医生", "就医", "治疗")),
    ("投资者", ("股民", "投资者", "股价", "市场", "资本")),
)


def _read_csv_rows(file_path: str) -> List[Dict[str, Any]]:
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"数据文件不存在: {file_path}")
    rows: List[Dict[str, Any]] = []
    for enc in ("utf-8-sig", "utf-8", "gb18030", "gbk"):
        try:
            with open(path, "r", encoding=enc, errors="strict") as f:
                rows = list(csv.DictReader(f))
            if rows:
                return rows
        except Exception:
            rows = []
    with open(path, "r", encoding="utf-8-sig", errors="replace") as f:
        return list(csv.DictReader(f))


def _read_json_file(path: str) -> Dict[str, Any]:
    p = Path(path)
    if not p.exists():
        return {}
    with open(p, "r", encoding="utf-8", errors="replace") as f:
        data = json_module.load(f)
    return data if isinstance(data, dict) else {}


def _identify_author_column(fieldnames: Sequence[str]) -> Optional[str]:
    for name in fieldnames:
        raw = str(name or "").strip()
        low = raw.lower()
        if raw == "作者" or "作者" in raw or "发布者" in raw or "author" in low or "screenname" in low:
            return raw
    return None


def _identify_ip_column(fieldnames: Sequence[str]) -> Optional[str]:
    for name in fieldnames:
        raw = str(name or "").strip()
        low = raw.lower()
        if raw == "IP属地" or ("ip" in low and ("属地" in raw or "location" in low)):
            return raw
    return None


def _iter_authors(raw_author: str) -> List[str]:
    text = str(raw_author or "").strip()
    if not text:
        return []
    parts = [text]
    for sep in _AUTHOR_SEPS:
        if sep in text:
            expanded: List[str] = []
            for item in parts:
                expanded.extend(item.split(sep))
            parts = expanded
    return [p.strip().strip("，,;；|｜/\\") for p in parts if p.strip() and p.strip() not in _UNKNOWN]


def _normalize_region(raw_region: str) -> str:
    region = str(raw_region or "").strip().replace(" ", "")
    if not region or region in _UNKNOWN:
        return ""
    for suffix in ("自治区", "特别行政区", "省", "市"):
        if region.endswith(suffix) and len(region) > len(suffix):
            return region[: -len(suffix)]
    return region


def _extract_joined_text(rows: Sequence[Dict[str, Any]], content_columns: Sequence[str]) -> str:
    parts: List[str] = []
    for row in rows:
        row_text = [str(row.get(col, "") or "").strip() for col in content_columns]
        row_text = [x for x in row_text if x]
        if row_text:
            parts.append(" ".join(row_text))
    return "\n".join(parts)


def _top_keywords(text: str, top_n: int = 12) -> List[str]:
    cleaned = clean_text_like_keyword_stats(text)
    if not cleaned:
        return []
    try:
        import jieba.posseg as pseg  # type: ignore

        counter: Counter[str] = Counter()
        for word, flag in pseg.cut(cleaned):
            token = str(word or "").strip()
            if len(token) < 2 or token in _UNKNOWN:
                continue
            if flag and not str(flag).startswith(("n", "v", "a", "nr", "ns", "nt")):
                continue
            counter[token] += 1
        return [item for item, _ in counter.most_common(top_n)]
    except Exception:
        counter = Counter(re.findall(r"[\u4e00-\u9fff]{2,}", cleaned))
        return [item for item, _ in counter.most_common(top_n)]


def _build_behavior_features(text: str) -> Tuple[List[str], Dict[str, int]]:
    counts = {label: sum(text.count(token) for token in patterns) for label, patterns in _BEHAVIOR_PATTERNS.items()}
    features = [name for name, count in sorted(counts.items(), key=lambda item: item[1], reverse=True) if count > 0][:4]
    return (features or ["持续围观", "转评讨论"]), counts


def _score_core_groups(text: str, has_top_authors: bool) -> Dict[str, Dict[str, Any]]:
    result: Dict[str, Dict[str, Any]] = {}
    for name, tokens in _GROUP_RULES:
        token_hits = {token: text.count(token) for token in tokens}
        score = sum(token_hits.values())
        evidence = [token for token, cnt in sorted(token_hits.items(), key=lambda item: item[1], reverse=True) if cnt > 0][:4]
        result[name] = {"score": score, "evidence_terms": evidence}
    if has_top_authors and "媒体与自媒体" in result:
        result["媒体与自媒体"]["score"] = int(result["媒体与自媒体"]["score"]) + 1
        if "作者活跃" not in result["媒体与自媒体"]["evidence_terms"]:
            result["媒体与自媒体"]["evidence_terms"].append("作者活跃")
    return result


def _build_core_groups(text: str, has_top_authors: bool) -> List[str]:
    scores = _score_core_groups(text, has_top_authors)
    groups = [name for name, info in sorted(scores.items(), key=lambda item: int(item[1]["score"]), reverse=True) if int(info["score"]) > 0][:4]
    if has_top_authors and "媒体与自媒体" not in groups:
        groups.append("媒体与自媒体")
    return (groups or ["普通网民", "媒体与自媒体"])[:4]


def _build_emotion_features(sentiment_json: Dict[str, Any]) -> List[str]:
    stats = sentiment_json.get("statistics") if isinstance(sentiment_json.get("statistics"), dict) else {}
    negative = int(stats.get("negative_count") or stats.get("negative") or 0)
    neutral = int(stats.get("neutral_count") or stats.get("neutral") or 0)
    positive = int(stats.get("positive_count") or stats.get("positive") or 0)
    total = max(negative + neutral + positive, 1)
    labels: List[str] = []
    if negative / total >= 0.4:
        labels.extend(["愤怒", "质疑"])
    if neutral / total >= 0.35:
        labels.append("观望")
    if positive / total >= 0.3:
        labels.append("支持")

    negative_summary = sentiment_json.get("negative_summary") if isinstance(sentiment_json.get("negative_summary"), list) else []
    positive_summary = sentiment_json.get("positive_summary") if isinstance(sentiment_json.get("positive_summary"), list) else []
    negative_text = " ".join(str(x) for x in negative_summary)
    positive_text = " ".join(str(x) for x in positive_summary)

    if any(token in negative_text for token in ("担心", "焦虑", "恐慌", "害怕")):
        labels.append("焦虑")
    if any(token in negative_text for token in ("失望", "寒心", "无语")):
        labels.append("失望")
    if any(token in positive_text for token in ("理解", "支持", "认可")):
        labels.append("理解")

    deduped: List[str] = []
    for label in labels:
        if label and label not in deduped:
            deduped.append(label)
    return deduped[:4] or ["观望", "质疑"]


def _build_sentiment_result_if_missing(task_id: str, data_file_path: str) -> Dict[str, Any]:
    try:
        raw = analysis_sentiment.invoke(
            {
                "eventIntroduction": "舆情事件",
                "dataFilePath": data_file_path,
                "preferExistingSentimentColumn": True,
            }
        )
        if not isinstance(raw, str):
            raw = str(raw)
        parsed = json_module.loads(raw)
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def _compute_portrait_confidence(
    *,
    total_rows: int,
    content_columns: Sequence[str],
    author_counter: Counter[str],
    region_counter: Counter[str],
    sentiment_json: Dict[str, Any],
) -> Dict[str, Any]:
    stats = sentiment_json.get("statistics") if isinstance(sentiment_json.get("statistics"), dict) else {}
    neg = int(stats.get("negative_count") or stats.get("negative") or 0)
    neu = int(stats.get("neutral_count") or stats.get("neutral") or 0)
    pos = int(stats.get("positive_count") or stats.get("positive") or 0)
    sentiment_total = neg + neu + pos

    top_author_share = 0.0
    if author_counter:
        top_author_share = float(author_counter.most_common(1)[0][1]) / float(max(sum(author_counter.values()), 1))

    score = 0
    if total_rows >= 200:
        score += 3
    elif total_rows >= 80:
        score += 2
    elif total_rows >= 30:
        score += 1

    if len(content_columns) >= 1:
        score += 1
    if len(region_counter) >= 5:
        score += 1
    if sentiment_total >= max(20, int(total_rows * 0.2)):
        score += 1
    if top_author_share <= 0.35:
        score += 1

    level = "low"
    if score >= 6:
        level = "high"
    elif score >= 4:
        level = "medium"

    notes: List[str] = []
    if total_rows < 30:
        notes.append("样本量较小，建议谨慎外推。")
    if top_author_share > 0.45:
        notes.append("头部作者占比偏高，可能存在表达偏置。")
    if len(region_counter) < 3:
        notes.append("地域覆盖较窄，跨区域结论可信度有限。")
    if sentiment_total < max(20, int(total_rows * 0.2)):
        notes.append("情绪样本覆盖偏低，情绪特征仅供参考。")

    return {
        "level": level,
        "score": score,
        "max_score": 7,
        "sample_size": total_rows,
        "region_coverage_count": len(region_counter),
        "sentiment_coverage_count": sentiment_total,
        "top_author_share": round(top_author_share, 4),
        "notes": notes,
    }


def _save_result_json(task_id: str, payload: Dict[str, Any]) -> str:
    process_dir = get_task_process_dir(task_id)
    process_dir.mkdir(parents=True, exist_ok=True)
    out_path = process_dir / "user_portrait.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json_module.dump(payload, f, ensure_ascii=False, indent=2)
    return str(out_path)


@tool
def user_portrait(dataFilePath: str, sentimentResultPath: str = "") -> str:
    """生成用户画像：核心群体、关注点、情绪特征、传播行为。"""
    task_id = get_task_id()
    if not task_id:
        return json_module.dumps({"error": "未找到任务ID", "user_portrait": {}, "result_file_path": ""}, ensure_ascii=False)

    try:
        rows = _read_csv_rows(dataFilePath)
    except Exception as exc:
        return json_module.dumps({"error": f"读取数据文件失败: {str(exc)}", "user_portrait": {}, "result_file_path": ""}, ensure_ascii=False)

    sentiment_json = _read_json_file(sentimentResultPath) if sentimentResultPath else {}
    if not sentiment_json:
        sentiment_json = _build_sentiment_result_if_missing(task_id, dataFilePath)
    if not rows:
        payload = {"total_rows": 0, "content_columns": [], "top_authors": [], "top_regions": [], "user_portrait": {}}
        payload["result_file_path"] = _save_result_json(task_id, payload)
        return json_module.dumps(payload, ensure_ascii=False)

    fieldnames = list(rows[0].keys())
    content_columns = _identify_content_columns(fieldnames)
    author_col = _identify_author_column(fieldnames)
    ip_col = _identify_ip_column(fieldnames)

    author_counter: Counter[str] = Counter()
    region_counter: Counter[str] = Counter()
    for row in rows:
        if author_col:
            for author in _iter_authors(str(row.get(author_col, "") or "")):
                author_counter[author] += 1
        if ip_col:
            region = _normalize_region(str(row.get(ip_col, "") or ""))
            if region:
                region_counter[region] += 1

    joined_text = _extract_joined_text(rows, content_columns)
    keywords = _top_keywords(joined_text, top_n=12)
    behavior_features, behavior_signal_counts = _build_behavior_features(joined_text)
    group_scores = _score_core_groups(joined_text, bool(author_counter))
    group_ranked = [
        {"group": g, "score": int(info.get("score") or 0), "evidence_terms": list(info.get("evidence_terms") or [])}
        for g, info in sorted(group_scores.items(), key=lambda item: int(item[1].get("score") or 0), reverse=True)
    ]
    confidence = _compute_portrait_confidence(
        total_rows=len(rows),
        content_columns=content_columns,
        author_counter=author_counter,
        region_counter=region_counter,
        sentiment_json=sentiment_json,
    )
    portrait = {
        "core_groups": _build_core_groups(joined_text, bool(author_counter)),
        "concerns": (keywords[:5] or ["事实真相", "责任划分", "后续处置"]),
        "emotion_features": _build_emotion_features(sentiment_json),
        "behavior_features": behavior_features,
        "confidence_level": confidence["level"],
    }

    payload = {
        "total_rows": len(rows),
        "content_columns": content_columns,
        "author_column_detected": author_col,
        "ip_location_column_detected": ip_col,
        "top_authors": [{"name": n, "count": c} for n, c in author_counter.most_common(8)],
        "top_regions": [{"name": n, "count": c} for n, c in region_counter.most_common(8)],
        "behavior_signal_counts": behavior_signal_counts,
        "core_group_scores": group_ranked[:8],
        "portrait_confidence": confidence,
        "seed_keywords": keywords,
        "user_portrait": portrait,
    }
    payload["result_file_path"] = _save_result_json(task_id, payload)
    return json_module.dumps(payload, ensure_ascii=False)
