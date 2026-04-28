"""Wiki CLI：本地检索 + 可选 LLM RAG 合成 + 可选微博智搜辅助。

默认由环境变量控制（测试见 ``tests/conftest.py`` 的 ``setdefault``）：

- ``SONA_WIKI_USE_LLM``：开启时用 ``workflow/wiki_rag`` 调用 tools profile 模型做 RAG。
- ``SONA_WIKI_WEIBO_AUX``：在事件概述/启示类问题下附加 ``weibo_aisearch`` 摘录进上下文。

Contract target (spec v1):
{
  "answer": str,
  "sources": [{"title": str, "path": str, "snippet": str, "score": float}]
}
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha1
from pathlib import Path
from typing import Any, Dict, List, Tuple

from utils.path import get_opinion_analysis_kb_root
from utils.harness_memory import (
    append_example_memory,
    get_domain_policy,
    load_project_domain_routing,
)


@dataclass(slots=True)
class WikiSource:
    title: str
    path: str
    snippet: str
    score: float

    def to_dict(self) -> Dict[str, Any]:
        snip = _strip_yaml_frontmatter(self.snippet)
        if len(snip) > 220:
            snip = snip[:219].rstrip() + "…"
        return {
            "title": self.title,
            "path": self.path,
            "snippet": snip,
            "score": round(float(self.score), 4),
        }


def _llm_excerpt_max_chars() -> int:
    raw = os.environ.get("SONA_WIKI_LLM_EXCERPT_CHARS", "12000").strip()
    try:
        n = int(raw)
    except Exception:
        n = 12000
    return max(800, min(n, 100_000))


def _output_candidate_enabled() -> bool:
    raw = str(os.environ.get("SONA_WIKI_OUTPUT_CANDIDATE_ENABLED", "true") or "").strip().lower()
    if not raw:
        return True
    return raw not in ("0", "false", "no", "n", "off")


def _high_value_threshold() -> int:
    raw = str(os.environ.get("SONA_WIKI_HIGH_VALUE_THRESHOLD", "75") or "").strip()
    try:
        v = int(raw)
    except Exception:
        v = 75
    return max(40, min(v, 95))


def _min_sources_for_candidate() -> int:
    raw = str(os.environ.get("SONA_WIKI_CANDIDATE_MIN_SOURCES", "3") or "").strip()
    try:
        v = int(raw)
    except Exception:
        v = 3
    return max(1, min(v, 8))


def _safe_slug(text: str, max_len: int = 72) -> str:
    s = re.sub(r"[^\w\u4e00-\u9fff-]+", "_", str(text or "").strip()).strip("_")
    if not s:
        s = "candidate"
    return s[:max_len]


def _is_time_sensitive_query(query: str) -> bool:
    q = str(query or "")
    time_keys = ("今天", "昨日", "刚刚", "最新", "实时", "现在", "本周", "本月", "热搜")
    return any(k in q for k in time_keys)


def _score_wiki_answer_value(query: str, answer: str, sources: List[WikiSource]) -> Dict[str, Any]:
    uniq_paths = {str(s.path or "") for s in sources if str(s.path or "").strip()}
    src_n = len(uniq_paths)
    report_like_n = sum(1 for s in sources if _wiki_path_is_report_like(s.path))
    avg_src_score = (
        sum(float(s.score or 0.0) for s in sources) / max(1, len(sources))
        if sources
        else 0.0
    )
    evidence = min(40.0, src_n * 8.0 + report_like_n * 3.0 + min(10.0, avg_src_score * 10.0))

    ans = str(answer or "")
    has_structure = 1.0 if ("###" in ans or "##" in ans or "1)" in ans or "1." in ans) else 0.0
    has_actionable = 1.0 if any(k in ans for k in ("建议", "可复核", "风险", "应对", "方法")) else 0.0
    q_quality = min(30.0, min(18.0, len(ans) / 75.0) + has_structure * 6.0 + has_actionable * 6.0)

    reusable_hits = sum(1 for k in ("方法论", "框架", "争议点", "风险点", "启示", "复盘", "机制") if k in ans)
    reuse = min(20.0, reusable_hits * 3.5 + (4.0 if len(ans) >= 400 else 0.0))

    stability = 10.0
    if _is_time_sensitive_query(query):
        stability = 4.0

    total = int(round(evidence + q_quality + reuse + stability))
    threshold = _high_value_threshold()
    high_value = bool(
        total >= threshold
        and src_n >= _min_sources_for_candidate()
        and evidence >= 18.0
    )
    return {
        "total": total,
        "threshold": threshold,
        "is_high_value": high_value,
        "dimensions": {
            "evidence": round(evidence, 2),
            "quality": round(q_quality, 2),
            "reuse": round(reuse, 2),
            "stability": round(stability, 2),
        },
        "features": {
            "source_count": src_n,
            "report_like_source_count": report_like_n,
            "avg_source_score": round(avg_src_score, 4),
            "answer_chars": len(ans),
            "time_sensitive_query": _is_time_sensitive_query(query),
        },
    }


def _write_output_candidate(
    *,
    wiki_root: Path,
    query: str,
    answer: str,
    sources: List[Dict[str, Any]],
    score_meta: Dict[str, Any],
) -> Dict[str, Any]:
    out_dir = wiki_root / "output" / "_candidates"
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest = out_dir / "index.jsonl"
    q_hash = sha1(str(query or "").strip().encode("utf-8", errors="replace")).hexdigest()[:12]
    now = datetime.now()
    ts = now.strftime("%Y%m%d_%H%M%S")
    slug = _safe_slug(query, max_len=48)
    fname = f"{ts}_{slug}_{q_hash}.md"
    rel = Path("output") / "_candidates" / fname
    fp = wiki_root / rel

    rows: List[str] = []
    rows.append("---")
    rows.append(f"title: 候选沉淀：{query.strip()[:80]}")
    rows.append(f"created_at: {now.strftime('%Y-%m-%d %H:%M:%S')}")
    rows.append("wiki_section: output_candidate")
    rows.append(f"value_score: {int(score_meta.get('total', 0))}")
    rows.append(f"value_threshold: {int(score_meta.get('threshold', 0))}")
    rows.append(f"query_hash: {q_hash}")
    rows.append("auto_generated: true")
    rows.append("status: pending_review")
    rows.append("---")
    rows.append("")
    rows.append("## 原始问题")
    rows.append("")
    rows.append(query.strip())
    rows.append("")
    rows.append("## 回答内容")
    rows.append("")
    rows.append(answer.strip())
    rows.append("")
    rows.append("## 评分明细")
    rows.append("")
    dims = score_meta.get("dimensions") if isinstance(score_meta.get("dimensions"), dict) else {}
    for k in ("evidence", "quality", "reuse", "stability"):
        rows.append(f"- {k}: {dims.get(k, 0)}")
    rows.append("")
    rows.append("## 来源证据")
    rows.append("")
    for i, s in enumerate(sources[:8], 1):
        if not isinstance(s, dict):
            continue
        rows.append(f"{i}. {s.get('title', '')}")
        rows.append(f"   - path: {s.get('path', '')}")
        rows.append(f"   - score: {s.get('score', 0)}")
        sn = str(s.get("snippet", "")).strip().replace("\n", " ")
        if len(sn) > 160:
            sn = sn[:160].rstrip() + "…"
        rows.append(f"   - snippet: {sn}")
    rows.append("")
    rows.append("## 审核建议")
    rows.append("")
    rows.append("- [ ] 是否具备长期复用价值")
    rows.append("- [ ] 是否需改写为正式 output 页面")
    rows.append("- [ ] 是否需要补充更多证据来源")
    rows.append("")

    fp.write_text("\n".join(rows), encoding="utf-8")
    rec = {
        "created_at": int(time.time() * 1000),
        "query": query.strip(),
        "query_hash": q_hash,
        "candidate_path": str(rel.as_posix()),
        "value_score": int(score_meta.get("total", 0)),
        "value_threshold": int(score_meta.get("threshold", 0)),
        "source_count": int((score_meta.get("features") or {}).get("source_count", 0)),
    }
    with open(manifest, "a", encoding="utf-8", errors="replace") as wf:
        import json

        wf.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return rec


def _source_dicts_for_llm(sources: List[WikiSource], project_root: Path) -> List[Dict[str, Any]]:
    """
    为 RAG 合成拉取长摘录：检索阶段 ``snippet`` 仅 ~220 字，不足以支撑模型总结全文。
    """
    cap = _llm_excerpt_max_chars()
    out: List[Dict[str, Any]] = []
    for s in sources:
        d = s.to_dict()
        try:
            fp = (project_root / s.path).resolve()
            if fp.is_file():
                raw = fp.read_text(encoding="utf-8", errors="replace")
                body = _strip_yaml_frontmatter(raw[:480_000])
                long_snip = body[:cap].strip()
                if long_snip:
                    d["snippet"] = long_snip
        except Exception:
            pass
        out.append(d)
    return out


def _enrich_source_dicts_with_local_files(source_dicts: List[Dict[str, Any]], project_root: Path) -> None:
    """为每条来源补充本地绝对路径与 file:// URI，便于在终端或 IDE 中打开原文。"""
    pr = project_root.resolve()
    for d in source_dicts:
        rel = str(d.get("path") or "").strip()
        if not rel:
            continue
        try:
            p = (pr / rel).resolve()
            if p.is_file():
                d["abs_path"] = str(p)
                d["file_uri"] = p.as_uri()
        except Exception:
            continue


