"""
舆情智库方法论加载器：融合本地方法论文档与 tools/oprag.py 输出。
"""

from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from utils.path import get_opinion_analysis_kb_root, get_project_root


PROJECT_ROOT = get_project_root()
METHODOLOGY_DIR = get_opinion_analysis_kb_root(PROJECT_ROOT)
SKILL_FILE = PROJECT_ROOT / "tools" / "oprag.py"
SKILL_REFERENCE_DIR = Path.home() / ".openclaw" / "skills" / "舆情智库" / "references"
PROJECT_REFERENCE_DIR = PROJECT_ROOT / "references"
LOCAL_REFERENCE_DIR = METHODOLOGY_DIR / "references"
EXPERT_NOTES_DIR = LOCAL_REFERENCE_DIR / "expert_notes"
WIKI_DIR = LOCAL_REFERENCE_DIR / "wiki"
WIKI_OUTPUT_DIR = WIKI_DIR / "output"

# 方法论文档候选（兼容旧目录结构）
THEORY_CANDIDATES = [
    METHODOLOGY_DIR / "references" / "舆情分析方法论.md",
    EXPERT_NOTES_DIR / "舆情分析方法论.md",
    WIKI_OUTPUT_DIR / "舆情分析方法论.md",
    METHODOLOGY_DIR / "舆情分析方法论.md",
]
OPINIONS_CANDIDATES = [
    METHODOLOGY_DIR / "references" / "舆情深度观点.md",
    EXPERT_NOTES_DIR / "舆情深度观点.md",
    WIKI_OUTPUT_DIR / "舆情深度观点.md",
    METHODOLOGY_DIR / "舆情分析可参考的一些深度观点.md",
]
YOUTH_CANDIDATES = [
    METHODOLOGY_DIR / "references" / "青年网民心态.md",
    EXPERT_NOTES_DIR / "青年网民心态.md",
    WIKI_OUTPUT_DIR / "青年网民心态.md",
    METHODOLOGY_DIR / "中国青年网民社会心态调查报告（2024）.md",
]

# 缓存动态加载的 skill 模块
_SKILL_MODULE: Any = None
_SKILL_LOADED = False

_TEXT_SUFFIX = {".md", ".txt", ".json", ".jsonl", ".csv"}


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""


def _first_existing(candidates: List[Path]) -> Optional[Path]:
    for p in candidates:
        if p.exists() and p.is_file():
            return p
    return None


def _truncate(text: str, max_chars: int) -> str:
    s = (text or "").strip()
    if len(s) <= max_chars:
        return s
    return s[:max_chars] + "..."


def _tokenize(text: str, max_tokens: int = 40) -> List[str]:
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

    out: List[str] = []
    seen = set()
    for t in sorted(tokens, key=len, reverse=True):
        k = t.lower()
        if len(t) < 2 or k in seen:
            continue
        seen.add(k)
        out.append(t)
        if len(out) >= max_tokens:
            break
    return out


def _split_paragraphs(text: str) -> List[str]:
    blocks = re.split(r"\n\s*\n", (text or "").replace("\r\n", "\n"))
    out = []
    for b in blocks:
        s = re.sub(r"\s+", " ", b).strip()
        if len(s) >= 20:
            out.append(s)
    return out


def _score_block(block: str, tokens: List[str]) -> float:
    if not block or not tokens:
        return 0.0
    low = block.lower()
    score = 0.0
    for t in tokens:
        if t.lower() in low:
            score += 1.0 + min(len(t), 10) * 0.08
    return score


def _extract_key_sections(content: str) -> str:
    """
    从方法论文本中提取关键章节，避免把全文无差别塞进 prompt。
    """
    if not content:
        return ""

    section_markers = [
        "舆情分析核心维度",
        "舆情基本要素",
        "舆情生命周期",
        "舆情规律",
        "沉默螺旋",
        "议程设置",
        "框架理论",
        "风险传播",
    ]

    key_sections: List[str] = []
    lines = content.splitlines()
    current_title = ""
    current_lines: List[str] = []

    def flush() -> None:
        nonlocal current_title, current_lines
        if not current_title or not current_lines:
            return
        body = "\n".join(current_lines[:28]).strip()
        if body:
            key_sections.append(f"### {current_title}\n{body}")
        current_lines = []

    for line in lines:
        line_s = line.strip()
        is_header = False
        for marker in section_markers:
            if marker in line_s and (line_s.startswith("#") or len(line_s) <= 40):
                is_header = True
                break
        if is_header:
            flush()
            current_title = line_s.replace("#", "").replace("*", "").strip()
            continue
        if current_title:
            current_lines.append(line)

    flush()
    if key_sections:
        return "## 舆情智库方法论（本地文档）\n\n" + "\n\n".join(key_sections[:8])

    return "## 舆情智库方法论（本地文档）\n\n" + _truncate(content, 3200)


