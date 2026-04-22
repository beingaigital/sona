"""Wiki + RAG：用本地检索片段（及可选微博智搜）作为上下文，经 LLM 合成回答。

环境变量：
- ``SONA_WIKI_USE_LLM``：``1/true``（默认）时尝试 LLM 合成；失败则回退模板答案。
- ``SONA_WIKI_WEIBO_AUX``：``1/true``（默认）时在事件类问题下附加微博智搜摘录（由调用方决定是否触发）。
- 测试/CI 建议在 ``conftest`` 中 ``setdefault`` 为 ``0`` 以保持确定性。
"""

from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, List, Optional

from langchain_core.messages import HumanMessage, SystemMessage

from model.factory import ModelFactory


def _wiki_debug_log(payload: Dict[str, Any]) -> None:
    try:
        line = dict(payload)
        line.setdefault("sessionId", "cec23a")
        line.setdefault("timestamp", int(time.time() * 1000))
        with open(
            "/Users/biaowenhuang/Documents/sona-master/.cursor/debug-cec23a.log",
            "a",
            encoding="utf-8",
        ) as f:
            f.write(json.dumps(line, ensure_ascii=False) + "\n")
    except Exception:
        pass


def _extract_text_from_ai_message(resp: Any) -> str:
    """兼容 content 为 str / list[dict]（多模态块）等形态，避免误判为空。"""
    c = getattr(resp, "content", None)
    if c is None:
        c = getattr(resp, "text", None)
    if isinstance(c, str) and c.strip():
        return c.strip()
    if isinstance(c, list):
        parts: List[str] = []
        for block in c:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                t = block.get("text")
                if isinstance(t, str):
                    parts.append(t)
                elif isinstance(block.get("content"), str):
                    parts.append(block["content"])
        joined = "".join(parts).strip()
        if joined:
            return joined
    if c is not None:
        s = str(c).strip()
        if s and s not in ("[]", "{}"):
            return s
    return ""


def _max_llm_context_chars() -> int:
    raw = os.environ.get("SONA_WIKI_LLM_MAX_CONTEXT_CHARS", "48000").strip()
    try:
        n = int(raw)
    except Exception:
        n = 48000
    return max(4000, min(n, 200_000))


