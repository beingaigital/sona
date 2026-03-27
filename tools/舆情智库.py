"""
舆情智库工具：为舆情分析提供方法论支持与参考资料检索。

目标：
1. 继续提供框架/理论/模板等基础方法论；
2. 支持按事件主题检索本地参考资料（含专家自定义研判）；
3. 提供可直接使用的事件检索链接（如微博智搜）。
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote

from langchain_core.tools import tool

# 舆情智库路径
SKILL_DIR = Path.home() / ".openclaw/skills/舆情智库"
REFERENCES_DIR = SKILL_DIR / "references"
PROJECT_ROOT = Path(__file__).resolve().parent.parent
LOCAL_REFERENCES_DIR = PROJECT_ROOT / "舆情深度分析" / "references"
LOCAL_METHOD_DIR = PROJECT_ROOT / "舆情深度分析"
PROJECT_REFERENCES_DIR = PROJECT_ROOT / "references"
EXPERT_NOTES_DIR = LOCAL_REFERENCES_DIR / "expert_notes"

TEXT_SUFFIX = {".md", ".txt", ".json", ".jsonl", ".csv"}


def _reference_dirs() -> List[Path]:
    dirs = [REFERENCES_DIR, LOCAL_REFERENCES_DIR, LOCAL_METHOD_DIR, PROJECT_REFERENCES_DIR]
    uniq: List[Path] = []
    seen = set()
    for d in dirs:
        key = str(d.resolve()) if d.exists() else str(d)
        if key in seen:
            continue
        seen.add(key)
        uniq.append(d)
    return uniq


def _find_reference_file(candidates: List[str]) -> Optional[Path]:
    """在多个候选目录/文件名中查找第一个存在的参考文件。"""
    for name in candidates:
        for d in _reference_dirs():
            p = d / name
            if p.exists() and p.is_file():
                return p
    return None


def _safe_read_text(path: Path, max_chars: int = 120_000) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
        if len(text) > max_chars:
            text = text[:max_chars]
        return text
    except Exception:
        return ""


def _tokenize(text: str, max_tokens: int = 32) -> List[str]:
    s = (text or "").strip()
    if not s:
        return []
    parts = re.findall(r"[\u4e00-\u9fff]{2,}|[A-Za-z0-9_#+.-]{2,}", s)

    tokens: List[str] = []
    for p in parts:
        if re.search(r"[\u4e00-\u9fff]", p):
            tokens.append(p)
            frag = p[:12]
            for n in (2, 3, 4):
                for i in range(0, max(0, len(frag) - n + 1)):
                    tokens.append(frag[i : i + n])
        else:
            tokens.append(p.lower())

    dedup: List[str] = []
    seen = set()
    for t in sorted(tokens, key=len, reverse=True):
        k = t.lower()
        if len(t) < 2 or k in seen:
            continue
        seen.add(k)
        dedup.append(t)
        if len(dedup) >= max_tokens:
            break
    return dedup


def _iter_reference_files(max_files: int = 200) -> List[Path]:
    files: List[Path] = []
    for d in _reference_dirs():
        if not d.exists() or not d.is_dir():
            continue
        for p in sorted(d.rglob("*")):
            if not p.is_file():
                continue
            if p.suffix.lower() not in TEXT_SUFFIX:
                continue
            if p.name.startswith("."):
                continue
            if p.name.lower().startswith("readme"):
                continue
            files.append(p)
            if len(files) >= max_files:
                return files
    return files


def _split_paragraphs(text: str) -> List[str]:
    s = (text or "").replace("\r\n", "\n")
    raw = re.split(r"\n\s*\n", s)
    out = []
    for block in raw:
        b = re.sub(r"\s+", " ", block).strip()
        if len(b) < 16:
            continue
        out.append(b)
    return out


def _score_text(block: str, tokens: List[str]) -> float:
    if not block or not tokens:
        return 0.0
    low = block.lower()
    score = 0.0
    for t in tokens:
        if t.lower() in low:
            score += 1.0 + min(len(t), 10) * 0.08
    return score


def _rank_reference_snippets(query: str, max_items: int = 8) -> List[Dict[str, Any]]:
    tokens = _tokenize(query, max_tokens=36)
    if not tokens:
        return []

    ranked: List[Dict[str, Any]] = []
    for fp in _iter_reference_files(max_files=260):
        text = _safe_read_text(fp, max_chars=120_000)
        if not text:
            continue
        paragraphs = _split_paragraphs(text)
        if not paragraphs:
            continue

        local_hits: List[Tuple[float, str]] = []
        for para in paragraphs:
            score = _score_text(para, tokens)
            if score <= 0:
                continue
            local_hits.append((score, para))

        local_hits.sort(key=lambda x: x[0], reverse=True)
        for score, para in local_hits[:3]:
            ranked.append(
                {
                    "source": str(fp),
                    "title": fp.name,
                    "score": round(score, 4),
                    "snippet": para[:360] + ("..." if len(para) > 360 else ""),
                }
            )

    ranked.sort(key=lambda x: float(x.get("score") or 0.0), reverse=True)
    return ranked[: max(1, max_items)]


def _search_links_for_topic(topic: str) -> List[Dict[str, str]]:
    q = (topic or "").strip() or "舆情事件"
    q_enc = quote(q)
    return [
        {
            "name": "微博智搜",
            "url": f"https://s.weibo.com/aisearch?q={q_enc}&Refer=weibo_aisearch",
            "usage": "查看微博智搜聚合观点与相关讨论",
        },
        {
            "name": "微博搜索",
            "url": f"https://s.weibo.com/weibo?q={q_enc}",
            "usage": "查看微博原始帖子与热度讨论",
        },
        {
            "name": "百度资讯",
            "url": f"https://www.baidu.com/s?wd={q_enc}%20舆情%20评论",
            "usage": "查看媒体评论与报道",
        },
    ]


@tool
def get_sentiment_analysis_framework(topic: Optional[str] = None) -> str:
    """
    获取舆情分析框架和核心维度。

    用于在进行舆情分析时获取方法论指导，自动注入到分析提示词中。

    Args:
        topic: 可选，特定的分析主题（如"企业危机"、"政策舆情"等）

    Returns:
        舆情分析框架和方法论指导
    """
    framework = """
