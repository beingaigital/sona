"""CLI bridge for /wiki command."""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv
from rich.prompt import Prompt

from cli.display import console
from tools.oprag import build_reference_wiki
from utils.env_loader import get_env_config, reload_env_config
from utils.path import get_opinion_analysis_kb_root
from workflow.wiki_cli import answer_wiki_query, _wiki_llm_enabled


def _slugify_filename(text: str, max_len: int = 72) -> str:
    s = re.sub(r"[^\w\u4e00-\u9fff-]+", "_", str(text or "").strip()).strip("_")
    if not s:
        s = "approved_output"
    return s[:max_len]


def _wiki_root(project_root: Path) -> Path:
    return get_opinion_analysis_kb_root(project_root) / "references" / "wiki"


def _candidate_dir(project_root: Path) -> Path:
    return _wiki_root(project_root) / "output" / "_candidates"


def _list_candidate_files(project_root: Path) -> List[Path]:
    cdir = _candidate_dir(project_root)
    if not cdir.is_dir():
        return []
    files = [p for p in cdir.glob("*.md") if p.is_file()]
    files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return files


def _pick_candidate(project_root: Path, selector: str | None = None) -> Optional[Path]:
    files = _list_candidate_files(project_root)
    if not files:
        return None
    key = str(selector or "").strip().lower()
    if not key:
        return files[0]
    # 先精确后模糊
    for p in files:
        if p.name.lower() == key or p.stem.lower() == key:
            return p
    for p in files:
        if key in p.name.lower():
            return p
    return None


def _extract_question_from_candidate(md_text: str) -> str:
    marker = "## 原始问题"
    idx = md_text.find(marker)
    if idx < 0:
        return ""
    rest = md_text[idx + len(marker) :]
    lines = [ln.strip() for ln in rest.splitlines() if ln.strip()]
    if not lines:
        return ""
    return lines[0][:120]


