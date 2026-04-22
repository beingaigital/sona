"""
OPRAG（opinion analysis knowledge）：舆情分析知识库工具，为流程提供方法论与参考资料检索。

目标：
1. 继续提供框架/理论/模板等基础方法论；
2. 支持按事件主题检索本地参考资料（含专家自定义研判）；
3. 提供可直接使用的事件检索链接（如微博智搜）。
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote

from langchain_core.tools import tool

from utils.path import get_opinion_analysis_kb_root

# 舆情智库路径
SKILL_DIR = Path.home() / ".openclaw/skills/舆情智库"
REFERENCES_DIR = SKILL_DIR / "references"
PROJECT_ROOT = Path(__file__).resolve().parent.parent
_KB_ROOT = get_opinion_analysis_kb_root(PROJECT_ROOT)
LOCAL_REFERENCES_DIR = _KB_ROOT / "references"
LOCAL_METHOD_DIR = _KB_ROOT
PROJECT_REFERENCES_DIR = PROJECT_ROOT / "references"
EXPERT_NOTES_DIR = LOCAL_REFERENCES_DIR / "expert_notes"
# 成体系的舆情理论释义（专家笔记子目录），用于报告「理论研判」与 OPRAG 召回
THEORY_EXPERT_NOTES_DIR = EXPERT_NOTES_DIR / "舆情分析的相关理论"
RAW_REFERENCES_DIR = LOCAL_REFERENCES_DIR / "raw"
WIKI_DIR = LOCAL_REFERENCES_DIR / "wiki"
WIKI_INDEX = WIKI_DIR / "index.md"
WIKI_LOG = WIKI_DIR / "log.md"
WIKI_SCHEMA = WIKI_DIR / "WIKI_SCHEMA.md"
WIKI_SOURCES_DIR = WIKI_DIR / "sources"
WIKI_CONCEPTS_DIR = WIKI_DIR / "concepts"
WIKI_ENTITIES_DIR = WIKI_DIR / "entities"
WIKI_OUTPUT_DIR = WIKI_DIR / "output"

# 与 workflow/event_analysis_pipeline、tools/report_html 共用的过程文件名
OPRAG_KNOWLEDGE_SNAPSHOT_FILENAME = "oprag_knowledge_snapshot.json"
OPRAG_RECALL_PREVIEW_FILENAME = "oprag_recall_preview.txt"
LEGACY_YQZK_KNOWLEDGE_SNAPSHOT_FILENAME = "yqzk_knowledge_snapshot.json"

TEXT_SUFFIX = {".md", ".txt", ".json", ".jsonl", ".csv"}


DEFAULT_WIKI_SCHEMA = """# 舆情智库 Wiki Schema（内置回退）