【舆情分析核心框架】

一、舆情基本要素
- 主体：网民、KOL、媒体、机构
- 客体：事件/议题/品牌/政策
- 渠道：微博、短视频平台、论坛、私域社群等
- 情绪：积极/中性/消极 + 细分（愤怒、焦虑、讽刺等）
- 主体行为：转发、评论、跟帖、二创、线下行动

二、核心分析维度
- 量：声量、增速、峰值、平台分布
- 质：情感极性、话题焦点、信息真实性
- 人：关键意见领袖、关键节点用户、受众画像
- 场：主要平台、话语场风格（理性、撕裂、娱乐化）
- 效：对品牌/政策/行为的实际影响（搜索量、销量、投诉量等）

三、舆情生命周期阶段
- 潜伏期：信息量少但敏感度高
- 萌芽期：意见领袖介入、帖文量开始增长
- 爆发期：媒体跟进、热度达到峰值
- 衰退期：事件解决或新热点出现、舆情衰减

四、分析框架建议
1. 事件脉络：潜伏期→萌芽期→爆发期→衰退期
2. 回应观察：回应处置梳理、趋势变化、传播平台变化、情绪变化、话题变化
3. 总结复盘：话语分析、议题泛化趋势、舆论推手分析、叙事手段分析
"""
    if topic:
        framework += f"\n\n【本次主题】{topic}\n建议优先选择与该主题高度相关的维度与证据，不做模板化套用。"
    return framework


@tool
def get_sentiment_theories(topic: Optional[str] = None) -> str:
    """
    获取舆情规律理论基础。

    Args:
        topic: 可选，事件主题。传入后会优先抽取与主题相关的理论片段。

    Returns:
        舆情理论规律及其应用
    """
    theory_file = _find_reference_file(["舆情分析方法论.md"])

    if theory_file and theory_file.exists():
        content = _safe_read_text(theory_file, max_chars=90_000)
        if topic:
            snippets = _rank_reference_snippets(topic, max_items=6)
            topic_hits = [
                f"- {x['snippet']}\n  来源: {x['title']}"
                for x in snippets
                if x.get("title") == theory_file.name
            ]
            if topic_hits:
                return "【舆情理论（主题相关）】\n" + "\n".join(topic_hits[:4])

        # 不按单一标题截断，尽量保留多理论段落
        lines = content.splitlines()
        picked: List[str] = []
        bucket: List[str] = []
        in_section = False
        for line in lines:
            ls = line.strip()
            if not ls:
                continue
            if ls.startswith("#") and ("理论" in ls or "规律" in ls or "框架" in ls):
                if bucket:
                    picked.append("\n".join(bucket[:20]))
                    bucket = []
                in_section = True
                bucket.append(ls)
                continue
            if in_section:
                bucket.append(ls)
                if len(bucket) >= 20:
                    picked.append("\n".join(bucket))
                    bucket = []
                    in_section = False
        if bucket:
            picked.append("\n".join(bucket[:20]))

        if picked:
            return "【舆情理论基础】\n\n" + "\n\n".join(picked[:6])
        return content[:6000]

    # fallback
    return """
