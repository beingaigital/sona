"""
舆情智库方法论加载器：融合本地方法论文档与 tools/舆情智库.py 输出。
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any, Optional

from utils.path import get_project_root


PROJECT_ROOT = get_project_root()
METHODOLOGY_DIR = PROJECT_ROOT / "舆情深度分析"
SKILL_FILE = PROJECT_ROOT / "tools" / "舆情智库.py"

# 方法论文档候选（兼容旧目录结构）
THEORY_CANDIDATES = [
    METHODOLOGY_DIR / "references" / "舆情分析方法论.md",
    METHODOLOGY_DIR / "舆情分析方法论.md",
]
OPINIONS_CANDIDATES = [
    METHODOLOGY_DIR / "references" / "舆情深度观点.md",
    METHODOLOGY_DIR / "舆情分析可参考的一些深度观点.md",
]
YOUTH_CANDIDATES = [
    METHODOLOGY_DIR / "references" / "青年网民心态.md",
    METHODOLOGY_DIR / "中国青年网民社会心态调查报告（2024）.md",
]

# 缓存动态加载的 skill 模块
_SKILL_MODULE: Any = None
_SKILL_LOADED = False


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""


def _first_existing(candidates: list[Path]) -> Optional[Path]:
    for p in candidates:
        if p.exists() and p.is_file():
            return p
    return None


def _truncate(text: str, max_chars: int) -> str:
    s = (text or "").strip()
    if len(s) <= max_chars:
        return s
    return s[:max_chars] + "..."


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
        "蝴蝶效应",
        "生命周期规律",
        "社会燃烧",
    ]

    key_sections: list[str] = []
    lines = content.splitlines()
    current_title = ""
    current_lines: list[str] = []

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


def _invoke_skill_tool(module: Any, tool_name: str, payload: dict[str, Any]) -> str:
    """
    调用 tools/舆情智库.py 中的 LangChain tool 对象（StructuredTool.invoke）。
    """
    try:
        tool_obj = getattr(module, tool_name, None)
        if tool_obj is None or not hasattr(tool_obj, "invoke"):
            return ""
        result = tool_obj.invoke(payload)
        return str(result or "").strip()
    except Exception:
        return ""


def _load_methodology_from_skill(topic: Optional[str] = None) -> str:
    module = _load_skill_module()
    if not module:
        return ""

    framework = _invoke_skill_tool(module, "get_sentiment_analysis_framework", {"topic": topic or "舆情事件"})
    theories = _invoke_skill_tool(module, "get_sentiment_theories", {})
    case_template = _invoke_skill_tool(module, "get_sentiment_case_template", {"case_type": "社会事件"})
    youth = _invoke_skill_tool(module, "load_sentiment_knowledge", {"keyword": "青年"})

    sections: list[str] = []
    if framework:
        sections.append("### 框架\n" + _truncate(framework, 2500))
    if theories:
        sections.append("### 理论\n" + _truncate(theories, 2500))
    if case_template:
        sections.append("### 模板\n" + _truncate(case_template, 1800))
    if youth:
        sections.append("### 青年心态\n" + _truncate(youth, 1800))

    if not sections:
        return ""
    return "## 舆情智库方法论（skills: 舆情智库.py）\n\n" + "\n\n".join(sections)


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
3. 蝴蝶效应规律 - 初始微扰的指数级放大
4. 生命周期规律 - 舆情的阶段性演变
5. 博弈均衡规律 - 政府与网民的策略互动
6. 社会燃烧规律 - 矛盾累积的临界点爆发
""".strip()


def get_methodology_content(topic: Optional[str] = None) -> str:
    """
    获取舆情智库方法论内容，用于报告生成。
    优先融合本地文档与 tools/舆情智库.py。
    """
    content_parts: list[str] = []

    theory_path = _first_existing(THEORY_CANDIDATES)
    if theory_path:
        theory_content = _read_text(theory_path)
        if theory_content:
            content_parts.append(_extract_key_sections(theory_content))

    opinions_path = _first_existing(OPINIONS_CANDIDATES)
    if opinions_path:
        opinions_content = _read_text(opinions_path)
        if opinions_content:
            content_parts.append("## 深度观点参考\n\n" + _truncate(opinions_content, 2000))

    youth_path = _first_existing(YOUTH_CANDIDATES)
    if youth_path:
        youth_content = _read_text(youth_path)
        if youth_content:
            content_parts.append("## 青年网民心态参考\n\n" + _truncate(youth_content, 2200))

    skill_content = _load_methodology_from_skill(topic=topic)
    if skill_content:
        content_parts.append(skill_content)

    if not content_parts:
        return _get_fallback_methodology()

    merged = "\n\n".join([p for p in content_parts if p.strip()])
    return _truncate(merged, 12000)


def load_methodology_for_report(topic: Optional[str] = None) -> str:
    """
    供外部调用的便捷函数：加载方法论内容。
    """
    return get_methodology_content(topic=topic)


if __name__ == "__main__":
    print(load_methodology_for_report(topic="消费者权益")[:2500])
