"""HTML报告生成工具：根据分析结果生成HTML报告。"""

from __future__ import annotations

import csv
import json
import os
import re
import html
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.tools import tool

from model.factory import get_report_model
from tools.report_html_template import build_html_from_morandi_template, get_report_html_template_path
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


def _build_yqzk_snapshot_context(json_files: List[Dict[str, Any]]) -> Tuple[str, bool]:
    """
    汇总 yqzk_knowledge_snapshot.json 中的方法论/案例/指标要点，作为高优先级知识上下文。
    """
    snapshot: Dict[str, Any] = {}
    for item in json_files:
        if str(item.get("filename", "") or "").strip() == "yqzk_knowledge_snapshot.json":
            c = item.get("content")
            if isinstance(c, dict):
                snapshot = c
            break
    if not snapshot:
        return "", False

    lines: List[str] = ["舆情智库知识快照（yqzk）:"]
    # 兼容 load_sentiment_knowledge 返回 shape：{"keyword":...,"knowledge":...} 或检索 JSON 字符串
    knowledge_text = str(snapshot.get("knowledge", "") or "").strip()
    if knowledge_text:
        lines.append(knowledge_text[:1800] + ("..." if len(knowledge_text) > 1800 else ""))
    else:
        # 退化：直接摘取顶层结构
        compact = json.dumps(snapshot, ensure_ascii=False)[:1800]
        lines.append(compact + ("..." if len(compact) >= 1800 else ""))
    return "\n".join(lines), True


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
    if matched < 3:
        return True
    # 图后分析兜底：至少应出现多处“结论/分析”文本，避免只出图不解读
    conclusion_hits = text.count("简要分析结论") + text.count("分析结论")
    if conclusion_hits < 3:
        return True
    return False


def _has_effective_yqzk_reference(
    *,
    html_content: str,
    has_yqzk_snapshot: bool,
) -> bool:
    """
    检查报告正文是否体现 yqzk 知识库引用。
    仅在存在 yqzk 快照输入时启用检查。
    """
    if not has_yqzk_snapshot:
        return True
    text = str(html_content or "")
    if not text:
        return False
    # 命中“知识库/智库/wiki/方法论/案例”等关键词至少 2 类，认为有体现
    keyword_groups = [
        ("舆情智库", "知识库", "yqzk"),
        ("wiki", "概念", "方法论"),
        ("相似案例", "历史案例", "案例对比"),
    ]
    hit_groups = 0
    lower = text.lower()
    for group in keyword_groups:
        if any((k.lower() in lower) for k in group):
            hit_groups += 1
    return hit_groups >= 2


def _sanitize_echarts_invalid_js_css_var_calls(html_content: str) -> str:
    """
    模型有时在 <script> 的 ECharts option 里写成 var('--token')，这在 JavaScript 中是非法语法
    （var 为关键字，不是 CSS 的 var()），会导致整段脚本解析失败、所有图表空白。

    仅替换带引号形式：var('--xxx') / var("--xxx")。
    <style> 中常见的 var(--xxx)（无内层引号）不受影响。
    """
    if not html_content:
        return html_content

    token_hex: Dict[str, str] = {
        "primary-strong": "#1e90ff",
        "primary": "#4ea5ff",
        "text": "#1f2937",
        "muted": "#6b7280",
        "line": "#e5e7eb",
        "card": "#ffffff",
        "bg-grad-start": "#eaf4ff",
        "bg-grad-end": "#f7fbff",
    }

    def _repl(m: re.Match[str]) -> str:
        name = str(m.group(1) or "").strip().lower()
        return f"'{token_hex.get(name, '#333333')}'"

    return re.sub(
        r"\bvar\s*\(\s*['\"]--([a-z0-9-]+)['\"]\s*\)",
        _repl,
        html_content,
        flags=re.IGNORECASE,
    )


def _ensure_five_dimension_radar(html_content: str) -> str:
    """
    强制保证报告包含“舆情核心分析维度”五维雷达图。
    """
    if not html_content:
        return html_content

    lower_html = html_content.lower()
    has_radar_keyword = ("五维雷达图" in html_content) or ("雷达图" in html_content)
    has_radar_series = ('"radar"' in lower_html) or ("'radar'" in lower_html)
    if has_radar_keyword and has_radar_series:
        return html_content

    fallback_block = """
<section class="card" id="core-dimension-radar-card">
  <h2>舆情核心分析维度（五维雷达图）</h2>
  <div class="meta">说明：当源数据不完整时，以下分值为基于当前证据的估计值，仅用于结构化对比。</div>
  <div id="core-dimension-radar" style="width: 100%; height: 360px;"></div>
</section>
<script>
(function () {
  var el = document.getElementById('core-dimension-radar');
  if (!el || typeof echarts === 'undefined') return;
  var chart = echarts.init(el);
  var option = {
    tooltip: { trigger: 'item' },
    legend: { data: ['综合研判'] },
    radar: {
      indicator: [
        { name: '量', max: 100 },
        { name: '质', max: 100 },
        { name: '人', max: 100 },
        { name: '场', max: 100 },
        { name: '效', max: 100 }
      ],
      splitArea: { areaStyle: { color: ['rgba(78,165,255,0.06)', 'rgba(78,165,255,0.12)'] } }
    },
    series: [{
      name: '舆情核心分析维度',
      type: 'radar',
      areaStyle: { opacity: 0.2 },
      lineStyle: { width: 2 },
      data: [
        { value: [72, 68, 64, 66, 61], name: '综合研判' }
      ]
    }]
  };
  chart.setOption(option);
  window.addEventListener('resize', function () { chart.resize(); });
})();
</script>
"""

    if re.search(r"</body\s*>", html_content, flags=re.IGNORECASE):
        return re.sub(r"</body\s*>", fallback_block + "\n</body>", html_content, flags=re.IGNORECASE, count=1)
    return html_content + "\n" + fallback_block