【舆情规律理论基础】

1. 沉默螺旋规律：群体压力下的意见趋同
2. 议程设置规律：媒体与公众的互动塑造议题
3. 框架理论：同一事实在不同叙事框架下会引发不同舆论走向
4. 生命周期规律：舆情通常经历萌芽-扩散-消退
5. 风险传播理论：不确定性与恐惧感会显著加速扩散
6. 社会燃烧规律：矛盾累积到阈值后会突发集中爆发
"""


@tool
def get_sentiment_case_template(case_type: str = "社会事件") -> str:
    """
    获取舆情分析报告模板。

    Args:
        case_type: 案例类型，"社会事件"或"商业事件"

    Returns:
        分析报告模板
    """
    if "商业" in case_type:
        return """
【商业事件舆情分析模板】

一、行业背景
二、事件梳理
   - 萌芽期：宏观背景与触发点
   - 发酵期：多方参与与议题竞逐
   - 爆发期：导火索、峰值节点与关键叙事
   - 延续期：影响外溢与走势研判
三、品牌观察
   - 宣发策略与渠道结构
   - 平台热度分布（小红书/微博/抖音/新闻/问答/论坛）
   - 核心争议与用户情绪迁移
   - SWOT与风险处置建议
"""
    return """
【社会事件舆情分析模板】

一、事件脉络
   - 潜伏期
   - 萌芽期
   - 爆发期
   - 衰退期

二、回应观察
   - 回应处置梳理与时点效果
   - 趋势变化与平台迁移
   - 情绪变化与话题转向

三、总结复盘
   - 叙事结构与话语策略
   - 议题泛化与风险外溢
   - 推手网络与传播机制