def _tokenize(text: str) -> List[str]:
    t = str(text or "").lower()
    tokens = re.findall(r"[a-z0-9_]{2,}|[\u4e00-\u9fff]{2,}", t)
    stop = {
        "什么", "如何", "怎么", "请问", "一下", "一个", "我们", "你们", "这个", "那个",
        "以及", "并且", "可以", "进行", "问题", "区别", "通常", "发生",
    }
    out: List[str] = []
    for tok in tokens:
        if tok in stop:
            continue
        out.append(tok)
    return out


def _infer_domain_for_wiki_query(query: str) -> str:
    """
    领域编码（domain routing）的轻量推断：用于在召回阶段为特定领域的专题/概念页加权。

    约束：宁可保守（返回空字符串）也不要过度泛化，以免把“禁烟/吸烟”误路由到无关领域。
    """
    q = str(query or "").strip().lower()
    if not q:
        return ""
    # 控烟 / 禁烟 / 烟草治理
    smoking_keys = (
        "控烟",
        "禁烟",
        "吸烟",
        "抽烟",
        "二手烟",
        "烟草",
        "烟卡",
        "无烟",
        "电子烟",
        "公共场所吸烟",
        "吸烟区",
        "高铁站吸烟",
        "高铁站禁烟",
        "车站禁烟",
        "站内吸烟",
    )
    if any(k in q for k in smoking_keys):
        return "控烟"
    return ""


def _infer_domain_from_project_memory(query: str, *, project_root: Path) -> str:
    """
    使用可版本化的项目记忆（workflow/domain_routing.json）推断领域。

    设计原则：
    - 保守：无把握则返回空字符串
    - 可审计：规则来自配置文件
    """
    q = str(query or "").strip().lower()
    if not q:
        return ""
    cfg = load_project_domain_routing(project_root)
    domains = cfg.get("domains")
    if not isinstance(domains, dict):
        return ""
    for domain, spec in domains.items():
        if not isinstance(spec, dict):
            continue
        match = spec.get("match")
        if not isinstance(match, dict):
            continue
        keywords = match.get("keywords")
        if not isinstance(keywords, list):
            continue
        keys = [str(x).strip().lower() for x in keywords if str(x).strip()]
        if keys and any(k in q for k in keys):
            return str(domain)
    return ""