def _approve_candidate_to_output(project_root: Path, candidate_file: Path) -> Dict[str, Any]:
    wiki_root = _wiki_root(project_root)
    out_dir = wiki_root / "output"
    out_dir.mkdir(parents=True, exist_ok=True)
    raw = candidate_file.read_text(encoding="utf-8", errors="replace")
    question = _extract_question_from_candidate(raw)
    stem = _slugify_filename(question or candidate_file.stem.replace("候选沉淀_", ""))
    target = out_dir / f"{stem}_回答沉淀.md"
    if target.exists():
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        target = out_dir / f"{stem}_回答沉淀_{ts}.md"

    rows: List[str] = []
    rows.append("---")
    rows.append(f"title: {question or '高价值回答沉淀'}")
    rows.append(f"approved_at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    rows.append("approved_by: human")
    rel_candidate = candidate_file.relative_to(wiki_root).as_posix()
    rows.append(f"source_candidate: {rel_candidate}")
    rows.append("status: approved")
    rows.append("---")
    rows.append("")
    rows.append(raw.strip())
    rows.append("")
    target.write_text("\n".join(rows), encoding="utf-8")

    # 触发增量编译，刷新 index/log/entities/concepts
    compile_result = build_reference_wiki.invoke({"limit": 120, "force": False})
    return {
        "ok": True,
        "candidate": rel_candidate,
        "output_path": target.relative_to(wiki_root).as_posix(),
        "compile_result": compile_result,
    }


def run_wiki_command(raw_query: str | None = None) -> None:
    query = str(raw_query or "").strip()
    if not query:
        query = Prompt.ask("请输入 wiki 问题").strip()
    if not query:
        console.print("[yellow]/wiki 需要问题文本，例如：/wiki 什么是舆情反转？[/yellow]")
        return

    root = Path(__file__).resolve().parents[1]
    env_file = root / ".env"
    if env_file.is_file():
        load_dotenv(env_file, override=True)
    reload_env_config()
    get_env_config()
    result = answer_wiki_query(query, topk=6, style="teach", project_root=Path(__file__).resolve().parents[1])
    meta = result.get("_wiki_meta") if isinstance(result.get("_wiki_meta"), dict) else {}
    if meta.get("llm_used"):
        console.print("[green]✓[/green] [bold]已使用 LLM（tools profile）基于检索片段生成回答[/bold]")
    elif result.get("sources"):
        hint = str(meta.get("llm_error") or "").strip()
        if hint:
            console.print(
                "[red][bold]✗ LLM 调用失败，已回退为检索模板拼接（非模型生成）[/bold][/red]\n"
                f"[yellow]{hint}[/yellow]"
            )
        elif not _wiki_llm_enabled():
            console.print(
                "[yellow]（未使用 LLM：`SONA_WIKI_USE_LLM` 为 0/false；"
                "在 .env 中设为 `1` 或 `true` 可启用 RAG 合成答案）[/yellow]"
            )
        else:
            console.print("[yellow]（未使用 LLM：未知原因，已用检索模板）[/yellow]")
    if isinstance(meta.get("weibo_aux"), dict) and meta["weibo_aux"].get("used"):
        console.print("[dim]（已附加微博智搜辅助线索）[/dim]")
    elif isinstance(meta.get("weibo_aux"), dict) and str(meta["weibo_aux"].get("error") or "").strip():
        console.print(f"[dim]（微博智搜未取到片段：{meta['weibo_aux'].get('error')}）[/dim]")
    score_meta = meta.get("value_score") if isinstance(meta.get("value_score"), dict) else {}
    if score_meta:
        total = score_meta.get("total", 0)
        threshold = score_meta.get("threshold", 0)
        is_high = bool(score_meta.get("is_high_value"))
        badge = "[green]高价值[/green]" if is_high else "[dim]普通[/dim]"
        console.print(f"[dim]（回答价值评分：{total}/{threshold}，判定：{badge}）[/dim]")
    candidate_meta = meta.get("output_candidate") if isinstance(meta.get("output_candidate"), dict) else {}
    if candidate_meta.get("created"):
        console.print(
            f"[green]✓[/green] [bold]已回流候选[/bold]："
            f"[cyan]{candidate_meta.get('path', '')}[/cyan]"
        )
    elif str(candidate_meta.get("error") or "").strip():
        console.print(f"[yellow]（候选回流失败：{candidate_meta.get('error')}）[/yellow]")
    console.print("\n[bold]Wiki Answer[/bold]")
    console.print(result.get("answer", ""))
    sources = result.get("sources") if isinstance(result.get("sources"), list) else []
    if sources:
        console.print("\n[bold]Sources（检索摘录；本地原文路径如下）[/bold]")
        for i, item in enumerate(sources[:6], 1):
            if not isinstance(item, dict):
                continue
            title = str(item.get("title", ""))
            path = str(item.get("path", ""))
            snippet = str(item.get("snippet", ""))
            score = item.get("score", 0)
            abs_path = str(item.get("abs_path") or "").strip()
            file_uri = str(item.get("file_uri") or "").strip()
            console.print(f"{i}. {title} [dim](score={score})[/dim]")
            console.print(f"   相对路径: {path}")
            if abs_path:
                console.print(f"   [cyan]本地文件: {abs_path}[/cyan]")
            if file_uri:
                console.print(f"   [dim]file URI: {file_uri}[/dim]")
            console.print(f"   摘录: {snippet}")
    else:
        console.print("[yellow]未检索到来源片段。[/yellow]")


def run_wiki_approve_command(raw_selector: str | None = None) -> None:
    """
    审核并回流候选沉淀到正式 output。

    用法：
    - /wiki-approve            -> 默认审核最新候选
    - /wiki-approve 关键词      -> 按文件名模糊匹配候选
    """
    root = Path(__file__).resolve().parents[1]
    env_file = root / ".env"
    if env_file.is_file():
        load_dotenv(env_file, override=True)
    reload_env_config()
    get_env_config()

    selector = str(raw_selector or "").strip()
    candidate = _pick_candidate(root, selector=selector if selector else None)
    if candidate is None:
        console.print("[yellow]未找到可审批候选。请先通过 /wiki 生成高价值候选。[/yellow]")
        return

    console.print(f"[cyan]待审批候选: {candidate.name}[/cyan]")
    result = _approve_candidate_to_output(root, candidate)
    console.print("[green]✓ 已完成候选审批并回流到正式 output[/green]")
    console.print(f"[green]  候选: {result.get('candidate','')}[/green]")
    console.print(f"[green]  正式页: {result.get('output_path','')}[/green]")