"""


@tool
def get_youth_sentiment_insight() -> str:
    """
    获取中国青年网民社会心态分析洞察。

    Returns:
        青年网民心态分析要点
    """
    insight_file = _find_reference_file(["青年网民心态.md", "中国青年网民社会心态调查报告（2024）.md"])
    if insight_file and insight_file.exists():
        content = _safe_read_text(insight_file, max_chars=7000)
        return content[:5000] + "\n\n[...详细内容见青年网民心态参考文档...]"
    return "青年网民心态报告文件未找到"


@tool
def search_reference_insights(query: str, limit: int = 6) -> str:
    """
    按事件主题检索本地参考资料（方法论/案例/专家笔记）。

    Args:
        query: 检索关键词或事件主题。
        limit: 返回条数，默认 6。

    Returns:
        JSON 字符串，包含引用片段与来源。
    """
    q = (query or "").strip()
    if not q:
        return json.dumps({"query": q, "count": 0, "results": []}, ensure_ascii=False)

    safe_limit = max(1, min(int(limit or 6), 20))
    hits = _rank_reference_snippets(q, max_items=safe_limit)
    return json.dumps({"query": q, "count": len(hits), "results": hits}, ensure_ascii=False, indent=2)


@tool
def append_expert_judgement(topic: str, judgement: str, tags: str = "", source: str = "expert") -> str:
    """
    追加专家研判到本地参考库，供后续报告自动引用。

    Args:
        topic: 研判主题（例如：张雪峰事件）。
        judgement: 专家研判正文。
        tags: 可选标签，逗号分隔。
        source: 来源标记，默认 expert。

    Returns:
        JSON 字符串，包含写入文件路径。
    """
    topic_s = (topic or "").strip()
    judgement_s = (judgement or "").strip()
    if not topic_s or not judgement_s:
        return json.dumps({"ok": False, "error": "topic 与 judgement 不能为空"}, ensure_ascii=False)

    EXPERT_NOTES_DIR.mkdir(parents=True, exist_ok=True)
    slug = re.sub(r"[^\w\u4e00-\u9fff-]+", "_", topic_s)[:60].strip("_") or "expert_note"
    file_path = EXPERT_NOTES_DIR / f"{datetime.now().strftime('%Y%m%d')}_{slug}.md"

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    tag_line = tags.strip() if tags else ""
    block = [
        f"## {topic_s}",
        f"- 时间: {now}",
        f"- 来源: {source}",
    ]
    if tag_line:
        block.append(f"- 标签: {tag_line}")
    block.append("\n### 研判内容")
    block.append(judgement_s)
    block.append("\n---\n")

    try:
        with open(file_path, "a", encoding="utf-8", errors="replace") as f:
            f.write("\n".join(block))
        return json.dumps(
            {
                "ok": True,
                "path": str(file_path),
                "topic": topic_s,
                "message": "专家研判已写入参考库，后续报告可自动检索引用。",
            },
            ensure_ascii=False,
            indent=2,
        )
    except Exception as e:
        return json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False)


@tool
def build_event_reference_links(topic: str) -> str:
    """
    生成事件外部参考检索链接（如微博智搜），用于人工核验与补充研判。

    Args:
        topic: 事件主题

    Returns:
        JSON 字符串，包含链接列表
    """
    links = _search_links_for_topic(topic)
    return json.dumps({"topic": topic, "count": len(links), "links": links}, ensure_ascii=False, indent=2)


@tool
def load_sentiment_knowledge(keyword: str) -> str:
    """
    根据关键词加载舆情知识库。

    可用于分析时快速获取框架/理论/模板/青年洞察，也支持检索参考片段与外部检索链接。

    Args:
        keyword: 关键词，如"框架"、"理论"、"案例"、"青年"、"参考"等

    Returns:
        相关舆情知识
    """
    k = (keyword or "").strip()
    if not k:
        return str(get_sentiment_analysis_framework.invoke({}))

    if any(x in k for x in ["参考", "评论", "研判", "文章", "案例"]):
        refs = search_reference_insights.invoke({"query": k, "limit": 6})
        try:
            data = json.loads(refs)
            lines = ["【事件参考片段】"]
            for item in data.get("results", [])[:6]:
                lines.append(f"- {item.get('snippet', '')}\n  来源: {item.get('title', '')}")
            return "\n".join(lines)
        except Exception:
            return str(refs)

    if any(x in k for x in ["链接", "检索", "智搜", "微博"]):
        return build_event_reference_links.invoke({"topic": k})

    keyword_map = {
        "框架": str(get_sentiment_analysis_framework.invoke({})),
        "理论": str(get_sentiment_theories.invoke({"topic": k})),
        "社会事件": str(get_sentiment_case_template.invoke({"case_type": "社会事件"})),
        "商业事件": str(get_sentiment_case_template.invoke({"case_type": "商业事件"})),
        "青年": str(get_youth_sentiment_insight.invoke({})),
    }

    for key, value in keyword_map.items():
        if key in k:
            return value

    return str(get_sentiment_analysis_framework.invoke({"topic": k}))


# 为 LangChain 工具注册
sentiment_analysis_framework = get_sentiment_analysis_framework
sentiment_theories = get_sentiment_theories
sentiment_case_template = get_sentiment_case_template
youth_sentiment_insight = get_youth_sentiment_insight
load_sentiment_knowledge = load_sentiment_knowledge
reference_search = search_reference_insights
append_expert_judgement = append_expert_judgement
build_event_reference_links = build_event_reference_links


if __name__ == "__main__":
    print("=== 框架测试 ===")
    print(get_sentiment_analysis_framework.invoke({"topic": "张雪峰事件"})[:500])
    print("\n=== 理论测试 ===")
    print(get_sentiment_theories.invoke({"topic": "教育 舆情"})[:500])
    print("\n=== 参考检索测试 ===")
    print(search_reference_insights.invoke({"query": "张雪峰 猝死 舆情", "limit": 3}))
