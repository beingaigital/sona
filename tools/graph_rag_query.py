"""Graph RAG 工具：查询 Neo4j 知识库，辅助舆情分析。"""

from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, Iterable, List, Optional, Tuple

from langchain_core.tools import tool

# Neo4j 连接配置（允许通过环境变量覆盖）
NEO4J_URI = os.environ.get("SONA_NEO4J_URI", "neo4j://127.0.0.1:7687")
NEO4J_USER = os.environ.get("SONA_NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.environ.get("SONA_NEO4J_PASSWORD", "bjtu1234")

CASE_LABEL_CANDIDATES = [
    "Case",
    "PublicOpinionCase",
    "EventCase",
    "Incident",
    "CaseStudy",
]
THEORY_LABEL_CANDIDATES = ["Theory", "Framework", "Methodology", "Rule"]
INDICATOR_LABEL_CANDIDATES = [
    "AnalysisMethod",
    "Indicator",
    "Metric",
    "Dimension",
    "Signal",
    "Feature",
    "Index",
]

CASE_PROPERTY_CANDIDATES = [
    "name",
    "title",
    "description",
    "summary",
    "content",
    "event_type",
    "eventType",
    "category",
    "domain",
    "industry",
    "field",
    "stage",
    "phase",
    "tags",
    "keywords",
    "source",
    "url",
    "time",
]

THEORY_PROPERTY_CANDIDATES = [
    "name",
    "title",
    "description",
    "summary",
    "keywords",
    "tags",
    "source",
]

INDICATOR_PROPERTY_CANDIDATES = [
    "name",
    "title",
    "description",
    "summary",
    "dimension",
    "category",
    "tags",
    "keywords",
    "source",
]

DIMENSION_ALIASES = {
    "count": ["count", "volume", "声量", "数量", "总量", "增速", "峰值", "趋势"],
    "sentiment": ["sentiment", "emotion", "情感", "情绪", "正负面", "极性"],
    "actor": ["actor", "role", "主体", "人群", "KOL", "媒体", "机构"],
    "attention": ["attention", "heat", "关注", "热度", "传播", "曝光", "扩散"],
    "quality": ["quality", "credibility", "质量", "可信", "真实性", "信源"],
    "trend": ["trend", "time", "timeline", "走势", "时间", "生命周期"],
    "risk": ["risk", "风险", "危机", "失控", "治理"],
    # 中文习惯写法
    "量": ["count", "volume", "声量", "数量", "增速", "峰值"],
    "质": ["quality", "可信", "真实性", "信息质量"],
    "人": ["actor", "主体", "用户", "KOL", "媒体"],
    "场": ["platform", "渠道", "平台", "话语场", "传播场"],
    "效": ["effect", "impact", "影响", "转化", "反馈"],
}

DOMAIN_ALIASES = {
    "教育": ["教育", "升学", "考研", "培训", "学校", "教师", "家长"],
    "互联网": ["互联网", "平台", "流量", "社交媒体", "短视频"],
    "医疗": ["医疗", "医院", "医生", "患者", "病历", "卫健委"],
    "汽车": ["汽车", "车企", "新能源", "智驾", "电池", "自燃"],
    "消费": ["消费", "品牌", "维权", "价格", "售后", "投诉"],
}

EVENT_ALIASES = {
    "突发事故": ["突发事故", "事故", "意外", "猝死", "伤亡", "安全事件"],
    "网络谣言": ["网络谣言", "谣言", "辟谣", "传闻", "不实信息"],
    "品牌危机": ["品牌危机", "公关危机", "舆情危机", "声誉风险"],
    "涉法涉诉": ["涉法涉诉", "诉讼", "司法", "违法", "调查"],
}

STAGE_ALIASES = {
    "潜伏期": ["潜伏期", "前期", "预热", "萌芽前"],
    "萌芽期": ["萌芽期", "起势", "起量", "发端"],
    "爆发期": ["爆发期", "峰值", "高峰", "集中发酵"],
    "扩散期": ["扩散期", "传播期", "发酵期", "外溢"],
    "对抗期": ["对抗期", "争议期", "冲突期", "博弈期"],
    "消退期": ["消退期", "回落期", "衰退期", "降温"],
    "回落期": ["回落期", "消退期", "收尾期", "降温"],
}


def _limit_int(value: Any, default: int, low: int, high: int) -> int:
    try:
        n = int(str(value).strip())
    except Exception:
        n = default
    return max(low, min(n, high))


def _to_text(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, (list, tuple, set)):
        return " ".join(_to_text(x) for x in v if _to_text(x))
    if isinstance(v, dict):
        return " ".join(f"{k}:{_to_text(val)}" for k, val in v.items() if _to_text(val))
    return str(v)


def _normalize_space(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip())


def _tokenize_for_match(text: str, max_tokens: int = 80) -> List[str]:
    s = _normalize_space(_to_text(text))
    if not s:
        return []

    items: List[str] = []
    blocks = re.findall(r"[\u4e00-\u9fff]{2,}|[A-Za-z0-9_#+.-]{2,}", s)
    for block in blocks:
        if re.search(r"[\u4e00-\u9fff]", block):
            items.append(block)
            # 给中文词补充 2-4 字片段，提升召回
            b = block[:12] if len(block) > 12 else block
            for n in (2, 3, 4):
                for i in range(0, max(0, len(b) - n + 1)):
                    items.append(b[i : i + n])
        else:
            items.append(block.lower())

    dedup: List[str] = []
    seen = set()
    for t in sorted(items, key=len, reverse=True):
        t = t.strip()
        if len(t) < 2 or t in seen:
            continue
        seen.add(t)
        dedup.append(t)
        if len(dedup) >= max_tokens:
            break
    return dedup


def _expand_alias_terms(raw: str, alias_map: Dict[str, List[str]], max_terms: int = 60) -> List[str]:
    tokens = _tokenize_for_match(raw, max_tokens=24)
    raw_l = (raw or "").lower()

    for key, aliases in alias_map.items():
        key_l = key.lower()
        if key_l in raw_l or key in (raw or "") or key in tokens:
            tokens.extend(aliases)

    dedup: List[str] = []
    seen = set()
    for t in sorted(tokens, key=len, reverse=True):
        k = t.lower()
        if k in seen:
            continue
        seen.add(k)
        dedup.append(t)
        if len(dedup) >= max_terms:
            break
    return dedup


def _first_non_empty(props: Dict[str, Any], keys: Iterable[str]) -> str:
    for k in keys:
        v = _normalize_space(_to_text(props.get(k, "")))
        if v:
            return v
    return ""


def _shorten(text: str, max_len: int = 220) -> str:
    s = _normalize_space(text)
    if len(s) <= max_len:
        return s
    return s[:max_len].rstrip() + "..."


def _node_search_text(props: Dict[str, Any], keys: List[str]) -> str:
    parts: List[str] = []
    for k in keys:
        v = _normalize_space(_to_text(props.get(k, "")))
        if v:
            parts.append(v)
    return " | ".join(parts)


def _match_score(text: str, terms: List[str]) -> Tuple[float, List[str]]:
    if not text or not terms:
        return 0.0, []

    text_l = text.lower()
    score = 0.0
    matched: List[str] = []
    for term in terms:
        t = term.strip()
        if not t:
            continue
        if t.lower() in text_l:
            weight = 1.0 + min(len(t), 10) * 0.1
            score += weight
            matched.append(t)
    return score, matched[:8]


def _merge_candidate_rows(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    merged: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        nid = row.get("nid")
        props = row.get("props") or {}
        labels = row.get("labels") or []
        fp = str(nid) if nid is not None else (
            _first_non_empty(props, ["name", "title"]) + "|" + _first_non_empty(props, ["description", "summary"])
        )
        if fp not in merged:
            merged[fp] = {
                "nid": nid,
                "labels": list(labels),
                "props": dict(props),
                "ft_score": float(row.get("ft_score") or 0.0),
            }
            continue

        old = merged[fp]
        old["ft_score"] = max(float(old.get("ft_score") or 0.0), float(row.get("ft_score") or 0.0))
        old_labels = set(old.get("labels") or [])
        for lb in labels:
            if lb not in old_labels:
                old_labels.add(lb)
        old["labels"] = list(old_labels)
    return list(merged.values())


def _get_neo4j_driver():
    """获取 Neo4j 驱动。"""
    try:
        from neo4j import GraphDatabase

        return GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
    except ImportError:
        return None


def _query_fulltext_candidates(session: Any, query_terms: List[str], limit: int) -> List[Dict[str, Any]]:
    if not query_terms:
        return []

    query_text = " OR ".join([f'"{t}"' if " " in t else t for t in query_terms[:12]])
    index_names = [
        x.strip()
        for x in os.environ.get(
            "SONA_GRAPH_RAG_FULLTEXT_INDEXES",
            "case_fulltext,global_fulltext,node_fulltext,rag_fulltext",
        ).split(",")
        if x.strip()
    ]

    rows: List[Dict[str, Any]] = []
    for idx in index_names:
        try:
            cypher = (
                "CALL db.index.fulltext.queryNodes($index_name, $query) "
                "YIELD node, score "
                "RETURN toString(elementId(node)) AS nid, labels(node) AS labels, properties(node) AS props, toFloat(score) AS ft_score "
                "LIMIT $limit"
            )
            result = session.run(cypher, {"index_name": idx, "query": query_text, "limit": limit})
            rows.extend([dict(r) for r in result])
        except Exception:
            # 索引不存在或版本不兼容时忽略
            continue
    return rows


def _query_nodes_by_labels(session: Any, labels: List[str], limit: int) -> List[Dict[str, Any]]:
    cypher = (
        "MATCH (n) "
        "WHERE any(lbl IN labels(n) WHERE lbl IN $labels) "
        "RETURN toString(elementId(n)) AS nid, labels(n) AS labels, properties(n) AS props "
        "LIMIT $limit"
    )
    return [dict(r) for r in session.run(cypher, {"labels": labels, "limit": limit})]


def _query_nodes_by_props(session: Any, keys: List[str], limit: int) -> List[Dict[str, Any]]:
    cypher = (
        "MATCH (n) "
        "WHERE any(k IN keys(n) WHERE k IN $keys) "
        "RETURN toString(elementId(n)) AS nid, labels(n) AS labels, properties(n) AS props "
        "LIMIT $limit"
    )
    return [dict(r) for r in session.run(cypher, {"keys": keys, "limit": limit})]


def _query_similar_cases(
    event_type: Optional[str] = None,
    domain: Optional[str] = None,
    stage: Optional[str] = None,
    limit: int = 5,
) -> List[Dict[str, Any]]:
    """查询相似历史案例（增强召回：标签扩展 + 属性扫描 + 全文索引）。"""
    driver = _get_neo4j_driver()
    if not driver:
        return [{"error": "Neo4j 驱动未安装"}]

    scan_limit = _limit_int(os.environ.get("SONA_GRAPH_RAG_SCAN_LIMIT", "1000"), 1000, 100, 5000)
    fulltext_limit = max(limit * 6, 30)

    event_terms = _expand_alias_terms(event_type or "", EVENT_ALIASES, max_terms=36)
    domain_terms = _expand_alias_terms(domain or "", DOMAIN_ALIASES, max_terms=30)
    stage_terms = _expand_alias_terms(stage or "", STAGE_ALIASES, max_terms=20)
    all_terms = []
    for t in event_terms + domain_terms + stage_terms:
        if t not in all_terms:
            all_terms.append(t)

    try:
        with driver.session() as session:
            rows: List[Dict[str, Any]] = []
            rows.extend(_query_fulltext_candidates(session, all_terms, fulltext_limit))
            rows.extend(_query_nodes_by_labels(session, CASE_LABEL_CANDIDATES, scan_limit))
            if len(rows) < limit * 4:
                rows.extend(_query_nodes_by_props(session, CASE_PROPERTY_CANDIDATES, scan_limit))

        candidates = _merge_candidate_rows(rows)
        ranked: List[Dict[str, Any]] = []

        for row in candidates:
            props = row.get("props") or {}
            labels = row.get("labels") or []
            node_text = _node_search_text(props, CASE_PROPERTY_CANDIDATES)
            ft_score = float(row.get("ft_score") or 0.0)

            event_score, event_hits = _match_score(node_text, event_terms)
            domain_score, domain_hits = _match_score(node_text, domain_terms)
            stage_score, stage_hits = _match_score(node_text, stage_terms)

            total_score = event_score * 1.8 + domain_score * 1.2 + stage_score * 1.0 + ft_score
            has_query = bool(all_terms)
            if has_query and total_score <= 0:
                continue

            title = _first_non_empty(props, ["title", "name", "case_name", "event_name"])
            if not title:
                title = f"案例节点#{row.get('nid')}"
            description = _shorten(_first_non_empty(props, ["description", "summary", "content", "abstract"]), 240)

            if title and all_terms:
                title_score, _ = _match_score(title, all_terms[:18])
                total_score += title_score * 0.7

            match_reasons: List[str] = []
            if event_hits:
                match_reasons.append("事件匹配: " + "、".join(event_hits[:3]))
            if domain_hits:
                match_reasons.append("领域匹配: " + "、".join(domain_hits[:3]))
            if stage_hits:
                match_reasons.append("阶段匹配: " + "、".join(stage_hits[:3]))
            if ft_score > 0:
                match_reasons.append(f"全文检索分: {round(ft_score, 3)}")

            ranked.append(
                {
                    "title": title,
                    "description": description,
                    "event_type": _first_non_empty(props, ["event_type", "eventType", "category"]),
                    "domain": _first_non_empty(props, ["domain", "industry", "field"]),
                    "stage": _first_non_empty(props, ["stage", "phase"]),
                    "source": _first_non_empty(props, ["source", "url"]),
                    "score": round(total_score, 4),
                    "match_reasons": match_reasons[:4],
                    "labels": labels[:4] if isinstance(labels, list) else [],
                }
            )

        ranked.sort(key=lambda x: (float(x.get("score") or 0.0), len(str(x.get("description") or ""))), reverse=True)
        return ranked[: max(1, limit)]
    finally:
        try:
            driver.close()
        except Exception:
            pass


def _query_theory(theory_name: Optional[str] = None, limit: int = 10) -> List[Dict[str, Any]]:
    """查询舆情规律理论（扩展标签与属性扫描）。"""
    driver = _get_neo4j_driver()
    if not driver:
        return [{"error": "Neo4j 驱动未安装"}]

    scan_limit = _limit_int(os.environ.get("SONA_GRAPH_RAG_SCAN_LIMIT", "1000"), 1000, 100, 5000)
    terms = _tokenize_for_match(theory_name or "", max_tokens=30)

    try:
        with driver.session() as session:
            rows: List[Dict[str, Any]] = []
            rows.extend(_query_fulltext_candidates(session, terms, max(limit * 4, 20)))
            rows.extend(_query_nodes_by_labels(session, THEORY_LABEL_CANDIDATES, scan_limit))
            if len(rows) < limit * 4:
                rows.extend(_query_nodes_by_props(session, THEORY_PROPERTY_CANDIDATES, scan_limit))

        candidates = _merge_candidate_rows(rows)
        ranked: List[Dict[str, Any]] = []
        for row in candidates:
            props = row.get("props") or {}
            labels = row.get("labels") or []
            text = _node_search_text(props, THEORY_PROPERTY_CANDIDATES)
            score, hits = _match_score(text, terms)
            score += float(row.get("ft_score") or 0.0)
            if terms and score <= 0:
                continue

            name = _first_non_empty(props, ["name", "title"])
            if not name:
                continue

            ranked.append(
                {
                    "name": name,
                    "description": _shorten(_first_non_empty(props, ["description", "summary", "content"]), 220),
                    "source": _first_non_empty(props, ["source", "url"]),
                    "score": round(score, 4),
                    "match_reasons": hits[:4],
                    "labels": labels[:4] if isinstance(labels, list) else [],
                }
            )

        ranked.sort(key=lambda x: float(x.get("score") or 0.0), reverse=True)
        return ranked[: max(1, limit)]
    finally:
        try:
            driver.close()
        except Exception:
            pass


def _compose_dimension_terms(dimension: str) -> List[str]:
    raw = (dimension or "").strip()
    if not raw:
        return []
    terms = _expand_alias_terms(raw, DIMENSION_ALIASES, max_terms=50)
    # 再补充原始词切分
    for t in _tokenize_for_match(raw, max_tokens=16):
        if t not in terms:
            terms.append(t)
    return terms[:60]


def _query_indicators(dimension: Optional[str] = None, limit: int = 20) -> List[Dict[str, Any]]:
    """查询分析维度指标（增强召回：维度同义词 + 多标签 + 全文索引）。"""
    driver = _get_neo4j_driver()
    if not driver:
        return [{"error": "Neo4j 驱动未安装"}]

    scan_limit = _limit_int(os.environ.get("SONA_GRAPH_RAG_SCAN_LIMIT", "1000"), 1000, 100, 5000)
    dim_terms = _compose_dimension_terms(dimension or "")

    try:
        with driver.session() as session:
            rows: List[Dict[str, Any]] = []
            rows.extend(_query_fulltext_candidates(session, dim_terms, max(limit * 6, 30)))
            rows.extend(_query_nodes_by_labels(session, INDICATOR_LABEL_CANDIDATES, scan_limit))
            if len(rows) < limit * 4:
                rows.extend(_query_nodes_by_props(session, INDICATOR_PROPERTY_CANDIDATES, scan_limit))

        candidates = _merge_candidate_rows(rows)
        ranked: List[Dict[str, Any]] = []

        for row in candidates:
            props = row.get("props") or {}
            labels = row.get("labels") or []
            text = _node_search_text(props, INDICATOR_PROPERTY_CANDIDATES)

            dim_score, hits = _match_score(text, dim_terms)
            score = dim_score * 1.7 + float(row.get("ft_score") or 0.0)
            if dim_terms and score <= 0:
                continue

            name = _first_non_empty(props, ["name", "title"])
            if not name:
                continue

            # 指标节点标题命中给予额外加权
            title_score, _ = _match_score(name, dim_terms[:20])
            score += title_score * 0.9

            ranked.append(
                {
                    "name": name,
                    "description": _shorten(_first_non_empty(props, ["description", "summary", "content"]), 220),
                    "dimension": _first_non_empty(props, ["dimension", "category", "tags"]),
                    "source": _first_non_empty(props, ["source", "url"]),
                    "score": round(score, 4),
                    "match_reasons": hits[:5],
                    "labels": labels[:4] if isinstance(labels, list) else [],
                }
            )

        ranked.sort(key=lambda x: float(x.get("score") or 0.0), reverse=True)
        return ranked[: max(1, limit)]
    finally:
        try:
            driver.close()
        except Exception:
            pass


def _query_case_by_id(case_id: str) -> Dict[str, Any]:
    """根据 ID 查询案例详情（兼容 case_id/id/uuid）。"""
    driver = _get_neo4j_driver()
    if not driver:
        return {"error": "Neo4j 驱动未安装"}

    try:
        with driver.session() as session:
            query = (
                "MATCH (c) "
                "WHERE c.case_id = $case_id OR c.id = $case_id OR c.uuid = $case_id OR toString(elementId(c)) = $case_id "
                "OPTIONAL MATCH (c)-[:HAS_ACTOR]->(a) "
                "OPTIONAL MATCH (c)-[:EXHIBITS_EMOTION]->(e) "
                "OPTIONAL MATCH (c)-[:USES_FRAME]->(f) "
                "RETURN labels(c) AS labels, properties(c) AS c, "
                "collect(DISTINCT properties(a)) AS actors, "
                "collect(DISTINCT properties(e)) AS emotions, "
                "collect(DISTINCT properties(f)) AS frames "
                "LIMIT 1"
            )
            record = session.run(query, {"case_id": case_id}).single()
            if not record:
                return {"error": f"案例 {case_id} 不存在"}

            case_props = dict(record.get("c") or {})
            case_props["labels"] = record.get("labels") or []
            case_props["actors"] = [x for x in (record.get("actors") or []) if isinstance(x, dict) and x]
            case_props["emotions"] = [x for x in (record.get("emotions") or []) if isinstance(x, dict) and x]
            case_props["frames"] = [x for x in (record.get("frames") or []) if isinstance(x, dict) and x]
            return case_props
    finally:
        try:
            driver.close()
        except Exception:
            pass


@tool
def graph_rag_query(
    query_type: str,
    event_type: Optional[str] = None,
    domain: Optional[str] = None,
    stage: Optional[str] = None,
    theory_name: Optional[str] = None,
    dimension: Optional[str] = None,
    case_id: Optional[str] = None,
    limit: int = 5,
) -> str:
    """
    描述：查询 Neo4j 知识库，获取舆情分析相关的历史案例、方法论理论和分析指标，辅助理解当前舆情事件。
    使用时机：当需要参考历史相似案例、或需要使用舆情分析方法论时调用本工具。

    输入：
    - query_type（必填）：查询类型，可选值：
      * "similar_cases" - 查询相似历史案例
      * "theory" - 查询舆情规律理论
      * "indicators" - 查询分析维度指标
      * "case_detail" - 查询案例详情
    - event_type（可选）：事件类型，用于相似案例查询，如"品牌危机"、"食品安全"等
    - domain（可选）：行业领域，如"餐饮"、"互联网"等
    - stage（可选）：舆情阶段，如"爆发期"、"消退期"等
    - theory_name（可选）：理论名称，如"沉默螺旋"、"议程设置"等
    - dimension（可选）：分析维度，如"count"、"quality"、"actor"等
    - case_id（可选）：案例ID，用于查询详情
    - limit（可选）：返回结果数量，默认5

    输出：JSON 字符串，包含查询结果。
    """
    try:
        safe_limit = _limit_int(limit, 5, 1, 50)

        if query_type == "similar_cases":
            results = _query_similar_cases(event_type=event_type, domain=domain, stage=stage, limit=safe_limit)
            return json.dumps({"type": "相似历史案例", "count": len(results), "results": results}, ensure_ascii=False, indent=2)

        if query_type == "theory":
            results = _query_theory(theory_name=theory_name, limit=safe_limit)
            return json.dumps({"type": "舆情规律理论", "count": len(results), "results": results}, ensure_ascii=False, indent=2)

        if query_type == "indicators":
            results = _query_indicators(dimension=dimension, limit=safe_limit)
            return json.dumps({"type": "分析维度指标", "count": len(results), "results": results}, ensure_ascii=False, indent=2)

        if query_type == "case_detail":
            if not case_id:
                return json.dumps({"error": "查询案例详情需要提供 case_id"}, ensure_ascii=False)
            result = _query_case_by_id(case_id)
            return json.dumps({"type": "案例详情", "result": result}, ensure_ascii=False, indent=2)

        return json.dumps(
            {
                "error": f"不支持的查询类型: {query_type}，可选值: similar_cases, theory, indicators, case_detail"
            },
            ensure_ascii=False,
        )
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)