当外部 schema 文件不可用时，按以下规则编译 wiki 页面：
- 需要 YAML frontmatter，至少包含：title、source_file、updated_at、tags
- 正文结构固定：事件概述、关键事实、传播机制、情绪与立场、风险点、可复用方法论、相似议题线索、引用片段
- 禁止编造，证据不足写“证据不足”
- 句子尽量短，确保检索友好
"""


def _load_wiki_schema_text(max_chars: int = 20_000) -> str:
    if WIKI_SCHEMA.exists() and WIKI_SCHEMA.is_file():
        text = _safe_read_text(WIKI_SCHEMA, max_chars=max_chars).strip()
        if text:
            return text
    return DEFAULT_WIKI_SCHEMA


def _get_wiki_schema_meta(max_chars: int = 20_000) -> Dict[str, str]:
    if WIKI_SCHEMA.exists() and WIKI_SCHEMA.is_file():
        schema_text = _safe_read_text(WIKI_SCHEMA, max_chars=max_chars).strip()
        if schema_text:
            source = str(WIKI_SCHEMA)
        else:
            schema_text = DEFAULT_WIKI_SCHEMA
            source = "builtin://DEFAULT_WIKI_SCHEMA"
    else:
        schema_text = DEFAULT_WIKI_SCHEMA
        source = "builtin://DEFAULT_WIKI_SCHEMA"

    schema_hash = hashlib.sha1(schema_text.encode("utf-8", errors="replace")).hexdigest()
    preview = re.sub(r"\s+", " ", schema_text).strip()[:160]
    return {
        "schema_file": source,
        "schema_hash": schema_hash,
        "schema_preview": preview,
    }


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


def _iter_theory_expert_note_files() -> List[Path]:
    """列出「舆情分析的相关理论」目录下全部 Markdown，供检索与理论槽位补全。"""
    if not THEORY_EXPERT_NOTES_DIR.exists() or not THEORY_EXPERT_NOTES_DIR.is_dir():
        return []
    return sorted([p for p in THEORY_EXPERT_NOTES_DIR.glob("*.md") if p.is_file()])


def _iter_reference_files(max_files: int = 200) -> List[Path]:
    files: List[Path] = []
    seen: set[str] = set()

    def _push(p: Path) -> None:
        nonlocal files, seen
        try:
            key = str(p.resolve())
        except Exception:
            key = str(p)
        if key in seen:
            return
        seen.add(key)
        files.append(p)

    # 理论子目录优先：避免在 references 树很大时被 max_files 截断挤出检索池
    for p in _iter_theory_expert_note_files():
        if p.suffix.lower() not in TEXT_SUFFIX:
            continue
        _push(p)
        if len(files) >= max_files:
            return files
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
            _push(p)
            if len(files) >= max_files:
                return files
    return files


def _wiki_sources_output_path_for_compile(fp: Path) -> Path:
    """
    将待编译源文件映射到 wiki/sources 下的稳定文件名，避免 raw 与 expert 同名冲突。
    """
    fp_res = fp.resolve()
    expert_root = EXPERT_NOTES_DIR.resolve()
    title = fp.stem
    if expert_root in fp_res.parents:
        try:
            rel = fp_res.relative_to(expert_root)
        except ValueError:
            rel = None
        else:
            if rel.parts and rel.parts[0] == THEORY_EXPERT_NOTES_DIR.name:
                rel_posix = rel.as_posix()
                digest = hashlib.sha1(rel_posix.encode("utf-8", errors="replace")).hexdigest()[:10]
                slug = _slugify_cn_filename(f"expert_{digest}_{title}", max_len=48)
                return WIKI_SOURCES_DIR / f"{slug}.md"
    is_output_note = WIKI_OUTPUT_DIR.resolve() in fp_res.parents
    slug = _slugify_cn_filename(f"output_{title}" if is_output_note else title)
    return WIKI_SOURCES_DIR / f"{slug}.md"


def _iter_wiki_files(max_files: int = 400) -> List[Path]:
    files: List[Path] = []
    if not WIKI_DIR.exists() or not WIKI_DIR.is_dir():
        return files
    for p in sorted(WIKI_DIR.rglob("*.md")):
        if not p.is_file():
            continue
        pp = p.as_posix()
        if "/output/_candidates/" in pp:
            continue
        if p.name.lower() in {"index.md", "log.md", "wiki_schema.md"}:
            continue
        files.append(p)
        if len(files) >= max_files:
            break
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


def _slugify_cn_filename(text: str, max_len: int = 48) -> str:
    s = re.sub(r"[^\w\u4e00-\u9fff-]+", "_", str(text or "").strip()).strip("_")
    if not s:
        s = "wiki_page"
    return s[:max_len]


def _extract_frontmatter_tags(md_text: str) -> List[str]:
    if not md_text.startswith("---"):
        return []
    m = re.match(r"^---\n([\s\S]*?)\n---", md_text)
    if not m:
        return []
    body = m.group(1)
    tags: List[str] = []
    for line in body.splitlines():
        if line.strip().startswith("tags:"):
            rhs = line.split(":", 1)[1]
            rhs = rhs.strip().strip("[]")
            for t in re.split(r"[，,\s]+", rhs):
                tt = t.strip().strip("-").strip()
                if tt:
                    tags.append(tt)
    return tags


def _extract_frontmatter_list(md_text: str, key: str) -> List[str]:
    if not md_text.startswith("---"):
        return []
    m = re.match(r"^---\n([\s\S]*?)\n---", md_text)
    if not m:
        return []
    body_lines = m.group(1).splitlines()
    out: List[str] = []

    i = 0
    while i < len(body_lines):
        line = body_lines[i]
        stripped = line.strip()
        if not stripped.startswith(f"{key}:"):
            i += 1
            continue

        rhs = stripped.split(":", 1)[1].strip()
        if rhs:
            rhs = rhs.strip("[]")
            for part in re.split(r"[，,]", rhs):
                token = part.strip().strip("'\"")
                if token:
                    out.append(token)
        else:
            j = i + 1
            while j < len(body_lines):
                cand = body_lines[j].strip()
                if not cand.startswith("- "):
                    break
                token = cand[2:].strip().strip("'\"")
                if token:
                    out.append(token)
                j += 1
            i = j - 1
        i += 1

    dedup: List[str] = []
    seen = set()
    for x in out:
        k = x.strip().lower()
        if not k or k in seen:
            continue
        seen.add(k)
        dedup.append(x.strip())
    return dedup


def _classify_tags_to_concepts_and_entities(tags: List[str]) -> Tuple[List[str], List[str]]:
    concept_keywords = (
        "机制",
        "理论",
        "模型",
        "框架",
        "议程",
        "螺旋",
        "叙事",
        "传播",
        "情绪",
        "风险",
        "治理",
        "回应",
        "舆情",
    )
    entity_keywords = (
        "平台",
        "媒体",
        "机构",
        "公司",
        "学校",
        "政府",
        "警方",
        "网友",
        "用户",
        "专家",
        "博主",
    )

    concepts: List[str] = []
    entities: List[str] = []
    for tag in tags:
        t = tag.strip()
        if not t:
            continue
        if any(k in t for k in concept_keywords):
            concepts.append(t)
            continue
        if any(k in t for k in entity_keywords):
            entities.append(t)
            continue
        if len(t) >= 6:
            concepts.append(t)
        else:
            entities.append(t)
    return concepts[:12], entities[:12]


def _upsert_knowledge_page(
    *,
    page_dir: Path,
    name: str,
    section: str,
    source_file: Path,
    source_wiki: Path,
    summary: str,
    related_concepts: List[str],
    related_entities: List[str],
) -> Optional[str]:
    page_dir.mkdir(parents=True, exist_ok=True)
    title = (name or "").strip()
    if not title:
        return None

    slug = _slugify_cn_filename(title)
    out_path = page_dir / f"{slug}.md"
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    source_rel = source_wiki.relative_to(WIKI_DIR).as_posix()
    source_key = str(source_file)
    source_line = f"- [{source_file.name}]({source_rel}) | {source_key}"
    summary_line = f"- {summary[:160]}" if summary else "- 证据不足"
    related_concept_links = [
        f"[{x}](../concepts/{_slugify_cn_filename(x)}.md)"
        for x in related_concepts
        if x and x.strip() and x.strip() != title
    ]
    related_entity_links = [
        f"[{x}](../entities/{_slugify_cn_filename(x)}.md)"
        for x in related_entities
        if x and x.strip() and x.strip() != title
    ]
    related_concepts_line = (
        "- " + "、".join(related_concept_links[:8]) if related_concept_links else "- 证据不足"
    )
    related_entities_line = (
        "- " + "、".join(related_entity_links[:8]) if related_entity_links else "- 证据不足"
    )

    if out_path.exists():
        existing = _safe_read_text(out_path, max_chars=200_000)
        has_section_gap = ("## 关联概念" not in existing) or ("## 关联实体" not in existing)
        has_new_links = any(
            link not in existing for link in (related_concept_links[:8] + related_entity_links[:8])
        )
        if (source_key in existing) and (not has_section_gap) and (not has_new_links):
            return None
        with open(out_path, "a", encoding="utf-8", errors="replace") as f:
            f.write(f"\n### 更新于 {now}\n")
            if source_key not in existing:
                f.write(f"{source_line}\n")
            f.write(f"{summary_line}\n")
            f.write(f"- 关联概念：{ '、'.join(related_concept_links[:8]) if related_concept_links else '证据不足' }\n")
            f.write(f"- 关联实体：{ '、'.join(related_entity_links[:8]) if related_entity_links else '证据不足' }\n")
        return str(out_path)

    content = [
        "---",
        f"title: {title}",
        f"updated_at: {now}",
        f"wiki_section: {section}",
        "source_type: mixed",
        "confidence: medium",
        "tags: [自动生成, 增量更新]",
        "---",
        "",
        "## 定义",
        "证据不足",
        "",
        "## 关联来源",
        source_line,
        "",
        "## 近期增量",
        summary_line,
        "",
        "## 关联概念",
        related_concepts_line,
        "",
        "## 关联实体",
        related_entities_line,
        "",
        "## 待补充",
        "- 可补充关键事实、机制解释与风险建议。",
        "",
    ]
    out_path.write_text("\n".join(content), encoding="utf-8")
    return str(out_path)


def _update_concepts_entities_from_source(source_wiki_path: Path, source_file: Path, md_text: str) -> Dict[str, Any]:
    tags = _extract_frontmatter_tags(md_text)
    topic_tags = _extract_frontmatter_list(md_text, "topics")
    entity_tags = _extract_frontmatter_list(md_text, "entities")
    merged_tags = list(dict.fromkeys(tags + topic_tags + entity_tags))
    concepts_by_tag, entities_by_tag = _classify_tags_to_concepts_and_entities(merged_tags)

    concepts = list(dict.fromkeys(topic_tags + concepts_by_tag))[:12]
    entities = list(dict.fromkeys(entity_tags + entities_by_tag))[:12]
    paragraphs = _split_paragraphs(md_text)
    summary = paragraphs[0] if paragraphs else "证据不足"

    concept_updates: List[str] = []
    entity_updates: List[str] = []
    for c in concepts:
        updated = _upsert_knowledge_page(
            page_dir=WIKI_CONCEPTS_DIR,
            name=c,
            section="concepts",
            source_file=source_file,
            source_wiki=source_wiki_path,
            summary=summary,
            related_concepts=[x for x in concepts if x != c],
            related_entities=entities,
        )
        if updated:
            concept_updates.append(updated)

    for e in entities:
        updated = _upsert_knowledge_page(
            page_dir=WIKI_ENTITIES_DIR,
            name=e,
            section="entities",
            source_file=source_file,
            source_wiki=source_wiki_path,
            summary=summary,
            related_concepts=concepts,
            related_entities=[x for x in entities if x != e],
        )
        if updated:
            entity_updates.append(updated)

    return {
        "concept_candidates": concepts,
        "entity_candidates": entities,
        "concept_updates": concept_updates,
        "entity_updates": entity_updates,
    }


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
                    "path": str(fp),
                    "title": fp.name,
                    "score": round(score, 4),
                    "snippet": para[:360] + ("..." if len(para) > 360 else ""),
                }
            )

    ranked.sort(key=lambda x: float(x.get("score") or 0.0), reverse=True)
    return ranked[: max(1, max_items)]


def _rank_wiki_snippets(query: str, max_items: int = 8) -> List[Dict[str, Any]]:
    tokens = _tokenize(query, max_tokens=40)
    if not tokens:
        return []

    ranked: List[Dict[str, Any]] = []
    for fp in _iter_wiki_files(max_files=500):
        text = _safe_read_text(fp, max_chars=120_000)
        if not text:
            continue
        tags = _extract_frontmatter_tags(text)
        paragraphs = _split_paragraphs(text)
        if not paragraphs:
            continue

        local_hits: List[Tuple[float, str]] = []
        for para in paragraphs:
            score = _score_text(para, tokens)
            if tags:
                tag_hit = sum(1 for t in tags if any(tok.lower() in t.lower() for tok in tokens[:12]))
                score += 0.35 * tag_hit
            if score <= 0:
                continue
            local_hits.append((score, para))

        local_hits.sort(key=lambda x: x[0], reverse=True)
        for score, para in local_hits[:3]:
            ranked.append(
                {
                    "source": str(fp),
                    "path": str(fp),
                    "title": fp.name,
                    "score": round(score + 0.6, 4),
                    "snippet": para[:360] + ("..." if len(para) > 360 else ""),
                    "kind": "wiki",
                }
            )
    ranked.sort(key=lambda x: float(x.get("score") or 0.0), reverse=True)
    return ranked[: max(1, max_items)]


def _rank_theory_folder_snippets(query: str, max_items: int = 8) -> List[Dict[str, Any]]:
    """
    仅从「舆情分析的相关理论」目录召回片段，用于报告理论研判与 OPRAG 去重补全。

    在主题词命中弱时仍返回少量低分首段，避免非相关事件检索下理论侧完全空白。
    """
    tokens = _tokenize(query, max_tokens=36)
    if not tokens:
        return []

    ranked: List[Dict[str, Any]] = []
    for fp in _iter_theory_expert_note_files():
        text = _safe_read_text(fp, max_chars=120_000)
        if not text.strip():
            continue
        paragraphs = _split_paragraphs(text)
        if not paragraphs:
            continue
        stem_score = _score_text(fp.stem, tokens)
        best_score = 0.0
        best_para = ""
        for para in paragraphs:
            score = _score_text(para, tokens) + stem_score * 0.35
            if score > best_score:
                best_score = score
                best_para = para
        if best_score <= 0.0 and stem_score > 0.0:
            best_score = stem_score * 0.45
            best_para = paragraphs[0]
        if best_score <= 0.0:
            best_score = 0.02
            best_para = paragraphs[0]
        if not best_para:
            continue
        ranked.append(
            {
                "source": str(fp),
                "path": str(fp),
                "title": fp.name,
                "score": round(best_score + 0.12, 4),
                "snippet": best_para[:360] + ("..." if len(best_para) > 360 else ""),
                "kind": "theory",
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


def _llm_compile_raw_to_wiki(raw_title: str, raw_text: str, source_path: str) -> str:
    from langchain_core.messages import HumanMessage, SystemMessage
    from model.factory import get_tools_model

    llm = get_tools_model()
    schema_text = _load_wiki_schema_text()
    prompt = (
        "你是舆情智库知识工程师。请将输入材料编译为一页可复用 wiki（Markdown）。\n"
        "你必须严格遵守下面的 WIKI_SCHEMA 约定，并按其结构输出。\n\n"
        "=== WIKI_SCHEMA BEGIN ===\n"
        f"{schema_text}\n"
        "=== WIKI_SCHEMA END ===\n\n"
        "额外硬约束：\n"
        "1) 输出纯 Markdown，不要解释。\n"
        "2) 禁止编造；证据不足处写“证据不足”。\n"
        "3) 若来源是 expert_notes，优先提炼概念、框架、判断标准。\n"
    )
    user = f"原始标题：{raw_title}\n来源文件：{source_path}\n\n原始内容：\n{raw_text[:120000]}"
    resp = llm.invoke([SystemMessage(content=prompt), HumanMessage(content=user)])
    out = getattr(resp, "content", "") or str(resp)
    return str(out).strip()


def _upsert_wiki_index(page_paths: List[Path]) -> None:
    WIKI_DIR.mkdir(parents=True, exist_ok=True)
    rows: List[str] = []
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    rows.append("# 舆情智库 Wiki 索引")
    rows.append("")
    rows.append(f"- 更新时间：{now}")
    rows.append(f"- 页面数量：{len(page_paths)}")
    rows.append("")
    rows.append("## 页面目录")
    rows.append("")
    for p in sorted(page_paths, key=lambda x: x.name):
        rel = p.relative_to(WIKI_DIR)
        text = _safe_read_text(p, max_chars=800)
        first = ""
        for para in _split_paragraphs(text):
            first = para
            if first:
                break
        one = first[:80] + ("..." if len(first) > 80 else "") if first else "（无摘要）"
        rows.append(f"- [{rel.as_posix()}]({rel.as_posix()}) - {one}")
    WIKI_INDEX.write_text("\n".join(rows) + "\n", encoding="utf-8")


def _append_wiki_log(entry: str) -> None:
    WIKI_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(WIKI_LOG, "a", encoding="utf-8", errors="replace") as f:
        f.write(f"## [{ts}] ingest | {entry}\n\n")


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
    theory_cap = min(4, safe_limit)
    theory_hits = _rank_theory_folder_snippets(q, max_items=max(24, theory_cap * 6))
    raw_hits = _rank_reference_snippets(q, max_items=max(4, safe_limit * 2))
    wiki_hits = _rank_wiki_snippets(q, max_items=max(4, safe_limit * 2))

    theory_sorted = sorted(theory_hits, key=lambda x: float(x.get("score") or 0.0), reverse=True)
    theory_pick: List[Dict[str, Any]] = []
    seen_sources: set[str] = set()
    for h in theory_sorted:
        src = str(h.get("source") or h.get("path") or "").strip()
        if not src or src in seen_sources:
            continue
        seen_sources.add(src)
        theory_pick.append(h)
        if len(theory_pick) >= theory_cap:
            break

    mixed_rest = wiki_hits + raw_hits
    mixed_rest.sort(key=lambda x: float(x.get("score") or 0.0), reverse=True)
    hits = list(theory_pick)
    for h in mixed_rest:
        if len(hits) >= safe_limit:
            break
        src = str(h.get("source") or h.get("path") or "").strip()
        if src and src in seen_sources:
            continue
        if src:
            seen_sources.add(src)
        hits.append(h)

    return json.dumps({"query": q, "count": len(hits), "results": hits}, ensure_ascii=False, indent=2)


@tool
def build_reference_wiki(limit: int = 30, force: bool = False) -> str:
    """
    将本地参考资料增量编译为 wiki/sources 页面，并维护 index/log。

    编译源（按顺序优先处理）：

    - ``expert_notes/舆情分析的相关理论``：理论释义，适合报告「理论研判」引用
    - ``references/raw``
    - ``references/wiki/output``（不含 ``_candidates``）

    Args:
        limit: 本次最多处理多少个源文件
        force: 是否强制重编译（默认仅编译尚未生成对应 wiki/sources 产物的文件）

    Returns:
        JSON 字符串，包含处理统计与输出目录。
    """
    RAW_REFERENCES_DIR.mkdir(parents=True, exist_ok=True)

    WIKI_DIR.mkdir(parents=True, exist_ok=True)
    WIKI_SOURCES_DIR.mkdir(parents=True, exist_ok=True)
    WIKI_CONCEPTS_DIR.mkdir(parents=True, exist_ok=True)
    WIKI_ENTITIES_DIR.mkdir(parents=True, exist_ok=True)
    WIKI_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    max_n = max(1, min(int(limit or 30), 500))
    schema_meta = _get_wiki_schema_meta()

    theory_note_files = _iter_theory_expert_note_files()
    raw_files = [p for p in sorted(RAW_REFERENCES_DIR.rglob("*")) if p.is_file() and p.suffix.lower() in TEXT_SUFFIX]
    output_files: List[Path] = []
    if WIKI_OUTPUT_DIR.exists() and WIKI_OUTPUT_DIR.is_dir():
        for p in sorted(WIKI_OUTPUT_DIR.rglob("*")):
            if not p.is_file():
                continue
            if "_candidates" in {x.lower() for x in p.parts}:
                continue
            if p.suffix.lower() != ".md":
                continue
            output_files.append(p)

    if not theory_note_files and not raw_files and not output_files:
        return json.dumps(
            {
                "ok": False,
                "error": f"无可用编译源: raw 空且无 wiki/output 笔记；理论目录: {THEORY_EXPERT_NOTES_DIR}",
            },
            ensure_ascii=False,
            indent=2,
        )

    all_source_files = theory_note_files + raw_files + output_files
    processed = []
    skipped = []
    errors = []
    concept_updated_files: List[str] = []
    entity_updated_files: List[str] = []

    for fp in all_source_files[:max_n]:
        title = fp.stem
        out_path = _wiki_sources_output_path_for_compile(fp)
        if out_path.exists() and not force:
            skipped.append(str(fp))
            continue

        raw_text = _safe_read_text(fp, max_chars=130_000)
        if not raw_text.strip():
            skipped.append(str(fp))
            continue
        try:
            md = _llm_compile_raw_to_wiki(title, raw_text, str(fp))
            if not md.strip():
                raise ValueError("模型返回空内容")
            out_path.write_text(md + ("\n" if not md.endswith("\n") else ""), encoding="utf-8")
            ce_meta = _update_concepts_entities_from_source(out_path, fp, md)
            concept_updated_files.extend(ce_meta.get("concept_updates", []))
            entity_updated_files.extend(ce_meta.get("entity_updates", []))
            processed.append({"source": str(fp), "wiki": str(out_path)})
            _append_wiki_log(fp.name)
        except Exception as e:
            errors.append({"source": str(fp), "error": str(e)})

    page_paths = _iter_wiki_files(max_files=5000)
    _upsert_wiki_index(page_paths)

    return json.dumps(
        {
            "ok": True,
            "wiki_dir": str(WIKI_DIR),
            "wiki_sources_dir": str(WIKI_SOURCES_DIR),
            "wiki_concepts_dir": str(WIKI_CONCEPTS_DIR),
            "wiki_entities_dir": str(WIKI_ENTITIES_DIR),
            "wiki_output_dir": str(WIKI_OUTPUT_DIR),
            "index_file": str(WIKI_INDEX),
            "log_file": str(WIKI_LOG),
            "schema_file": schema_meta["schema_file"],
            "schema_hash": schema_meta["schema_hash"],
            "schema_preview": schema_meta["schema_preview"],
            "processed_count": len(processed),
            "skipped_count": len(skipped),
            "error_count": len(errors),
            "theory_note_source_count": len(theory_note_files),
            "raw_source_count": len(raw_files),
            "output_source_count": len(output_files),
            "concept_updates_count": len(set(concept_updated_files)),
            "entity_updates_count": len(set(entity_updated_files)),
            "concept_updates": sorted(set(concept_updated_files))[:30],
            "entity_updates": sorted(set(entity_updated_files))[:30],
            "processed": processed[:50],
            "errors": errors[:20],
        },
        ensure_ascii=False,
        indent=2,
    )


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
    if any(x in k for x in ["相似", "类似", "复盘", "对照"]):
        return search_reference_insights.invoke({"query": k, "limit": 8})

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
build_reference_wiki = build_reference_wiki


if __name__ == "__main__":
    print("=== 框架测试 ===")
    print(get_sentiment_analysis_framework.invoke({"topic": "张雪峰事件"})[:500])
    print("\n=== 理论测试 ===")
    print(get_sentiment_theories.invoke({"topic": "教育 舆情"})[:500])
    print("\n=== 参考检索测试 ===")
    print(search_reference_insights.invoke({"query": "张雪峰 猝死 舆情", "limit": 3}))
