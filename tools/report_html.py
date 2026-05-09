"""HTML报告生成工具：根据分析结果生成HTML报告。"""

from __future__ import annotations

import csv
import json
import os
import re
import html
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.tools import tool

from model.factory import get_report_model
from utils.path import ensure_task_dirs, get_task_result_dir
from utils.prompt_loader import get_report_html_prompt
from utils.task_context import get_task_id
from utils.methodology_loader import load_methodology_for_report
import webbrowser


def _read_json_files(directory: str) -> List[Dict[str, Any]]:
    """
    读取目录中所有JSON文件。
    
    Args:
        directory: 目录路径
        
    Returns:
        JSON文件列表，每个元素包含文件名和内容
    """
    dir_path = Path(directory)
    if not dir_path.exists():
        raise FileNotFoundError(f"目录不存在: {directory}")
    
    json_files = []
    for json_file in dir_path.glob("*.json"):
        try:
            with open(json_file, 'r', encoding='utf-8') as f:
                content = json.load(f)
                json_files.append({
                    "filename": json_file.name,
                    "content": content
                })
        except Exception as e:
            # 跳过无法读取的文件
            continue
    
    return json_files


def _build_graph_rag_context(json_files: List[Dict[str, Any]]) -> Tuple[str, bool]:
    """
    从 graph_rag_enrichment.json 提炼可读摘要，显式喂给报告模型。
    """
    graph_obj: Optional[Dict[str, Any]] = None
    for item in json_files:
        if str(item.get("filename", "")).strip() == "graph_rag_enrichment.json":
            content = item.get("content")
            if isinstance(content, dict):
                graph_obj = content
                break

    if not graph_obj:
        return "", False

    status = str(graph_obj.get("status", "") or "").strip()
    lines: List[str] = [f"Graph RAG 状态: {status or 'unknown'}"]
    decision = graph_obj.get("user_decision") if isinstance(graph_obj.get("user_decision"), dict) else {}
    decision_mode = str(decision.get("mode", "") or "").strip().lower()
    enabled = status.startswith("enabled") and decision_mode != "none"

    if decision:
        before = decision.get("before") if isinstance(decision.get("before"), dict) else {}
        after = decision.get("after") if isinstance(decision.get("after"), dict) else {}
        lines.append(
            "用户采纳策略: "
            + str(decision_mode or "all")
            + (
                f"（similar {before.get('similar_cases', 0)}->{after.get('similar_cases', 0)}, "
                f"theory {before.get('theories', 0)}->{after.get('theories', 0)}, "
                f"indicators {before.get('indicators', 0)}->{after.get('indicators', 0)}）"
                if before or after
                else ""
            )
        )

    if status == "disabled_skip":
        lines.append(f"跳过原因: {str(graph_obj.get('reason', '未提供原因'))}")
        return "\n".join(lines), False

    similar = graph_obj.get("similar_cases") if isinstance(graph_obj.get("similar_cases"), dict) else {}
    similar_results = similar.get("results") if isinstance(similar, dict) else []
    if isinstance(similar_results, list) and similar_results:
        lines.append("相似案例:")
        for row in similar_results[:5]:
            if not isinstance(row, dict):
                continue
            title = str(row.get("title", "") or "").strip()
            desc = str(row.get("description", "") or "").strip()
            if title:
                lines.append(f"- {title}" + (f"：{desc[:100]}" if desc else ""))

    theories = graph_obj.get("theories") if isinstance(graph_obj.get("theories"), list) else []
    theory_names: List[str] = []
    for blk in theories[:6]:
        if not isinstance(blk, dict):
            continue
        rs = blk.get("results")
        if not isinstance(rs, list):
            continue
        for x in rs[:5]:
            if isinstance(x, dict):
                name = str(x.get("name", "") or "").strip()
                if name:
                    theory_names.append(name)
    if theory_names:
        lines.append("方法论补充: " + "、".join(theory_names[:8]))

    indicators = graph_obj.get("indicators") if isinstance(graph_obj.get("indicators"), list) else []
    indicator_names: List[str] = []
    for blk in indicators[:6]:
        if not isinstance(blk, dict):
            continue
        rs = blk.get("results")
        if not isinstance(rs, list):
            continue
        for x in rs[:8]:
            if isinstance(x, dict):
                name = str(x.get("name", "") or "").strip()
                if name:
                    indicator_names.append(name)
    if indicator_names:
        lines.append("分析指标补充: " + "、".join(indicator_names[:10]))

    err = str(graph_obj.get("error", "") or "").strip()
    if err:
        lines.append("执行异常: " + err[:200])

    if len(lines) == 1:
        lines.append("未检索到可用补充内容。")
    return "\n".join(lines), enabled


def _build_wiki_kb_snapshot_context(json_files: List[Dict[str, Any]]) -> Tuple[str, bool]:
    """
    从 wiki_qa_snapshot.json 提炼「领域路由 + 来源路径」摘要，供报告模型写入正文举证。
    """
    snap: Optional[Dict[str, Any]] = None
    for item in json_files:
        if str(item.get("filename", "") or "").strip() == "wiki_qa_snapshot.json":
            c = item.get("content")
            if isinstance(c, dict):
                snap = c
            break
    if not snap:
        return "", False

    meta = snap.get("_wiki_meta") if isinstance(snap.get("_wiki_meta"), dict) else {}
    domain = str(meta.get("domain", "") or "").strip()
    retrieved = meta.get("retrieved_count", 0)
    llm_used = bool(meta.get("llm_used")) if isinstance(meta, dict) else False
    answer = str(snap.get("answer", "") or "").strip()
    sources = snap.get("sources") if isinstance(snap.get("sources"), list) else []

    lines: List[str] = [
        "领域路由（domain_routing，来自 project memory / 关键词命中）: "
        + (domain if domain else "未命中（走通用 wiki 索引召回）"),
        f"检索条数: {retrieved}",
        f"是否经大模型合成答案: {'是' if llm_used else '否'}",
    ]
    if answer:
        lines.append("合成答案摘录: " + answer[:450] + ("…" if len(answer) > 450 else ""))
    if sources:
        lines.append("Top 知识来源路径（可对照 domains/ 与 references/wiki）:")
        for row in sources[:12]:
            if not isinstance(row, dict):
                continue
            title = str(row.get("title", "") or "").strip()
            path_s = str(row.get("path", "") or "").strip()
            score = row.get("score", "")
            if path_s or title:
                lines.append(f"- {title or '（无标题）'} | path={path_s} | score={score}")
    return "\n".join(lines), True