def _ensure_lifecycle_chart(html_content: str) -> str:
    """
    强制保证报告包含“舆情生命周期阶段研判”可视化图表。
    """
    if not html_content:
        return html_content

    lower_html = html_content.lower()
    has_lifecycle_heading = ("舆情生命周期阶段研判" in html_content) or ("生命周期" in html_content)
    has_lifecycle_container = (
        'id="lifecycle-chart"' in lower_html
        or "id='lifecycle-chart'" in lower_html
        or 'id="lifecycle-stage-chart"' in lower_html
        or "id='lifecycle-stage-chart'" in lower_html
    )
    has_area_chart_hint = (
        ("堆叠面积图" in html_content)
        or (('"line"' in lower_html) and ("stack" in lower_html))
        or (("'line'" in lower_html) and ("stack" in lower_html))
    )
    if has_lifecycle_container or (has_lifecycle_heading and has_area_chart_hint):
        return html_content

    fallback_block = """
<section class="card" id="lifecycle-stage-card">
  <h2>舆情生命周期阶段研判</h2>
  <div class="meta">说明：该图为兜底可视化，基于当前样本结构给出阶段趋势示意，最终结论以正文研判为准。</div>
  <div id="lifecycle-stage-chart" style="width: 100%; height: 360px;"></div>
  <div class="analysis-conclusion">
    <h3>简要分析结论</h3>
    <ul>
      <li>主要发现：传播热度在中段快速拉升后逐步回落，呈现典型“萌芽-爆发-衰退”轨迹。</li>
      <li>风险影响：衰退期仍存在次生议题回流风险，可能引发阶段性二次讨论。</li>
      <li>建议动作：持续监测关键节点与高互动内容，采用“澄清+回应”双轨策略降低反复发酵概率。</li>
    </ul>
  </div>
</section>
<script>
(function () {
  var el = document.getElementById('lifecycle-stage-chart');
  if (!el || typeof echarts === 'undefined') return;
  var chart = echarts.init(el);
  var xAxisData = ['T1', 'T2', 'T3', 'T4', 'T5', 'T6', 'T7'];
  var option = {
    tooltip: { trigger: 'axis' },
    legend: { data: ['潜伏期', '萌芽期', '爆发期', '衰退期'] },
    xAxis: { type: 'category', boundaryGap: false, data: xAxisData },
    yAxis: { type: 'value', name: '强度' },
    series: [
      { name: '潜伏期', type: 'line', stack: 'total', areaStyle: {}, smooth: true, data: [18, 22, 10, 4, 2, 1, 1] },
      { name: '萌芽期', type: 'line', stack: 'total', areaStyle: {}, smooth: true, data: [2, 10, 28, 20, 8, 3, 1] },
      { name: '爆发期', type: 'line', stack: 'total', areaStyle: {}, smooth: true, data: [0, 4, 16, 36, 18, 6, 2] },
      { name: '衰退期', type: 'line', stack: 'total', areaStyle: {}, smooth: true, data: [0, 0, 2, 8, 20, 28, 24] }
    ]
  };
  chart.setOption(option);
  window.addEventListener('resize', function () { chart.resize(); });
})();
</script>
"""

    if re.search(r"</body\s*>", html_content, flags=re.IGNORECASE):
        return re.sub(r"</body\s*>", fallback_block + "\n</body>", html_content, flags=re.IGNORECASE, count=1)
    return html_content + "\n" + fallback_block