def _domain_seed_pages_from_policy(
    wiki_root: Path,
    *,
    project_root: Path,
    domain: str,
) -> List[Path]:
    """
    从项目记忆生成领域“种子页”（优先注入召回候选）。
    """
    dom = str(domain or "").strip()
    if not dom:
        return []
    policy = get_domain_policy(project_root, dom)
    if policy is None:
        return []

    seeds: List[Path] = []
    must = policy.must_include
    prefer = policy.prefer
    for rel in (must.get("concept_pages") or []) + (prefer.get("concept_pages") or []):
        p = (project_root / str(rel)).resolve()
        if p.is_file():
            seeds.append(p)
    for rel in (must.get("wiki_sources") or []):
        p = (project_root / str(rel)).resolve()
        if p.is_file():
            seeds.append(p)

    # 去重并保持顺序
    out: List[Path] = []
    seen: set[str] = set()
    for p in seeds:
        key = str(p)
        if key in seen:
            continue
        seen.add(key)
        out.append(p)
    return out


def _domain_injection_limits(project_root: Path, domain: str) -> Dict[str, Any]:
    policy = get_domain_policy(project_root, str(domain or "").strip())
    return policy.injection_limits if policy is not None else {}


def _domain_blocklisted_path(project_root: Path, domain: str, rel_path: str) -> bool:
    policy = get_domain_policy(project_root, str(domain or "").strip())
    if policy is None:
        return False
    bl = policy.blocklist
    if not isinstance(bl, dict):
        return False
    s = str(rel_path or "")
    contains = bl.get("path_contains")
    if isinstance(contains, list) and any(str(x) and str(x) in s for x in contains):
        return True
    return False


