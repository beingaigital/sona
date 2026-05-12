"""从 sandbox 事件分析产物生成 Wiki 标准案例页（任务 15：案例库）。

在 ``interpretation.json``、``dataset_summary``、``report_meta``、``user_portrait`` 等
过程文件基础上构建可与 HTML 报告对齐的案例正文，供 ``cases/*.md`` 长期沉淀与 wiki 召回。
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

import yaml

from utils.path import get_opinion_analysis_kb_root, get_project_root


def wiki_cases_dir(project_root: Path | None = None) -> Path:
    """Wiki 下案例目录：``opinion_analysis_kb/references/wiki/cases``。"""
    root = project_root if project_root is not None else get_project_root()
    return get_opinion_analysis_kb_root(root) / "references" / "wiki" / "cases"


def _rel_posix(project_root: Path, path: Path | str) -> str:
    p = Path(path).expanduser()
    try:
        return p.resolve().relative_to(project_root.resolve()).as_posix()
    except Exception:
        return str(p).replace("\\", "/")


def _read_json(path: Path) -> Optional[Dict[str, Any]]:
    if not path.is_file():
        return None
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            obj = json.load(f)
        return obj if isinstance(obj, dict) else None
    except Exception:
        return None


def _pick_latest_json(process_dir: Path, stem: str) -> Optional[Path]:
    """优先 ``stem.json``，否则取 ``stem_*.json`` 中修改时间最新者。"""
    direct = process_dir / f"{stem}.json"
    if direct.is_file():
        return direct
    candidates = sorted(process_dir.glob(f"{stem}_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    return candidates[0] if candidates else None


def _interpretation_core(raw: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not isinstance(raw, dict):
        return {}
    inner = raw.get("interpretation")
    return inner if isinstance(inner, dict) else {}


def _timeline_entries(timeline_json: Optional[Dict[str, Any]], max_items: int = 14) -> List[str]:
    if not isinstance(timeline_json, dict):
        return []
    raw = timeline_json.get("timeline")
    if not isinstance(raw, list):
        return []
    out: List[str] = []
    for item in raw[: max_items * 2]:
        if isinstance(item, dict):
            t = str(item.get("time") or item.get("date") or item.get("timestamp") or "").strip()
            s = str(
                item.get("summary")
                or item.get("text")
                or item.get("title")
                or item.get("content")
                or ""
            ).strip()
            line = f"{t}: {s}".strip(": ").strip()
            if line:
                out.append(line[:500])
        elif isinstance(item, str) and item.strip():
            out.append(item.strip()[:500])
        if len(out) >= max_items:
            break
    return out


def _merge_unique_lines(primary: List[str], secondary: List[str], *, max_total: int) -> List[str]:
    seen: set[str] = set()
    out: List[str] = []
    for src in (primary, secondary):
        for line in src:
            k = (line or "").strip()
            if not k:
                continue
            key = k[:160]
            if key in seen:
                continue
            seen.add(key)
            out.append(k[:800])
            if len(out) >= max_total:
                return out
    return out


def _risk_from_sentiment(sentiment_json: Optional[Dict[str, Any]], max_items: int = 10) -> List[str]:
    if not isinstance(sentiment_json, dict):
        return []
    neg = sentiment_json.get("negative_summary")
    if not isinstance(neg, list):
        return []
    return [str(x).strip() for x in neg if str(x).strip()][:max_items]


def _is_graph_noise_row(it: Any) -> bool:
    if isinstance(it, dict) and set(it.keys()) <= {"error"}:
        return True
    if isinstance(it, dict) and str(it.get("error", "")).strip() and len(it) <= 2:
        return True
    return False


def _risk_from_graph(graph: Optional[Dict[str, Any]], max_items: int = 10) -> List[str]:
    if not isinstance(graph, dict):
        return []
    out: List[str] = []

    def _consume_list(blk: List[Any]) -> None:
        nonlocal out
        for it in blk[: max_items * 3]:
            if len(out) >= max_items:
                return
            if _is_graph_noise_row(it):
                continue
            if isinstance(it, dict):
                title = str(it.get("title") or it.get("name") or "").strip()
                summ = str(it.get("summary") or it.get("snippet") or it.get("description") or "").strip()
                if title or summ:
                    out.append(f"{title}: {summ}".strip(": ").strip()[:500])
            elif isinstance(it, str) and it.strip():
                out.append(it.strip()[:500])

    sim = graph.get("similar_cases")
    if isinstance(sim, list):
        _consume_list(sim)
    elif isinstance(sim, dict):
        for key in ("cases", "results", "items", "data"):
            blk = sim.get(key)
            if isinstance(blk, list):
                _consume_list(blk)
    for block_name in ("theories", "indicators"):
        blk = graph.get(block_name)
        if isinstance(blk, list):
            for pack in blk[:6]:
                if not isinstance(pack, dict):
                    continue
                results = pack.get("results")
                if isinstance(results, list):
                    _consume_list(results)
    return out[:max_items]


def _tactics_from_search_plan(search_plan: Dict[str, Any]) -> List[str]:
    chk = search_plan.get("verificationChecklist")
    if isinstance(chk, list):
        return [str(x).strip() for x in chk if str(x).strip()][:12]
    return []


def _tactics_from_reference(ref: Optional[Dict[str, Any]], max_items: int = 12) -> List[str]:
    if not isinstance(ref, dict):
        return []
    items = ref.get("items") or ref.get("results") or ref.get("snippets")
    if not isinstance(items, list):
        return []
    out: List[str] = []
    for it in items[: max_items * 2]:
        if isinstance(it, dict):
            title = str(it.get("title") or it.get("source") or "").strip()
            sn = str(it.get("snippet") or it.get("text") or "").strip()
            sn = re.sub(r"(?s)^---\s*.*?\s*---\s*", "", sn).strip()
            line = f"{title} — {sn}".strip("— ").strip()
            if line:
                out.append(line[:520])
        if len(out) >= max_items:
            break
    return out


def _theory_tactics(inter: Dict[str, Any]) -> List[str]:
    names = inter.get("theory_names")
    if not isinstance(names, list):
        return []
    out: List[str] = []
    for n in names[:8]:
        s = str(n).strip()
        if s:
            out.append(f"理论复盘：结合「{s}」对照本事件阶段与议程")
    return out


def _dataset_evidence_lines(ds_raw: Optional[Dict[str, Any]], project_root: Path) -> List[str]:
    if not isinstance(ds_raw, dict):
        return []
    inner = ds_raw.get("dataset_summary")
    if not isinstance(inner, dict):
        return []
    lines: List[str] = []
    rc = inner.get("row_count")
    if rc is not None:
        lines.append(f"【数据样本】结构化帖文约 {int(rc)} 条（dataset_summary）")
    tc = inner.get("time_coverage")
    if isinstance(tc, dict):
        mn = str(tc.get("min_time") or "").strip()
        mx = str(tc.get("max_time") or "").strip()
        if mn or mx:
            lines.append(f"【时间覆盖】{mn} — {mx}")
    sp = str(ds_raw.get("save_path") or "").strip()
    if sp:
        lines.append(f"【数据文件】{_rel_posix(project_root, sp)}")
    return lines


def _report_meta_lines(meta: Optional[Dict[str, Any]]) -> List[str]:
    if not isinstance(meta, dict):
        return []
    lines: List[str] = []
    secs = meta.get("sections")
    if isinstance(secs, list) and secs:
        lines.append("【报告章节】HTML 报告包含: " + "、".join(str(x) for x in secs[:12]))
    th = meta.get("theory_frameworks")
    if isinstance(th, list) and th:
        lines.append("【理论框架】报告元数据记载: " + "、".join(str(x) for x in th[:10]))
    if meta.get("has_public_opinion_patterns"):
        lines.append(
            f"【舆情模式】报告含模式分析（要点数 meta≈{meta.get('pattern_points_count', 0)}）"
        )
    if meta.get("has_analogous_cases"):
        lines.append(f"【类案】报告含类比案例（条数≈{meta.get('analogous_cases_count', 0)}）")
    rc = meta.get("references_count")
    if rc is not None:
        lines.append(f"【参考文献条数】{int(rc)}")
    return lines


def _user_portrait_actor_lines(portrait: Optional[Dict[str, Any]], max_items: int = 10) -> List[str]:
    if not isinstance(portrait, dict):
        return []
    tops = portrait.get("top_authors")
    if not isinstance(tops, list):
        return []
    out: List[str] = []
    for it in tops[:max_items]:
        if isinstance(it, dict):
            name = str(it.get("name") or "").strip()
            cnt = it.get("count")
            if name:
                out.append(f"{name}（声量计数≈{cnt}）" if cnt is not None else name)
        elif isinstance(it, str) and it.strip():
            out.append(it.strip())
    return out


def _wiki_qa_excerpt(wiki: Optional[Dict[str, Any]], max_chars: int = 600) -> str:
    if not isinstance(wiki, dict):
        return ""
    ans = str(wiki.get("answer") or "").strip()
    trivial = "证据不足" in ans and len(ans) < 80
    if trivial and not wiki.get("sources"):
        return ""
    if len(ans) > max_chars:
        return ans[: max_chars - 1].rstrip() + "…"
    return ans


def _expert_note_lines(path: Path) -> List[str]:
    raw = _read_json(path)
    if not isinstance(raw, dict):
        return []
    note = str(raw.get("expert_note") or "").strip()
    if not note:
        return []
    return [f"【专家补充】{note[:900]}" + ("…" if len(note) > 900 else "")]


def _judgement_lines(path: Path) -> List[str]:
    raw = _read_json(path)
    if not isinstance(raw, dict):
        return []
    parts: List[str] = []
    for key in ("user_judgement", "judgement", "focus_points", "notes"):
        v = raw.get(key)
        if isinstance(v, str) and v.strip():
            parts.append(v.strip()[:600])
        elif isinstance(v, list):
            parts.extend(str(x).strip() for x in v if str(x).strip())
    if not parts:
        return []
    return ["【用户研判输入】" + "；".join(parts[:4])[:900]]


def _channel_one_liner(ch: Optional[Dict[str, Any]]) -> str:
    if not isinstance(ch, dict):
        return ""
    dist = ch.get("distribution") or ch.get("channel_counts") or ch.get("counts")
    if isinstance(dist, dict) and dist:
        items = sorted(((str(k), int(v)) for k, v in dist.items() if int(v or 0) > 0), key=lambda x: -x[1])[:6]
        if items:
            return "【平台分布】" + "；".join(f"{k}:{v}" for k, v in items)
    return ""


def _evidence_paths(
    project_root: Path,
    process_dir: Path,
    result_dir: Path,
    *,
    html_report_path: str,
    extras: Sequence[str],
) -> List[str]:
    paths: List[str] = []
    for p in (process_dir, result_dir):
        if p.is_dir():
            paths.append(_rel_posix(project_root, p))
    for name in extras:
        if name:
            paths.append(name)
    if html_report_path:
        paths.append(_rel_posix(project_root, html_report_path))
    seen: set[str] = set()
    uniq: List[str] = []
    for x in paths:
        if x in seen:
            continue
        seen.add(x)
        uniq.append(x)
    return uniq[:48]


def _default_actors(event_intro: str) -> List[str]:
    text = str(event_intro or "").strip()
    if not text:
        return ["公众", "媒体", "平台", "涉事主体（待核验）"]
    parts = re.split(r"[，,、;；|｜/]", text)
    actors = [p.strip() for p in parts if 2 <= len(p.strip()) <= 40][:8]
    return actors or ["公众", "媒体", "涉事方"]


def append_case_to_wiki_index(wiki_root: Path, rel_under_wiki: str, preview: str) -> bool:
    """在 ``index.md`` 的「页面目录」下插入一条案例链接（若已存在同路径则跳过）。"""
    index_path = wiki_root / "index.md"
    if not index_path.is_file():
        return False
    needle = f"]({rel_under_wiki})"
    try:
        text = index_path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return False
    if needle in text:
        return False
    preview_clean = re.sub(r"\s+", " ", str(preview or "").strip())[:160]
    line = f"- [{rel_under_wiki}]({rel_under_wiki}) - {preview_clean}\n"
    marker = "## 页面目录\n"
    if marker in text:
        head, tail = text.split(marker, 1)
        new_text = head + marker + line + tail
    else:
        new_text = text.rstrip() + "\n\n" + marker.strip() + "\n" + line
    try:
        index_path.write_text(new_text, encoding="utf-8")
        return True
    except Exception:
        return False


def _infer_domain_label(project_root: Path, user_query: str, event_intro: str) -> str:
    try:
        from workflow.wiki_cli import _infer_domain_for_wiki_query

        blob = f"{user_query}\n{event_intro}".strip()
        d = str(_infer_domain_for_wiki_query(blob) or "").strip()
        return d or "general"
    except Exception:
        return "general"


def _latest_report_meta_path(result_dir: Path) -> Optional[Path]:
    if not result_dir.is_dir():
        return None
    metas = sorted(result_dir.glob("report_meta_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    if metas:
        return metas[0]
    legacy = result_dir / "report_meta.json"
    return legacy if legacy.is_file() else None


def _choose_title(event_intro: str, user_query: str, narrative: str) -> str:
    narrative = (narrative or "").strip()
    if len(narrative) >= 48:
        one = re.split(r"[。！？\n]", narrative, maxsplit=1)[0].strip()
        if len(one) >= 12:
            return (one[:80] + ("…" if len(one) > 80 else ""))[:90]
    base = str(event_intro or "").strip() or str(user_query or "").strip()
    return (base[:72] + ("…" if len(base) > 72 else "")) if base else "舆情事件案例"


def write_event_analysis_case_wiki(
    *,
    project_root: Path,
    task_id: str,
    process_dir: Path,
    search_plan: Dict[str, Any],
    user_query: str,
    html_report_path: str,
    timeline_json: Optional[Dict[str, Any]] = None,
    sentiment_json: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    聚合 sandbox 过程文件与检索计划，写入标准 ``case_*.md``。

    优先吸收 ``interpretation.json``、``dataset_summary``、``report_meta``、
    ``user_portrait`` 等与 HTML 报告同源的中间结论，使案例页可作为知识库复用。
    """
    result_dir = project_root / "sandbox" / task_id / "结果文件"

    interp_path = _pick_latest_json(process_dir, "interpretation")
    interp_raw = _read_json(interp_path) if interp_path else None
    inter = _interpretation_core(interp_raw)

    ds_path = _pick_latest_json(process_dir, "dataset_summary")
    ds_raw = _read_json(ds_path) if ds_path else None

    graph = _read_json(process_dir / "graph_rag_enrichment.json")
    ref_ins = _read_json(process_dir / "reference_insights.json")
    portrait = _read_json(process_dir / "user_portrait.json")
    wiki_snap = _read_json(process_dir / "wiki_qa_snapshot.json")
    channel = _read_json(process_dir / "channel_distribution.json")

    meta_path = _latest_report_meta_path(result_dir)
    report_meta = _read_json(meta_path) if meta_path else None

    narrative = str(inter.get("narrative_summary") or "").strip()
    event_intro = str(search_plan.get("eventIntroduction") or user_query or "").strip()
    title_base = _choose_title(event_intro, user_query, narrative)

    slug = re.sub(r"[^\w\u4e00-\u9fff-]+", "_", title_base).strip("_")[:48] or "event"
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_tid = re.sub(r"[^\w-]+", "", str(task_id))[:12] or "task"
    filename = f"case_{safe_tid}_{slug[:32]}_{ts}.md"
    if len(filename) > 180:
        filename = f"case_{safe_tid}_{ts}.md"

    cases_dir = wiki_cases_dir(project_root)
    cases_dir.mkdir(parents=True, exist_ok=True)
    out_path = cases_dir / filename

    domain_inter = str(inter.get("domain") or "").strip()
    domain_wiki = _infer_domain_label(project_root, user_query, event_intro)
    domain = domain_inter or domain_wiki
    etype = str(inter.get("event_type") or "").strip()
    stage = str(inter.get("stage") or "").strip()

    actors = _merge_unique_lines(
        _default_actors(event_intro),
        _user_portrait_actor_lines(portrait, max_items=8),
        max_total=14,
    )
    if etype:
        actors = _merge_unique_lines([f"事件类型锚点：{etype}"], actors, max_total=14)

    tl_tool = _timeline_entries(timeline_json, max_items=16)
    ke = inter.get("key_events")
    key_events = [str(x).strip() for x in ke if str(x).strip()][:14] if isinstance(ke, list) else []
    timeline = _merge_unique_lines(key_events, tl_tool, max_total=20)
    if not timeline:
        timeline = [f"分析时间窗：{search_plan.get('timeRange') or '（见采集配置）'}"]

    kr = inter.get("key_risks")
    interp_risks = [str(x).strip() for x in kr if str(x).strip()][:12] if isinstance(kr, list) else []
    risk_patterns = _merge_unique_lines(
        interp_risks,
        _risk_from_sentiment(sentiment_json) + _risk_from_graph(graph, max_items=8),
        max_total=20,
    )
    if not risk_patterns:
        risk_patterns = ["（风险模式待结合数据进一步核验）"]

    response_tactics = _merge_unique_lines(
        _theory_tactics(inter),
        _tactics_from_search_plan(search_plan) + _tactics_from_reference(ref_ins),
        max_total=22,
    )
    if not response_tactics:
        response_tactics = ["快速核实关键事实", "分层回应核心质疑", "同步平台治理动作"]

    ev_text: List[str] = []
    if narrative:
        ev_text.append("【叙事摘要】" + (narrative[:680] + ("…" if len(narrative) > 680 else "")))
    ev_text.extend(_dataset_evidence_lines(ds_raw, project_root))
    ev_text.extend(_report_meta_lines(report_meta))
    ch_line = _channel_one_liner(channel)
    if ch_line:
        ev_text.append(ch_line)
    wiki_ex = _wiki_qa_excerpt(wiki_snap)
    if wiki_ex:
        ev_text.append("【本地 Wiki 检索摘要】" + wiki_ex)
    ev_text.extend(_expert_note_lines(process_dir / "user_expert_notes.json"))
    ev_text.extend(_judgement_lines(process_dir / "user_judgement_input.json"))
    snippets = search_plan.get("evidenceSnippets")
    if isinstance(snippets, list):
        for x in snippets[:6]:
            s = str(x).strip()
            if s:
                ev_text.append("【检索计划证据句】" + s[:400])

    extras = [
        _rel_posix(project_root, p)
        for p in (
            process_dir / "graph_rag_enrichment.json",
            process_dir / "wiki_qa_snapshot.json",
            process_dir / "reference_insights.json",
            process_dir / "interpretation.json",
        )
        if p.is_file()
    ]
    if interp_path and interp_path.name != "interpretation.json":
        extras.append(_rel_posix(project_root, interp_path))
    if ds_path:
        extras.append(_rel_posix(project_root, ds_path))
    if meta_path:
        extras.append(_rel_posix(project_root, meta_path))
    evidence = ev_text + _evidence_paths(project_root, process_dir, result_dir, html_report_path=html_report_path, extras=extras)

    report_path = _rel_posix(project_root, html_report_path) if html_report_path else ""

    fm: Dict[str, Any] = {
        "title": title_base,
        "domain": domain,
        "actors": actors,
        "timeline": timeline,
        "risk_patterns": risk_patterns,
        "response_tactics": response_tactics,
        "evidence": evidence,
        "report_path": report_path,
    }

    fm_yaml = yaml.safe_dump(
        fm,
        allow_unicode=True,
        default_flow_style=False,
        sort_keys=False,
    ).strip()

    body_parts: List[str] = [
        "## 与报告对齐的深度摘要",
        "",
        narrative or event_intro or "（无解读叙事摘要，已回退事件梗概。）",
        "",
        "## 事件阶段与类型",
        "",
    ]
    if stage or etype:
        if stage:
            body_parts.append(f"- **舆情阶段**（interpretation）：{stage}")
        if etype:
            body_parts.append(f"- **事件类型**（interpretation）：{etype}")
        body_parts.append("")
    if key_events:
        body_parts.extend(["## 关键节点（解读模块 key_events）", ""])
        for i, ev in enumerate(key_events, 1):
            body_parts.append(f"{i}. {ev}")
        body_parts.append("")
    body_parts.extend(
        [
            "## 数据与样本（与 dataset_summary / 报告数据块同源）",
            "",
        ]
    )
    body_parts.extend([f"- {line}" for line in _dataset_evidence_lines(ds_raw, project_root)] or ["- （未找到 dataset_summary.json）"])
    body_parts.append("")
    body_parts.extend(["## HTML 报告元数据（report_meta）", ""])
    body_parts.extend([f"- {line}" for line in _report_meta_lines(report_meta)] or ["- （未找到 report_meta_*.json）"])
    body_parts.append("")
    tops = _user_portrait_actor_lines(portrait, max_items=12)
    if tops:
        body_parts.extend(["## 主要发声主体（user_portrait 节选）", ""])
        for t in tops:
            body_parts.append(f"- {t}")
        body_parts.append("")
    body_parts.extend(
        [
            "## 分析产物索引（sandbox）",
            "",
            f"- 任务 ID：`{task_id}`",
            f"- 过程目录：`{_rel_posix(project_root, process_dir)}`",
            f"- 结果目录：`{_rel_posix(project_root, result_dir)}`",
            f"- HTML 报告：`{report_path or '（路径待补）'}`",
            "",
            "## 知识库复用说明",
            "",
            "本页由当次事件分析流水线自动汇总，**正文优先与 `interpretation` / `report_meta` / 统计 JSON 对齐**，",
            "便于后续相似事件在 wiki 召回时对照「阶段—风险—数据—理论」结构；细粒度结论仍以 HTML 与原始 JSON 为准。",
            "",
        ]
    )
    body = "\n".join(body_parts)
    md = f"---\n{fm_yaml}\n---\n\n{body}"

    out_path.write_text(md, encoding="utf-8")

    wiki_root = get_opinion_analysis_kb_root(project_root) / "references" / "wiki"
    rel = f"cases/{filename}"
    preview = f"{title_base} | {stage or domain}"
    indexed = append_case_to_wiki_index(wiki_root, rel, preview)

    sidecar = {
        "wiki_case_file": _rel_posix(project_root, out_path),
        "index_updated": indexed,
        "created_at": datetime.now().isoformat(sep=" ", timespec="seconds"),
        "sources_used": {
            "interpretation": str(interp_path) if interp_path else "",
            "dataset_summary": str(ds_path) if ds_path else "",
            "report_meta": str(meta_path) if meta_path else "",
        },
    }
    try:
        with open(process_dir / "wiki_case_library_entry.json", "w", encoding="utf-8", errors="replace") as f:
            json.dump(sidecar, f, ensure_ascii=False, indent=2)
    except Exception:
        pass

    return {"case_path": str(out_path), "case_rel": rel, **sidecar}