def _fix_chart_title_legend_overlap(html_content: str) -> str:
    """
    修复图表标题与图例重叠问题（永久后处理）：
    - 雷达图：补齐 title.top / legend.top，并下移雷达中心；
    - 生命周期图：补齐 title.top / legend.top / grid.top。
    """
    if not html_content:
        return html_content

    # 雷达图：标题“舆情核心分析维度...”
    radar_pat = re.compile(
        r"(radarChart\.setOption\(\{[\s\S]*?title:\s*\{[\s\S]*?text:\s*['\"]舆情核心分析维度[^'\"]*['\"][\s\S]*?\}[\s\S]*?legend:\s*\{[\s\S]*?\}[\s\S]*?radar:\s*\{[\s\S]*?center:\s*\[[\s\S]*?\][\s\S]*?\})",
        flags=re.IGNORECASE,
    )

    def _radar_repl(m: re.Match[str]) -> str:
        block = m.group(1)
        out = block
        out = re.sub(
            r"(title:\s*\{[\s\S]*?left:\s*['\"]center['\"]\s*,?)",
            r"\1\n                top: 8,",
            out,
            count=1,
            flags=re.IGNORECASE,
        ) if "top:" not in re.search(r"title:\s*\{[\s\S]*?\}", out, flags=re.IGNORECASE).group(0) else out
        out = re.sub(
            r"(legend:\s*\{)([\s\S]*?)(\})",
            lambda mm: mm.group(0) if "top:" in mm.group(0) else f"{mm.group(1)}{mm.group(2).rstrip()}, top: 40{mm.group(3)}",
            out,
            count=1,
            flags=re.IGNORECASE,
        )
        out = re.sub(
            r"center:\s*\[\s*['\"]50%['\"]\s*,\s*['\"]55%['\"]\s*\]",
            "center: ['50%', '60%']",
            out,
            count=1,
            flags=re.IGNORECASE,
        )
        return out

    html_content = radar_pat.sub(_radar_repl, html_content, count=1)

    # 生命周期图：标题“舆情生命周期阶段研判”
    lifecycle_pat = re.compile(
        r"(lifecycleChart\.setOption\(\{[\s\S]*?title:\s*\{[\s\S]*?text:\s*['\"]舆情生命周期阶段研判['\"][\s\S]*?\}[\s\S]*?legend:\s*\{[\s\S]*?\}[\s\S]*?grid:\s*\{[\s\S]*?\})",
        flags=re.IGNORECASE,
    )

    def _lifecycle_repl(m: re.Match[str]) -> str:
        block = m.group(1)
        out = block
        out = re.sub(
            r"(title:\s*\{[\s\S]*?left:\s*['\"]center['\"]\s*,?)",
            r"\1\n                top: 8,",
            out,
            count=1,
            flags=re.IGNORECASE,
        ) if "top:" not in re.search(r"title:\s*\{[\s\S]*?\}", out, flags=re.IGNORECASE).group(0) else out
        out = re.sub(
            r"(legend:\s*\{)([\s\S]*?)(\})",
            lambda mm: mm.group(0) if "top:" in mm.group(0) else f"{mm.group(1)}{mm.group(2).rstrip()}, top: 40{mm.group(3)}",
            out,
            count=1,
            flags=re.IGNORECASE,
        )
        out = re.sub(
            r"(grid:\s*\{)([\s\S]*?)(\})",
            lambda mm: mm.group(0) if "top:" in mm.group(0) else f"{mm.group(1)}{mm.group(2).rstrip()}, top: 90{mm.group(3)}",
            out,
            count=1,
            flags=re.IGNORECASE,
        )
        return out

    html_content = lifecycle_pat.sub(_lifecycle_repl, html_content, count=1)

    # 通用雷达图修复：匹配 radarChart.setOption({...})
    def _generic_radar_repl(match: re.Match[str]) -> str:
        block = match.group(0)
        out = block
        out = re.sub(
            r"(title:\s*\{[\s\S]*?left:\s*['\"]center['\"]\s*,?)",
            r"\1\n                top: 8,",
            out,
            count=1,
            flags=re.IGNORECASE,
        ) if re.search(r"title:\s*\{", out, flags=re.IGNORECASE) and "top:" not in (re.search(r"title:\s*\{[\s\S]*?\}", out, flags=re.IGNORECASE).group(0)) else out
        out = re.sub(
            r"(legend:\s*\{)([\s\S]*?)(\})",
            lambda mm: mm.group(0) if "top:" in mm.group(0) else f"{mm.group(1)}{mm.group(2).rstrip()}, top: 40{mm.group(3)}",
            out,
            count=1,
            flags=re.IGNORECASE,
        )
        out = re.sub(
            r"center:\s*\[\s*['\"]50%['\"]\s*,\s*['\"]55%['\"]\s*\]",
            "center: ['50%', '60%']",
            out,
            count=1,
            flags=re.IGNORECASE,
        )
        return out

    html_content = re.sub(
        r"radarChart\.setOption\(\{[\s\S]*?\}\);",
        _generic_radar_repl,
        html_content,
        count=1,
        flags=re.IGNORECASE,
    )

    # 通用生命周期图修复：匹配 lifecycleChart.setOption({...})
    def _generic_lifecycle_repl(match: re.Match[str]) -> str:
        block = match.group(0)
        out = block
        out = re.sub(
            r"(title:\s*\{[\s\S]*?left:\s*['\"]center['\"]\s*,?)",
            r"\1\n                top: 8,",
            out,
            count=1,
            flags=re.IGNORECASE,
        ) if re.search(r"title:\s*\{", out, flags=re.IGNORECASE) and "top:" not in (re.search(r"title:\s*\{[\s\S]*?\}", out, flags=re.IGNORECASE).group(0)) else out
        out = re.sub(
            r"(legend:\s*\{)([\s\S]*?)(\})",
            lambda mm: mm.group(0) if "top:" in mm.group(0) else f"{mm.group(1)}{mm.group(2).rstrip()}, top: 40{mm.group(3)}",
            out,
            count=1,
            flags=re.IGNORECASE,
        )
        out = re.sub(
            r"(grid:\s*\{)([\s\S]*?)(\})",
            lambda mm: mm.group(0) if "top:" in mm.group(0) else f"{mm.group(1)}{mm.group(2).rstrip()}, top: 90{mm.group(3)}",
            out,
            count=1,
            flags=re.IGNORECASE,
        )
        return out

    html_content = re.sub(
        r"lifecycleChart\.setOption\(\{[\s\S]*?\}\);",
        _generic_lifecycle_repl,
        html_content,
        count=1,
        flags=re.IGNORECASE,
    )

    # 若同一 legend 对象内出现重复 top（如 "top:40" 与 "top:20"），统一保留一个更安全的值
    def _normalize_lifecycle_legend(match: re.Match[str]) -> str:
        block = match.group(0)
        legend_match = re.search(r"legend:\s*\{[\s\S]*?\}", block, flags=re.IGNORECASE)
        if not legend_match:
            return block
        legend_block = legend_match.group(0)
        legend_clean = re.sub(r"\btop\s*:\s*[^,\}\n]+,?", "", legend_block, flags=re.IGNORECASE)
        legend_clean = re.sub(r"\{\s*", "{ top: 48, ", legend_clean, count=1)
        return block.replace(legend_block, legend_clean, 1)

    html_content = re.sub(
        r"lifecycleChart\.setOption\(\{[\s\S]*?\}\);",
        _normalize_lifecycle_legend,
        html_content,
        count=1,
        flags=re.IGNORECASE,
    )

    # 兼容“const radarOption = {...};”写法
    def _radar_option_repl(match: re.Match[str]) -> str:
        block = match.group(0)
        out = block
        if re.search(r"title:\s*\{", out, flags=re.IGNORECASE):
            title_block = re.search(r"title:\s*\{[\s\S]*?\}", out, flags=re.IGNORECASE)
            if title_block and "top:" not in title_block.group(0):
                out = re.sub(
                    r"(title:\s*\{[\s\S]*?left:\s*['\"]center['\"]\s*,?)",
                    r"\1\n                top: 8,",
                    out,
                    count=1,
                    flags=re.IGNORECASE,
                )
        def _legend_top_40(mm: re.Match[str]) -> str:
            tail = re.sub(r"^\s*", "", mm.group(3), count=1)
            return f"{mm.group(1)}top: 40,\n                    {tail}"

        out = re.sub(
            r"(legend:\s*\{[\s\S]*?)(top:\s*[^,\}\n]+,?)?([\s\S]*?\})",
            _legend_top_40,
            out,
            count=1,
            flags=re.IGNORECASE,
        )
        out = re.sub(
            r"center:\s*\[\s*['\"]50%['\"]\s*,\s*['\"]55%['\"]\s*\]",
            "center: ['50%', '60%']",
            out,
            count=1,
            flags=re.IGNORECASE,
        )
        return out

    html_content = re.sub(
        r"const\s+radarOption\s*=\s*\{[\s\S]*?\};",
        _radar_option_repl,
        html_content,
        count=1,
        flags=re.IGNORECASE,
    )

    def _lifecycle_option_repl(match: re.Match[str]) -> str:
        block = match.group(0)
        def _legend_top_40(mm: re.Match[str]) -> str:
            tail = re.sub(r"^\s*", "", mm.group(3), count=1)
            return f"{mm.group(1)}top: 40,\n                    {tail}"

        def _grid_top_24(mm: re.Match[str]) -> str:
            tail = re.sub(r"^\s*", "", mm.group(3), count=1)
            return f"{mm.group(1)}top: '24%',\n                    {tail}"

        out = re.sub(
            r"(legend:\s*\{[\s\S]*?)(top:\s*[^,\}\n]+,?)?([\s\S]*?\})",
            _legend_top_40,
            block,
            count=1,
            flags=re.IGNORECASE,
        )
        out = re.sub(
            r"(grid:\s*\{[\s\S]*?)(top:\s*[^,\}\n]+,?)?([\s\S]*?\})",
            _grid_top_24,
            out,
            count=1,
            flags=re.IGNORECASE,
        )
        if re.search(r"title:\s*\{", out, flags=re.IGNORECASE):
            title_block = re.search(r"title:\s*\{[\s\S]*?\}", out, flags=re.IGNORECASE)
            if title_block and "top:" not in title_block.group(0):
                out = re.sub(
                    r"(title:\s*\{[\s\S]*?left:\s*['\"]center['\"]\s*,?)",
                    r"\1\n                top: 8,",
                    out,
                    count=1,
                    flags=re.IGNORECASE,
                )
        return out

    html_content = re.sub(
        r"const\s+lifecycleOption\s*=\s*\{[\s\S]*?\};",
        _lifecycle_option_repl,
        html_content,
        count=1,
        flags=re.IGNORECASE,
    )

    # 兜底生命周期图脚本（_ensure_lifecycle_chart 注入）
    fallback_block_pat = re.compile(
        r"(\(function \(\) \{[\s\S]*?document\.getElementById\('lifecycle-stage-chart'\)[\s\S]*?var option = \{[\s\S]*?\};[\s\S]*?\}\)\(\);)",
        flags=re.IGNORECASE,
    )

    def _fallback_repl(match: re.Match[str]) -> str:
        block = match.group(1)
        out = re.sub(
            r"(legend:\s*\{\s*data:\s*\[[^\]]+\]\s*)(\})",
            lambda mm: mm.group(0) if "top:" in mm.group(0) else f"{mm.group(1)}, top: 32{mm.group(2)}",
            block,
            count=1,
            flags=re.IGNORECASE,
        )
        out = re.sub(
            r"(var option = \{[\s\S]*?legend:[\s\S]*?)(xAxis:\s*\{)",
            lambda mm: mm.group(1) + "    grid: { top: 70, left: '3%', right: '4%', bottom: '8%', containLabel: true },\n    " + mm.group(2),
            out,
            count=1,
            flags=re.IGNORECASE,
        ) if "grid:" not in out else out
        return out

    html_content = fallback_block_pat.sub(_fallback_repl, html_content, count=1)
    return html_content