def _build_reference_context(json_files: List[Dict[str, Any]]) -> Tuple[str, bool]:
    """
    从 reference_insights/reference_links 中提炼“可引用参考证据”摘要。
    """
    ref_hits: List[Dict[str, Any]] = []
    ref_links: List[Dict[str, Any]] = []

    for item in json_files:
        name = str(item.get("filename", "") or "").strip()
        content = item.get("content")
        if not isinstance(content, dict):
            continue
        if name == "reference_insights.json":
            results = content.get("results")
            if isinstance(results, list):
                ref_hits = [x for x in results if isinstance(x, dict)]
        elif name == "reference_links.json":
            links = content.get("links")
            if isinstance(links, list):
                ref_links = [x for x in links if isinstance(x, dict)]

    if not ref_hits and not ref_links:
        return "", False

    lines: List[str] = []
    if ref_hits:
        lines.append("参考资料命中（本地智库检索）:")
        for row in ref_hits[:6]:
            snippet = str(row.get("snippet", "") or "").strip()
            title = str(row.get("title", "") or "").strip()
            if snippet:
                lines.append(f"- {snippet[:140]}" + ("..." if len(snippet) > 140 else "") + (f"（来源: {title}）" if title else ""))
    if ref_links:
        lines.append("可复核外部入口:")
        for row in ref_links[:4]:
            name = str(row.get("name", "") or "").strip()
            url = str(row.get("url", "") or "").strip()
            if name and url:
                lines.append(f"- {name}: {url}")

    return "\n".join(lines), bool(ref_hits)


def _build_collab_context(json_files: List[Dict[str, Any]]) -> Tuple[str, bool]:
    """
    汇总用户协同输入与外部补充参考。
    """
    judgement: Dict[str, Any] = {}
    expert_notes: Dict[str, Any] = {}
    weibo_ref: Dict[str, Any] = {}

    for item in json_files:
        name = str(item.get("filename", "") or "").strip()
        content = item.get("content")
        if not isinstance(content, dict):
            continue
        if name == "user_judgement_input.json":
            judgement = content
        elif name == "user_expert_notes.json":
            expert_notes = content
        elif name == "weibo_aisearch_reference.json":
            weibo_ref = content

    lines: List[str] = []
    has_any = False

    user_judgement = str(judgement.get("user_judgement", "") or "").strip()
    if user_judgement:
        has_any = True
        lines.append("用户研判重点:")
        lines.append(f"- {user_judgement[:220]}" + ("..." if len(user_judgement) > 220 else ""))
        focus_keywords = judgement.get("focus_keywords")
        if isinstance(focus_keywords, list) and focus_keywords:
            keys = [str(x).strip() for x in focus_keywords if str(x).strip()]
            if keys:
                lines.append("用户关注关键词: " + "、".join(keys[:10]))

    expert_note = str(expert_notes.get("expert_note", "") or "").strip()
    if expert_note:
        has_any = True
        lines.append("专家补充观点:")
        lines.append(f"- {expert_note[:260]}" + ("..." if len(expert_note) > 260 else ""))

    weibo_results = weibo_ref.get("results") if isinstance(weibo_ref.get("results"), list) else []
    if weibo_results:
        has_any = True
        lines.append("微博智搜片段（外部参考）:")
        for row in weibo_results[:5]:
            if not isinstance(row, dict):
                continue
            snip = str(row.get("snippet", "") or "").strip()
            if snip:
                lines.append(f"- {snip[:160]}" + ("..." if len(snip) > 160 else ""))
        weibo_url = str(weibo_ref.get("url", "") or "").strip()
        if weibo_url:
            lines.append(f"微博智搜入口: {weibo_url}")

    return "\n".join(lines), has_any


def _extract_user_judgement_payload(json_files: List[Dict[str, Any]]) -> Dict[str, Any]:
    payload: Dict[str, Any] = {}
    for item in json_files:
        name = str(item.get("filename", "") or "").strip()
        if name != "user_judgement_input.json":
            continue
        content = item.get("content")
        if isinstance(content, dict):
            payload = content
            break
    return payload