def _cap_domain_seed_chars_for_llm(
    *,
    project_root: Path,
    domain: str,
    llm_payloads: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """
    记忆约束：限制领域“种子资料”进入 LLM 的总字符预算，避免挤占事件本体证据。
    """
    lim = _domain_injection_limits(project_root, domain)
    max_chars = lim.get("max_seed_chars_for_llm")
    try:
        cap = int(max_chars)
    except Exception:
        cap = 0
    if cap <= 0:
        return llm_payloads

    policy = get_domain_policy(project_root, domain)
    if policy is None:
        return llm_payloads
    must = policy.must_include
    seed_paths = set()
    for rel in (must.get("concept_pages") or []) + (must.get("wiki_sources") or []):
        seed_paths.add(str(rel))

    used = 0
    out: List[Dict[str, Any]] = []
    for d in llm_payloads:
        if not isinstance(d, dict):
            continue
        nd = dict(d)
        rel = str(nd.get("path") or "").strip()
        sn = str(nd.get("snippet") or "")
        # 只对“必引种子”做预算限制；其它来源保持完整，有利于事件证据覆盖
        if rel and rel in seed_paths:
            remaining = max(0, cap - used)
            if remaining <= 0:
                nd["snippet"] = ""
            elif len(sn) > remaining:
                nd["snippet"] = sn[: max(0, remaining - 1)].rstrip() + "…"
                used = cap
            else:
                used += len(sn)
        out.append(nd)
    return out


def _domain_seed_pages(wiki_root: Path, domain: str) -> List[Path]:
    """
    为领域召回准备“种子页”：优先选择 concepts 下的总览页，其次补充若干高相关概念页。
    """
    dom = str(domain or "").strip()
    if not dom:
        return []
    cdir = wiki_root / "concepts"
    if not cdir.is_dir():
        return []

    def _p(name: str) -> Path:
        return cdir / f"{name}.md"

    seeds: List[Path] = []
    if dom == "控烟":
        for name in ("控烟", "烟草控制", "公共卫生", "公共健康", "政策法规"):
            fp = _p(name)
            if fp.is_file():
                seeds.append(fp)
        # 同时把领域专题的“源资料页”作为种子页注入（比概念页更容易提供可引用的长段落证据）。
        sdir = wiki_root / "sources"
        if sdir.is_dir():
            for fp in (
                sdir / "2025年控烟舆情分析年度报告0318.md",
                sdir / "2024第三季度控烟舆情监测报告_20250102.md",
                sdir / "2024年第四季度控烟舆情报告.md",
                sdir / "2024年控烟舆情监测年度报告0320.md",
                sdir / "2024年上半年控烟舆情报告1110.md",
                sdir / "2024年_烟卡_舆情专题分析报告.md",
            ):
                if fp.is_file():
                    seeds.append(fp)
    return seeds


def _cn_ngrams(text: str, *, min_len: int = 2, max_len: int = 4) -> List[str]:
    """Short Chinese n-grams (2–4 chars) inside each CJK run; helps index/path matching."""
    out: List[str] = []
    for m in re.finditer(r"[\u4e00-\u9fff]{2,}", str(text or "")):
        run = m.group(0)
        upper = min(max_len, len(run))
        for n in range(min_len, upper + 1):
            for i in range(0, len(run) - n + 1):
                out.append(run[i : i + n])
    return out


def _ngrams_for_relevance_bonus(ngrams: List[str] | None, *, min_len: int = 3) -> List[str]:
    """仅用语义更长的 n-gram 参与加分，避免「效应」「问题」等二字泛匹配污染检索。"""
    if not ngrams:
        return []
    return [g for g in ngrams if len(g) >= min_len]


def _meme_or_slang_intent(raw_query: str) -> bool:
    """梗/网络用语/段子类问法，走事件型叙述而非「可验证的概念课」。"""
    q = str(raw_query or "")
    keys = (
        "什么梗",
        "啥梗",
        "梗是什么",
        "梗指",
        "网络用语",
        "谐音梗",
        "段子",
        "出自哪",
        "出自哪里",
        "怎么来的",
        "什么来头",
    )
    return any(k in q for k in keys)


def _definitional_intent(raw_query: str) -> bool:
    q = str(raw_query or "")
    if _meme_or_slang_intent(q):
        return False
    keys = ("什么是", "是啥", "何谓", "定义", "概念", "含义", "指什么", "怎么理解")
    return any(k in q for k in keys)


def _event_overview_intent(raw_query: str) -> bool:
    """用户要「事件梗概/始末」而非抽象概念课。"""
    q = str(raw_query or "")
    keys = (
        "什么事件",
        "啥事件",
        "怎么回事",
        "来龙去脉",
        "事件概述",
        "事件经过",
        "事件始末",
        "前因后果",
        "背景是什么",
        "发生了什么",
    )
    return any(k in q for k in keys)


def _event_insights_intent(raw_query: str) -> bool:
    """事件延伸：启示、教训、评价类问题（可辅以微博讨论线索）。"""
    q = str(raw_query or "")
    keys = (
        "启示",
        "教训",
        "反思",
        "借鉴",
        "意味着什么",
        "对我们有什么",
        "后续影响",
        "怎么看",
        "评价一下",
        "有何影响",
    )
    return any(k in q for k in keys)


def _should_enrich_with_weibo(raw_query: str) -> bool:
    v = os.environ.get("SONA_WIKI_WEIBO_AUX", "true").strip().lower()
    if v in ("0", "false", "no", "n", "off"):
        return False
    return (
        _meme_or_slang_intent(raw_query)
        or _event_overview_intent(raw_query)
        or _event_insights_intent(raw_query)
    )


def _wiki_llm_enabled() -> bool:
    # 显式设置空字符串时 os.environ.get 会返回 ""，默认 "true" 不会生效，此处按「未配置=开启」处理
    raw = os.environ.get("SONA_WIKI_USE_LLM")
    if raw is None:
        return True
    s = str(raw).strip()
    if s == "":
        return True
    return s.lower() in ("1", "true", "yes", "y", "on")


_FRONTMATTER_RE = re.compile(r"^---\s*[\s\S]*?^---\s*\n?", re.MULTILINE)
_YAML_META_LINE = re.compile(
    r"^(title|source_file|updated_at|wiki_section|source_type|confidence|tags)\s*:",
    re.IGNORECASE,
)


def _strip_yaml_frontmatter(text: str) -> str:
    """
    去掉 YAML front matter。检索块常被截断在 220 字以内，可能没有闭合的第二个 ``---``，
    此时仍应剥掉 ``--- title: ...`` 这类噪声行。
    """
    t = str(text or "").lstrip("\ufeff")
    if not t.startswith("---"):
        return t
    m = _FRONTMATTER_RE.match(t)
    if m:
        return t[m.end() :].lstrip()
    lines = t.splitlines()
    if not lines or not lines[0].strip().startswith("---"):
        return t
    # 有开头 --- 但未在片段内闭合：剥 metadata / tags 短行，直到 Markdown 正文
    i = 1
    while i < len(lines):
        ls = lines[i].strip()
        if not ls:
            i += 1
            continue
        if ls == "---" or (ls.startswith("---") and len(ls) <= 4):
            return "\n".join(lines[i + 1 :]).lstrip() or t
        if _YAML_META_LINE.match(ls):
            i += 1
            continue
        if ls.startswith("##") or re.match(r"^#\s+\S", ls):
            return "\n".join(lines[i:]).lstrip()
        if ls.startswith("- ") and len(ls) < 120 and "http" not in ls and "://" not in ls:
            i += 1
            continue
        return "\n".join(lines[i:]).lstrip()
    return t


# 从检索片段拼答案时去掉「文章自我指涉」句，避免答非所问（用户要事件事实而非书评腔）
_META_SMEAR_PATTERNS: Tuple[re.Pattern[str], ...] = (
    re.compile(r"本文从[^。]{0,120}分析事件[^。]*。"),
    re.compile(r"倡导[「\"][^」\"]{2,48}[」\"][^。]{0,60}。"),
    re.compile(r"本文作者从[^。]{0,120}。"),
)


def _prune_article_meta_sentences(text: str) -> str:
    t = str(text or "")
    for pat in _META_SMEAR_PATTERNS:
        t = pat.sub("", t)
    return re.sub(r"\s+", " ", t).strip()


def _normalize_query(query: str) -> str:
    q = str(query or "").strip()
    drop_words = [
        "请问",
        "请用",
        "请你",
        "请",
        "解释",
        "说明",
        "一下",
        "通常",
        "如何",
        "什么是",
        "有什么区别",
    ]
    for w in drop_words:
        q = q.replace(w, " ")
    q = re.sub(r"[^\u4e00-\u9fffA-Za-z0-9]+", " ", q)
    q = re.sub(r"\s+", " ", q).strip()
    return q


def _chunk_text(text: str, max_chars: int = 220) -> List[str]:
    lines = [x.strip() for x in str(text or "").splitlines() if x.strip()]
    chunks: List[str] = []
    cur = ""
    for ln in lines:
        if len(cur) + len(ln) + 1 > max_chars and cur:
            chunks.append(cur)
            cur = ln
        else:
            cur = f"{cur} {ln}".strip()
    if cur:
        chunks.append(cur)
    return chunks


def _score(query_tokens: List[str], chunk: str, *, ngrams: List[str] | None = None) -> float:
    c_tokens = set(_tokenize(chunk))
    if not query_tokens and not ngrams:
        return 0.0
    base = 0.0
    if query_tokens and c_tokens:
        hit = sum(1 for t in set(query_tokens) if t in c_tokens)
        base = hit / max(1, len(set(query_tokens)))
    bonus = 0.0
    ng_use = _ngrams_for_relevance_bonus(ngrams, min_len=3)
    if ng_use:
        ch = str(chunk or "")
        bh = sum(1 for g in set(ng_use) if g in ch)
        bonus = min(0.35, 0.08 * bh)
    return min(1.0, base + bonus)


def _rerank_score(raw_score: float, *, normalized_query: str, source: WikiSource) -> float:
    bonus = 0.0
    nq = normalized_query.replace(" ", "")
    snippet = source.snippet.replace(" ", "")
    title = source.title.replace(" ", "")
    if nq and nq in snippet:
        bonus += 0.35
    if nq and nq in title:
        bonus += 0.2
    # reward sources that mention any query token in title/path
    q_tokens = _tokenize(normalized_query)
    if q_tokens:
        title_path = f"{source.title} {source.path}"
        hit = sum(1 for t in set(q_tokens) if t in title_path)
        bonus += min(0.2, hit * 0.05)
    return raw_score + bonus


def _stem_fingerprint(stem: str) -> str:
    """
    宽松文档指纹：合并同一报告在 ``sources/output_*.md`` 与 ``output/*.md``
    等多路径下的重复收录，避免 RAG 上下文堆叠同文多段。
    """
    s = re.sub(r"(?i)^output_", "", str(stem or "").strip())
    s = re.sub(r"[0-9_（）\s().-]+", "", s)
    return s


def _is_entity_stub_source(src: WikiSource) -> bool:
    """
    过滤自动生成的实体占位页（常见「## 定义」下仅「证据不足」），
    避免污染 LLM 上下文并诱发「全文证据不足」式回答。
    """
    p = str(src.path or "").replace("\\", "/")
    if "/entities/" not in p:
        return False
    sn = _strip_yaml_frontmatter(str(src.snippet or ""))
    if re.search(r"定义\s*证据不足", sn):
        return True
    if "证据不足" in sn and len(sn.strip()) < 200:
        return True
    return False


def _wiki_path_is_report_like(path: str) -> bool:
    """是否为 ``sources/`` 或 ``wiki/output/`` 下的正文型词条（非 entities/concepts 导航页）。"""
    p = str(path or "").replace("\\", "/")
    return "/references/wiki/sources/" in p or "/references/wiki/output/" in p


def _filter_entities_when_report_docs_present(ranked: List[WikiSource]) -> List[WikiSource]:
    """
    当检索已命中报告/综述类正文时，压低仅含链接或占位语的实体页，
    避免与长篇报告重复占位、拉低 RAG 叙事质量。
    """
    has_doc = any(_wiki_path_is_report_like(x.path) and float(x.score) >= 0.28 for x in ranked)
    if not has_doc:
        return ranked
    return [
        x
        for x in ranked
        if not (
            "/entities/" in str(x.path or "").replace("\\", "/")
            and float(x.score) < 0.45
        )
    ]


def _resolve_wiki_root(project_root: Path) -> Path | None:
    env = str(os.environ.get("SONA_WIKI_ROOT", "") or "").strip()
    if env:
        p = Path(env).expanduser()
        if not p.is_absolute():
            p = project_root / p
        if p.is_dir():
            return p
    default = get_opinion_analysis_kb_root(project_root) / "references" / "wiki"
    if default.is_dir():
        return default
    return None


def _score_index_line(query_tokens: List[str], line: str, *, ngrams: List[str] | None = None) -> float:
    low = line.lower()
    score = 0.0
    if query_tokens:
        hit = sum(1 for t in set(query_tokens) if t in low)
        score = hit / max(1, len(set(query_tokens)))
    bonus = 0.0
    ng_use = _ngrams_for_relevance_bonus(ngrams, min_len=3)
    if ng_use:
        uniq = set(ng_use)
        bh = sum(1 for g in uniq if g in line)
        bonus = min(0.45, 0.12 * bh)
    return min(1.0, score + bonus)


_INDEX_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")

_WIKI_HUB_RELS = (
    "sources/output_舆情分析方法论.md",
    "sources/output_舆情分析深度研究资料.md",
    "sources/output_舆情分析可参考的一些深度观点.md",
)


def _hub_fallback_candidates(wiki_root: Path, *, max_pages: int) -> List[Tuple[Path, float, str]]:
    out: List[Tuple[Path, float, str]] = []
    for rel in _WIKI_HUB_RELS:
        if len(out) >= max_pages:
            break
        p = (wiki_root / rel).resolve()
        try:
            p.relative_to(wiki_root.resolve())
        except Exception:
            continue
        if p.is_file():
            out.append((p, 0.08, f"hub:{rel}"))
    return out


def _parse_index_candidates(
    wiki_root: Path,
    *,
    raw_query: str,
    query_tokens: List[str],
    normalized_query: str,
    max_pages: int,
) -> List[Tuple[Path, float, str]]:
    index_path = wiki_root / "index.md"
    if not index_path.is_file():
        return []
    ngrams = _cn_ngrams(normalized_query or raw_query)
    want_concept = _definitional_intent(raw_query)
    ranked: List[Tuple[Path, float, str]] = []
    try:
        with open(index_path, "r", encoding="utf-8", errors="replace") as f:
            for raw in f:
                line = raw.strip()
                if not line.startswith("- "):
                    continue
                ls = _score_index_line(query_tokens, line, ngrams=ngrams)
                m = _INDEX_LINK_RE.search(line)
                if not m:
                    continue
                rel = str(m.group(2) or "").strip()
                if not rel.endswith(".md"):
                    continue
                # 禁止「只要是定义问法就给所有 concepts/ 加权重」——会与查询零相关仍进候选（如 蝴蝶效应 → AI_影响）
                if want_concept and rel.startswith("concepts/") and ls > 0:
                    ls = min(1.0, ls + 0.22)
                target = (wiki_root / rel).resolve()
                try:
                    target.relative_to(wiki_root.resolve())
                except Exception:
                    continue
                if not target.is_file():
                    continue
                if ls <= 0:
                    continue
                ranked.append((target, float(ls), line))
    except Exception:
        return []

    ranked.sort(key=lambda x: x[1], reverse=True)
    dedup: List[Tuple[Path, float, str]] = []
    seen: set[str] = set()
    for p, s, ln in ranked:
        key = str(p)
        if key in seen:
            continue
        seen.add(key)
        dedup.append((p, s, ln))
        if len(dedup) >= max_pages:
            break
    if not dedup:
        dedup = _hub_fallback_candidates(wiki_root, max_pages=min(max_pages, 6))
    return dedup


def _sources_from_markdown_file(
    file_path: Path,
    *,
    project_root: Path,
    query_tokens: List[str],
    normalized_query: str,
    index_line_score: float,
    ngrams: List[str] | None = None,
) -> List[WikiSource]:
    out: List[WikiSource] = []
    try:
        text = file_path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return out
    # Large pages: avoid scanning entire file for MVP latency
    head = _strip_yaml_frontmatter(text[:240_000])
    for chunk in _chunk_text(head):
        s = _score(query_tokens, chunk, ngrams=ngrams)
        if s <= 0 and index_line_score <= 0:
            continue
        base = max(s, 0.0) + min(0.35, float(index_line_score) * 0.25)
        if base <= 0:
            continue
        source = WikiSource(
            title=file_path.stem,
            path=str(file_path.relative_to(project_root)),
            snippet=chunk[:220],
            score=base,
        )
        source.score = _rerank_score(source.score, normalized_query=normalized_query, source=source)
        out.append(source)
    return out


def _legacy_candidate_files(project_root: Path) -> List[Path]:
    candidates: List[Path] = []
    for rel in (
        "docs/specs",
        "prompt",
        "tests/fixtures/README.md",
    ):
        p = project_root / rel
        if p.is_file():
            candidates.append(p)
            continue
        if p.is_dir():
            candidates.extend(sorted(p.glob("*.md")))
    return [p for p in candidates if p.exists() and p.is_file()]


def retrieve_wiki_sources(query: str, *, topk: int = 6, project_root: Path | None = None) -> List[WikiSource]:
    root = project_root or Path(__file__).resolve().parents[1]
    normalized_query = _normalize_query(query)
    query_tokens = _tokenize(normalized_query or query)
    ngrams = _cn_ngrams(normalized_query or str(query or ""))
    ranked: List[WikiSource] = []

    wiki_root = _resolve_wiki_root(root)
    index_used = False
    index_hits = 0
    pages_scanned = 0

    if wiki_root is not None:
        domain = _infer_domain_from_project_memory(query, project_root=root) or _infer_domain_for_wiki_query(query)
        domain_seeds = _domain_seed_pages_from_policy(wiki_root, project_root=root, domain=domain) or _domain_seed_pages(
            wiki_root, domain
        )
        lim = _domain_injection_limits(root, domain)
        try:
            max_seed_pages = int(lim.get("max_seed_pages", 0))
        except Exception:
            max_seed_pages = 0
        if max_seed_pages <= 0:
            max_seed_pages = 8
        # 领域编码：先注入领域种子页，让“专题/概念总览”在 topk 很小的情况下也更稳定进入候选。
        for seed in domain_seeds[: max(1, min(max_seed_pages, 20))]:
            ranked.extend(
                _sources_from_markdown_file(
                    seed,
                    project_root=root,
                    query_tokens=query_tokens,
                    normalized_query=normalized_query,
                    index_line_score=0.55,
                    ngrams=ngrams,
                )
            )
            pages_scanned += 1

        candidates = _parse_index_candidates(
            wiki_root,
            raw_query=str(query or ""),
            query_tokens=query_tokens,
            normalized_query=normalized_query,
            max_pages=28,
        )
        index_used = True
        index_hits = len(candidates)
        for page_path, line_score, _line in candidates:
            relp = ""
            try:
                relp = str(page_path.relative_to(root).as_posix())
            except Exception:
                relp = str(page_path)
            if domain and _domain_blocklisted_path(root, domain, relp):
                continue
            ranked.extend(
                _sources_from_markdown_file(
                    page_path,
                    project_root=root,
                    query_tokens=query_tokens,
                    normalized_query=normalized_query,
                    index_line_score=line_score,
                    ngrams=ngrams,
                )
            )
            pages_scanned += 1

        log_path = wiki_root / "log.md"
        qlow = str(query or "").lower()
        log_keys = (
            "log",
            "ingest",
            "更新",
            "索引",
            "维护",
            "lint",
            "收录",
            "同步",
            "changelog",
            "变更",
            "新增",
            "入库",
            "时间线",
            "最近更新",
            "wiki日志",
            "审计",
        )
        if log_path.is_file() and any(k in qlow for k in log_keys):
            ranked.extend(
                _sources_from_markdown_file(
                    log_path,
                    project_root=root,
                    query_tokens=query_tokens,
                    normalized_query=normalized_query,
                    index_line_score=0.15,
                    ngrams=ngrams,
                )
            )
            pages_scanned += 1

    # Fallback: small dev corpus + specs (keeps tests/dev usable if wiki path missing)
    if not ranked:
        for file_path in _legacy_candidate_files(root):
            ranked.extend(
                _sources_from_markdown_file(
                    file_path,
                    project_root=root,
                    query_tokens=query_tokens,
                    normalized_query=normalized_query,
                    index_line_score=0.0,
                    ngrams=ngrams,
                )
            )

    # Anchor boosting: for event-like queries, ensure key entity/phrase matches get a small lift.
    q0 = re.sub(r"\s+", "", str(query or ""))
    anchor_terms: List[str] = []
    if "12306" in q0:
        anchor_terms.extend(["12306", "铁路12306"])
    m_gap = re.search(r"相隔\d{1,2}(?:个)?车厢", q0)
    if m_gap:
        anchor_terms.append(m_gap.group(0).replace("个车厢", "车厢"))
    for seg in re.findall(r"[\u4e00-\u9fff]{3,6}", q0):
        if seg in {"舆情分析", "事件分析", "分析报告"}:
            continue
        anchor_terms.append(seg)
        if len(anchor_terms) >= 10:
            break
    seen_a: set[str] = set()
    dedup_a: List[str] = []
    for t in anchor_terms:
        s = str(t or "").strip()
        if not s:
            continue
        if s in seen_a:
            continue
        seen_a.add(s)
        dedup_a.append(s)
    anchor_terms = dedup_a[:10]

    if anchor_terms:
        for s in ranked:
            hay = f"{s.title} {s.snippet}"
            if any(t in hay for t in anchor_terms[:6]):
                s.score = float(s.score) + 0.08

    ranked.sort(key=lambda x: x.score, reverse=True)
    # 若存在非占位来源，则丢弃「实体页 + 定义证据不足」类片段
    non_stub = [x for x in ranked if not _is_entity_stub_source(x)]
    if non_stub:
        ranked = non_stub
    ranked = _filter_entities_when_report_docs_present(ranked)

    # 多样性：每文件最多 1 段；并按 stem 指纹去重（同源多路径只保留高分一条）
    out: List[WikiSource] = []
    seen_path_count: Dict[str, int] = {}
    seen_fp: set[str] = set()
    for item in ranked:
        cnt = seen_path_count.get(item.path, 0)
        if cnt >= 1:
            continue
        fp = _stem_fingerprint(Path(item.path).stem)
        if fp and fp in seen_fp:
            continue
        if fp:
            seen_fp.add(fp)
        out.append(item)
        seen_path_count[item.path] = cnt + 1
        if len(out) >= max(1, min(int(topk), 12)):
            break

    min_evidence = float(os.environ.get("SONA_WIKI_MIN_EVIDENCE_SCORE", "0.09") or "0.09")
    if out and max(s.score for s in out) < min_evidence:
        # 若存在 anchor_term 命中，则保留命中来源以避免“有相关但整体分数偏低”被清空
        if anchor_terms:
            kept: List[WikiSource] = []
            for s in out:
                hay = f"{s.title} {s.snippet}"
                if any(t in hay for t in anchor_terms[:6]):
                    kept.append(s)
            out = kept[: max(1, min(int(topk), 6))] if kept else []
        else:
            out = []

    # Attach lightweight debug meta via a side-channel object is not possible here; caller adds meta.
    # We stash on function attribute for tests/debug only (best-effort).
    retrieve_wiki_sources._last_debug = {  # type: ignore[attr-defined]
        "wiki_root": str(wiki_root) if wiki_root else "",
        "index_used": index_used,
        "index_hits": index_hits,
        "pages_scanned": pages_scanned,
        "domain": domain,
    }
    return out


def _compose_template_answer(query: str, sources: List[WikiSource], style_l: str, key_phrase: str) -> str:
    """无 LLM 或 LLM 失败时的拼接答案（与历史行为一致）。"""

    def _evidence_body(max_chars: int = 520) -> str:
        parts: List[str] = []
        for s in sources[:3]:
            frag = _prune_article_meta_sentences(_strip_yaml_frontmatter(s.snippet))
            if frag.strip():
                parts.append(frag.strip())
        merged = " ".join(parts)
        if len(merged) > max_chars:
            return merged[: max_chars - 1].rstrip() + "…"
        return merged

    if _meme_or_slang_intent(query):
        body = _evidence_body(520)
        fallback = _prune_article_meta_sentences(_strip_yaml_frontmatter(sources[0].snippet))[:360]
        if style_l == "teach":
            return (
                f"关于「{query.strip()}」（梗/说法，以检索摘录为准）：\n"
                f"1) 大致指什么；2) 出处与语境；3) 讨论里常被提到的争议点。\n"
                f"要点：{body if body else fallback}"
            )
        lead = f"{query.strip()}：" if query.strip() else ""
        return f"{lead}{body if body else fallback}"

    if _event_overview_intent(query):
        body = _evidence_body(520)
        if style_l == "teach":
            return (
                f"针对「{query.strip()}」，按检索材料整理事件梗概（细节以引用原文为准）：\n"
                f"1) 触发点；2) 升级过程；3) 当前争议焦点。\n"
                f"要点：{body if body else sources[0].snippet[:200]}"
            )
        lead = f"{query.strip()}：" if query.strip() else ""
        return f"{lead}{body if body else sources[0].snippet[:360]}"

    if style_l == "teach" and _definitional_intent(query):
        raw_snip = _prune_article_meta_sentences(_strip_yaml_frontmatter(sources[0].snippet))
        return (
            f"{query}（核心关键词：{key_phrase}）：可先把它理解为一个可验证的概念问题。\n"
            f"1) 先看定义与边界；2) 再看典型案例；3) 最后看实务判断要点。\n"
            f"基于当前检索证据，核心结论是：{raw_snip[:96]}..."
        )
    if style_l == "teach":
        raw_snip = _prune_article_meta_sentences(_strip_yaml_frontmatter(sources[0].snippet))
        return (
            f"{query}（核心关键词：{key_phrase}）：基于当前检索证据，建议按「证据—推论—可复核点」阅读：\n"
            f"{raw_snip[:220]}..."
        )
    raw_snip = _prune_article_meta_sentences(_strip_yaml_frontmatter(sources[0].snippet))
    return (
        f"{query}（核心关键词：{key_phrase}）：基于检索证据的简要回答："
        f"{raw_snip[:140]}..."
    )


def answer_wiki_query(
    query: str,
    *,
    topk: int = 6,
    style: str = "concise",
    project_root: Path | None = None,
) -> Dict[str, Any]:
    try:
        from utils.env_loader import get_env_config

        get_env_config()
    except Exception:
        pass
    root = (project_root or Path(__file__).resolve().parents[1]).resolve()
    normalized_query = _normalize_query(query)
    # Dynamic topk: event overview / entity-heavy queries benefit from a wider recall set.
    try:
        topk_in = int(topk or 6)
    except Exception:
        topk_in = 6
    q0 = re.sub(r"\s+", "", str(query or ""))
    dynamic_topk = topk_in
    if any(k in q0 for k in ("回应", "通报", "说明", "事件", "一事", "舆情")) or "12306" in q0:
        dynamic_topk = max(dynamic_topk, 9)
    sources = retrieve_wiki_sources(query, topk=dynamic_topk, project_root=project_root)
    if not sources:
        return {
            "answer": "当前证据不足，未在知识库中检索到足够相关片段。建议缩小问题范围或换更具体关键词继续追问。",
            "sources": [],
            "_wiki_meta": {
                "normalized_query": normalized_query,
                "fallback_used": True,
                "fallback_reason": "insufficient_evidence",
                "retrieved_count": 0,
                "retrieval_debug": getattr(retrieve_wiki_sources, "_last_debug", {}),
            },
        }

    key_phrase = (normalized_query or query).strip()
    style_l = str(style).strip().lower()
    source_dicts = [s.to_dict() for s in sources]
    _enrich_source_dicts_with_local_files(source_dicts, root)

    weibo_aux_text = ""
    weibo_meta: Dict[str, Any] = {"used": False}
    if _should_enrich_with_weibo(query):
        from workflow.wiki_rag import fetch_weibo_snippets_for_wiki

        topic = (key_phrase or query.strip())[:120]
        weibo_aux_text, weibo_meta = fetch_weibo_snippets_for_wiki(topic=topic, limit=8)

    domain_for_meta = _infer_domain_from_project_memory(query, project_root=root) or _infer_domain_for_wiki_query(query)
    meta: Dict[str, Any] = {
        "normalized_query": normalized_query,
        "fallback_used": False,
        "retrieved_count": len(sources),
        "rerank_enabled": True,
        "retrieval_debug": getattr(retrieve_wiki_sources, "_last_debug", {}),
        "domain": domain_for_meta,
        "weibo_aux": weibo_meta,
    }

    llm_used = False
    llm_error = ""
    answer = ""
    llm_payloads = _source_dicts_for_llm(sources, root)
    domain_for_llm = _infer_domain_from_project_memory(query, project_root=root) or _infer_domain_for_wiki_query(query)
    if domain_for_llm:
        llm_payloads = _cap_domain_seed_chars_for_llm(
            project_root=root,
            domain=domain_for_llm,
            llm_payloads=llm_payloads,
        )
    _enrich_source_dicts_with_local_files(llm_payloads, root)
    # #region agent log
    try:
        import json
        import time

        with open(
            "/Users/biaowenhuang/Documents/sona-master/.cursor/debug-cec23a.log",
            "a",
            encoding="utf-8",
        ) as _wf:
            _wf.write(
                json.dumps(
                    {
                        "sessionId": "cec23a",
                        "hypothesisId": "H_env",
                        "location": "wiki_cli.answer_wiki_query",
                        "message": "pre_llm",
                        "data": {
                            "sources_n": len(sources),
                            "wiki_use_llm_raw": os.environ.get("SONA_WIKI_USE_LLM", ""),
                            "llm_enabled": _wiki_llm_enabled(),
                            "first_llm_snip_len": len(str((llm_payloads[0] or {}).get("snippet") or ""))
                            if llm_payloads
                            else 0,
                        },
                        "timestamp": int(time.time() * 1000),
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
    except Exception:
        pass
    # #endregion
    if _wiki_llm_enabled():
        try:
            from workflow.wiki_rag import synthesize_wiki_rag_answer

            wb_for_llm = weibo_aux_text if isinstance(weibo_meta, dict) and weibo_meta.get("used") else None
            answer = synthesize_wiki_rag_answer(
                query=query,
                style=style_l,
                sources=llm_payloads,
                weibo_aux=wb_for_llm,
            )
            llm_used = True
        except Exception as exc:
            llm_error = str(exc)
            answer = _compose_template_answer(query, sources, style_l, key_phrase)
    else:
        answer = _compose_template_answer(query, sources, style_l, key_phrase)

    meta["llm_used"] = llm_used
    if llm_error:
        meta["llm_error"] = llm_error

    # 半自动回流：高价值回答自动入 output/_candidates，正式 output 仍由人工审核。
    try:
        score_meta = _score_wiki_answer_value(query=query, answer=answer, sources=sources)
        meta["value_score"] = score_meta
        wiki_root = _resolve_wiki_root(root)
        if (
            _output_candidate_enabled()
            and wiki_root is not None
            and bool(score_meta.get("is_high_value"))
        ):
            rec = _write_output_candidate(
                wiki_root=wiki_root,
                query=query,
                answer=answer,
                sources=source_dicts,
                score_meta=score_meta,
            )
            meta["output_candidate"] = {
                "created": True,
                "path": rec.get("candidate_path", ""),
                "score": rec.get("value_score", 0),
                "threshold": rec.get("value_threshold", 0),
            }
            # 样例记忆：把高价值候选沉淀为可检索样例，供回归评测/检索策略迭代。
            try:
                append_example_memory(
                    root,
                    record={
                        "type": "wiki_output_candidate",
                        "domain": domain_for_meta,
                        "query": str(query or "").strip(),
                        "query_hash": str(rec.get("query_hash") or ""),
                        "candidate_path": str(rec.get("candidate_path") or ""),
                        "value_score": int(rec.get("value_score") or 0),
                        "value_threshold": int(rec.get("value_threshold") or 0),
                        "sources": [
                            {"title": s.get("title", ""), "path": s.get("path", ""), "score": s.get("score", 0)}
                            for s in (source_dicts[:8] if isinstance(source_dicts, list) else [])
                            if isinstance(s, dict)
                        ],
                    },
                )
            except Exception:
                pass
        else:
            meta["output_candidate"] = {
                "created": False,
                "reason": "below_threshold_or_disabled",
                "score": int(score_meta.get("total", 0)),
                "threshold": int(score_meta.get("threshold", 0)),
            }
    except Exception as exc:
        meta["output_candidate"] = {
            "created": False,
            "error": str(exc),
        }

    # #region agent log
    try:
        import json
        import time

        with open(
            "/Users/biaowenhuang/Documents/sona-master/.cursor/debug-cec23a.log",
            "a",
            encoding="utf-8",
        ) as _wf:
            _wf.write(
                json.dumps(
                    {
                        "sessionId": "cec23a",
                        "hypothesisId": "H_post",
                        "location": "wiki_cli.answer_wiki_query",
                        "message": "post_llm",
                        "data": {
                            "llm_used": llm_used,
                            "answer_len": len(str(answer or "")),
                            "llm_error_prefix": (llm_error or "")[:800],
                        },
                        "timestamp": int(time.time() * 1000),
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
    except Exception:
        pass
    # #endregion

    return {
        "answer": answer,
        "sources": source_dicts,
        "_wiki_meta": meta,
    }