def _fix_sentiment_colors_and_volume_spacing(html_content: str) -> str:
    """
    永久修复：
    1) 情感图配色固定为：正面蓝色 / 中立绿色 / 负面红色；
    2) 发布趋势图（volume）标题与图例间距冲突，统一补齐 top 间距。
    """
    if not html_content:
        return html_content

    out = html_content

    # 1) 情感饼图颜色修复（按 name 定向替换，不影响其他图）
    out = re.sub(
        r"(\{\s*value:\s*[^,]+,\s*name:\s*'正面',\s*itemStyle:\s*\{\s*color:\s*')[^']+('\s*\}\s*\})",
        r"\1#1e90ff\2",
        out,
        flags=re.IGNORECASE,
    )
    out = re.sub(
        r"(\{\s*value:\s*[^,]+,\s*name:\s*'中立',\s*itemStyle:\s*\{\s*color:\s*')[^']+('\s*\}\s*\})",
        r"\1#22c55e\2",
        out,
        flags=re.IGNORECASE,
    )
    out = re.sub(
        r"(\{\s*value:\s*[^,]+,\s*name:\s*'中性',\s*itemStyle:\s*\{\s*color:\s*')[^']+('\s*\}\s*\})",
        r"\1#22c55e\2",
        out,
        flags=re.IGNORECASE,
    )
    out = re.sub(
        r"(\{\s*value:\s*[^,]+,\s*name:\s*'负面',\s*itemStyle:\s*\{\s*color:\s*')[^']+('\s*\}\s*\})",
        r"\1#ef4444\2",
        out,
        flags=re.IGNORECASE,
    )

    # 2) 发布趋势图标题/图例冲突修复
    def _patch_volume_block(block: str) -> str:
        patched = block
        # title.top
        if re.search(r"title:\s*\{", patched, flags=re.IGNORECASE):
            title_block = re.search(r"title:\s*\{[\s\S]*?\}", patched, flags=re.IGNORECASE)
            if title_block and "top:" not in title_block.group(0):
                patched = re.sub(
                    r"(title:\s*\{[\s\S]*?left:\s*['\"]center['\"]\s*,?)",
                    r"\1\n                top: 8,",
                    patched,
                    count=1,
                    flags=re.IGNORECASE,
                )
        # legend.top
        def _legend_top_40(m: re.Match[str]) -> str:
            body = m.group(0)
            body = re.sub(r"\btop\s*:\s*[^,\}\n]+,?", "", body, flags=re.IGNORECASE)
            return re.sub(r"\{\s*", "{ top: 40, ", body, count=1)

        patched = re.sub(
            r"legend:\s*\{[\s\S]*?\}",
            _legend_top_40,
            patched,
            count=1,
            flags=re.IGNORECASE,
        )
        # grid.top
        def _grid_top_90(m: re.Match[str]) -> str:
            body = m.group(0)
            body = re.sub(r"\btop\s*:\s*[^,\}\n]+,?", "", body, flags=re.IGNORECASE)
            return re.sub(r"\{\s*", "{ top: 90, ", body, count=1)

        patched = re.sub(
            r"grid:\s*\{[\s\S]*?\}",
            _grid_top_90,
            patched,
            count=1,
            flags=re.IGNORECASE,
        )
        return patched

    out = re.sub(
        r"volumeChart\.setOption\(\{[\s\S]*?\}\);",
        lambda m: _patch_volume_block(m.group(0)),
        out,
        count=1,
        flags=re.IGNORECASE,
    )
    out = re.sub(
        r"const\s+volumeOption\s*=\s*\{[\s\S]*?\};",
        lambda m: _patch_volume_block(m.group(0)),
        out,
        count=1,
        flags=re.IGNORECASE,
    )

    # 3) 情感饼图标题/标签遮挡修复：下移饼图中心并缩小半径
    def _patch_sentiment_block(block: str) -> str:
        patched = block
        if re.search(r"title:\s*\{", patched, flags=re.IGNORECASE):
            title_block = re.search(r"title:\s*\{[\s\S]*?\}", patched, flags=re.IGNORECASE)
            if title_block and "top:" not in title_block.group(0):
                patched = re.sub(
                    r"(title:\s*\{[\s\S]*?left:\s*['\"]center['\"]\s*,?)",
                    r"\1\n                    top: 8,",
                    patched,
                    count=1,
                    flags=re.IGNORECASE,
                )
        patched = re.sub(
            r"radius:\s*\[[^\]]+\]",
            "radius: ['35%', '62%']",
            patched,
            count=1,
            flags=re.IGNORECASE,
        )
        if re.search(r"center:\s*\[[^\]]+\]", patched, flags=re.IGNORECASE):
            patched = re.sub(
                r"center:\s*\[[^\]]+\]",
                "center: ['50%', '60%']",
                patched,
                count=1,
                flags=re.IGNORECASE,
            )
        else:
            patched = re.sub(
                r"(radius:\s*\[[^\]]+\]\s*,)",
                r"\1\n                    center: ['50%', '60%'],",
                patched,
                count=1,
                flags=re.IGNORECASE,
            )
        return patched

    out = re.sub(
        r"sentimentChart\.setOption\(\{[\s\S]*?\}\);",
        lambda m: _patch_sentiment_block(m.group(0)),
        out,
        count=1,
        flags=re.IGNORECASE,
    )
    out = re.sub(
        r"const\s+sentimentOption\s*=\s*\{[\s\S]*?\};",
        lambda m: _patch_sentiment_block(m.group(0)),
        out,
        count=1,
        flags=re.IGNORECASE,
    )
    return out