def _inject_user_judgement_section(html_content: str, judgement_payload: Dict[str, Any]) -> str:
    """
    将用户在协同研判阶段输入的关注点，作为“固定可见章节”注入报告 HTML。
    说明：即使模型在正文里已经回应，也仍展示原文，便于复核与协作。
    """
    if not html_content:
        return html_content
    user_judgement = str(judgement_payload.get("user_judgement", "") or "").strip()
    if not user_judgement:
        return html_content
    focus_keywords = judgement_payload.get("focus_keywords")
    keywords: List[str] = []
    if isinstance(focus_keywords, list):
        keywords = [str(x).strip() for x in focus_keywords if str(x).strip()]

    block = (
        "\n"
        "<div class=\"section\" id=\"sona-user-judgement\">\n"
        "  <h2 class=\"section-title\">协同研判输入（用户关注点）</h2>\n"
        "  <div class=\"highlight-box\">\n"
        f"    <p><span class=\"key-point\">研判重点</span>{html.escape(user_judgement)}</p>\n"
        "  </div>\n"
    )
    if keywords:
        block += (
            "  <p style=\"margin-top: 10px; color: var(--muted); font-size: 0.95rem;\">"
            f"<strong>关注关键词：</strong>{html.escape('、'.join(keywords[:12]))}"
            "</p>\n"
        )
    block += "</div>\n"

    # 优先插入在 header 后，保证靠前可见；否则插入到 body 开头
    if "</header>" in html_content:
        return html_content.replace("</header>", "</header>\n" + block, 1)
    if "<body" in html_content:
        m = re.search(r"<body[^>]*>", html_content, re.IGNORECASE)
        if m:
            pos = m.end()
            return html_content[:pos] + "\n" + block + html_content[pos:]
    return block + html_content


def _inject_multi_perspective_section(html_content: str, judgement_payload: Dict[str, Any]) -> str:
    """
    兜底：当报告模型未显式生成“不同人物态度 / 多视角分析”板块时，强制注入一个可见章节。
    注意：该注入不编造事实，仅把“分析框架 + 用户关注点”落地到报告结构中。
    """
    if not html_content:
        return html_content
    if "不同人物态度 / 多视角分析" in html_content:
        return html_content

    user_judgement = str(judgement_payload.get("user_judgement", "") or "").strip()
    focus_keywords = judgement_payload.get("focus_keywords")
    keywords: List[str] = []
    if isinstance(focus_keywords, list):
        keywords = [str(x).strip() for x in focus_keywords if str(x).strip()]

    intro = ""
    if user_judgement:
        intro = (
            "<div class=\"highlight-box\">"
            f"<p><span class=\"key-point\">本次研判重点</span>{html.escape(user_judgement)}</p>"
            + (
                f"<p style=\"margin-top:8px;color:var(--muted);font-size:0.95rem;\"><strong>关注关键词：</strong>{html.escape('、'.join(keywords[:12]))}</p>"
                if keywords else
                ""
            )
            + "</div>"
        )

    # 兜底内容：给出可执行的“多视角框架”，并提示证据不足的处理方式
    block = (
        "\n"
        "<div class=\"section\" id=\"sona-multi-perspective\">\n"
        "  <h2 class=\"section-title\">不同人物态度 / 多视角分析</h2>\n"
        f"  {intro}\n"
        "  <div class=\"insight-card\">\n"
        "    <p style=\"color: var(--muted);\">"
        "为确保“多视角研判”在报告中可复核，本节按角色给出结构化对照。若输入材料中缺乏直接证据，将明确标注“证据不足”。"
        "</p>\n"
        "  </div>\n"
        "  <div class=\"two-column\">\n"
        "    <div class=\"insight-card\"><h4 style=\"color: var(--primary-strong); margin-bottom: 8px;\">当事人/相关个体</h4><p><strong>立场与态度：</strong>证据不足</p><p><strong>典型表达/证据：</strong>证据不足</p><p><strong>风险与建议：</strong>避免二次伤害；沟通以事实澄清与情绪安抚为先</p></div>\n"
        "    <div class=\"insight-card\"><h4 style=\"color: var(--primary-strong); margin-bottom: 8px;\">官方机构/监管/公安</h4><p><strong>立场与态度：</strong>证据不足</p><p><strong>典型表达/证据：</strong>证据不足</p><p><strong>风险与建议：</strong>统一口径、节点发布；用程序正义压制谣言扩散</p></div>\n"
        "    <div class=\"insight-card\"><h4 style=\"color: var(--primary-strong); margin-bottom: 8px;\">媒体/自媒体/KOL</h4><p><strong>立场与态度：</strong>证据不足</p><p><strong>典型表达/证据：</strong>证据不足</p><p><strong>风险与建议：</strong>防止标签化与煽动性标题；建议给出可核查来源链路</p></div>\n"
        "    <div class=\"insight-card\"><h4 style=\"color: var(--primary-strong); margin-bottom: 8px;\">平台方</h4><p><strong>立场与态度：</strong>证据不足</p><p><strong>典型表达/证据：</strong>证据不足</p><p><strong>风险与建议：</strong>强化群组治理、联动处置；对未成年人保护设置更高阈值</p></div>\n"
        "    <div class=\"insight-card\"><h4 style=\"color: var(--primary-strong); margin-bottom: 8px;\">普通网民</h4><p><strong>立场与态度：</strong>证据不足</p><p><strong>典型表达/证据：</strong>证据不足</p><p><strong>风险与建议：</strong>对立点需拆解；用“事实是否结清/情绪是否接住”组织回应</p></div>\n"
        "    <div class=\"insight-card\"><h4 style=\"color: var(--primary-strong); margin-bottom: 8px;\">争议群体/饭圈/对立阵营</h4><p><strong>立场与态度：</strong>证据不足</p><p><strong>典型表达/证据：</strong>证据不足</p><p><strong>风险与建议：</strong>重点识别组织化动员；避免以偏概全，区分个体表达与群体操控</p></div>\n"
        "  </div>\n"
        "</div>\n"
    )

    if "</div>\n\n        <footer" in html_content:
        return html_content.replace("</div>\n\n        <footer", block + "\n</div>\n\n        <footer", 1)
    if "</body>" in html_content:
        return html_content.replace("</body>", block + "\n</body>", 1)
    return html_content + "\n" + block


def _safe_int(value: Any) -> int:
    try:
        return int(str(value).replace(",", "").strip())
    except Exception:
        return 0