def _iter_reference_files(max_files: int = 220) -> List[Path]:
    dirs = [LOCAL_REFERENCE_DIR, EXPERT_NOTES_DIR, METHODOLOGY_DIR, SKILL_REFERENCE_DIR, PROJECT_REFERENCE_DIR]
    files: List[Path] = []
    for d in dirs:
        if not d.exists() or not d.is_dir():
            continue
        for p in sorted(d.rglob("*")):
            if not p.is_file():
                continue
            if p.suffix.lower() not in _TEXT_SUFFIX:
                continue
            if p.name.startswith("."):
                continue
            if p.name.lower().startswith("readme"):
                continue
            files.append(p)
            if len(files) >= max_files:
                return files
    return files


def _load_topic_references_from_local(topic: Optional[str], max_items: int = 8) -> str:
    topic_s = (topic or "").strip()
    if not topic_s:
        return ""

    tokens = _tokenize(topic_s, max_tokens=40)
    if not tokens:
        return ""

    scored: List[Dict[str, Any]] = []
    for fp in _iter_reference_files(max_files=260):
        text = _read_text(fp)
        if not text:
            continue
        for block in _split_paragraphs(text):
            score = _score_block(block, tokens)
            if score <= 0:
                continue
            scored.append(
                {
                    "score": score,
                    "source": str(fp),
                    "title": fp.name,
                    "snippet": _truncate(block, 360),
                }
            )

    if not scored:
        return ""

    scored.sort(key=lambda x: float(x.get("score") or 0.0), reverse=True)
    lines = [f"## 事件定向参考资料（topic={topic_s}）"]
    for item in scored[: max(1, max_items)]:
        lines.append(f"- {item['snippet']}\n  来源: {item['title']}")
    return "\n".join(lines)


def _load_skill_module() -> Any:
    global _SKILL_MODULE, _SKILL_LOADED
    if _SKILL_LOADED:
        return _SKILL_MODULE
    _SKILL_LOADED = True

    if not SKILL_FILE.exists():
        _SKILL_MODULE = None
        return None

    try:
        spec = importlib.util.spec_from_file_location("sona_sentiment_skill", SKILL_FILE)
        if not spec or not spec.loader:
            _SKILL_MODULE = None
            return None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _SKILL_MODULE = module
        return module
    except Exception:
        _SKILL_MODULE = None
        return None


def _invoke_skill_tool(module: Any, tool_name: str, payload: Dict[str, Any]) -> str:
    """
    调用 tools/oprag.py 中的 LangChain tool 对象（StructuredTool.invoke）。
    """
    try:
        tool_obj = getattr(module, tool_name, None)
        if tool_obj is None or not hasattr(tool_obj, "invoke"):
            return ""
        result = tool_obj.invoke(payload)
        return str(result or "").strip()
    except Exception:
        return ""


def _format_skill_reference_hits(raw: str, max_items: int = 6) -> str:
    if not raw:
        return ""
    try:
        data = json.loads(raw)
    except Exception:
        return _truncate(raw, 1600)

    if not isinstance(data, dict):
        return _truncate(raw, 1600)

    results = data.get("results") if isinstance(data.get("results"), list) else []
    if not results:
        return ""

    lines = ["### 事件参考片段（skill 检索）"]
    for item in results[: max_items]:
        if not isinstance(item, dict):
            continue
        snippet = _truncate(str(item.get("snippet", "") or ""), 240)
        title = str(item.get("title", "") or "")
        if snippet:
            lines.append(f"- {snippet}\n  来源: {title}")
    return "\n".join(lines)


def _format_skill_links(raw: str, max_items: int = 4) -> str:
    if not raw:
        return ""
    try:
        data = json.loads(raw)
    except Exception:
        return ""

    links = data.get("links") if isinstance(data, dict) else None
    if not isinstance(links, list) or not links:
        return ""

    lines = ["### 外部检索入口"]
    for item in links[: max_items]:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name", "") or "")
        url = str(item.get("url", "") or "")
        usage = str(item.get("usage", "") or "")
        if name and url:
            lines.append(f"- {name}: {url}" + (f"（{usage}）" if usage else ""))
    return "\n".join(lines)