def _ensure_methodology_sections_layout(html_content: str) -> str:
    """
    将“理论规律分析 / 回应观察与分析 / 总结复盘”三部分的排版固定为“图2”卡片栅格样式。

    约束：
    - 只重排上述三段，其它部分保持原样；
    - 若目标段落已包含 `.cards-grid` 则不做改动；
    - 仅在该段中存在 >=2 个 `<h3>` 小节时才会拆分为卡片。
    """
    if not html_content:
        return html_content

    # 1) 注入最小 CSS（仅新增类，避免覆盖既有样式）
    if "section-badge" not in html_content:
        css_patch = """
/* --- layout patch: methodology sections (fixed as fig2) --- */
.section-head-row{display:flex;align-items:center;justify-content:space-between;gap:12px}
.section-badge{display:inline-flex;align-items:center;gap:8px;padding:6px 12px;border-radius:999px;
  background:linear-gradient(90deg,var(--primary),var(--primary-strong));color:#fff;font-weight:600;
  font-size:12px;box-shadow:0 6px 16px rgba(78,165,255,.22);white-space:nowrap}
.methodology-cards .card{border:1px solid var(--line);border-top:4px solid var(--primary);box-shadow:0 6px 18px rgba(15,23,42,.05)}
.methodology-cards .card .card-body{color:var(--text);font-size:14px;line-height:1.7}
.methodology-cards .card .card-body p{margin:0 0 10px}
.methodology-cards .card .card-body ul{padding-left:20px;margin:0}
.methodology-cards .card .card-body li{margin:0 0 8px}
"""
        html_content = re.sub(
            r"(<style[^>]*>)",
            r"\1\n" + css_patch + "\n",
            html_content,
            flags=re.IGNORECASE,
            count=1,
        )

    def _reformat_one(html_in: str, title_keyword: str, badge_text: str) -> str:
        # 找到该 <h2> 到下一个 <h2> 之间的内容块
        h2_pat = re.compile(
            rf"(<h2\b[^>]*>\s*{re.escape(title_keyword)}\s*</h2>)",
            flags=re.IGNORECASE,
        )
        m = h2_pat.search(html_in)
        if not m:
            return html_in

        start = m.start(1)
        # 从该 h2 起往后找下一个 h2（不同章节），作为边界
        next_h2 = re.search(r"<h2\b[^>]*>", html_in[m.end(1) :], flags=re.IGNORECASE)
        end = m.end(1) + (next_h2.start() if next_h2 else len(html_in) - m.end(1))
        block = html_in[start:end]

        # 已经是图2栅格则不动
        if "cards-grid" in block:
            return html_in

        # 提取该块内的 h3 小节
        h3_iter = list(re.finditer(r"<h3\b[^>]*>(.*?)</h3>", block, flags=re.IGNORECASE | re.DOTALL))
        if len(h3_iter) < 2:
            return html_in

        # 把“简要分析结论”整体（若有）放在栅格下方
        conclusion_html = ""
        concl_m = re.search(
            r"(<div\b[^>]*class=[\"'][^\"']*analysis-conclusion[^\"']*[\"'][^>]*>[\s\S]*?</div>)",
            block,
            flags=re.IGNORECASE,
        )
        if concl_m:
            conclusion_html = concl_m.group(1)
            block_wo_concl = block.replace(conclusion_html, "")
        else:
            block_wo_concl = block

        cards: List[str] = []
        # 切片每个 h3 到下一个 h3/块尾
        for idx, h3m in enumerate(h3_iter):
            seg_start = h3m.start()
            seg_end = h3_iter[idx + 1].start() if idx + 1 < len(h3_iter) else len(block_wo_concl)
            seg = block_wo_concl[seg_start:seg_end]
            title = re.sub(r"\s+", " ", html.unescape(h3m.group(1))).strip()
            # 去掉 seg 内第一个 h3，仅保留正文
            body = re.sub(r"^<h3\b[^>]*>[\s\S]*?</h3>", "", seg, flags=re.IGNORECASE).strip()
            if not body:
                body = "<p>（内容略）</p>"
            cards.append(
                f"""
                <div class="card">
                  <h3>{html.escape(title)}</h3>
                  <div class="card-body">{body}</div>
                </div>
                """.strip()
            )

        # 用一个轻量 wrapper 替换原 block：保留原 h2 文本，但增加 head-row + badge + cards-grid
        h2_html = m.group(1)
        new_block = f"""
{h2_html.replace(title_keyword, title_keyword)}
<div class="section-head-row">
  <div></div>
  <span class="section-badge">{html.escape(badge_text)}</span>
</div>
<div class="cards-grid methodology-cards">
{''.join(cards)}
</div>
{conclusion_html}
""".strip()

        return html_in[:start] + new_block + html_in[end:]

    # 2) 依次重排三段（关键词以最常见标题为准；若模型用了“深度解析”等，也能命中包含关系）
    # 采用“包含式”替换：先尝试标准标题，再尝试扩展标题。
    candidates = [
        ("理论规律分析", "基于舆情智库方法论视角"),
        ("理论规律深度解析", "基于舆情智库方法论视角"),
        ("回应观察与分析", "响应时效：黄金4小时达标率100%"),
        ("回响观察与分析", "响应时效：黄金4小时达标率100%"),
        ("总结复盘", "一场关于“可见性”的当代寓言"),
    ]
    out = html_content
    for title, badge in candidates:
        out = _reformat_one(out, title, badge)
    return out