def _extract_dataset_csv_path(json_files: List[Dict[str, Any]]) -> Optional[Path]:
    """
    从 dataset_summary*.json 中提取原始 CSV 路径。
    """
    for item in json_files:
        filename = str(item.get("filename", "") or "")
        if not filename.startswith("dataset_summary"):
            continue
        content = item.get("content")
        if not isinstance(content, dict):
            continue
        save_path = str(content.get("save_path", "") or "").strip()
        if not save_path:
            ds = content.get("dataset_summary")
            if isinstance(ds, dict):
                save_path = str(ds.get("save_path", "") or "").strip()
        if save_path:
            p = Path(save_path).expanduser()
            if p.exists() and p.is_file():
                return p
    return None


def _build_dataset_evidence(csv_path: Optional[Path], limit: int = 8) -> str:
    """
    从原始数据中提取高互动样本，作为“事件证据引用池”。
    """
    if not csv_path:
        return ""

    try:
        with open(csv_path, "r", encoding="utf-8", errors="replace") as f:
            reader = csv.DictReader(f)
            rows = list(reader)
    except Exception:
        return ""

    if not rows:
        return ""

    def pick(row: Dict[str, Any], keys: List[str]) -> str:
        for k in keys:
            v = str(row.get(k, "") or "").strip()
            if v:
                return v
        return ""

    candidates: List[Dict[str, Any]] = []
    for i, row in enumerate(rows):
        if i >= 2500:
            break
        content = pick(row, ["内容", "content", "正文", "text"])
        title = pick(row, ["标题", "title"])
        text = content or title
        if not text or len(text) < 12:
            continue

        comments = _safe_int(pick(row, ["评论数", "comment_count", "comments"]))
        reposts = _safe_int(pick(row, ["转发数", "repost_count", "reposts"]))
        likes = _safe_int(pick(row, ["点赞数", "like_count", "likes"]))
        score = comments * 2 + reposts * 3 + likes

        candidates.append(
            {
                "score": score,
                "title": title,
                "content": text,
                "time": pick(row, ["发布时间", "time", "publish_time"]),
                "author": pick(row, ["作者", "author", "user_name"]),
                "platform": pick(row, ["平台", "platform"]),
                "comments": comments,
                "reposts": reposts,
                "likes": likes,
            }
        )

    if not candidates:
        return ""

    candidates.sort(key=lambda x: (int(x.get("score", 0)), len(str(x.get("content", "")))), reverse=True)
    lines: List[str] = []
    seen = set()
    for item in candidates:
        key = re.sub(r"\s+", " ", str(item.get("content", ""))).strip()[:120]
        if not key or key in seen:
            continue
        seen.add(key)
        text = str(item.get("content", ""))
        brief = text[:140] + ("..." if len(text) > 140 else "")
        meta = (
            f"时间={item.get('time', '') or '未知'}；"
            f"作者={item.get('author', '') or '未知'}；"
            f"平台={item.get('platform', '') or '未知'}；"
            f"互动=评{item.get('comments', 0)}/转{item.get('reposts', 0)}/赞{item.get('likes', 0)}"
        )
        lines.append(f"- {brief}\n  {meta}")
        if len(lines) >= max(1, limit):
            break

    return "\n".join(lines)