def _cap_source_snippets_for_api(sources: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """避免多文件长摘录拼进单次请求后触发上限或异常空响应。"""
    max_total = _max_llm_context_chars()
    n = len([s for s in sources if isinstance(s, dict)])
    if n == 0:
        return sources
    per = max(800, max_total // n)
    out: List[Dict[str, Any]] = []
    for s in sources:
        if not isinstance(s, dict):
            continue
        d = dict(s)
        sn = str(d.get("snippet") or "")
        if len(sn) > per:
            d["snippet"] = sn[: per - 1].rstrip() + "…"
        out.append(d)
    return out


_WIKI_RAG_SYSTEM = """你是舆情知识库的问答助手。用户问题与「本地知识库摘录」及可选「微博智搜摘录」将一并给出。

硬性规则：
1) 回答必须主要依据本地知识库摘录；事实性陈述应能在摘录中找到依据，不要编造摘录中未出现的具体日期、机构表态或数据。
2) 当摘录中已有报告、综述、要点列表等实质性内容时：先给出结构化总结与结论，满足用户问题；仅在摘录确实无法覆盖问题的核心时，再用一两句话说明「知识库未覆盖的方面」或需查阅原文之处。不要把「证据不足」当作全文主基调。
3) 若某条摘录来自实体词条且仅含「定义：证据不足」类占位句，而同主题在其它摘录中有详细段落，以详细摘录为准，勿重复或放大占位句。
4) 若提供微博智搜摘录：仅作为社会化讨论的辅助线索，语气上标注为「微博侧可见讨论」或类似，不得将其当作已核验的单一事实源。
5) 根据用户问题的风格（teach / concise）组织篇幅：teach 可分层小标题与要点；concise 控制在约 400 字内。
6) 使用简体中文，不要使用 YAML front matter，不要复述「摘录来源路径」等技术细节。"""


def fetch_weibo_snippets_for_wiki(*, topic: str, limit: int = 8) -> tuple[str, Dict[str, Any]]:
    """
    调用 ``weibo_aisearch`` 抓取微博智搜片段，拼成可注入 RAG 的纯文本。

    Returns:
        (aux_text, meta) ；无可用片段时 aux_text 为空字符串，meta 含 error 等字段。
    """
    topic = str(topic or "").strip()[:120] or "舆情"
    lim = max(1, min(int(limit or 8), 20))
    meta: Dict[str, Any] = {"used": False, "topic": topic, "limit": lim}

    try:
        from tools.weibo_aisearch import weibo_aisearch

        raw = weibo_aisearch.invoke({"query": topic, "limit": lim})
    except Exception as exc:
        meta["error"] = f"invoke_failed:{exc}"
        return "", meta

    try:
        obj = json.loads(raw) if isinstance(raw, str) else raw
    except Exception:
        meta["error"] = "json_parse_failed"
        return "", meta

    if not isinstance(obj, dict):
        meta["error"] = "invalid_payload"
        return "", meta

    results = obj.get("results")
    snippets: List[str] = []
    if isinstance(results, list):
        for it in results[:lim]:
            if isinstance(it, dict):
                sn = str(it.get("snippet") or "").strip()
                if len(sn) >= 8:
                    snippets.append(sn[:400])

    if not snippets:
        err = str(obj.get("error") or "").strip() or "no_snippets"
        meta["error"] = err
        meta["url"] = str(obj.get("url") or "")
        return "", meta

    lines = "\n".join(f"- {s}" for s in snippets)
    meta["used"] = True
    meta["count"] = len(snippets)
    meta["url"] = str(obj.get("url") or "")
    max_chars = int(os.environ.get("SONA_WIKI_WEIBO_MAX_CHARS", "3500") or "3500")
    text = lines[: max(500, max_chars)]
    return text, meta


def synthesize_wiki_rag_answer(
    *,
    query: str,
    style: str,
    sources: List[Dict[str, Any]],
    weibo_aux: Optional[str] = None,
) -> str:
    """
    基于检索到的 ``sources``（title/path/snippet）与可选 ``weibo_aux`` 调用 LLM 生成最终回答。
    """
    style_l = str(style or "concise").strip().lower()
    style_hint = (
        "用户期望「教学型」分层回答（定义—要点—可复核处）。"
        if style_l == "teach"
        else "用户期望「简洁」回答，优先结论与要点列表，控制篇幅。"
    )

    sources = _cap_source_snippets_for_api([s for s in sources if isinstance(s, dict)])

    blocks: List[str] = []
    for i, s in enumerate(sources[:10], 1):
        if not isinstance(s, dict):
            continue
        title = str(s.get("title") or "").strip()
        snip = str(s.get("snippet") or "").strip()
        path = str(s.get("path") or "").strip()
        blocks.append(f"### 摘录[{i}] {title}\n{snip}\n（内部路径：{path}，回答中勿逐字复述路径）")

    ctx = "\n\n".join(blocks) if blocks else "（无可用摘录）"
    wb_block = ""
    if weibo_aux and weibo_aux.strip():
        wb_block = (
            "\n\n### 微博智搜可见片段（辅助线索，非单一事实核验源）\n"
            f"{weibo_aux.strip()[:4000]}"
        )

    human = (
        f"{style_hint}\n\n"
        f"用户问题：\n{query.strip()}\n\n"
        f"本地知识库摘录：\n{ctx}"
        f"{wb_block}"
    )

    temp_raw = os.environ.get("SONA_WIKI_LLM_TEMPERATURE", "0.25").strip()
    try:
        temperature = float(temp_raw)
    except Exception:
        temperature = 0.25

    # streaming=False：避免部分兼容接口在 invoke 时与流式默认行为不一致
    model = ModelFactory.create(
        profile="tools",
        temperature=max(0.0, min(temperature, 1.0)),
        streaming=False,
    )
    messages = [
        SystemMessage(content=_WIKI_RAG_SYSTEM),
        HumanMessage(content=human),
    ]
    try:
        resp = model.invoke(messages)
    except Exception as exc:
        _wiki_debug_log(
            {
                "hypothesisId": "H_invoke",
                "location": "wiki_rag.synthesize_wiki_rag_invoke",
                "message": "invoke_failed",
                "data": {"error": str(exc)[:1200]},
            }
        )
        raise

    text = _extract_text_from_ai_message(resp)
    raw_type = type(getattr(resp, "content", None)).__name__
    _wiki_debug_log(
        {
            "hypothesisId": "H_content",
            "location": "wiki_rag.synthesize_wiki_rag_answer",
            "message": "post_invoke",
            "data": {
                "content_type": raw_type,
                "text_len": len(text),
                "human_chars": len(human),
            },
        }
    )
    if not text:
        _wiki_debug_log(
            {
                "hypothesisId": "H_content",
                "location": "wiki_rag.synthesize_wiki_rag_answer",
                "message": "empty_after_extract",
                "data": {
                    "resp_type": type(resp).__name__,
                    "raw_content_preview": repr(getattr(resp, "content", None))[:400],
                },
            }
        )
        raise ValueError("empty_llm_response")
    return text