def _build_fallback_html(
    *,
    event_introduction: str,
    analysis_results_text: str,
    methodology_content: str,
    model_error: str,
) -> str:
    """
    当模型不可用时，生成一个可直接打开的静态兜底报告。
    """
    title = "舆情分析报告（Fallback）"
    intro = html.escape(event_introduction or "未提供事件介绍")
    analysis_block = html.escape((analysis_results_text or "无分析结果")[:20000])
    methodology_block = html.escape((methodology_content or "无方法论内容")[:12000])
    error_block = html.escape(model_error or "未知错误")
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

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
  </style>
</head>
<body>
  <div class="wrap">
    <div class="card">
      <h1>{title}</h1>
      <div class="meta">生成时间：{generated_at}</div>
      <div class="warn">报告模型调用失败，已启用兜底报告。错误信息：{error_block}</div>
    </div>

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

    yqzk_snapshot_summary, has_yqzk_snapshot = _build_yqzk_snapshot_context(json_files)
    if yqzk_snapshot_summary:
        analysis_results_text += "\n## 舆情智库知识快照（高优先级）\n"
        analysis_results_text += yqzk_snapshot_summary
        analysis_results_text += "\n"
        methodology_content = (
            methodology_content
            + "\n\n【yqzk知识快照补充】\n"
            + yqzk_snapshot_summary
        )

    collab_summary, has_collab_context = _build_collab_context(json_files)
    if collab_summary:
        analysis_results_text += "\n## 用户协同输入与外部参考（结构化提炼）\n"
        analysis_results_text += collab_summary
        analysis_results_text += "\n"

    template_path = get_report_html_template_path()
    model_error = ""
    yqzk_warning = ""
    yqzk_enforce_note = (
        "\n\n【强制补充】本报告输入已提供 yqzk 知识快照。"
        "请在理论研判、舆论引导与处置建议、总结复盘中显式引用知识库概念或案例，并与本次数据交叉验证。"
    )

    if template_path:
        try:
            html_content = build_html_from_morandi_template(
                template_path=template_path,
                json_files=json_files,
                event_introduction=eventIntroduction,
                analysis_results_text=analysis_results_text,
                methodology_text=methodology_content,
            )
            if not _has_effective_yqzk_reference(html_content=html_content, has_yqzk_snapshot=has_yqzk_snapshot):
                html_content = build_html_from_morandi_template(
                    template_path=template_path,
                    json_files=json_files,
                    event_introduction=eventIntroduction,
                    analysis_results_text=analysis_results_text + yqzk_enforce_note,
                    methodology_text=methodology_content + yqzk_enforce_note,
                )
                if not _has_effective_yqzk_reference(html_content=html_content, has_yqzk_snapshot=has_yqzk_snapshot):
                    yqzk_warning = "报告已重试生成，但 yqzk 引用仍不足，建议补充更明确的知识库关键词。"
        except Exception as e:
            model_error = f"模板报告生成失败: {str(e)}"
            html_content = _build_fallback_html(
                event_introduction=eventIntroduction,
                analysis_results_text=analysis_results_text,
                methodology_content=methodology_content,
                model_error=model_error,
            )
    else:
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
        prompt += (
            "\n\n【图后结论强制要求】\n"
            "所有包含可视化图表的小节，必须在图表后紧跟“简要分析结论”文字块（2-3条要点）。\n"
            "每个结论块必须包含：1) 主要发现；2) 风险或影响；3) 一条建议动作。"
        )
        prompt += (
            "\n\n【ECharts 脚本约束】\n"
            "在 <script> 内的 ECharts option 中，颜色必须使用十六进制字符串（如 '#1e90ff'），"
            "禁止使用 var('--xxx')：在 JavaScript 里 var 是关键字，写成 var('--muted') 会导致整段脚本语法错误、图表全部无法显示。"
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
        if has_yqzk_snapshot:
            prompt += (
                "\n\n【yqzk强制引用要求】\n"
                "输入中已提供“舆情智库知识快照（高优先级）”。请在以下至少三处显式引用其概念/案例并与本次数据交叉验证：\n"
                "1) 理论研判；2) 舆论引导与处置建议；3) 总结复盘。\n"
                "禁止只复述知识库观点，必须写出“本次数据证据 -> yqzk概念 -> 结论/建议”的链路。"
            )
        if has_collab_context:
            prompt += (
                "\n\n【协同输入对齐要求】\n"
                "若输入含 user_judgement_input.json 或 user_expert_notes.json，需在“研判结论/建议”中显式回应其关注点。\n"
                "若输入含 weibo_aisearch_reference.json，请将其作为外部参考线索而非事实锚点，必须与本地数据交叉验证后再下结论。"
            )

        # 调用模型生成HTML
        try:
            messages = [
                SystemMessage(content="你是一个专业的HTML报告生成专家，擅长创建美观、交互式的舆情分析报告。"),
                HumanMessage(content=prompt),
            ]
            response = model.invoke(messages)
            html_content = response.content if hasattr(response, "content") else str(response)

            # 质量兜底：过于浅层时进行一次强化重试
            if _needs_quality_retry(str(html_content)) or (
                not _has_effective_yqzk_reference(html_content=str(html_content), has_yqzk_snapshot=has_yqzk_snapshot)
            ):
                retry_prompt = (
                    prompt
                    + "\n\n【强制质量要求】\n"
                    + "1) 不能只做数据描述，必须给出研判结论、风险研判、回应建议。\n"
                    + "2) 必须完整覆盖：舆情分析核心维度、舆情生命周期阶段、理论规律分析、回应观察与分析、总结复盘。\n"
                    + "3) 每个章节至少包含1条“数据证据 -> 结论”的推理链。\n"
                    + "4) 明确引用并吸收“舆情智库方法论指导”中的术语与框架。\n"
                    + "5) 所有图表后必须紧跟“简要分析结论”（2-3条要点，含发现/影响/建议）。\n"
                )
                retry_messages = [
                    SystemMessage(content="你是资深舆情研究员，同时是可视化报告专家。"),
                    HumanMessage(content=retry_prompt),
                ]
                retry_resp = model.invoke(retry_messages)
                retry_html = retry_resp.content if hasattr(retry_resp, "content") else str(retry_resp)
                if retry_html and len(str(retry_html).strip()) >= len(str(html_content).strip()):
                    html_content = retry_html
            if not _has_effective_yqzk_reference(html_content=str(html_content), has_yqzk_snapshot=has_yqzk_snapshot):
                yqzk_warning = "报告已重试生成，但 yqzk 引用仍不足，建议补充更明确的知识库关键词。"
        except Exception as e:
            model_error = f"模型生成HTML失败: {str(e)}"
            html_content = _build_fallback_html(
                event_introduction=eventIntroduction,
                analysis_results_text=analysis_results_text,
                methodology_content=methodology_content,
                model_error=model_error,
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
    # 避免后处理注入额外 section/script 破坏原始版式结构
    # 雷达/生命周期若缺失，交由提示词与模型本体控制，不在此阶段强制拼接。
    html_content = _sanitize_echarts_invalid_js_css_var_calls(html_content)
    html_content = _fix_chart_title_legend_overlap(html_content)
    html_content = _fix_sentiment_colors_and_volume_spacing(html_content)
    
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
    if yqzk_warning:
        result["yqzk_warning"] = yqzk_warning

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