def _extract_fallback_chart_data(json_files: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    """从分析结果 JSON 中提取兜底报告可视化所需的基础图表数据。"""
    keyword_data: List[Dict[str, Any]] = []
    region_data: List[Dict[str, Any]] = []
    sentiment_data: List[Dict[str, Any]] = []
    volume_data: List[Dict[str, Any]] = []

    for item in json_files:
        filename = str(item.get("filename", "") or "").strip()
        content = item.get("content")
        if not isinstance(content, dict):
            continue

        if filename.startswith("keyword_stats"):
            rows = content.get("top_keywords") if isinstance(content.get("top_keywords"), list) else []
            for row in rows[:12]:
                if not isinstance(row, dict):
                    continue
                name = str(row.get("word", "") or "").strip()
                value = _safe_int(row.get("count", 0))
                if name and value > 0:
                    keyword_data.append({"name": name, "value": value})

        elif filename.startswith("region_stats"):
            rows = content.get("top_provinces") if isinstance(content.get("top_provinces"), list) else []
            for row in rows[:12]:
                if not isinstance(row, dict):
                    continue
                name = str(row.get("province", "") or "").strip()
                value = _safe_int(row.get("count", 0))
                if name and value > 0:
                    region_data.append({"name": name, "value": value})

        elif filename.startswith("volume_stats"):
            rows = content.get("data") if isinstance(content.get("data"), list) else []
            for row in rows[-14:]:
                if not isinstance(row, dict):
                    continue
                name = str(row.get("name", "") or "").strip()
                value = _safe_int(row.get("value", 0))
                if name and value >= 0:
                    volume_data.append({"name": name, "value": value})

        elif filename.startswith("sentiment_analysis") or filename.startswith("sentiment"):
            stats = content.get("statistics") if isinstance(content.get("statistics"), dict) else {}
            pos = _safe_int(stats.get("positive_count", stats.get("positive", 0)))
            neu = _safe_int(stats.get("neutral_count", stats.get("neutral", 0)))
            neg = _safe_int(stats.get("negative_count", stats.get("negative", 0)))
            if pos or neu or neg:
                sentiment_data = [
                    {"name": "正向", "value": pos},
                    {"name": "中性", "value": neu},
                    {"name": "负向", "value": neg},
                ]

    return {
        "keywords": keyword_data,
        "regions": region_data,
        "sentiment": sentiment_data,
        "volume": volume_data,
    }


def _needs_quality_retry(html_content: str) -> bool:
    """
    质量兜底：当报告过短或未覆盖关键方法论章节时，触发一次重试。
    """
    if not html_content:
        return True
    text = html_content.strip()
    if len(text) < 2200:
        return True
    required_terms = [
        "舆情分析核心维度",
        "舆情生命周期",
        "理论规律",
        "回应观察",
        "总结复盘",
    ]
    matched = sum(1 for t in required_terms if t in text)
    return matched < 3


def _build_fallback_html(
    *,
    event_introduction: str,
    analysis_results_text: str,
    methodology_content: str,
    model_error: str,
    chart_data: Optional[Dict[str, List[Dict[str, Any]]]] = None,
) -> str:
    """
    当模型不可用时，生成一个可直接打开的静态兜底报告（含基础图表）。
    """
    title = "舆情分析报告（Fallback）"
    intro = html.escape(event_introduction or "未提供事件介绍")
    analysis_block = html.escape((analysis_results_text or "无分析结果")[:20000])
    methodology_block = html.escape((methodology_content or "无方法论内容")[:12000])
    error_block = html.escape(model_error or "未知错误")
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    raw_chart_data = chart_data or {}

    def _normalize_items(items: Any, max_items: int = 12) -> List[Dict[str, Any]]:
        if not isinstance(items, list):
            return []
        normalized: List[Dict[str, Any]] = []
        for row in items:
            if not isinstance(row, dict):
                continue
            name = str(row.get("name", "") or "").strip()
            value = _safe_int(row.get("value", 0))
            if not name or value < 0:
                continue
            normalized.append({"name": name, "value": value})
            if len(normalized) >= max_items:
                break
        return normalized

    keyword_items = _normalize_items(raw_chart_data.get("keywords"), max_items=12)
    region_items = _normalize_items(raw_chart_data.get("regions"), max_items=12)
    sentiment_items = _normalize_items(raw_chart_data.get("sentiment"), max_items=8)
    volume_items = _normalize_items(raw_chart_data.get("volume"), max_items=14)

    def _build_chart_card(section_title: str, items: List[Dict[str, Any]], color: str) -> str:
        if not items:
            return ""
        max_value = max((int(x.get("value", 0)) for x in items), default=1)
        rows: List[str] = []
        for row in items:
            label = html.escape(str(row.get("name", "") or ""))
            value = int(row.get("value", 0))
            width = max(4.0, (value / max_value) * 100.0) if max_value > 0 else 4.0
            rows.append(
                "<div class=\"chart-row\">"
                f"<div class=\"label\" title=\"{label}\">{label}</div>"
                "<div class=\"bar-wrap\">"
                f"<div class=\"bar\" style=\"width:{width:.2f}%;background:{color};\"></div>"
                "</div>"
                f"<div class=\"value\">{value}</div>"
                "</div>"
            )
        rows_html = "".join(rows)
        return (
            "<div class=\"card\">"
            f"<h2>{html.escape(section_title)}</h2>"
            "<div class=\"chart\">"
            f"{rows_html}"
            "</div>"
            "</div>"
        )

    chart_sections = [
        _build_chart_card("关键词热度 Top", keyword_items, "#2563eb"),
        _build_chart_card("地域分布 Top", region_items, "#7c3aed"),
        _build_chart_card("情绪分布", sentiment_items, "#db2777"),
        _build_chart_card("声量趋势（近14个时间点）", volume_items, "#0d9488"),
    ]
    charts_html = "".join([x for x in chart_sections if x])

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>{title}</title>
  <style>
    :root {{
      --bg: #f5f7fb;
      --card: #ffffff;
      --text: #1f2937;
      --muted: #6b7280;
      --line: #e5e7eb;
      --accent: #1d4ed8;
    }}
    body {{
      margin: 0;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif;
      background: linear-gradient(180deg, #f9fbff 0%, var(--bg) 100%);
      color: var(--text);
    }}
    .wrap {{
      max-width: 1100px;
      margin: 28px auto;
      padding: 0 16px 28px;
    }}
    .card {{
      background: var(--card);
      border: 1px solid var(--line);
      border-radius: 14px;
      padding: 18px 20px;
      margin-bottom: 14px;
      box-shadow: 0 6px 18px rgba(15, 23, 42, 0.05);
    }}
    h1 {{ margin: 0 0 8px; color: var(--accent); font-size: 28px; }}
    h2 {{ margin: 0 0 10px; font-size: 18px; }}
    .meta {{ color: var(--muted); font-size: 13px; margin-bottom: 10px; }}
    pre {{
      margin: 0;
      white-space: pre-wrap;
      word-break: break-word;
      line-height: 1.6;
      font-size: 13px;
      background: #f8fafc;
      border: 1px solid #e2e8f0;
      border-radius: 10px;
      padding: 12px;
    }}
    .warn {{
      color: #991b1b;
      background: #fef2f2;
      border: 1px solid #fecaca;
      border-radius: 10px;
      padding: 10px 12px;
      font-size: 13px;
    }}
    .chart {{
      display: flex;
      flex-direction: column;
      gap: 8px;
    }}
    .chart-row {{
      display: grid;
      grid-template-columns: 180px 1fr 64px;
      align-items: center;
      gap: 10px;
    }}
    .label {{
      color: #374151;
      font-size: 12px;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }}
    .bar-wrap {{
      height: 12px;
      background: #e5e7eb;
      border-radius: 999px;
      overflow: hidden;
    }}
    .bar {{
      height: 12px;
      border-radius: 999px;
    }}
    .value {{
      text-align: right;
      font-size: 12px;
      color: #111827;
      font-variant-numeric: tabular-nums;
    }}
  </style>
</head>
<body>
  <div class="wrap">
    <div class="card">
      <h1>{title}</h1>
      <div class="meta">生成时间：{generated_at}</div>
      <div class="warn">报告模型调用失败，已启用兜底报告。错误信息：{error_block}</div>
    </div>

    {charts_html}

    <div class="card">
      <h2>事件基础介绍</h2>
      <pre>{intro}</pre>
    </div>

    <div class="card">
      <h2>分析结果原始摘要</h2>
      <pre>{analysis_block}</pre>
    </div>

    <div class="card">
      <h2>舆情智库方法论参考</h2>
      <pre>{methodology_block}</pre>
    </div>
  </div>
</body>
</html>"""


def _inject_echarts_cdn_guard(html_content: str) -> str:
    """为外链 ECharts 增加离线兜底，避免 CDN 失败导致页面不可用。"""
    if "echarts.min.js" not in html_content:
        return html_content

    guard_script = """
<script>
(function () {
  function installFallback() {
    if (window.echarts) return;
    window.__SONA_ECHARTS_UNAVAILABLE__ = true;
    window.echarts = {
      init: function (el) {
        return {
          setOption: function () {
            if (!el) return;
            el.innerHTML = '<div style="height:100%;display:flex;align-items:center;justify-content:center;padding:12px;color:#6b7280;font-size:13px;border:1px dashed #cbd5e1;border-radius:10px;background:#f8fafc;">图表库加载失败（网络/CDN受限），当前仅展示文字内容。</div>';
          },
          resize: function () {}
        };
      }
    };

    if (!document.getElementById("sona-echarts-warning")) {
      var node = document.createElement("div");
      node.id = "sona-echarts-warning";
      node.style.cssText = "margin:12px 0;padding:10px 12px;border:1px solid #fecaca;border-radius:10px;background:#fef2f2;color:#991b1b;font-size:13px;";
      node.textContent = "检测到图表库加载失败（CDN 不可达），图表已降级为占位提示。";
      var anchor = document.body ? (document.querySelector(".container") || document.querySelector(".wrap") || document.body) : null;
      if (anchor && anchor.firstChild) {
        anchor.insertBefore(node, anchor.firstChild);
      } else if (anchor) {
        anchor.appendChild(node);
      }
    }
  }

  // 关键：若 CDN 加载失败，先立刻安装 fallback，避免后续脚本因 echarts 未定义而直接中断。
  // 这样即使离线打开 HTML，也会在图表区域看到明确的降级提示，而不是空白。
  if (!window.echarts) {
    installFallback();
  }

  window.addEventListener("error", function (evt) {
    var msg = (evt && evt.message) ? String(evt.message) : "";
    if (msg.toLowerCase().indexOf("echarts") >= 0) {
      installFallback();
    }
  }, true);

  setTimeout(function () {
    if (!window.echarts) {
      installFallback();
    }
  }, 2200);
})();
</script>
""".strip()

    echarts_tag_pattern = re.compile(r"<script[^>]*echarts[^>]*></script>", re.IGNORECASE)
    if echarts_tag_pattern.search(html_content):
        return echarts_tag_pattern.sub(lambda m: m.group(0) + "\n" + guard_script, html_content, count=1)

    if "</body>" in html_content:
        return html_content.replace("</body>", guard_script + "\n</body>", 1)
    return html_content + "\n" + guard_script


def _is_rate_limit_error(exc: Exception) -> bool:
    """判断是否为可重试的限流/并发类错误。"""
    msg = str(exc).lower()
    keywords = [
        "429",
        "rate_limit",
        "rate limit",
        "concurrency",
        "too many requests",
    ]
    return any(k in msg for k in keywords)


def _invoke_model_with_retry(
    model: Any,
    messages: List[Any],
    *,
    max_attempts: int = 4,
    base_delay_seconds: float = 1.0,
) -> Any:
    """
    调用模型并在限流/并发报错时自动重试。
    使用指数退避，尽量规避短时并发峰值导致的 429。
    """
    last_error: Optional[Exception] = None
    for attempt in range(1, max_attempts + 1):
        try:
            return model.invoke(messages)
        except Exception as exc:
            last_error = exc
            if not _is_rate_limit_error(exc) or attempt >= max_attempts:
                raise
            delay = base_delay_seconds * (2 ** (attempt - 1))
            time.sleep(delay)
    if last_error:
        raise last_error
    raise RuntimeError("模型调用失败：未知错误")


def _get_file_url(file_path: Path) -> str:
    """
    获取文件的 file:// URL。
    
    Args:
        file_path: 文件路径
        
    Returns:
        file:// URL 字符串
    """
    # 使用 pathlib 的 URI 转换，自动处理中文/空格等字符编码，避免 macOS 打开 file:// 报 -43
    abs_path = file_path.resolve()
    try:
        return abs_path.as_uri()
    except Exception:
        if os.name == "nt":
            url_path = str(abs_path).replace("\\", "/")
            return f"file:///{url_path}"
        return f"file://{abs_path}"


@tool
def report_html(
    eventIntroduction: str,
    analysisResultsDir: str
) -> str:
    """
    描述：生成HTML报告。根据提供的事件基础介绍和分析结果文件夹，生成美观的HTML舆情分析报告。
    使用时机：当需要生成最终的HTML报告时调用本工具。
    输入：
    - eventIntroduction（必填）：事件基础介绍，由 extract_search_terms 工具生成，用于告知模型事件背景，避免分析跑偏。
    - analysisResultsDir（必填）：分析结果文件夹路径，通常是 sandbox/任务ID/过程文件，包含所有分析结果的JSON文件。
    输出：JSON字符串，包含以下字段：
    - html_file_path：生成的HTML文件路径（保存在任务的结果文件夹中）
    - file_url：本地文件访问地址（file:// 协议，可直接在浏览器中打开）
    """
    import json as json_module
    
    # 获取任务ID
    task_id = get_task_id()
    if not task_id:
        return json_module.dumps({
            "error": "未找到任务ID，请确保在Agent上下文中调用",
            "html_file_path": "",
            "file_url": ""
        }, ensure_ascii=False)
    
    # 读取分析结果文件夹中的所有JSON文件
    try:
        json_files = _read_json_files(analysisResultsDir)
    except Exception as e:
        return json_module.dumps({
            "error": f"读取分析结果文件夹失败: {str(e)}",
            "html_file_path": "",
            "file_url": ""
        }, ensure_ascii=False)
    
    if not json_files:
        return json_module.dumps({
            "error": "分析结果文件夹中没有找到JSON文件",
            "html_file_path": "",
            "file_url": ""
        }, ensure_ascii=False)
    
    # 获取报告模型和prompt
    try:
        model = get_report_model()
        prompt_template = get_report_html_prompt()
    except Exception as e:
        return json_module.dumps({
            "error": f"获取报告模型失败: {str(e)}",
            "html_file_path": "",
            "file_url": ""
        }, ensure_ascii=False)
    
    # 读取舆情智库方法论
    methodology_content = load_methodology_for_report(topic=eventIntroduction)
    
    # 构建提示词
    analysis_results_text = ""
    for json_file in json_files:
        analysis_results_text += f"\n## 文件: {json_file['filename']}\n"
        analysis_results_text += json_module.dumps(json_file['content'], ensure_ascii=False, indent=2)
        analysis_results_text += "\n"

    # 从原始 CSV 中补充“高互动样本证据”，避免报告只依赖汇总 JSON
    csv_path = _extract_dataset_csv_path(json_files)
    dataset_evidence = _build_dataset_evidence(csv_path, limit=8)
    if dataset_evidence:
        analysis_results_text += "\n## 事件原始高互动样本（CSV提取）\n"
        analysis_results_text += dataset_evidence
        analysis_results_text += "\n"

    graph_rag_summary, graph_rag_enabled = _build_graph_rag_context(json_files)
    if graph_rag_summary:
        analysis_results_text += "\n## Graph RAG 增强摘要（结构化提炼）\n"
        analysis_results_text += graph_rag_summary
        analysis_results_text += "\n"

    reference_summary, has_reference_hits = _build_reference_context(json_files)
    if reference_summary:
        analysis_results_text += "\n## 舆情智库参考摘要（结构化提炼）\n"
        analysis_results_text += reference_summary
        analysis_results_text += "\n"

    wiki_kb_summary, _has_wiki_kb = _build_wiki_kb_snapshot_context(json_files)
    if wiki_kb_summary:
        analysis_results_text += "\n## Wiki / 领域知识库召回快照（答辩举证：路由领域与来源路径）\n"
        analysis_results_text += wiki_kb_summary
        analysis_results_text += "\n"

    collab_summary, has_collab_context = _build_collab_context(json_files)
    if collab_summary:
        analysis_results_text += "\n## 用户协同输入与外部参考（结构化提炼）\n"
        analysis_results_text += collab_summary
        analysis_results_text += "\n"

    user_judgement_payload = _extract_user_judgement_payload(json_files)
    
    # 格式化prompt（包含方法论）
    # 使用“定向占位符替换”，避免 ECharts 模板中的 {name}/{value} 被误解析
    def _replace_placeholders(t: str, *, event_intro: str, analysis_text: str, methodology_text: str) -> str:
        try:
            import re as _re
        except Exception:
            return (
                t.replace("{event_introduction}", event_intro)
                .replace("{analysis_results}", analysis_text)
                .replace("{methodology}", methodology_text)
            )

        mapping = {
            "event_introduction": event_intro,
            "analysis_results": analysis_text,
            "methodology": methodology_text,
        }
        pattern = _re.compile(r"\{(event_introduction|analysis_results|methodology)\}")

        def repl(m):
            key = m.group(1)
            return str(mapping.get(key, m.group(0)))

        return pattern.sub(repl, t)

    prompt = _replace_placeholders(
        prompt_template,
        event_intro=eventIntroduction,
        analysis_text=analysis_results_text,
        methodology_text=methodology_content,
    )
    prompt += (
        "\n\n【事实边界要求】\n"
        "你只能引用输入材料中出现的事实、名称与数据；若证据不足，请明确写“证据不足”，不得编造案例或观点。"
    )
    if dataset_evidence:
        prompt += (
            "\n\n【证据链要求】\n"
            "请至少引用 2 条“事件原始高互动样本（CSV提取）”中的内容，形成“数据证据 -> 研判结论”链路。"
        )
    if graph_rag_enabled:
        prompt += (
            "\n\n【Graph RAG 融合要求】\n"
            "请在报告中单独设置“Graph RAG 增强洞察”小节，"
            "明确引用相似案例、理论与指标补充，并说明它们如何改变风险判断与建议。\n"
            "只能引用 Graph RAG 摘要里真实出现的案例/理论/指标；若 similar_cases 为空，必须明确写“暂无可比历史案例”。"
        )
    if has_reference_hits:
        prompt += (
            "\n\n【参考资料引用要求】\n"
            "若输入中存在 reference_insights.json，请至少引用 2 条其中的 snippet，并在句末标注来源标题。\n"
            "理论/观点优先使用 reference_insights 与 Graph RAG 中真实出现的内容；未出现者不得强行套用。\n"
            "若证据不足，请明确写“证据不足”。"
        )
    if has_collab_context:
        prompt += (
            "\n\n【协同输入对齐要求】\n"
            "若输入含 user_judgement_input.json 或 user_expert_notes.json，需在“研判结论/建议”中显式回应其关注点。\n"
            "若输入含 weibo_aisearch_reference.json，请将其作为外部参考线索而非事实锚点，必须与本地数据交叉验证后再下结论。"
        )
    
    # 调用模型生成HTML
    model_error = ""
    fallback_chart_data = _extract_fallback_chart_data(json_files)
    try:
        messages = [
            SystemMessage(content="你是一个专业的HTML报告生成专家，擅长创建美观、交互式的舆情分析报告。"),
            HumanMessage(content=prompt)
        ]
        response = _invoke_model_with_retry(model, messages)
        html_content = response.content if hasattr(response, 'content') else str(response)

        # 质量兜底：过于浅层时进行一次强化重试
        if _needs_quality_retry(str(html_content)):
            retry_prompt = (
                prompt
                + "\n\n【强制质量要求】\n"
                + "1) 不能只做数据描述，必须给出研判结论、风险研判、回应建议。\n"
                + "2) 必须完整覆盖：舆情分析核心维度、舆情生命周期阶段、理论规律分析、回应观察与分析、总结复盘。\n"
                + "3) 每个章节至少包含1条“数据证据 -> 结论”的推理链。\n"
                + "4) 明确引用并吸收“舆情智库方法论指导”中的术语与框架。\n"
            )
            retry_messages = [
                SystemMessage(content="你是资深舆情研究员，同时是可视化报告专家。"),
                HumanMessage(content=retry_prompt),
            ]
            retry_resp = _invoke_model_with_retry(model, retry_messages)
            retry_html = retry_resp.content if hasattr(retry_resp, "content") else str(retry_resp)
            if retry_html and len(str(retry_html).strip()) >= len(str(html_content).strip()):
                html_content = retry_html
    except Exception as e:
        model_error = f"模型生成HTML失败: {str(e)}"
        html_content = _build_fallback_html(
            event_introduction=eventIntroduction,
            analysis_results_text=analysis_results_text,
            methodology_content=methodology_content,
            model_error=model_error,
            chart_data=fallback_chart_data,
        )
    
    # 清理HTML内容（移除markdown代码块标记）
    html_content = html_content.strip()
    if html_content.startswith("```html"):
        html_content = html_content[7:]
    elif html_content.startswith("```"):
        html_content = html_content[3:]
    if html_content.endswith("```"):
        html_content = html_content[:-3]
    html_content = html_content.strip()

    html_content = _inject_user_judgement_section(html_content, user_judgement_payload)
    html_content = _inject_multi_perspective_section(html_content, user_judgement_payload)
    html_content = _inject_echarts_cdn_guard(html_content)

    # 确保结果文件夹存在
    result_dir = get_task_result_dir(task_id)
    result_dir.mkdir(parents=True, exist_ok=True)
    
    # 生成HTML文件名
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    html_filename = f"report_{timestamp}.html"
    html_file_path = result_dir / html_filename
    
    # 保存HTML文件
    try:
        with open(html_file_path, 'w', encoding='utf-8') as f:
            f.write(html_content)
    except Exception as e:
        return json_module.dumps({
            "error": f"保存HTML文件失败: {str(e)}",
            "html_file_path": "",
            "file_url": ""
        }, ensure_ascii=False)
    
    # 生成 file:// URL
    file_url = _get_file_url(html_file_path)
    
    # 返回结果（包含HTML文件路径和 file:// URL）
    result = {
        "html_file_path": str(html_file_path),
        "file_url": file_url
    }
    if model_error:
        result["warning"] = model_error

    # 尝试在默认浏览器中自动打开（失败不影响主流程）
    try:
        if file_url:
            webbrowser.open(file_url)
            result["opened_in_browser"] = True
        else:
            result["opened_in_browser"] = False
    except Exception as _:
        result["opened_in_browser"] = False

    return json_module.dumps(result, ensure_ascii=False)


def _extract_eval_report_feedback(project_root: Optional[Path] = None) -> Dict[str, Any]:
    """
    从 eval_results 下最新一轮 summary.json 提取「报告阶段」评测反馈，供报告生成回灌。
    若无有效结果则 has_feedback=False。
    """
    from utils.path import get_project_root

    root = Path(project_root) if project_root is not None else Path(get_project_root())
    eval_dir = root / "eval_results"
    if not eval_dir.is_dir():
        return {"has_feedback": False}

    run_dirs = sorted((p for p in eval_dir.iterdir() if p.is_dir()), key=lambda p: p.name, reverse=True)
    for run in run_dirs:
        summary_path = run / "summary.json"
        if not summary_path.is_file():
            continue
        try:
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        results = summary.get("results")
        if not isinstance(results, list):
            continue
        reasons: List[str] = []
        for item in results:
            if not isinstance(item, dict):
                continue
            if str(item.get("stage", "")).strip() != "report":
                continue
            for fr in item.get("fail_reasons") or []:
                if fr is not None and str(fr).strip():
                    reasons.append(str(fr).strip())
        if not reasons:
            continue
        return {
            "has_feedback": True,
            "run_id": str(summary.get("run_id", run.name)),
            "reasons": reasons,
        }
    return {"has_feedback": False}


def _build_eval_feedback_block(feedback: Dict[str, Any]) -> str:
    """将评测反馈格式化为可注入 HTML 的说明块（无反馈时返回空串）。"""
    if not feedback.get("has_feedback"):
        return ""
    reasons = feedback.get("reasons") or []
    if not isinstance(reasons, list):
        reasons = [str(reasons)]
    lines: List[str] = [
        "<section class='eval-feedback'>",
        "<h3>上次评测回灌（自动）</h3>",
        "<p><strong>需修复问题</strong></p>",
        "<ul>",
    ]
    for r in reasons:
        lines.append(f"<li>{html.escape(str(r))}</li>")
    lines.append("</ul>")
    lines.append("<p><strong>本次生成硬约束</strong></p>")
    lines.append("<p>请逐条对照上述问题修复后再输出终稿。</p>")
    lines.append("</section>")
    return "\n".join(lines)
