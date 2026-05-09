"""Wiki / 本地知识库召回快照：写入过程目录，供报告与答辩举证。"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional


def _to_clean_str_list(value: Any, *, max_items: int = 12) -> List[str]:
    if value is None:
        return []
    if isinstance(value, list):
        raw_items = value
    elif isinstance(value, str):
        raw_items = [value]
    else:
        raw_items = [str(value)]
    result: List[str] = []
    seen: set[str] = set()
    for raw in raw_items:
        s = str(raw or "").strip()
        if not s or s in seen:
            continue
        seen.add(s)
        result.append(s)
        if len(result) >= max_items:
            break
    return result


def build_wiki_reference_query(user_query: str, search_plan: Dict[str, Any]) -> str:
    """与 event_analysis_pipeline._build_reference_query 对齐。"""
    uq = str(user_query or "").strip()
    words = _to_clean_str_list(search_plan.get("searchWords"), max_items=12)
    query_templates = _to_clean_str_list(search_plan.get("queryTemplates"), max_items=10)
    clean_words = [w for w in words if not str(w).startswith("#") and len(str(w)) <= 10]
    if len(clean_words) < 3:
        clean_words = words[:5]
    template_tokens: List[str] = []
    for t in query_templates[:4]:
        for token in re.findall(r"[\u4e00-\u9fffA-Za-z0-9#·_-]{2,10}", str(t)):
            if token not in template_tokens:
                template_tokens.append(token)
            if len(template_tokens) >= 4:
                break
        if len(template_tokens) >= 4:
            break
    parts: List[str] = []
    if uq:
        parts.append(uq)
    if clean_words:
        parts.append(" ".join(clean_words[:5]))
    if template_tokens:
        parts.append(" ".join(template_tokens[:3]))
    return " ".join([p for p in parts if p]).strip()


def _write_text_file(path: Path, text: str) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8", errors="replace") as f:
            f.write(str(text or ""))
    except Exception:
        pass


def write_wiki_kb_snapshot(
    *,
    process_dir: Path,
    user_query: str,
    search_plan: Dict[str, Any],
    project_root: Path,
    debug: bool = False,
    console: Optional[Any] = None,
) -> None:
    """
    调用 answer_wiki_query，写入 wiki_qa_snapshot.json 与 wiki_recall_preview.txt。
    CLI 与 pipeline 共用。
    """
    if debug and console is not None:
        try:
            console.print("[bold]Step10.2: wiki_snapshot[/bold]")
        except Exception:
            pass

    try:
        from workflow.wiki_cli import answer_wiki_query

        wiki_query = build_wiki_reference_query(user_query, search_plan)
        wiki_query = wiki_query or (
            f"{search_plan.get('eventIntroduction', user_query)}".strip() or str(user_query or "").strip()
        )
        wiki_out = answer_wiki_query(
            wiki_query,
            topk=6,
            style="teach",
            project_root=project_root,
        )
        weak_wiki = False
        if isinstance(wiki_out, dict):
            _src = wiki_out.get("sources")
            if not isinstance(_src, list) or len(_src) < 3:
                weak_wiki = True
        if weak_wiki:
            fallback_out = answer_wiki_query(
                str(user_query or "").strip(),
                topk=10,
                style="teach",
                project_root=project_root,
            )
            if isinstance(fallback_out, dict):
                fallback_sources = fallback_out.get("sources")
                old_sources = wiki_out.get("sources") if isinstance(wiki_out, dict) else []
                if isinstance(fallback_sources, list) and len(fallback_sources) > (
                    len(old_sources) if isinstance(old_sources, list) else 0
                ):
                    wiki_out = fallback_out

        wiki_snapshot_path = process_dir / "wiki_qa_snapshot.json"
        with open(wiki_snapshot_path, "w", encoding="utf-8", errors="replace") as f:
            json.dump(wiki_out, f, ensure_ascii=False, indent=2)

        sources = wiki_out.get("sources") if isinstance(wiki_out, dict) else []
        meta = wiki_out.get("_wiki_meta") if isinstance(wiki_out, dict) else {}
        retrieved_count = (
            int(meta.get("retrieved_count", 0))
            if isinstance(meta, dict)
            else (len(sources) if isinstance(sources, list) else 0)
        )
        llm_used = bool(meta.get("llm_used")) if isinstance(meta, dict) else False
        domain = str(meta.get("domain", "") or "").strip() if isinstance(meta, dict) else ""
        preview_lines = [
            f"wiki_query: {wiki_query[:160]}",
            f"domain_routing: {domain or '（未命中专题路由）'}",
            f"retrieved_count: {retrieved_count}",
            f"llm_used: {llm_used}",
        ]
        if isinstance(sources, list) and sources:
            preview_lines.append("top_sources:")
            for row in sources[:8]:
                if not isinstance(row, dict):
                    continue
                title = str(row.get("title", "") or "").strip()
                path_s = str(row.get("path", "") or "").strip()
                score = row.get("score", 0)
                preview_lines.append(f"- {title} (score={score}) | {path_s}")
        _write_text_file(process_dir / "wiki_recall_preview.txt", "\n".join(preview_lines))

        if debug and console is not None:
            try:
                console.print("[dim]Wiki KB 召回预览（用于核验报告引用）[/dim]")
                console.print(f"[dim]{chr(10).join(preview_lines)}[/dim]")
            except Exception:
                pass
    except Exception as e:
        try:
            from workflow.telemetry import append_ndjson_log

            log_path = os.environ.get("SONA_DEBUG_LOG_PATH", str(project_root / ".cursor" / "debug.log"))
            append_ndjson_log(
                log_path=log_path,
                run_id="event_analysis_wiki_kb",
                hypothesis_id="H44_wiki_snapshot_optional",
                location="workflow/wiki_kb_snapshot.write_wiki_kb_snapshot",
                message="Wiki KB 召回快照构建失败，已跳过",
                data={"error": str(e)},
            )
        except Exception:
            pass