def _load_methodology_from_skill(topic: Optional[str] = None) -> str:
    module = _load_skill_module()
    if not module:
        return ""

    topic_text = topic or "舆情事件"
    framework = _invoke_skill_tool(module, "get_sentiment_analysis_framework", {"topic": topic_text})
    theories = _invoke_skill_tool(module, "get_sentiment_theories", {"topic": topic_text})
    case_template = _invoke_skill_tool(module, "get_sentiment_case_template", {"case_type": "社会事件"})
    youth = _invoke_skill_tool(module, "load_sentiment_knowledge", {"keyword": "青年"})
    reference_hits_raw = _invoke_skill_tool(module, "search_reference_insights", {"query": topic_text, "limit": 6})
    reference_links_raw = _invoke_skill_tool(module, "build_event_reference_links", {"topic": topic_text})

    sections: List[str] = []
    if framework:
        sections.append("### 框架\n" + _truncate(framework, 2500))
    if theories:
        sections.append("### 理论\n" + _truncate(theories, 2600))
    if case_template:
        sections.append("### 模板\n" + _truncate(case_template, 1800))
    if youth:
        sections.append("### 青年心态\n" + _truncate(youth, 1600))

    refs = _format_skill_reference_hits(reference_hits_raw, max_items=6)
    if refs:
        sections.append(refs)

    links = _format_skill_links(reference_links_raw, max_items=4)
    if links:
        sections.append(links)

    if not sections:
        return ""
    return "## 舆情智库方法论（skills: oprag.py）\n\n" + "\n\n".join(sections)


def _get_fallback_methodology() -> str:
    return """
## 舆情智库方法论（内置默认值）

### 【舆情分析核心维度】
1. 舆情基本要素：主体(网民/KOL/媒体/机构)、客体(事件/议题/品牌/政策)、渠道、情绪、主体行为
2. 核心分析维度：
   - 量：声量、增速、峰值、平台分布
   - 质：情感极性、话题焦点、信息真实性
   - 人：关键意见领袖、关键节点用户、受众画像
   - 场：主要平台、话语场风格
   - 效：实际影响（搜索量/销量/投诉量等）

### 【舆情生命周期阶段】
- 潜伏期：信息量少，敏感度高
- 萌芽期：意见领袖介入，帖文量开始增长
- 爆发期：媒体跟进，热度达到峰值
- 衰退期：事件解决或新热点出现，舆情衰减

### 【理论规律参考】
1. 沉默螺旋规律 - 群体压力下的意见趋同
2. 议程设置规律 - 媒介与公众的互动博弈
3. 框架理论 - 不同叙事框架塑造不同风险感知
4. 生命周期规律 - 舆情的阶段性演变
5. 风险传播理论 - 不确定性增强扩散速度
6. 社会燃烧规律 - 矛盾累积的临界点爆发
""".strip()


def get_methodology_content(topic: Optional[str] = None) -> str:
    """
    获取舆情智库方法论内容，用于报告生成。
    优先融合本地文档、事件定向参考与 tools/oprag.py。
    """
    content_parts: List[str] = []

    theory_path = _first_existing(THEORY_CANDIDATES)
    if theory_path:
        theory_content = _read_text(theory_path)
        if theory_content:
            content_parts.append(_extract_key_sections(theory_content))

    opinions_path = _first_existing(OPINIONS_CANDIDATES)
    if opinions_path:
        opinions_content = _read_text(opinions_path)
        if opinions_content:
            content_parts.append("## 深度观点参考\n\n" + _truncate(opinions_content, 2200))

    youth_path = _first_existing(YOUTH_CANDIDATES)
    if youth_path:
        youth_content = _read_text(youth_path)
        if youth_content:
            content_parts.append("## 青年网民心态参考\n\n" + _truncate(youth_content, 2200))

    topic_refs = _load_topic_references_from_local(topic=topic, max_items=8)
    if topic_refs:
        content_parts.append(topic_refs)

    skill_content = _load_methodology_from_skill(topic=topic)
    if skill_content:
        content_parts.append(skill_content)

    if not content_parts:
        return _get_fallback_methodology()

    merged = "\n\n".join([p for p in content_parts if p.strip()])
    return _truncate(merged, 18000)


def load_methodology_for_report(topic: Optional[str] = None) -> str:
    """
    供外部调用的便捷函数：加载方法论内容。
    """
    return get_methodology_content(topic=topic)


if __name__ == "__main__":
    print(load_methodology_for_report(topic="张雪峰心源性猝死事件")[:3000])
