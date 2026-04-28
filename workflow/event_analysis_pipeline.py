"""舆情事件分析工作流编排（由 cli 迁入 workflow）。

`run_event_analysis_pipeline` 为完整主流程；CLI 仅保留薄入口并调用 `workflow.runner`。
"""

from __future__ import annotations

import json
import os
import sys
import webbrowser
import re
import hashlib
import csv
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
import select
import time
from typing import Any, Dict, List, Optional, Tuple
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError

from rich.console import Console
from rich.prompt import Prompt
from rich.progress import Progress, SpinnerColumn, BarColumn, TextColumn, TimeElapsedColumn

from tools import (
    extract_search_terms,
    data_num,
    data_collect,
    analysis_timeline,
    analysis_sentiment,
    user_portrait,
    keyword_stats,
    region_stats,
    author_stats,
    volume_stats,
    dataset_summary,
    generate_interpretation,
    graph_rag_query,
    report_html,
    weibo_aisearch,
    search_reference_insights,
    build_event_reference_links,
    load_sentiment_knowledge,
)
from utils.path import ensure_task_dirs, get_sandbox_dir, ensure_task_readable_alias
from utils.task_context import set_task_id
from utils.session_manager import SessionManager
from workflow.telemetry import append_ndjson_log as _telemetry_append_ndjson_log
from workflow.netinsight_keywords import NETINSIGHT_PLATFORMS, build_data_num_search_words
from workflow.netinsight_collect import merge_netinsight_csv_by_content
from workflow.search_plan import coerce_search_plan_v1
from workflow.runtime_harness import RuntimeHarness
from tools.oprag import OPRAG_KNOWLEDGE_SNAPSHOT_FILENAME, OPRAG_RECALL_PREVIEW_FILENAME
from workflow.wiki_cli import answer_wiki_query
from tools.report_html_template import normalize_report_length


console = Console()

_ROOT = Path(__file__).resolve().parents[1]
LOG_PATH = os.getenv("SONA_DEBUG_LOG_PATH", str(_ROOT / ".cursor" / "debug.log"))
EXPERIENCE_PATH = str(_ROOT / "memory" / "LTM" / "search_plan_experience.jsonl")

# 为避免 Rich progress 刷新覆盖用户输入，交互提示前会暂停进度条刷新。
_PROMPT_PROGRESS_HOOKS: Dict[str, Any] = {"pause": None, "resume": None}


def _set_prompt_progress_hooks(*, pause: Any, resume: Any) -> None:
    _PROMPT_PROGRESS_HOOKS["pause"] = pause
    _PROMPT_PROGRESS_HOOKS["resume"] = resume


def _pause_progress_for_prompt() -> None:
    fn = _PROMPT_PROGRESS_HOOKS.get("pause")
    try:
        if callable(fn):
            fn()
    except Exception:
        return


def _resume_progress_after_prompt() -> None:
    fn = _PROMPT_PROGRESS_HOOKS.get("resume")
    try:
        if callable(fn):
            fn()
    except Exception:
        return


def _append_ndjson_log(
    *,
    run_id: str,
    hypothesis_id: str,
    location: str,
    message: str,
    data: Optional[Dict[str, Any]] = None,
) -> None:
    """
    直接追加 NDJSON 到 Cursor debug log（用于 DEBUG MODE 运行证据）。
    """
    _telemetry_append_ndjson_log(
        log_path=LOG_PATH,
        run_id=run_id,
        hypothesis_id=hypothesis_id,
        location=location,
        message=message,
        data=data,
    )


def _coerce_search_plan_contract(search_plan: Dict[str, Any], *, user_query: str) -> Dict[str, Any]:
    """Normalize loose search plan payload into `search_plan_v1` contract shape."""
    plan = dict(search_plan or {})
    plan.setdefault("version", "search_plan_v1")
    coerced = coerce_search_plan_v1(plan)
    if coerced is None:
        fallback_words = _fallback_search_words_from_query(user_query)
        fallback_days = _infer_default_time_range_days(user_query)
        fallback_time_range = _build_default_time_range(fallback_days)
        return {
            "version": "search_plan_v1",
            "eventIntroduction": str(plan.get("eventIntroduction") or user_query),
            "searchWords": fallback_words,
            "timeRange": fallback_time_range,
            "keywordGroups": [],
            "secondaryKeywords": [],
            "queryTemplates": [],
            "verificationChecklist": [],
            "evidenceSnippets": [],
        }
    normalized = coerced.to_dict()
    normalized["timeRange"] = _normalize_time_range_input(str(normalized.get("timeRange") or ""))
    return normalized


def _prompt_yes_no_timeout(question: str, timeout_sec: int = 20, default_yes: bool = True) -> bool:
    """
    以 y/n 方式询问，并提供超时：timeout 后默认继续（默认 y）。
    """

    _pause_progress_for_prompt()
    try:
        console.print()
        console.print(f"{question}（{timeout_sec}s 无响应默认 {'y' if default_yes else 'n'}）")
        sys.stdout.flush()

        try:
            rlist, _, _ = select.select([sys.stdin], [], [], timeout_sec)
            if not rlist:
                return default_yes
            ans = sys.stdin.readline().strip()
        except Exception:
            # 若 select 不可用则退化为阻塞输入
            ans = Prompt.ask(question, default="y" if default_yes else "n")

        if not ans:
            return default_yes
        ans_l = ans.lower()
        if ans_l in {"y", "yes", "1", "true", "t", "是", "好", "ok"} or ans_l.startswith("y"):
            return True
        if ans_l in {"n", "no", "0", "false", "f", "否", "不", "不要"} or ans_l.startswith("n"):
            return False
        return default_yes
    finally:
        _resume_progress_after_prompt()


def _prompt_collect_plan_confirmation(*, edited: bool = False) -> str:
    """
    强确认采集方案分支，避免 y/n 在流式输出阶段被误读。
    返回：accept | edit | abort
    """
    title = "请确认采集方案动作"
    if edited:
        title = "请确认编辑后采集方案动作"
    _pause_progress_for_prompt()
    try:
        console.print()
        console.print(
            f"{title}：输入 [bold]1[/bold]/ACCEPT 执行，"
            f"[bold]2[/bold]/EDIT 修改，"
            f"[bold]3[/bold]/ABORT 终止（输入后回车）。"
        )
        ans = Prompt.ask("请输入动作", default="2").strip().lower()
        if ans in {"1", "accept", "a", "y", "yes"}:
            return "accept"
        if ans in {"2", "edit", "e", "n", "no"}:
            return "edit"
        if ans in {"3", "abort", "quit", "q", "stop"}:
            return "abort"
        # 未识别输入时保守进入编辑，避免误走 accept。
        return "edit"
    finally:
        _resume_progress_after_prompt()


def _prompt_text_timeout(question: str, timeout_sec: int = 35, default_text: str = "") -> str:
    """
    询问自由文本输入，timeout 后返回默认值。
    """
    _pause_progress_for_prompt()
    try:
        console.print()
        console.print(f"{question}（{timeout_sec}s 无响应则跳过）")
        sys.stdout.flush()
        try:
            rlist, _, _ = select.select([sys.stdin], [], [], timeout_sec)
            if not rlist:
                return default_text
            ans = sys.stdin.readline().strip()
            return ans or default_text
        except Exception:
            try:
                ans = Prompt.ask(question, default=default_text)
                return str(ans or "").strip()
            except Exception:
                return default_text
    finally:
        _resume_progress_after_prompt()


def _is_interactive_session() -> bool:
    try:
        return bool(sys.stdin.isatty() and sys.stdout.isatty())
    except Exception:
        return False


def _event_collab_mode() -> str:
    """
    事件工作流协作模式：
    - auto: 全自动（无额外交互）
    - hybrid: 关键节点交互（默认）
    - manual: 尽可能交互
    """
    mode = str(os.environ.get("SONA_EVENT_COLLAB_MODE", "hybrid")).strip().lower()
    if mode not in {"auto", "hybrid", "manual"}:
        return "hybrid"
    return mode


def _collab_enabled() -> bool:
    return _event_collab_mode() != "auto" and _is_interactive_session()


def _platforms_from_search_plan(search_plan: Dict[str, Any]) -> List[str]:
    """Extract desired NetInsight platforms from search plan payload."""
    raw = search_plan.get("platforms")
    if isinstance(raw, list):
        cleaned = _to_clean_str_list(raw, max_items=12)
        return cleaned or ["微博"]
    if isinstance(raw, str) and raw.strip():
        return _parse_platforms_input(raw, default=["微博"])
    return ["微博"]


def _collab_timeout(default_sec: int = 20) -> int:
    try:
        v = int(str(os.environ.get("SONA_EVENT_COLLAB_TIMEOUT_SEC", default_sec)).strip())
        return max(8, min(v, 180))
    except Exception:
        return default_sec


def _analysis_stage_enabled(kind: str) -> bool:
    """
    分析阶段开关（timeline/sentiment 可独立启停）。
    默认开启，可用环境变量关闭：
    - SONA_ANALYSIS_ENABLE_TIMELINE=false
    - SONA_ANALYSIS_ENABLE_SENTIMENT=false
    """
    key = str(kind or "").strip().lower()
    if key not in {"timeline", "sentiment"}:
        return True
    env_name = f"SONA_ANALYSIS_ENABLE_{key.upper()}"
    raw = str(os.environ.get(env_name, "true")).strip().lower()
    return raw in {"1", "true", "yes", "y", "on"}


def _build_skipped_analysis_payload(kind: str, reason: str) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "error": "",
        "skipped": True,
        "skip_reason": reason,
        "result_file_path": "",
    }
    if kind == "timeline":
        payload["timeline"] = []
        payload["summary"] = ""
    if kind == "sentiment":
        payload["statistics"] = {
            "total": 0,
            "positive_count": 0,
            "negative_count": 0,
            "neutral_count": 0,
            "positive_ratio": 0.0,
            "negative_ratio": 0.0,
            "neutral_ratio": 0.0,
            "sentiment_source": "skipped",
        }
        payload["positive_summary"] = []
        payload["negative_summary"] = []
    return payload


@dataclass(frozen=True)
class ToolJsonResult:
    raw: str
    data: Dict[str, Any]


def _parse_tool_json(raw: str) -> Dict[str, Any]:
    try:
        parsed = json.loads(raw)
    except Exception as e:
        raise ValueError(f"工具返回不是合法 JSON：{str(e)}") from e
    if not isinstance(parsed, dict):
        raise ValueError("工具返回 JSON 不是对象")
    return parsed


def _invoke_tool_to_json(tool_obj: Any, payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    统一调用 LangChain StructuredTool，并把字符串 JSON 结果解析为 dict。
    """
    raw = tool_obj.invoke(payload)
    if not isinstance(raw, str):
        raw = str(raw)
    return _parse_tool_json(raw)


def _invoke_tool_with_timing(tool_obj: Any, payload: Dict[str, Any]) -> tuple[Dict[str, Any], float]:
    """调用工具并返回 (json_result, elapsed_sec)。"""
    ts = time.time()
    result = _invoke_tool_to_json(tool_obj, payload)
    elapsed = round(time.time() - ts, 3)
    return result, elapsed


def _invoke_tool_to_json_with_timeout(
    tool_obj: Any,
    payload: Dict[str, Any],
    *,
    timeout_sec: int,
    tool_name: str,
) -> Dict[str, Any]:
    """
    为单个工具调用增加超时保护，避免顺序执行场景下某一步无限阻塞。
    """
    sec = max(10, min(int(timeout_sec or 120), 3600))
    pool = ThreadPoolExecutor(max_workers=1)
    fut = pool.submit(_invoke_tool_to_json, tool_obj, payload)
    try:
        return fut.result(timeout=sec)
    except FuturesTimeoutError:
        fut.cancel()
        return {
            "error": f"{tool_name} 超时（>{sec}s）",
            "result_file_path": "",
        }
    except Exception as e:
        return {
            "error": f"{tool_name} 执行异常: {str(e)}",
            "result_file_path": "",
        }
    finally:
        # 关键：超时后不等待后台线程收尾，避免“逻辑超时但主流程仍阻塞”。
        pool.shutdown(wait=False, cancel_futures=True)


def _ensure_analysis_result_file(
    *,
    process_dir: Path,
    kind: str,
    result_json: Dict[str, Any],
) -> str:
    """
    确保 analysis_* 有可用的 result_file_path。
    若工具未返回有效文件路径，则写入 fallback 文件并返回其路径。
    """
    path_raw = str(result_json.get("result_file_path") or "").strip()
    if path_raw and Path(path_raw).exists():
        return path_raw

    fallback_payload: Dict[str, Any] = {"kind": kind, "generated_at": datetime.now().isoformat(sep=" ")}
    if kind == "timeline":
        fallback_payload["timeline"] = result_json.get("timeline", [])
        fallback_payload["summary"] = result_json.get("summary", "") or ""
    elif kind == "sentiment":
        fallback_payload["statistics"] = result_json.get("statistics", {}) or {}
        fallback_payload["positive_summary"] = result_json.get("positive_summary", []) or []
        fallback_payload["negative_summary"] = result_json.get("negative_summary", []) or []
    else:
        fallback_payload["result"] = result_json
    if "error" in result_json:
        fallback_payload["error"] = result_json.get("error")
    if "raw_result" in result_json and result_json.get("raw_result"):
        fallback_payload["raw_result"] = result_json.get("raw_result")

    fallback_path = process_dir / f"{kind}_analysis_fallback_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with open(fallback_path, "w", encoding="utf-8", errors="replace") as f:
        json.dump(fallback_payload, f, ensure_ascii=False, indent=2)
    return str(fallback_path)


def _validate_time_range(time_range: str) -> bool:
    """
    timeRange 格式： "YYYY-MM-DD HH:MM:SS;YYYY-MM-DD HH:MM:SS"
    """

    if not time_range or ";" not in time_range:
        return False
    normalized = _normalize_time_range_input(time_range)
    if not normalized:
        return False
    start, end = [x.strip() for x in normalized.split(";", maxsplit=1)]
    if not start or not end:
        return False
    return True


def _normalize_time_range_input(time_range: str) -> str:
    """
    规范化 timeRange：
    - 支持 `YYYY-MM-DD;YYYY-MM-DD`
    - 支持 `YYYY-MM-DD HH:MM:SS;YYYY-MM-DD HH:MM:SS`
    - 自动统一输出为 `YYYY-MM-DD HH:MM:SS;YYYY-MM-DD HH:MM:SS`
    """
    if not time_range or ";" not in time_range:
        return ""
    start_raw, end_raw = [x.strip() for x in time_range.split(";", maxsplit=1)]
    if not start_raw or not end_raw:
        return ""

    from datetime import datetime as dt

    def _parse_one(value: str, *, is_end: bool) -> Optional[dt]:
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S", "%Y-%m-%d", "%Y/%m/%d"):
            try:
                parsed = dt.strptime(value, fmt)
                if fmt in ("%Y-%m-%d", "%Y/%m/%d"):
                    if is_end:
                        parsed = parsed.replace(hour=23, minute=59, second=59)
                    else:
                        parsed = parsed.replace(hour=0, minute=0, second=0)
                return parsed
            except Exception:
                continue
        return None

    start_dt = _parse_one(start_raw, is_end=False)
    end_dt = _parse_one(end_raw, is_end=True)
    if not start_dt or not end_dt or start_dt > end_dt:
        return ""
    return f"{start_dt.strftime('%Y-%m-%d %H:%M:%S')};{end_dt.strftime('%Y-%m-%d %H:%M:%S')}"


def _time_range_to_user_date_range(time_range: str) -> str:
    normalized = _normalize_time_range_input(time_range)
    if not normalized or ";" not in normalized:
        return time_range
    start, end = [x.strip() for x in normalized.split(";", maxsplit=1)]
    return f"{start[:10]};{end[:10]}"


def _should_force_sentiment_rerun(user_query: str) -> bool:
    q = str(user_query or "").strip().lower()
    keys = (
        "重新跑情感",
        "重跑情感",
        "重新分析情感",
        "重算情感",
        "重做情感",
        "rerun sentiment",
        "re-run sentiment",
    )
    return any(k in q for k in keys)


def _count_csv_rows(file_path: str) -> int:
    try:
        import csv

        for enc in ("utf-8-sig", "utf-8", "gb18030", "gbk"):
            try:
                with open(file_path, "r", encoding=enc, errors="strict") as f:
                    return sum(1 for _ in csv.DictReader(f))
            except Exception:
                continue
    except Exception:
        return 0
    return 0


def _count_channels_from_csv(file_path: str) -> Dict[str, int]:
    """
    从最终样本 CSV 统计渠道占比，优先反映“实际入样”而非计划采集数。
    """
    p = str(file_path or "").strip()
    if not p or not Path(p).exists():
        return {}
    col_candidates = ("平台", "platform", "source", "来源平台", "站点")
    best: Dict[str, int] = {}
    try:
        import csv

        for enc in ("utf-8-sig", "utf-8", "gb18030", "gbk"):
            try:
                with open(p, "r", encoding=enc, errors="strict") as f:
                    reader = csv.DictReader(f)
                    rows = list(reader)
                if not rows:
                    continue
                headers = list(rows[0].keys())
                platform_col = ""
                for c in headers:
                    cs = str(c or "").strip()
                    if any(x.lower() == cs.lower() for x in col_candidates):
                        platform_col = cs
                        break
                if not platform_col:
                    continue
                counts: Dict[str, int] = {}
                for row in rows:
                    name = str(row.get(platform_col, "") or "").strip()
                    if not name:
                        continue
                    counts[name] = counts.get(name, 0) + 1
                if counts:
                    best = counts
                    break
            except Exception:
                continue
    except Exception:
        return {}
    return best


def _normalize_search_words_for_collection(words: List[str], user_query: str) -> List[str]:
    """
    采集前关键词增强：
    - 保留原词
    - 去掉“舆情分析/事件分析/报告”等后缀，生成更可检索短语
    - 结合 query 提取候选词，避免单个超长词导致 data_num/data_collect 命中低
    """
    base = [str(w or "").strip() for w in words if str(w or "").strip()]
    q_words = _fallback_search_words_from_query(user_query, max_words=10)
    precise_event_words = _derive_precise_event_search_words(user_query)
    extra: List[str] = []
    suffixes = ("舆情分析", "事件分析", "分析报告", "舆情事件", "事件舆情", "舆情")
    for w in base:
        t = w
        for suf in suffixes:
            t = t.replace(suf, "")
        t = re.sub(r"\s+", "", t).strip("，,。.;；:：")
        if len(t) >= 4:
            extra.append(t)
        # 对“大学生高铁骂熊孩子事件”这类长串做轻量切分
        if len(t) >= 8:
            for seg in re.findall(r"[\u4e00-\u9fff]{2,6}", t):
                if len(seg) >= 3:
                    extra.append(seg)
    merged = precise_event_words + base + extra + q_words
    dedup: List[str] = []
    seen = set()
    for w in merged:
        k = w.strip().lower()
        if not k or k in seen:
            continue
        seen.add(k)
        dedup.append(w.strip())
        if len(dedup) >= 16:
            break
    return dedup or base or q_words


def _truthy_env(name: str, default: str = "false") -> bool:
    v = str(os.environ.get(name, default) or "").strip().lower()
    return v in ("1", "true", "yes", "y", "on")


def _event_query_strict_enabled() -> bool:
    """
    事件型 query 的“核心词优先”模式：
    - 默认关闭，避免对非事件主题造成采集不足
    - 开启后：优先使用 _derive_precise_event_search_words 的核心词；若样本不足再逐轮放开
    """
    return _truthy_env("SONA_EVENT_QUERY_STRICT_MODE", "false")


def _build_search_word_levels(*, base_words: List[str], user_query: str) -> Dict[str, List[str]]:
    """
    将 searchWords 拆成 3 层（核心/扩展/宽泛），用于事件型分轮回退。

    - core: 高置信短语（机构/关键冲突短语），用于第一轮采集
    - extended: core + 轻量切分/去后缀短语（仍偏精确）
    - broad: extended + query 回退词（更宽）
    """
    base = [str(w or "").strip() for w in (base_words or []) if str(w or "").strip()]
    q_words = _fallback_search_words_from_query(user_query, max_words=10)
    precise_event_words = _derive_precise_event_search_words(user_query)

    # 扩展：复用原逻辑的 extra（去后缀 + 2~6 字切分）
    extra: List[str] = []
    suffixes = ("舆情分析", "事件分析", "分析报告", "舆情事件", "事件舆情", "舆情")
    for w in base:
        t = w
        for suf in suffixes:
            t = t.replace(suf, "")
        t = re.sub(r"\s+", "", t).strip("，,。.;；:：")
        if len(t) >= 4:
            extra.append(t)
        if len(t) >= 8:
            for seg in re.findall(r"[\u4e00-\u9fff]{2,6}", t):
                if len(seg) >= 3:
                    extra.append(seg)

    def _dedup(items: List[str], *, cap: int) -> List[str]:
        out: List[str] = []
        seen: set[str] = set()
        for it in items:
            s = str(it or "").strip()
            if not s:
                continue
            k = s.lower()
            if k in seen:
                continue
            seen.add(k)
            out.append(s)
            if len(out) >= cap:
                break
        return out

    core = _dedup(precise_event_words, cap=3)
    extended = _dedup(core + base + extra, cap=10)
    broad = _dedup(extended + q_words, cap=16)
    return {"core": core, "extended": extended, "broad": broad}


def _pick_search_words_for_round(
    *,
    base_words: List[str],
    user_query: str,
    round_idx: int,
) -> tuple[List[str], str]:
    """
    返回 (search_words_for_collect, level_name)。

    round_idx 从 1 开始：
    - strict 模式下：1=core, 2=extended, >=3=broad
    - 非 strict：保持旧行为（等价于 broad）
    """
    levels = _build_search_word_levels(base_words=base_words, user_query=user_query)
    strict = _event_query_strict_enabled() and bool(levels.get("core"))
    if not strict:
        broad = levels.get("broad") or _normalize_search_words_for_collection(base_words, user_query)
        return broad, "broad"

    if round_idx <= 1:
        return levels.get("core") or [], "core"
    if round_idx == 2:
        return levels.get("extended") or (levels.get("core") or []), "extended"
    return levels.get("broad") or (levels.get("extended") or []), "broad"


def _derive_precise_event_search_words(user_query: str) -> List[str]:
    """
    为“事件型 query”提取更短、更可检索的核心词，降低长句误召回。
    例如：`12306回应家长和孩子相隔14个车厢事件` -> `铁路12306`, `家长孩子相隔14车厢`
    """
    q = str(user_query or "").strip()
    if not q:
        return []
    q0 = re.sub(r"\s+", "", q)
    out: List[str] = []

    if "12306" in q0:
        out.append("铁路12306")

    m_gap = re.search(r"家长.{0,3}孩子.{0,6}相隔\d{1,2}(?:个)?车厢", q0)
    if m_gap:
        phrase = m_gap.group(0)
        phrase = phrase.replace("和", "").replace("与", "").replace("及", "")
        phrase = phrase.replace("个车厢", "车厢")
        out.append(phrase)

    # 常见“X回应Y事件”：拆出机构词 + 事件短语词
    m_resp = re.search(r"([^\s，。；、]{2,18}?)(回应|通报|说明)([^\s，。；、]{3,24})", q0)
    if m_resp:
        actor = m_resp.group(1)
        evt = m_resp.group(3)
        actor = re.sub(r"(近日|今日|昨天|关于)$", "", actor)
        evt = re.sub(r"(事件|问题|一事)$", "", evt)
        if actor and len(actor) >= 2:
            out.append(actor)
        if evt and len(evt) >= 3:
            out.append(evt)

    # 去重并限制长度
    dedup: List[str] = []
    seen: set[str] = set()
    for w in out:
        s = str(w or "").strip("，,。.;；:： ")
        if not s:
            continue
        if len(s) > 18:
            s = s[:18]
        if s in seen:
            continue
        seen.add(s)
        dedup.append(s)
        if len(dedup) >= 4:
            break
    return dedup


def _save_collect_manifest(
    *,
    process_dir: Path,
    user_query: str,
    save_path: str,
    rows: int,
    time_range: str,
    search_words: List[str],
) -> None:
    """
    将本次采集成功结果写入任务内清单，便于后续检索与复用。
    """
    try:
        payload = {
            "saved_at": datetime.now().isoformat(sep=" "),
            "user_query": user_query,
            "save_path": save_path,
            "rows": rows,
            "time_range": time_range,
            "search_words": search_words[:16],
        }
        out = process_dir / "collected_dataset_manifest.json"
        with open(out, "w", encoding="utf-8", errors="replace") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
    except Exception:
        return


def _build_default_time_range(days: int = 30) -> str:
    """
    生成默认时间范围：昨天 23:59:59 往前 days 天。
    """
    end = datetime.now().replace(hour=23, minute=59, second=59, microsecond=0) - timedelta(days=1)
    start = end - timedelta(days=days)
    return f"{start.strftime('%Y-%m-%d %H:%M:%S')};{end.strftime('%Y-%m-%d %H:%M:%S')}"


def _infer_default_time_range_days(user_query: str) -> int:
    """
    为事件分析推断更合理的默认时间窗天数。
    - 若 query 显式提及“最近一周/两周/一个月/30天/3天/48小时”等，按其含义转换
    - 否则使用较短窗口（默认 7 天），减少突发事件的数据污染
    可用环境变量 SONA_DEFAULT_TIME_RANGE_DAYS 覆盖。
    """
    # env override
    try:
        env_days_raw = str(os.environ.get("SONA_DEFAULT_TIME_RANGE_DAYS", "")).strip()
        if env_days_raw:
            return max(2, min(_safe_int(env_days_raw, 10), 60))
    except Exception:
        pass

    q = str(user_query or "")
    q = re.sub(r"\s+", "", q)

    # common CN hints
    if any(k in q for k in ("最近一月", "最近1个月", "最近一个月", "近一月", "近1个月", "近一个月", "一个月内", "一个月")):
        return 30
    if any(k in q for k in ("最近两周", "最近2周", "近两周", "近2周", "两周内")):
        return 14
    if any(k in q for k in ("最近一周", "最近1周", "近一周", "近1周", "一周内", "7天")):
        return 7

    # explicit days like “3天/10天”
    m = re.search(r"(\d{1,2})天", q)
    if m:
        try:
            return max(2, min(int(m.group(1)), 60))
        except Exception:
            pass

    # explicit hours like “48小时/24小时”
    mh = re.search(r"(\d{1,3})小时", q)
    if mh:
        try:
            hours = int(mh.group(1))
            return max(2, min((hours + 23) // 24, 60))
        except Exception:
            pass

    # 突发事件：默认 3~5 天更贴近真实起点，减少历史噪声污染
    burst_keywords = (
        "突发",
        "怒斥",
        "怒吼",
        "冲突",
        "打人",
        "纠纷",
        "热搜",
        "曝光",
        "高铁",
        "地铁",
        "校园",
        "熊孩子",
    )
    if any(k in q for k in burst_keywords):
        return max(3, min(_safe_int(os.environ.get("SONA_BURST_EVENT_DAYS", "5"), 5), 7))

    # 默认 7 天（更贴近大多数公共事件的有效周期）
    return 7


def _supported_platforms_for_netinsight() -> List[str]:
    """NetInsight 支持的平台列表（与 tools/data_collect.py / data_num.py 一致）。"""
    return list(NETINSIGHT_PLATFORMS)


def _parse_platforms_input(value: str, *, default: Optional[List[str]] = None) -> List[str]:
    """
    解析平台输入，支持：
    - "ALL"/"全选"
    - 编号： "1,3,5" 或 "1 3 5"
    - 名称： "微博;微信" / "微博, 微信"
    """
    default = default or ["微博"]
    raw = str(value or "").strip()
    if not raw:
        return default
    low = raw.lower().replace(" ", "")
    if low in {"all", "全部", "全选"}:
        return _supported_platforms_for_netinsight()

    platforms = _supported_platforms_for_netinsight()
    # numbers
    nums = re.findall(r"\d{1,2}", raw)
    picked: List[str] = []
    if nums and (len(nums) >= 1) and all(n.isdigit() for n in nums):
        for n in nums:
            idx = int(n)
            if 1 <= idx <= len(platforms):
                picked.append(platforms[idx - 1])
        picked = _to_clean_str_list(picked, max_items=12)
        return picked or default

    # names
    parts = re.split(r"[;,，、\s]+", raw)
    name_map = {p: p for p in platforms}
    name_map.update({p.lower(): p for p in platforms})
    for part in parts:
        s = str(part or "").strip()
        if not s:
            continue
        key = s if s in name_map else s.lower()
        if key in name_map:
            picked.append(name_map[key])
    picked = _to_clean_str_list(picked, max_items=12)
    return picked or default


def _build_quick_time_range(kind: str) -> str:
    """
    kind: today | 24h | 3d | 7d | 30d
    """
    now = datetime.now().replace(microsecond=0)
    k = str(kind or "").strip().lower()
    if k == "today":
        start = now.replace(hour=0, minute=0, second=0)
        end = now
        return f"{start.strftime('%Y-%m-%d %H:%M:%S')};{end.strftime('%Y-%m-%d %H:%M:%S')}"
    if k == "24h":
        start = now - timedelta(hours=24)
        end = now
        return f"{start.strftime('%Y-%m-%d %H:%M:%S')};{end.strftime('%Y-%m-%d %H:%M:%S')}"
    if k == "3d":
        return _build_default_time_range(3)
    if k == "7d":
        return _build_default_time_range(7)
    if k == "30d":
        return _build_default_time_range(30)
    return ""


def _prompt_time_range_with_quick_choices(*, default_time_range: str) -> str:
    """
    协作模式下的 timeRange 交互：提供快捷选项 + 自定义输入。
    返回规范化后的 timeRange（若输入非法则回退 default_time_range）。
    """
    default_user = _time_range_to_user_date_range(str(default_time_range or ""))
    tip = (
        "修改 timeRange：输入 1=今天 2=24h 3=3天 4=7天 5=30天，或直接输入"
        " 'YYYY-MM-DD;YYYY-MM-DD'（可省略时分秒）"
    )
    raw = Prompt.ask(tip, default=default_user).strip() or default_user
    if raw in {"1", "今天"}:
        return _build_quick_time_range("today") or default_time_range
    if raw in {"2", "24h", "24H"}:
        return _build_quick_time_range("24h") or default_time_range
    if raw in {"3", "3天"}:
        return _build_quick_time_range("3d") or default_time_range
    if raw in {"4", "7天"}:
        return _build_quick_time_range("7d") or default_time_range
    if raw in {"5", "30天"}:
        return _build_quick_time_range("30d") or default_time_range
    normalized = _normalize_time_range_input(raw)
    return normalized if _validate_time_range(normalized) else default_time_range


def _edit_collect_plan_interactively(suggested_collect_plan: Dict[str, Any]) -> tuple[Dict[str, Any], bool]:
    """
    交互式编辑采集方案（平台/时间范围/return_count/布尔策略/并发），并二次确认是否执行。

    Returns:
        (updated_plan, accepted)
    """
    plan = dict(suggested_collect_plan or {})

    def _clean_list(value: Any, *, max_items: int = 12) -> List[str]:
        raw_items: List[Any]
        if value is None:
            raw_items = []
        elif isinstance(value, list):
            raw_items = value
        elif isinstance(value, str):
            raw_items = [value]
        else:
            raw_items = [str(value)]
        out: List[str] = []
        seen: set[str] = set()
        for it in raw_items:
            s = str(it or "").strip()
            if not s or s in seen:
                continue
            seen.add(s)
            out.append(s)
            if len(out) >= max_items:
                break
        return out

    default_platforms = plan.get("platforms") or ["微博"]
    platforms_hint = "；".join(_clean_list(default_platforms, max_items=12) or ["微博"])
    platforms_list = _supported_platforms_for_netinsight()
    platform_menu = " / ".join([f"{i+1}:{p}" for i, p in enumerate(platforms_list)])
    platform_in_raw = (
        Prompt.ask(
            f"修改平台（多选：输入 ALL 或 编号如 1,3,5 或 名称用 ; 分隔）。可选：{platform_menu}",
            default=platforms_hint,
        ).strip()
        or platforms_hint
    )
    platforms_in = _parse_platforms_input(platform_in_raw, default=_clean_list(default_platforms, max_items=12) or ["微博"])

    return_count_in = (
        Prompt.ask(
            "修改返回结果条数 return_count（1-10000；不填则默认）",
            default=str(plan.get("return_count", 2000)),
        ).strip()
        or str(plan.get("return_count", 2000))
    )
    return_count_in_int = max(1, min(_safe_int(return_count_in, int(plan.get("return_count", 2000) or 2000)), 10000))

    plan["time_range"] = _prompt_time_range_with_quick_choices(default_time_range=str(plan.get("time_range") or ""))

    boolean_in = (
        Prompt.ask(
            "修改布尔策略（OR 或 AND；默认 OR）",
            default=str(plan.get("boolean_strategy") or "").startswith("AND") and "AND" or "OR",
        )
        .strip()
        .upper()
    )
    if boolean_in not in ("OR", "AND"):
        boolean_in = "OR"

    plan["platforms"] = platforms_in
    plan["return_count"] = return_count_in_int
    plan["boolean_strategy"] = (
        f"{boolean_in}（当前实现：{ '逐词分别检索再合并' if boolean_in=='OR' else '单次表达式合并（依赖 API 对 ; 的支持）' }）"
    )

    data_num_workers_in = Prompt.ask("修改 data_num 并发（1-8）", default=str(plan.get("data_num_workers", 4))).strip()
    data_collect_workers_in = Prompt.ask("修改 data_collect 并发（1-8）", default=str(plan.get("data_collect_workers", 3))).strip()
    analysis_workers_in = Prompt.ask("修改分析并发（1-8）", default=str(plan.get("analysis_workers", 2))).strip()
    plan["data_num_workers"] = max(1, min(_safe_int(data_num_workers_in, 4), 8))
    plan["data_collect_workers"] = max(1, min(_safe_int(data_collect_workers_in, 3), 8))
    plan["analysis_workers"] = max(1, min(_safe_int(analysis_workers_in, 2), 8))

    edited_action = _prompt_collect_plan_confirmation(edited=True)
    accepted = edited_action == "accept"
    return plan, accepted


def _fallback_search_words_from_query(user_query: str, max_words: int = 6) -> List[str]:
    """
    当 extract_search_terms 返回空关键词时，从用户 query 兜底提取检索词。
    """
    if not user_query:
        return []

    stop_words = {
        "帮我", "请帮", "一下", "进行", "分析", "报告", "生成", "数据", "舆情",
        "事件", "关于", "相关", "看看", "给我", "这个", "那个", "我们", "你们",
    }
    chunks = re.findall(r"[\u4e00-\u9fffA-Za-z0-9#·_-]{2,}", user_query)
    words: List[str] = []
    seen: set[str] = set()
    for c in chunks:
        item = c.strip()
        if not item or item in stop_words:
            continue
        if item in seen:
            continue
        seen.add(item)
        words.append(item)
        if len(words) >= max_words:
            break
    if words:
        return words
    q = user_query.strip()
    return [q[:30]] if q else []


def _build_search_plan_from_weibo_aisearch(
    *,
    user_query: str,
    process_dir: Path,
) -> Dict[str, Any]:
    """
    使用微博智搜结果生成 search_plan，并将原始结果写入过程文件。
    """
    limit = max(4, min(_safe_int(os.environ.get("SONA_WEIBO_AISEARCH_PLAN_LIMIT", "12"), 12), 30))
    weibo_json = _invoke_tool_to_json(
        weibo_aisearch,
        {"query": user_query, "limit": limit},
    )

    weibo_ref_path = process_dir / "weibo_aisearch_reference.json"
    with open(weibo_ref_path, "w", encoding="utf-8", errors="replace") as f:
        json.dump(weibo_json, f, ensure_ascii=False, indent=2)

    snippets = [
        str((it or {}).get("snippet", "")).strip()
        for it in (weibo_json.get("results") or [])
        if isinstance(it, dict) and str((it or {}).get("snippet", "")).strip()
    ]
    if not snippets:
        err = str(weibo_json.get("error", "") or "微博智搜未返回可用片段")
        raise ValueError(err)

    stop_words = {
        "舆情", "事件", "相关", "分析", "我们", "你们", "这个", "那个", "进行", "报道", "网友", "表示", "评论",
        "视频", "现场", "微博", "平台", "发布", "消息", "近日", "今日", "其中", "以及", "因为", "已经", "可以",
    }
    token_re = re.compile(r"[\u4e00-\u9fffA-Za-z0-9#·_-]{2,18}")
    token_counter: Counter[str] = Counter()
    hashtag_counter: Counter[str] = Counter()
    mention_counter: Counter[str] = Counter()
    for text in snippets[:20]:
        for tag in re.findall(r"#([^#\s]{2,24})#", text or ""):
            t = str(tag).strip()
            if t and t not in stop_words:
                hashtag_counter[t] += 1
        for m in re.findall(r"@([A-Za-z0-9_\-\u4e00-\u9fff]{2,24})", text or ""):
            u = str(m).strip()
            if u and u not in stop_words:
                mention_counter[u] += 1
        for token in token_re.findall(text):
            s = token.strip()
            if not s or s in stop_words:
                continue
            if len(s) <= 2 and not re.search(r"[A-Za-z0-9#]", s):
                continue
            token_counter[s] += 1

    ranked_tokens = [w for w, _ in token_counter.most_common(24)]
    ranked_hashtags = [f"#{w}#" for w, _ in hashtag_counter.most_common(10)]
    ranked_mentions = [f"@{w}" for w, _ in mention_counter.most_common(6)]

    # 核心关键词：避免把“分析...舆情分析”整句当成检索词。
    # 做法：把 user_query 先“去功能词”得到一个紧凑串，然后从其中枚举 2~4 字 n-gram，
    # 用其在智搜 snippets 中的出现次数打分，选出最像“可检索关键词”的前几个。
    seed_words_fallback = _fallback_search_words_from_query(user_query, max_words=6)
    user_q = str(user_query or "").strip()
    snip_text = " ".join(snippets[:12])

    def _compact_query_for_keywords(q: str) -> str:
        # 先粗暴去掉常见功能词/虚词，避免长句直接入库
        drop = [
            "舆情分析",
            "舆情",
            "分析",
            "报告",
            "事件",
            "关于",
            "相关",
            "进行",
            "的",
            "了",
            "和",
            "与",
            "及",
            "在",
            "对",
            "将",
            "帮我",
            "请帮",
            "一下",
        ]
        out = str(q or "")
        for w in drop:
            out = out.replace(w, " ")
        out = re.sub(r"\s+", "", out)
        out = re.sub(r"[^\u4e00-\u9fffA-Za-z0-9#·_-]+", "", out)
        return out.strip()

    compact = _compact_query_for_keywords(user_q)
    ngram_counter: Counter[str] = Counter()
    if compact:
        # 2~4 字中文片段
        for n in (4, 3, 2):
            for i in range(0, max(0, len(compact) - n + 1)):
                seg = compact[i : i + n]
                if not seg or seg in stop_words:
                    continue
                # 只接受“包含中文”的片段，避免 Apache/ULV 之类噪声
                if not re.search(r"[\u4e00-\u9fff]", seg):
                    continue
                ngram_counter[seg] += snip_text.count(seg)

    seed_words_from_ngrams: List[str] = []
    for seg, score in ngram_counter.most_common(24):
        if score <= 0:
            continue
        if seg in seed_words_from_ngrams:
            continue
        seed_words_from_ngrams.append(seg)
        if len(seed_words_from_ngrams) >= 6:
            break

    seed_words: List[str] = []
    for item in seed_words_from_ngrams + seed_words_fallback:
        s = str(item or "").strip()
        if not s:
            continue
        if len(s) >= 12 and ("分析" in s or "舆情" in s or "报告" in s):
            continue
        if s in stop_words:
            continue
        if s not in seed_words:
            seed_words.append(s)
        if len(seed_words) >= 6:
            break
    merged: List[str] = []
    seen: set[str] = set()
    for item in seed_words + ranked_hashtags + ranked_mentions + ranked_tokens:
        s = str(item or "").strip()
        if not s or s in seen:
            continue
        seen.add(s)
        merged.append(s)
        if len(merged) >= 12:
            break

    event_intro = "；".join(snippets[:2]).strip()[:240]
    fallback_days = _infer_default_time_range_days(user_query)
    secondary_keywords = [w for w in ranked_tokens if w not in set(seed_words)][:18]
    keyword_groups: List[Dict[str, Any]] = [
        {"name": "核心关键词（来自用户Query）", "keywords": seed_words[:8]},
        {"name": "话题标签（来自微博智搜片段）", "keywords": ranked_hashtags[:8]},
        {"name": "相关账号（来自微博智搜片段）", "keywords": ranked_mentions[:6]},
        {"name": "扩展关键词（来自微博智搜片段）", "keywords": secondary_keywords[:12]},
    ]
    keyword_groups = [g for g in keyword_groups if g.get("keywords")]

    # 为后续“二次检索/验证”提供可执行 query 列表（不依赖特定平台语法）
    query_templates: List[str] = []
    base = seed_words[0] if seed_words else (merged[0] if merged else user_query.strip()[:20])
    for w in (secondary_keywords[:8] + ranked_hashtags[:4]):
        t = str(w or "").strip()
        if not t:
            continue
        q = f"{base} {t}".strip()
        if q and q not in query_templates:
            query_templates.append(q)
        if len(query_templates) >= 10:
            break

    verification_checklist: List[str] = [
        "最早传播来源：最早时间点/原帖/首发媒体或账号是谁？",
        "官方回应：涉事机构/当事人/平台是否发布公告或通报？",
        "关键证据：网传截图/视频/音频是否有原链接与完整上下文？",
        "时间线：按时间排序关键节点（发生-扩散-处置-后续）。",
        "争议点与反驳：核心指控是什么？有哪些反证或澄清？",
    ]
    return {
        "version": "search_plan_v1",
        "eventIntroduction": event_intro or user_query[:200],
        "searchWords": merged,
        "timeRange": _build_default_time_range(fallback_days),
        "keywordGroups": keyword_groups,
        "secondaryKeywords": secondary_keywords[:18],
        "queryTemplates": query_templates,
        "verificationChecklist": verification_checklist,
        "evidenceSnippets": snippets[: min(8, len(snippets))],
        "_weibo_meta": {
            "path": str(weibo_ref_path),
            "count": len(snippets),
            "fallback_used": bool(weibo_json.get("fallback_used")),
            "source": str(weibo_json.get("source", "")),
        },
    }


def _to_clean_str_list(value: Any, *, max_items: int = 12) -> List[str]:
    """将输入归一化为去重字符串列表。"""
    if value is None:
        return []
    raw_items: List[Any]
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
        if not s:
            continue
        if s in seen:
            continue
        seen.add(s)
        result.append(s)
        if len(result) >= max_items:
            break
    return result


def _resolve_to_csv_path(path_like: str) -> str:
    """
    将输入路径解析为可直接用于 dataset_summary / analysis_* 的 CSV 文件路径。

    支持：
    1) 直接传入 CSV；
    2) 传入 dataset_summary*.json（会读取 save_path）；
    3) 传入目录（会自动选择最新 CSV）。
    """
    if not path_like:
        raise ValueError("数据路径为空")

    normalized = str(path_like).strip()
    if normalized.startswith("file://"):
        normalized = normalized[7:]

    p = Path(normalized).expanduser()
    if not p.exists():
        raise ValueError(f"指定的数据路径不存在: {normalized}")

    def _from_json_file(json_path: Path) -> Optional[str]:
        try:
            with open(json_path, "r", encoding="utf-8", errors="replace") as f:
                obj = json.load(f)
            if not isinstance(obj, dict):
                return None
            candidates: List[str] = []
            for key in ("save_path", "csv_path", "dataFilePath", "file_path", "path"):
                v = obj.get(key)
                if isinstance(v, str) and v.strip():
                    candidates.append(v.strip())
            ds = obj.get("dataset_summary")
            if isinstance(ds, dict):
                v = ds.get("save_path")
                if isinstance(v, str) and v.strip():
                    candidates.append(v.strip())
            for raw in candidates:
                c = Path(raw).expanduser()
                if c.exists() and c.is_file() and c.suffix.lower() == ".csv":
                    return str(c)
        except Exception:
            return None
        return None

    def _pick_csv_from_dir(dir_path: Path) -> Optional[str]:
        if not dir_path.exists() or not dir_path.is_dir():
            return None
        csv_files = [f for f in dir_path.rglob("*.csv") if f.is_file()]
        if not csv_files:
            return None
        preferred = [
            f for f in csv_files
            if "netinsight" in f.name.lower() or "汇总" in f.name
        ]
        bucket = preferred or csv_files
        bucket = sorted(bucket, key=lambda x: x.stat().st_mtime, reverse=True)
        return str(bucket[0])

    # 1) 直接 CSV
    if p.is_file() and p.suffix.lower() == ".csv":
        return str(p)

    # 2) JSON（优先尝试从 JSON 解析出真实 CSV）
    if p.is_file() and p.suffix.lower() == ".json":
        from_json = _from_json_file(p)
        if from_json:
            return from_json
        # 若 JSON 同目录已有 CSV，取最新
        from_sibling = _pick_csv_from_dir(p.parent)
        if from_sibling:
            return from_sibling
        raise ValueError(f"JSON 文件未包含可用 CSV 路径，且同目录无 CSV: {p}")

    # 3) 目录
    if p.is_dir():
        # 先尝试目录中的 dataset_summary*.json 反解
        json_candidates = sorted(
            [f for f in p.rglob("dataset_summary*.json") if f.is_file()],
            key=lambda x: x.stat().st_mtime,
            reverse=True,
        )
        for jf in json_candidates:
            from_json = _from_json_file(jf)
            if from_json:
                return from_json
        from_dir = _pick_csv_from_dir(p)
        if from_dir:
            return from_dir
        raise ValueError(f"目录中未找到可用 CSV: {p}")

    raise ValueError(f"无法解析为 CSV 路径: {normalized}")


def _find_recent_reusable_csv(
    *,
    current_task_id: str,
    limit: int = 8,
) -> List[str]:
    """
    扫描 sandbox 内最近可复用的 CSV，按修改时间倒序返回。
    """
    sandbox_dir = get_sandbox_dir()
    if not sandbox_dir.exists():
        return []

    csv_files: List[Path] = []
    for task_dir in sandbox_dir.iterdir():
        if not task_dir.is_dir():
            continue
        if task_dir.name == current_task_id:
            continue

        preferred_dirs = [task_dir / "过程文件", task_dir / "结果文件", task_dir]
        for base_dir in preferred_dirs:
            if not base_dir.exists() or not base_dir.is_dir():
                continue
            for f in base_dir.rglob("*.csv"):
                if not f.is_file():
                    continue
                lower_name = f.name.lower()
                if "tmp" in lower_name or "temp" in lower_name:
                    continue
                csv_files.append(f)

    if not csv_files:
        return []

    csv_files = sorted(csv_files, key=lambda p: p.stat().st_mtime, reverse=True)
    deduped: List[str] = []
    seen: set[str] = set()
    for f in csv_files:
        path_str = str(f)
        if path_str in seen:
            continue
        seen.add(path_str)
        deduped.append(path_str)
        if len(deduped) >= max(1, limit):
            break
    return deduped


def _pretty_print_dict(title: str, payload: Dict[str, Any]) -> None:
    console.print()
    console.print(f"[bold cyan]{title}[/bold cyan]")
    console.print(f"[dim]{json.dumps(payload, ensure_ascii=False, indent=2)[:5000]}[/dim]")
    if len(json.dumps(payload, ensure_ascii=False)) > 5000:
        console.print("[yellow]（输出已截断）[/yellow]")


def _write_text_file(path: Path, text: str) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8", errors="replace") as f:
            f.write(str(text or ""))
    except Exception:
        return


def _preview_oprag_snapshot(snapshot: Dict[str, Any]) -> str:
    """
    将 OPRAG 知识快照提炼成可读预览，用于控制台与日志。
    """
    if not isinstance(snapshot, dict):
        return ""
    lines: List[str] = []
    q = str(snapshot.get("query", "") or "").strip()
    if q:
        lines.append(f"query: {q[:120]}")

    knowledge = snapshot.get("knowledge")
    knowledge_text = json.dumps(knowledge, ensure_ascii=False) if isinstance(knowledge, (dict, list)) else str(knowledge or "")
    knowledge_text = re.sub(r"\s+", " ", knowledge_text).strip()
    if knowledge_text:
        lines.append("knowledge_preview: " + knowledge_text[:260] + ("..." if len(knowledge_text) > 260 else ""))

    refs = snapshot.get("references") if isinstance(snapshot.get("references"), dict) else {}
    results = refs.get("results") if isinstance(refs, dict) else []
    if isinstance(results, list) and results:
        lines.append(f"reference_hits: {len(results)}")
        for row in results[:3]:
            if not isinstance(row, dict):
                continue
            title = str(row.get("title", "") or "").strip()
            snippet = str(row.get("snippet", "") or "").strip()
            if title or snippet:
                s = (snippet[:160] + ("..." if len(snippet) > 160 else "")) if snippet else ""
                lines.append(f"- {title[:60]}：{s}")
    return "\n".join(lines).strip()


def _make_progress() -> Progress:
    return Progress(
        SpinnerColumn(),
        TextColumn("[bold]{task.description}[/bold]"),
        BarColumn(bar_width=None),
        TextColumn("{task.completed}/{task.total}"),
        TimeElapsedColumn(),
        console=console,
        transient=False,
    )


def _safe_int(value: Any, default: int) -> int:
    try:
        v = int(value)
        return v
    except Exception:
        return default


def _safe_float(value: Any, default: float) -> float:
    try:
        return float(str(value).strip())
    except Exception:
        return default


def _allow_history_fallback() -> bool:
    v = os.environ.get("SONA_ALLOW_HISTORY_FALLBACK", "false").strip().lower()
    return v in ("1", "true", "yes", "y", "on")


def _auto_reuse_history_data_enabled() -> bool:
    """
    历史经验命中后，是否自动复用历史 CSV（跳过 data_num/data_collect）。
    默认关闭，可通过 SONA_AUTO_REUSE_HISTORY_DATA=true 开启。
    """
    v = os.environ.get("SONA_AUTO_REUSE_HISTORY_DATA", "false").strip().lower()
    return v in ("1", "true", "yes", "y", "on")


def _experience_reuse_enabled() -> bool:
    """
    是否允许复用历史经验（search_plan/collect_plan）。
    默认关闭，确保每次事件分析都从当前 query 重新开始。
    """
    v = os.environ.get("SONA_REUSE_EXPERIENCE", "false").strip().lower()
    return v in ("1", "true", "yes", "y", "on")


def _force_fresh_start_enabled() -> bool:
    """
    是否强制每次任务全新开始（不复用历史经验/CSV/分析结果）。
    默认开启，避免历史小样本污染当前任务。
    """
    v = os.environ.get("SONA_FORCE_FRESH_START", "true").strip().lower()
    return v in ("1", "true", "yes", "y", "on")


def _resolve_reusable_csv_from_history(best_exp: Dict[str, Any], *, current_task_id: str) -> Optional[str]:
    """
    从历史经验记录中定位可复用 CSV。
    """
    history_task_id = str((best_exp or {}).get("task_id") or "").strip()
    if not history_task_id or history_task_id == current_task_id:
        return None

    sandbox_dir = get_sandbox_dir()
    history_root = sandbox_dir / history_task_id
    if not history_root.exists():
        return None

    candidates = [
        history_root / "过程文件",
        history_root / "结果文件",
        history_root,
    ]
    for c in candidates:
        try:
            resolved = _resolve_to_csv_path(str(c))
            if resolved and Path(resolved).exists():
                return resolved
        except Exception:
            continue
    return None


def _analysis_reuse_enabled(kind: str) -> bool:
    env_map = {
        "sentiment": "SONA_REUSE_SENTIMENT_RESULT",
        "timeline": "SONA_REUSE_TIMELINE_RESULT",
    }
    key = env_map.get(kind, "")
    if not key:
        return False
    v = str(os.environ.get(key, "false")).strip().lower()
    return v in ("1", "true", "yes", "y", "on")


def _extract_task_id_from_path(path_like: str) -> str:
    try:
        p = Path(str(path_like or "")).expanduser().resolve()
        sandbox_root = get_sandbox_dir().resolve()
        rel = p.relative_to(sandbox_root)
        parts = list(rel.parts)
        if parts:
            return str(parts[0])
    except Exception:
        pass
    return ""


def _compute_file_fingerprint(path_like: str) -> str:
    """
    计算数据文件轻量指纹：size + mtime + 前 2MB sha1。
    """
    try:
        p = Path(str(path_like or "")).expanduser().resolve()
        if not p.exists() or not p.is_file():
            return ""
        stat = p.stat()
        h = hashlib.sha1()
        with open(p, "rb") as f:
            h.update(f.read(2 * 1024 * 1024))
        return f"{int(stat.st_size)}:{int(stat.st_mtime)}:{h.hexdigest()}"
    except Exception:
        return ""


def _load_json_dict(path: Path) -> Dict[str, Any]:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            obj = json.load(f)
        if isinstance(obj, dict):
            return obj
    except Exception:
        pass
    return {}


def _find_reusable_analysis_result(
    *,
    kind: str,
    save_path: str,
    current_task_id: str,
    preferred_task_id: str = "",
) -> Dict[str, Any]:
    """
    在历史任务中查找可复用分析结果。优先顺序：
    1) preferred_task_id
    2) 数据文件所在 task_id
    3) 最近任务
    """
    if kind not in {"sentiment", "timeline"}:
        return {}
    if not save_path:
        return {}

    save_resolved = ""
    try:
        save_resolved = str(Path(save_path).expanduser().resolve())
    except Exception:
        save_resolved = str(save_path)
    data_task_id = _extract_task_id_from_path(save_path)
    data_fp = _compute_file_fingerprint(save_path)

    sandbox_root = get_sandbox_dir()
    if not sandbox_root.exists():
        return {}

    task_order: List[str] = []
    for tid in (preferred_task_id, data_task_id):
        t = str(tid or "").strip()
        if t and t not in task_order and t != current_task_id:
            task_order.append(t)

    others: List[Tuple[float, str]] = []
    for td in sandbox_root.iterdir():
        if not td.is_dir():
            continue
        tid = td.name
        if tid == current_task_id or tid in task_order:
            continue
        try:
            mt = float(td.stat().st_mtime)
        except Exception:
            mt = 0.0
        others.append((mt, tid))
    others.sort(key=lambda x: x[0], reverse=True)
    task_order.extend([tid for _, tid in others])

    patterns = {
        "sentiment": ["sentiment_analysis_*.json"],
        "timeline": ["timeline_analysis_*.json"],
    }.get(kind, [])

    for tid in task_order:
        process_dir = sandbox_root / tid / "过程文件"
        if not process_dir.exists():
            continue
        candidates: List[Path] = []
        for pat in patterns:
            candidates.extend(list(process_dir.glob(pat)))
        candidates = [p for p in candidates if p.is_file()]
        candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        for fp in candidates:
            obj = _load_json_dict(fp)
            if not obj:
                continue
            if str(obj.get("error", "")).strip():
                continue

            # 情感结果复用要求：必须是大模型重判结果（避免复用旧情感列）
            if kind == "sentiment":
                st = obj.get("statistics") if isinstance(obj.get("statistics"), dict) else {}
                if str(st.get("sentiment_source", "")).strip() != "llm_scoring":
                    continue

            path_hit = False
            fp_hit = False
            raw_data_path = str(obj.get("data_file_path", "") or "").strip()
            raw_data_fp = str(obj.get("data_file_fingerprint", "") or "").strip()
            if raw_data_path:
                try:
                    path_hit = str(Path(raw_data_path).expanduser().resolve()) == save_resolved
                except Exception:
                    path_hit = raw_data_path == save_resolved
            if raw_data_fp and data_fp:
                fp_hit = raw_data_fp == data_fp

            # 兼容旧产物：没有元数据时，仅允许复用“同一数据 task”中的结果
            legacy_same_task = (not raw_data_path and not raw_data_fp and tid == data_task_id)
            if not (path_hit or fp_hit or legacy_same_task):
                continue

            out = dict(obj)
            out["result_file_path"] = str(fp)
            out["_reused_from_task_id"] = tid
            out["_reused_kind"] = kind
            out["_reuse_match"] = {
                "path_hit": path_hit,
                "fp_hit": fp_hit,
                "legacy_same_task": legacy_same_task,
            }
            return out

    return {}


def _graph_valid_result_count(block: Any) -> int:
    if not isinstance(block, dict):
        return 0
    rows = block.get("results")
    if not isinstance(rows, list):
        return 0
    c = 0
    for row in rows:
        if isinstance(row, dict):
            if str(row.get("error", "") or "").strip():
                continue
            if any(str(row.get(k, "") or "").strip() for k in ("title", "name", "description", "source", "dimension")):
                c += 1
        elif row:
            c += 1
    return c


def _graph_trim_block(block: Any, keep: int) -> Dict[str, Any]:
    if not isinstance(block, dict):
        return {"results": [], "count": 0}
    rows = block.get("results")
    if not isinstance(rows, list):
        out = dict(block)
        out["results"] = []
        out["count"] = 0
        return out
    keep_n = max(0, keep)
    out = dict(block)
    out_rows = rows[:keep_n]
    out["results"] = out_rows
    out["count"] = len(out_rows)
    return out


def _build_uniform_search_matrix(search_words: List[str], target_total: int) -> Dict[str, int]:
    """
    当 data_num 不可用时，按关键词均分生成兜底采集矩阵，确保流程仍可进入 data_collect。
    """
    words = [str(w or "").strip() for w in (search_words or []) if str(w or "").strip()]
    if not words:
        return {}

    total = max(1, int(target_total or 1))
    n = len(words)
    base = max(1, total // n)
    matrix: Dict[str, int] = {w: base for w in words}
    assigned = base * n

    # 把余数补给前几个词，保证总量尽量贴近 target_total。
    remain = max(0, total - assigned)
    for i in range(remain):
        matrix[words[i % n]] += 1
    return matrix


def _sanitize_search_matrix(raw: Any, target_total: int) -> Dict[str, int]:
    """
    将 data_num 返回的 search_matrix 清洗为 data_collect 可接受的格式：
    - key: 非空字符串
    - value: int 且 >= 1
    同时尽量让总量贴近 target_total（当 target_total < 关键词数时，保留前 target_total 个词，每个分配 1）。
    """
    if not isinstance(raw, dict):
        return {}

    items: list[tuple[str, int]] = []
    for k, v in raw.items():
        key = str(k or "").strip()
        if not key:
            continue
        try:
            count = int(v)
        except Exception:
            continue
        if count <= 0:
            continue
        items.append((key, count))

    if not items:
        return {}

    # 合并重复 key（理论上不应出现，但防御性处理）
    merged: Dict[str, int] = {}
    for key, count in items:
        merged[key] = merged.get(key, 0) + count

    target = max(1, int(target_total or 1))
    keys = list(merged.keys())
    n = len(keys)

    # target 小于关键词数时，无法做到每个>=1且总量<=target：保留“高权重”前 target 个词
    if target < n:
        top_keys = [k for k, _ in sorted(merged.items(), key=lambda kv: kv[1], reverse=True)[:target]]
        return {k: 1 for k in top_keys}

    current_sum = sum(merged.values())
    if current_sum == target:
        return merged

    # sum 过小：用轮询补齐
    if current_sum < target:
        out = dict(merged)
        remain = target - current_sum
        for i in range(remain):
            out[keys[i % n]] += 1
        return out

    # sum 过大：按比例缩放，保证每个>=1，再做微调到 target
    scaled: Dict[str, int] = {}
    for k in keys:
        scaled[k] = max(1, int(round(merged[k] * target / current_sum)))

    # 缩放后的和可能偏离 target，做确定性微调
    sum_scaled = sum(scaled.values())
    if sum_scaled > target:
        # 从计数最大的开始减，直到命中 target（保持 >=1）
        for k, _ in sorted(scaled.items(), key=lambda kv: kv[1], reverse=True):
            if sum_scaled <= target:
                break
            if scaled[k] > 1:
                scaled[k] -= 1
                sum_scaled -= 1
        # 若仍然大于 target（极端情况下全是 1），则截断保留前 target 个
        if sum_scaled > target:
            top_keys = [k for k, _ in sorted(scaled.items(), key=lambda kv: kv[1], reverse=True)[:target]]
            return {k: 1 for k in top_keys}
        return scaled

    if sum_scaled < target:
        remain = target - sum_scaled
        for i in range(remain):
            scaled[keys[i % n]] += 1
    return scaled


def _fallback_sentiment_from_csv(data_file_path: str) -> Dict[str, Any]:
    """
    当 analysis_sentiment 失败时，从原始 CSV 的“情感/情绪/emotion”列做兜底统计。
    仅提供统计分布（不生成 LLM 摘要），确保报告至少有可用结果。
    """
    import csv

    p = str(data_file_path or "").strip()
    if not p:
        return {
            "error": "sentiment fallback 失败：data_file_path 为空",
            "statistics": {},
            "positive_summary": [],
            "negative_summary": [],
            "result_file_path": "",
        }

    counts: Dict[str, int] = {}
    total = 0
    try:
        with open(p, "r", encoding="utf-8", errors="replace", newline="") as f:
            reader = csv.DictReader(f)
            if not reader.fieldnames:
                raise ValueError("CSV 无表头")

            # 常见列名：情感 / 情绪 / emotion
            candidates = ["情感", "情绪", "emotion", "Emotion", "sentiment", "Sentiment"]
            col = ""
            for c in candidates:
                if c in reader.fieldnames:
                    col = c
                    break
            if not col:
                raise ValueError(f"未找到情感列，fieldnames={reader.fieldnames[:20]}")

            for row in reader:
                raw = str((row.get(col) or "")).strip()
                if not raw:
                    continue
                total += 1
                # 归一化：尽量映射为 正面/负面/中性，其余归入 raw
                v = raw
                if any(k in raw for k in ("正", "积极", "支持", "好评")):
                    v = "正面"
                elif any(k in raw for k in ("负", "消极", "反对", "差评", "骂", "愤怒")):
                    v = "负面"
                elif any(k in raw for k in ("中", "一般", "客观", "无明显")):
                    v = "中性"
                counts[v] = counts.get(v, 0) + 1
    except Exception as e:
        return {
            "error": f"sentiment fallback 失败：{str(e)}",
            "statistics": {},
            "positive_summary": [],
            "negative_summary": [],
            "result_file_path": "",
        }

    if total <= 0:
        return {
            "error": "sentiment fallback 无有效情感数据（列为空）",
            "statistics": {},
            "positive_summary": [],
            "negative_summary": [],
            "result_file_path": "",
        }

    def _pct(x: int) -> float:
        return round(100.0 * float(x) / float(total), 2)

    statistics = {
        "total": total,
        "distribution": {k: {"count": v, "pct": _pct(v)} for k, v in sorted(counts.items(), key=lambda kv: kv[1], reverse=True)},
        "positive": {"count": counts.get("正面", 0), "pct": _pct(counts.get("正面", 0))},
        "negative": {"count": counts.get("负面", 0), "pct": _pct(counts.get("负面", 0))},
        "neutral": {"count": counts.get("中性", 0), "pct": _pct(counts.get("中性", 0))},
        "sentiment_source": "existing_column_fallback",
    }

    return {
        "error": "",
        "statistics": statistics,
        "positive_summary": [],
        "negative_summary": [],
        "result_file_path": "",
    }


def _normalize_opt_str(value: Any) -> Optional[str]:
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    return s


def _infer_event_type_from_text(text: str) -> str:
    s = str(text or "")
    if any(k in s for k in ("猝死", "去世", "身亡", "死亡", "事故", "抢救")):
        return "突发事故"
    if any(k in s for k in ("谣言", "传闻", "辟谣", "不实")):
        return "网络谣言"
    if any(k in s for k in ("品牌", "公关", "危机", "翻车")):
        return "品牌危机"
    return "突发事故"


def _infer_domain_from_text(text: str) -> str:
    s = str(text or "")
    if any(k in s for k in ("控烟", "禁烟", "吸烟", "抽烟", "二手烟", "烟草", "无烟", "烟卡", "电子烟")):
        return "控烟"
    if any(k in s for k in ("教育", "考研", "高考", "学校", "老师", "张雪峰")):
        return "教育"
    if any(k in s for k in ("医疗", "医院", "医生", "病历", "健康")):
        return "医疗"
    if any(k in s for k in ("平台", "互联网", "流量", "社交媒体")):
        return "互联网"
    return "互联网"


def _infer_stage_from_text(text: str) -> str:
    s = str(text or "")
    if any(k in s for k in ("讣告", "确认", "官宣", "全网热议", "冲上热搜", "爆发")):
        return "爆发期"
    if any(k in s for k in ("持续讨论", "扩散", "发酵")):
        return "扩散期"
    return "爆发期"


def _set_session_final_query(session_manager: SessionManager, task_id: str, final_query: str) -> None:
    session_data = session_manager.load_session(task_id)
    if session_data:
        session_manager.save_session(task_id, session_data, final_query=final_query)


def _normalize_tokens(text: str) -> set[str]:
    """
    轻量分词：用于历史经验相似度匹配（非严格 NLP，仅用于复用检索方案）。
    """
    if not text:
        return set()
    cleaned = re.sub(r"[^\w\u4e00-\u9fff]+", " ", text.lower())
    segments = re.findall(r"[a-z0-9]+|[\u4e00-\u9fff]+", cleaned)
    stop_words = {"分析", "舆情", "舆论", "事件", "相关", "一下", "帮我", "请帮", "进行", "这个", "那个", "报告"}

    tokens: set[str] = set()
    for seg in segments:
        s = seg.strip()
        if not s:
            continue
        if s not in stop_words:
            tokens.add(s)
        if re.fullmatch(r"[\u4e00-\u9fff]+", s):
            # 对中文连续短语补充 2~4 字片段，提升“分析…”与“分析一下…”等近似 query 的召回
            max_n = min(4, len(s))
            for n in range(2, max_n + 1):
                for i in range(0, len(s) - n + 1):
                    gram = s[i : i + n]
                    if gram and gram not in stop_words:
                        tokens.add(gram)
    return tokens


def _build_reference_query(user_query: str, search_plan: Dict[str, Any]) -> str:
    """
    构造更聚焦的参考检索 query：优先保留用户原始意图，避免被宽泛 eventIntroduction 稀释。
    """
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


def _load_top_keywords(keyword_stats_path: str, *, max_items: int = 80) -> List[str]:
    """Load top keyword words from keyword_stats output."""
    p = Path(str(keyword_stats_path or "").strip())
    if not p.exists():
        return []
    try:
        with open(p, "r", encoding="utf-8", errors="replace") as f:
            obj = json.load(f)
    except Exception:
        return []
    items = obj.get("top_keywords")
    if not isinstance(items, list):
        return []
    words: List[str] = []
    for it in items[: max(20, max_items)]:
        if not isinstance(it, dict):
            continue
        w = str(it.get("word") or "").strip()
        if not w:
            continue
        words.append(w)
        if len(words) >= max_items:
            break
    return words


def _topic_relevance_metrics(
    *,
    user_query: str,
    search_words: List[str],
    top_keywords: List[str],
) -> Dict[str, Any]:
    """Estimate topic relevance between query anchors and top keywords.

    Guard token rules are intentionally conservative (no n-gram expansion),
    otherwise anchor_tokens can explode and artificially lower coverage.
    """

    stop = {
        "分析",
        "舆情",
        "舆论",
        "事件",
        "相关",
        "一下",
        "帮我",
        "请帮",
        "进行",
        "这个",
        "那个",
        "报告",
        # 泛化词：对 topic guard 贡献低，且容易误导 overlap_terms
        "公共",
        "公共场所",
        "场所",
    }

    def _anchorize(text: str) -> set[str]:
        cleaned = re.sub(r"[^\w\u4e00-\u9fff]+", " ", str(text or "").lower())
        segs = re.findall(r"[a-z0-9]+|[\u4e00-\u9fff]+", cleaned)
        out: set[str] = set()
        for seg in segs:
            s = seg.strip()
            if not s or s in stop:
                continue
            if len(s) >= 2:
                out.add(s)
        return out

    anchor_tokens: set[str] = set()
    anchor_tokens |= _anchorize(user_query)
    for w in search_words[:10]:
        ww = str(w or "").strip()
        if ww.startswith("#") and ww.endswith("#") and len(ww) > 3:
            ww = ww.strip("#")
        ww = ww.lstrip("#")
        anchor_tokens |= _anchorize(ww)

    keyword_tokens: set[str] = set()
    keyword_raw: List[str] = []
    for w in top_keywords[:80]:
        ws = str(w or "").strip().lower()
        if not ws:
            continue
        keyword_raw.append(ws)
        keyword_tokens |= _anchorize(ws)

    hard_overlap = set(anchor_tokens & keyword_tokens)
    soft_overlap: set[str] = set()
    for a in anchor_tokens:
        if a in hard_overlap:
            soft_overlap.add(a)
            continue
        for kw in keyword_raw:
            # 软匹配：覆盖“14车厢 / 相隔14车厢 / 12306回应”等近似写法
            if a in kw or kw in a:
                soft_overlap.add(a)
                break
            if len(a) >= 3 and re.search(re.escape(a), kw):
                soft_overlap.add(a)
                break

    overlap = sorted(soft_overlap)
    # 分母更保守，避免 anchor 过多时 coverage 被稀释到接近 0
    denom = max(1, min(len(anchor_tokens), 10))
    coverage = float(len(overlap)) / float(denom)

    # phrase coverage: favor event anchors (e.g. "12306", "相隔14车厢") over generic tokens
    def _extract_query_phrases(q: str, ws: List[str]) -> List[str]:
        q0 = re.sub(r"\s+", "", str(q or ""))
        phrases: List[str] = []
        if "12306" in q0:
            phrases.append("12306")
            phrases.append("铁路12306")
        m = re.search(r"相隔\d{1,2}(?:个)?车厢", q0)
        if m:
            phrases.append(m.group(0).replace("个车厢", "车厢"))
        # add explicit search words as phrases (but cap length)
        for x in (ws or [])[:10]:
            s = str(x or "").strip()
            if 2 <= len(s) <= 18:
                phrases.append(s)
        # light chunks from query (3-6 chars) as fallback
        for seg in re.findall(r"[\u4e00-\u9fff]{3,6}", q0):
            if seg in {"舆情分析", "事件分析", "分析报告"}:
                continue
            phrases.append(seg)
            if len(phrases) >= 14:
                break
        # dedup keep order
        out: List[str] = []
        seen: set[str] = set()
        for p in phrases:
            t = str(p or "").strip()
            if not t:
                continue
            if t in seen:
                continue
            seen.add(t)
            out.append(t)
        return out[:12]

    query_phrases = _extract_query_phrases(user_query, search_words)
    phrase_hits: List[str] = []
    kw_text = " ".join(str(k or "") for k in top_keywords[:120])
    for p in query_phrases:
        if p and p in kw_text:
            phrase_hits.append(p)
    phrase_denom = max(1, min(len(query_phrases), 8))
    coverage_phrase = float(len(set(phrase_hits))) / float(phrase_denom)
    composite = round(0.55 * coverage + 0.45 * coverage_phrase, 4)

    return {
        "anchor_count": len(anchor_tokens),
        "keyword_token_count": len(keyword_tokens),
        "overlap_count": len(overlap),
        "overlap_terms": overlap[:20],
        "coverage": round(coverage, 4),
        "coverage_phrase": round(coverage_phrase, 4),
        "phrase_hits": list(dict.fromkeys(phrase_hits))[:12],
        "composite": composite,
    }


def _filter_reference_hits(
    ref_json: Dict[str, Any],
    *,
    user_query: str,
    search_words: List[str],
    min_keep: int = 3,
) -> Dict[str, Any]:
    """
    过滤低相关参考命中，降低“主题漂移”。
    规则：优先保留与 query/关键词有词项重合的结果；若过少则保底保留前 min_keep。
    """
    if not isinstance(ref_json, dict):
        return ref_json
    raw_results = ref_json.get("results")
    if not isinstance(raw_results, list) or not raw_results:
        return ref_json

    q_tokens = _normalize_tokens(str(user_query or ""))
    kw_tokens: set[str] = set()
    for w in search_words:
        kw_tokens.update(_normalize_tokens(str(w)))
    anchor_tokens = {t for t in (q_tokens | kw_tokens) if len(t) >= 2}
    if not anchor_tokens:
        return ref_json

    theory_rows: List[Dict[str, Any]] = []
    event_rows: List[Dict[str, Any]] = []
    for item in raw_results:
        if not isinstance(item, dict):
            continue
        if str(item.get("kind", "") or "").lower() == "theory":
            theory_rows.append(item)
        else:
            event_rows.append(item)

    kept: List[Dict[str, Any]] = []
    dropped = 0
    for item in event_rows:
        title = str(item.get("title", "") or "")
        path = str(item.get("path", "") or "") + " " + str(item.get("source", "") or "")
        snippet = str(item.get("snippet", "") or "")
        hay_tokens = _normalize_tokens(f"{title} {path} {snippet}")
        overlap = len(anchor_tokens & hay_tokens)
        if overlap >= 1:
            item2 = dict(item)
            item2["overlap_terms"] = overlap
            kept.append(item2)
        else:
            dropped += 1

    theory_rows.sort(key=lambda x: float(x.get("score") or 0.0), reverse=True)
    theory_kept = [dict(x) for x in theory_rows[:6]]

    merged: List[Dict[str, Any]] = []
    seen_src: set[str] = set()
    for item in theory_kept + kept:
        src = str(item.get("source") or item.get("path") or "").strip()
        if src:
            if src in seen_src:
                continue
            seen_src.add(src)
        merged.append(item)

    if len(merged) < min_keep:
        merged = [x for x in raw_results if isinstance(x, dict)][:min_keep]
    ref_out = dict(ref_json)
    ref_out["results"] = merged
    ref_out["count"] = len(merged)
    ref_out["_filtered"] = {"dropped": dropped, "anchor_terms": sorted(list(anchor_tokens))[:20]}
    return ref_out


def _jaccard_score(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return float(inter) / float(union) if union else 0.0


def _load_experience_items(limit: int = 300) -> List[Dict[str, Any]]:
    """
    从本地 LTM jsonl 读取历史检索经验。
    """
    path = Path(EXPERIENCE_PATH)
    if not path.exists():
        return []
    rows: List[Dict[str, Any]] = []
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                    if isinstance(obj, dict):
                        rows.append(obj)
                except Exception:
                    continue
    except Exception:
        return []
    return rows[-limit:]


def _find_best_experience(user_query: str) -> Optional[Dict[str, Any]]:
    """
    查找最相似历史经验。
    """
    query_tokens = _normalize_tokens(user_query)
    if not query_tokens:
        return None
    best: Optional[Dict[str, Any]] = None
    best_score = 0.0
    normalized_query = " ".join(sorted(query_tokens))
    for item in _load_experience_items():
        past_query = str(item.get("user_query", "") or "")
        past_tokens = _normalize_tokens(past_query)
        # 精确匹配优先：token 集完全一致直接命中
        if past_tokens and " ".join(sorted(past_tokens)) == normalized_query:
            best = dict(item)
            best["_similarity"] = 1.0
            return best
        score = _jaccard_score(query_tokens, past_tokens)
        if score > best_score:
            best_score = score
            best = item
    if not best:
        return None
    best = dict(best)
    best["_similarity"] = round(best_score, 4)
    # 经验阈值：太低不推荐
    if best_score < 0.08:
        return None
    return best


def _save_experience_item(
    *,
    task_id: str,
    user_query: str,
    search_plan: Dict[str, Any],
    collect_plan: Dict[str, Any],
) -> None:
    """
    将本次可复用经验写入本地 LTM。
    """
    try:
        path = Path(EXPERIENCE_PATH)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "task_id": task_id,
            "user_query": user_query,
            "search_plan": search_plan,
            "collect_plan": collect_plan,
            "saved_at": datetime.now().isoformat(sep=" "),
        }
        with open(path, "a", encoding="utf-8", errors="replace") as f:
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")
        # #region debug_log_H14_experience_saved
        _append_ndjson_log(
            run_id="event_analysis_experience",
            hypothesis_id="H14_experience_saved",
            location="workflow/event_analysis_pipeline.py:save_experience",
            message="历史经验已写入本地 LTM",
            data={"task_id": task_id, "path": EXPERIENCE_PATH},
        )
        # #endregion debug_log_H14_experience_saved
    except Exception:
        # #region debug_log_H14_experience_save_failed
        _append_ndjson_log(
            run_id="event_analysis_experience",
            hypothesis_id="H14_experience_save_failed",
            location="workflow/event_analysis_pipeline.py:save_experience",
            message="历史经验写入失败",
            data={"task_id": task_id, "path": EXPERIENCE_PATH},
        )
        # #endregion debug_log_H14_experience_save_failed
        return


def _is_graph_rag_enabled() -> bool:
    """
    Graph RAG 开关：
    - 显式 false/off -> 关闭
    - 显式 true/on -> 开启
    - 未设置时默认开启（避免“Step10 存在但常被静默跳过”）
    """
    v = os.environ.get("SONA_ENABLE_GRAPH_RAG", "auto").strip().lower()
    if v in ("0", "false", "no", "n", "off"):
        return False
    if v in ("1", "true", "yes", "y", "on"):
        return True
    return True


def run_event_analysis_pipeline(
    user_query: str,
    task_id: str,
    session_manager: SessionManager,
    *,
    debug: bool = False,
    default_threshold: int = 2000,
    existing_data_path: Optional[str] = None,
    skip_data_collect: bool = False,
    force_fresh_start: Optional[bool] = None,
    report_length: Optional[str] = None,
) -> str:
    """
    在 CLI 中运行"4.1 舆情事件分析工作流"。
    
    Args:
        user_query: 用户查询
        task_id: 任务 ID
        session_manager: 会话管理器
        debug: 是否开启调试模式
        default_threshold: 默认数据量阈值
        existing_data_path: 已有数据的文件路径（可选，提供后跳过数据采集）
        skip_data_collect: 是否跳过数据采集阶段（与 existing_data_path 配合使用）
        force_fresh_start: 是否强制全新开始（None 表示按环境变量 SONA_FORCE_FRESH_START）
        report_length: 报告篇幅偏好（短篇/中篇/长篇），传入 ``report_html`` 与模板叙事模型。
    
    Returns:
        report_html 生成的 `file_url`（若为空则返回 html 文件路径）。
    """

    # 关键：让 tools/* 能读取 task_id 写入过程目录
    set_task_id(task_id)
    effective_report_length = normalize_report_length(report_length)
    process_dir = ensure_task_dirs(task_id)
    runtime_harness = RuntimeHarness(task_id=task_id, process_dir=process_dir, user_query=user_query)
    # 进度条在交互式输入时会覆盖回显甚至卡死；因此交互会话默认禁用进度条，仅保留文字步骤输出。
    interactive_session = _is_interactive_session()
    collab_mode = _event_collab_mode()
    collab_enabled = collab_mode != "auto" and interactive_session
    enable_progress = bool(debug and (not collab_enabled) and (not interactive_session))
    progress = _make_progress() if enable_progress else None
    progress_task_id: Optional[int] = None
    progress_total_steps = 7
    progress_started = False
    progress_paused_for_prompt = False

    def _progress_start_if_needed(first_desc: str) -> None:
        nonlocal progress_task_id, progress_started
        if not enable_progress or progress is None:
            return
        if progress_started:
            return
        progress_started = True
        progress.start()
        progress_task_id = progress.add_task(first_desc, total=progress_total_steps)

    def _pause_progress() -> None:
        nonlocal progress_paused_for_prompt
        if not enable_progress or progress is None:
            return
        if not progress_started or progress_paused_for_prompt:
            return
        try:
            progress.stop()
        except Exception:
            return
        progress_paused_for_prompt = True

    def _resume_progress() -> None:
        nonlocal progress_paused_for_prompt
        if not enable_progress or progress is None:
            return
        if not progress_started or not progress_paused_for_prompt:
            return
        try:
            progress.start()
        except Exception:
            return
        progress_paused_for_prompt = False

    def _progress_step(desc: str) -> None:
        if not debug:
            return
        if not enable_progress or progress is None:
            return
        _progress_start_if_needed(desc)
        if progress_task_id is not None:
            progress.update(progress_task_id, description=desc)

    def _progress_advance() -> None:
        if not enable_progress or progress is None:
            return
        if progress_task_id is not None:
            progress.advance(progress_task_id, 1)

    # 让所有 Prompt 输入在暂停进度条时进行，避免输入被覆盖/刷屏。
    _set_prompt_progress_hooks(pause=_pause_progress, resume=_resume_progress)
    # 生成可读别名目录（时间+事件+任务），便于在 sandbox 中人工识别
    try:
        ensure_task_readable_alias(task_id, user_query)
    except Exception:
        pass

    if collab_mode == "manual" and not interactive_session:
        raise RuntimeError(
            "SONA_EVENT_COLLAB_MODE=manual 需要交互式终端（TTY）；"
            "当前会话不可交互，无法执行 y/n 采集方案确认。"
        )
    fresh_start = _force_fresh_start_enabled() if force_fresh_start is None else bool(force_fresh_start)
    if fresh_start:
        existing_data_path = None
        skip_data_collect = False
    # 协同输入步骤需要更充分的人工输入时间：默认 45s，可用 SONA_EVENT_COLLAB_TIMEOUT_SEC 覆盖
    collab_timeout_sec = _collab_timeout(45)

    if debug:
        console.print(f"[green]🔧 进入 EventAnalysisWorkflow[/green] task_id={task_id}")
        console.print(
            f"[dim]协作模式: mode={collab_mode}, interactive={interactive_session}, enabled={collab_enabled}, timeout={collab_timeout_sec}s[/dim]"
        )

    session_manager.add_message(task_id, "user", user_query)
    _set_session_final_query(session_manager, task_id, user_query)

    _append_ndjson_log(
        run_id="event_analysis_collab_mode",
        hypothesis_id="H38_collab_mode_state",
        location="workflow/event_analysis_pipeline.py:startup",
        message="协作模式状态",
        data={
            "mode": collab_mode,
            "interactive_session": interactive_session,
            "collab_enabled": collab_enabled,
            "collab_timeout_sec": collab_timeout_sec,
            "force_fresh_start": fresh_start,
        },
    )

    # ============ 0) 历史经验复用（可跳过 extract） ============
    best_exp = _find_best_experience(user_query) if (_experience_reuse_enabled() and not fresh_start) else None
    # #region debug_log_H9_experience_lookup
    _append_ndjson_log(
        run_id="event_analysis_experience",
        hypothesis_id="H9_experience_lookup",
        location="workflow/event_analysis_pipeline.py:experience_lookup",
        message="历史经验检索结果",
        data={
            "reuse_experience_enabled": _experience_reuse_enabled() and (not fresh_start),
            "found": bool(best_exp),
            "similarity": (best_exp or {}).get("_similarity", 0.0),
            "has_search_plan": bool((best_exp or {}).get("search_plan")),
            "has_collect_plan": bool((best_exp or {}).get("collect_plan")),
        },
    )
    # #endregion debug_log_H9_experience_lookup

    search_plan: Dict[str, Any]
    suggested_collect_plan: Dict[str, Any]
    used_experience = False
    if best_exp and isinstance(best_exp.get("search_plan"), dict) and isinstance(best_exp.get("collect_plan"), dict):
        preview = {
            "similarity": best_exp.get("_similarity", 0.0),
            "history_query": str(best_exp.get("user_query", ""))[:120],
            "search_plan": best_exp.get("search_plan"),
            "collect_plan": best_exp.get("collect_plan"),
        }
        if debug:
            _pretty_print_dict("检测到历史相似案例（可复用经验）", preview)
        similarity = _safe_float(best_exp.get("_similarity", 0.0), 0.0)
        if collab_enabled:
            default_use_history = similarity >= 0.16 or collab_mode == "manual"
            use_history = _prompt_yes_no_timeout(
                f"检测到历史相似经验（sim={round(similarity, 3)}），是否复用并优先跳过采集？(y 复用 / n 不复用)",
                timeout_sec=collab_timeout_sec,
                default_yes=default_use_history,
            )
        else:
            auto_threshold = max(
                0.05,
                min(_safe_float(os.environ.get("SONA_AUTO_HISTORY_SIMILARITY", "0.18"), 0.18), 0.95),
            )
            use_history = similarity >= auto_threshold
            if debug:
                console.print(
                    f"[dim]自动历史复用判定: sim={round(similarity,3)} >= threshold={round(auto_threshold,3)} -> {use_history}[/dim]"
                )
        if use_history:
            search_plan = dict(best_exp.get("search_plan") or {})
            suggested_collect_plan = dict(best_exp.get("collect_plan") or {})
            # 与当前 query 绑定，确保 session 描述等仍按本次 query
            search_plan["eventIntroduction"] = str(search_plan.get("eventIntroduction", "") or "")
            search_plan["searchWords"] = _to_clean_str_list(search_plan.get("searchWords"), max_items=12)
            search_plan["timeRange"] = str(search_plan.get("timeRange", "") or "")
            used_experience = True
            # 历史经验命中时，优先自动复用历史 CSV，避免重复 data_num/data_collect
            if (not skip_data_collect) and (not existing_data_path) and _auto_reuse_history_data_enabled():
                if similarity >= 0.12:
                    history_csv = _resolve_reusable_csv_from_history(best_exp, current_task_id=task_id)
                    if history_csv:
                        existing_data_path = history_csv
                        skip_data_collect = True
                        # #region debug_log_H35_auto_reuse_history_data
                        _append_ndjson_log(
                            run_id="event_analysis_experience",
                            hypothesis_id="H35_auto_reuse_history_data",
                            location="workflow/event_analysis_pipeline.py:auto_reuse_history_data",
                            message="历史经验命中后自动复用历史 CSV，跳过 data_num/data_collect",
                            data={
                                "task_id": task_id,
                                "history_task_id": str(best_exp.get("task_id", "")),
                                "similarity": similarity,
                                "reuse_csv_path": history_csv,
                            },
                        )
                        # #endregion debug_log_H35_auto_reuse_history_data
                        if debug:
                            console.print(f"[green]♻️ 自动复用历史数据[/green] save_path={history_csv}")
            # #region debug_log_H10_experience_reused
            _append_ndjson_log(
                run_id="event_analysis_experience",
                hypothesis_id="H10_experience_reused",
                location="workflow/event_analysis_pipeline.py:experience_reused",
                message="本次执行复用了历史经验",
                data={"similarity": best_exp.get("_similarity", 0.0)},
            )
            # #endregion debug_log_H10_experience_reused
        else:
            search_plan = {}
            suggested_collect_plan = {}
    else:
        search_plan = {}
        suggested_collect_plan = {}

    # ============ 1) 搜索方案生成 ============
    if debug:
        console.print("[bold]Step1: extract_search_terms[/bold]")

    if not used_experience:
        step1_start = time.time()
        _progress_step("Step1: extract_search_terms")
        # 默认回到 extract_search_terms：更稳定、可控；微博智搜方案仅作为可选增强开关。
        enable_weibo_plan = str(os.environ.get("SONA_SEARCH_PLAN_USE_WEIBO_AISEARCH", "false")).strip().lower() in (
            "1",
            "true",
            "yes",
            "y",
            "on",
        )
        plan_json: Dict[str, Any]
        if enable_weibo_plan:
            try:
                plan_json = _build_search_plan_from_weibo_aisearch(
                    user_query=user_query,
                    process_dir=process_dir,
                )
                _append_ndjson_log(
                    run_id="event_analysis_pre_confirm",
                    hypothesis_id="H40_search_plan_from_weibo_aisearch",
                    location="workflow/event_analysis_pipeline.py:search_plan_from_weibo_aisearch",
                    message="使用微博智搜结果构建 search_plan",
                    data=plan_json.get("_weibo_meta", {}),
                )
            except Exception as weibo_err:
                _append_ndjson_log(
                    run_id="event_analysis_fallback",
                    hypothesis_id="H41_search_plan_weibo_fallback_to_extract",
                    location="workflow/event_analysis_pipeline.py:search_plan_weibo_fallback",
                    message="微博智搜构建 search_plan 失败，已回退 extract_search_terms",
                    data={"error": str(weibo_err)},
                )
                plan_json = _invoke_tool_to_json(extract_search_terms, {"query": user_query})
        else:
            plan_json = _invoke_tool_to_json(extract_search_terms, {"query": user_query})
        # #region debug_log_H13_step_timing_extract
        _append_ndjson_log(
            run_id="event_analysis_timing",
            hypothesis_id="H13_step_timing_extract",
            location="workflow/event_analysis_pipeline.py:after_extract_search_terms",
            message="extract_search_terms 耗时",
            data={"elapsed_sec": round(time.time() - step1_start, 3)},
        )
        # #endregion debug_log_H13_step_timing_extract
        search_plan = {
            "eventIntroduction": str(plan_json.get("eventIntroduction", "") or ""),
            "searchWords": _to_clean_str_list(plan_json.get("searchWords"), max_items=12),
            "timeRange": _normalize_time_range_input(str(plan_json.get("timeRange", "") or "")),
        }
        # 保留扩展字段（兼容 extract_search_terms 旧输出：缺失则忽略）
        for k in (
            "version",
            "keywordGroups",
            "secondaryKeywords",
            "queryTemplates",
            "verificationChecklist",
            "evidenceSnippets",
            "_weibo_meta",
            "platforms",
            "netinsightKeywordMode",
            "netinsightAdvancedQuery",
        ):
            if k in plan_json and k not in search_plan:
                search_plan[k] = plan_json.get(k)
        _progress_advance()

        if not search_plan["searchWords"]:
            fallback_words = _fallback_search_words_from_query(user_query)
            if fallback_words:
                search_plan["searchWords"] = fallback_words
                # #region debug_log_H28_search_words_fallback
                _append_ndjson_log(
                    run_id="event_analysis_fallback",
                    hypothesis_id="H28_search_words_fallback",
                    location="workflow/event_analysis_pipeline.py:extract_search_words_fallback",
                    message="extract_search_terms 返回空 searchWords，已使用 query 兜底关键词",
                    data={"fallback_words": fallback_words[:8]},
                )
                # #endregion debug_log_H28_search_words_fallback
            else:
                raise ValueError("searchWords 为空，且无法从 query 提取兜底关键词")
        if not _validate_time_range(search_plan["timeRange"]):
            fallback_days = _infer_default_time_range_days(user_query)
            fallback_time_range = _build_default_time_range(fallback_days)
            search_plan["timeRange"] = fallback_time_range
            # #region debug_log_H29_time_range_fallback
            _append_ndjson_log(
                run_id="event_analysis_fallback",
                hypothesis_id="H29_time_range_fallback",
                location="workflow/event_analysis_pipeline.py:extract_time_range_fallback",
                message="extract_search_terms 返回非法 timeRange，已回退默认时间范围",
                data={"fallback_time_range": fallback_time_range, "fallback_days": fallback_days},
            )
            # #endregion debug_log_H29_time_range_fallback

    # ============ 2) 提出建议的搜索采集方案并等待 y/n（20s 无响应默认继续） ============
        # 该"采集方案"是针对 extract_search_terms 的扩展描述，最终仍映射到现有 data_num / data_collect 能力。
        # 其中 boolean 与关键词 ; 语义需要在真实运行中与 API 行为对齐（后续你看 debug log 我们再校准）。
        default_platforms = _platforms_from_search_plan(search_plan)
        platform_count = max(1, len(default_platforms))
        auto_data_num_workers = max(2, min(platform_count, 8))
        auto_data_collect_workers = max(1, min(platform_count, 8))
        auto_analysis_workers = max(2, min(platform_count, 4))
        suggested_collect_plan = {
            "keyword_combination_mode": "逐词检索并合并（当前实现）",
            "boolean_strategy": "OR（当前实现：各词分别检索再合并）",
            "keywords_join_with": ";",
            "platforms": default_platforms,
            "time_range": search_plan["timeRange"],
            "return_count": max(200, min(_safe_int(os.environ.get("SONA_RETURN_COUNT", ""), 2000), 10000)),
            "data_num_workers": max(
                1,
                min(
                    _safe_int(os.environ.get("SONA_DATA_NUM_MAX_WORKERS", str(auto_data_num_workers)), auto_data_num_workers),
                    8,
                ),
            ),
            "data_collect_workers": max(
                1,
                min(
                    _safe_int(
                        os.environ.get("SONA_DATA_COLLECT_MAX_WORKERS", str(auto_data_collect_workers)),
                        auto_data_collect_workers,
                    ),
                    8,
                ),
            ),
            "analysis_workers": max(
                1,
                min(
                    _safe_int(os.environ.get("SONA_ANALYSIS_MAX_WORKERS", str(auto_analysis_workers)), auto_analysis_workers),
                    8,
                ),
            ),
            "searchWords_preview": search_plan["searchWords"][:10],
        }
    else:
        # 复用经验时保证关键字段健全
        search_plan["searchWords"] = _to_clean_str_list(search_plan.get("searchWords"), max_items=12)
        if not search_plan.get("searchWords"):
            fallback_words = _fallback_search_words_from_query(user_query)
            if fallback_words:
                search_plan["searchWords"] = fallback_words
            else:
                raise ValueError("复用经验失败：searchWords 为空，且无法从 query 兜底")
        if not _validate_time_range(str(search_plan.get("timeRange", ""))):
            fallback_days = _infer_default_time_range_days(user_query)
            search_plan["timeRange"] = _build_default_time_range(fallback_days)
        suggested_collect_plan = {
            "keyword_combination_mode": str(suggested_collect_plan.get("keyword_combination_mode") or "逐词检索并合并（当前实现）"),
            "boolean_strategy": str(suggested_collect_plan.get("boolean_strategy") or "OR（当前实现：各词分别检索再合并）"),
            "keywords_join_with": ";",
            "platforms": suggested_collect_plan.get("platforms") or _platforms_from_search_plan(search_plan),
            "time_range": _normalize_time_range_input(str(suggested_collect_plan.get("time_range") or search_plan["timeRange"])) or search_plan["timeRange"],
            "return_count": max(200, min(_safe_int(suggested_collect_plan.get("return_count"), 2000), 10000)),
            "data_num_workers": max(1, min(_safe_int(suggested_collect_plan.get("data_num_workers"), 4), 8)),
            "data_collect_workers": max(1, min(_safe_int(suggested_collect_plan.get("data_collect_workers"), 3), 8)),
            "analysis_workers": max(1, min(_safe_int(suggested_collect_plan.get("analysis_workers"), 2), 8)),
            "searchWords_preview": search_plan["searchWords"][:10],
        }

    # 将多来源 search_plan（微博智搜/抽取/经验复用）统一收敛为 search_plan_v1 契约。
    search_plan = _coerce_search_plan_contract(search_plan, user_query=user_query)
    if not search_plan.get("searchWords"):
        fallback_words = _fallback_search_words_from_query(user_query)
        if fallback_words:
            search_plan["searchWords"] = fallback_words
    if not _validate_time_range(str(search_plan.get("timeRange", ""))):
        fallback_days = _infer_default_time_range_days(user_query)
        search_plan["timeRange"] = _build_default_time_range(fallback_days)
    suggested_collect_plan["platforms"] = _to_clean_str_list(
        suggested_collect_plan.get("platforms") or _platforms_from_search_plan(search_plan),
        max_items=12,
    ) or ["微博"]
    suggested_collect_plan["searchWords_preview"] = _to_clean_str_list(search_plan.get("searchWords"), max_items=10)
    suggested_collect_plan["time_range"] = (
        _normalize_time_range_input(str(suggested_collect_plan.get("time_range") or search_plan.get("timeRange") or ""))
        or str(search_plan.get("timeRange") or "")
    )

    # #region debug_log_H1_search_collect_plan_generated
    _append_ndjson_log(
        run_id="event_analysis_pre_confirm",
        hypothesis_id="H1_search_collect_plan_generated",
        location="workflow/event_analysis_pipeline.py:after_collect_plan",
        message="生成建议搜索采集方案",
        data={
            "timeRange": search_plan["timeRange"],
            "return_count": suggested_collect_plan["return_count"],
            "platforms": suggested_collect_plan["platforms"],
        },
    )
    # #endregion debug_log_H1_search_collect_plan_generated

    if debug:
        _pretty_print_dict("建议搜索采集方案（等待确认）", suggested_collect_plan)

    if debug:
        console.print("[bold]Step2: confirm_collect_plan[/bold]")
    if collab_enabled:
        decision_action = _prompt_collect_plan_confirmation(edited=False)
        accept = decision_action == "accept"
    else:
        accept = True
        decision_action = "accept"
    runtime_harness.record(
        "collect_plan_first_decision",
        {
            "collab_enabled": collab_enabled,
            "decision": "accept" if accept else "reject",
            "timeout_sec": collab_timeout_sec if collab_enabled else 0,
            "action": decision_action,
        },
    )

    # #region debug_log_H2_timeout_or_user_choice
    _append_ndjson_log(
        run_id="event_analysis_pre_confirm",
        hypothesis_id="H2_timeout_or_user_choice",
        location="workflow/event_analysis_pipeline.py:confirm_choice",
        message="用户对采集方案的 y/n 决策结果记录",
        data={"accept": accept, "timeout_sec": collab_timeout_sec if collab_enabled else 0, "collab_enabled": collab_enabled},
    )
    # #endregion debug_log_H2_timeout_or_user_choice

    # 若用户选择 n，则允许编辑"平台、返回条数、时间范围、布尔策略"等（仍先通过 y 再执行）
    if collab_enabled and not accept:
        continue_with_edit = decision_action == "edit"
        if not continue_with_edit:
            runtime_harness.record(
                "collect_plan_outcome",
                {"outcome": "aborted", "reason": "user_reject_and_no_edit"},
            )
            runtime_harness.finalize()
            raise RuntimeError("用户拒绝当前采集方案并选择不修改，本次执行中止。")
        default_platforms = suggested_collect_plan.get("platforms") or ["微博"]
        platforms_hint = "；".join(default_platforms)
        platforms_list = _supported_platforms_for_netinsight()
        platform_menu = " / ".join([f"{i+1}:{p}" for i, p in enumerate(platforms_list)])
        platform_in_raw = Prompt.ask(
            f"修改平台（多选：输入 ALL 或 编号如 1,3,5 或 名称用 ; 分隔）。可选：{platform_menu}",
            default=platforms_hint,
        ).strip() or platforms_hint
        platforms_in = _parse_platforms_input(platform_in_raw, default=default_platforms)
        # return_count：最大 10000
        return_count_in = Prompt.ask(
            "修改返回结果条数 return_count（1-10000；不填则默认）",
            default=str(suggested_collect_plan["return_count"]),
        ).strip() or str(suggested_collect_plan["return_count"])
        return_count_in_int = _safe_int(return_count_in, int(suggested_collect_plan["return_count"]))
        return_count_in_int = max(1, min(return_count_in_int, 10000))

        # timeRange
        suggested_collect_plan["time_range"] = _prompt_time_range_with_quick_choices(
            default_time_range=str(suggested_collect_plan["time_range"])
        )

        # boolean strategy（目前仅影响我们如何拼接 searchWords 给 data_num）
        boolean_in = Prompt.ask(
            "修改布尔策略（OR 或 AND；默认 OR）",
            default=str(suggested_collect_plan["boolean_strategy"]).startswith("AND") and "AND" or "OR",
        ).strip().upper()
        if boolean_in not in ("OR", "AND"):
            boolean_in = "OR"

        suggested_collect_plan["platforms"] = platforms_in
        suggested_collect_plan["return_count"] = return_count_in_int
        suggested_collect_plan["boolean_strategy"] = f"{boolean_in}（当前实现：{ '逐词分别检索再合并' if boolean_in=='OR' else '单次表达式合并（依赖 API 对 ; 的支持）' }）"
        data_num_workers_in = Prompt.ask(
            "修改 data_num 并发（1-8）",
            default=str(suggested_collect_plan.get("data_num_workers", 4)),
        ).strip()
        data_collect_workers_in = Prompt.ask(
            "修改 data_collect 并发（1-8）",
            default=str(suggested_collect_plan.get("data_collect_workers", 3)),
        ).strip()
        analysis_workers_in = Prompt.ask(
            "修改分析并发（1-8）",
            default=str(suggested_collect_plan.get("analysis_workers", 2)),
        ).strip()
        suggested_collect_plan["data_num_workers"] = max(1, min(_safe_int(data_num_workers_in, 4), 8))
        suggested_collect_plan["data_collect_workers"] = max(1, min(_safe_int(data_collect_workers_in, 3), 8))
        suggested_collect_plan["analysis_workers"] = max(1, min(_safe_int(analysis_workers_in, 2), 8))

        # #region debug_log_H1_search_collect_plan_edited
        _append_ndjson_log(
            run_id="event_analysis_pre_confirm",
            hypothesis_id="H1_search_collect_plan_edited",
            location="workflow/event_analysis_pipeline.py:edit_collect_plan",
            message="用户在采集方案 n 分支下进行了编辑",
            data={
                "platforms": platforms_in,
                "return_count": return_count_in_int,
                "boolean": boolean_in,
            },
        )
        # #endregion debug_log_H1_search_collect_plan_edited

        # 再次确认 y/n（仍保留 20s 默认继续）
        edited_action = _prompt_collect_plan_confirmation(edited=True)
        accept = edited_action == "accept"

        # #region debug_log_H2_timeout_or_user_choice_after_edit
        _append_ndjson_log(
            run_id="event_analysis_pre_confirm",
            hypothesis_id="H2_timeout_or_user_choice_after_edit",
            location="workflow/event_analysis_pipeline.py:confirm_choice_after_edit",
            message="用户对编辑后采集方案的 y/n 决策结果记录",
            data={"accept": accept, "timeout_sec": collab_timeout_sec, "action": edited_action},
        )
        # #endregion debug_log_H2_timeout_or_user_choice_after_edit

        if not accept:
            runtime_harness.record(
                "collect_plan_outcome",
                {"outcome": "aborted", "reason": "user_reject_after_edit"},
            )
            runtime_harness.finalize()
            raise RuntimeError("用户未确认采集方案（选择 n），本次执行中止。")
        runtime_harness.record("collect_plan_outcome", {"outcome": "edited_then_accept"})
    else:
        runtime_harness.record(
            "collect_plan_outcome",
            {"outcome": "accepted_directly", "collab_enabled": collab_enabled},
        )

    # 经验前置落库：搜索/采集方案一旦确认就写入，避免后续步骤失败导致无可复用经验
    _save_experience_item(
        task_id=task_id,
        user_query=user_query,
        search_plan=search_plan,
        collect_plan={
            "keyword_combination_mode": suggested_collect_plan.get("keyword_combination_mode"),
            "boolean_strategy": suggested_collect_plan.get("boolean_strategy"),
            "keywords_join_with": suggested_collect_plan.get("keywords_join_with"),
            "platforms": suggested_collect_plan.get("platforms"),
            "time_range": suggested_collect_plan.get("time_range"),
            "return_count": suggested_collect_plan.get("return_count"),
            "searchWords_preview": suggested_collect_plan.get("searchWords_preview"),
        },
    )

    # ============ 2.5) 跳过数据采集：使用现有数据 ============
    save_path: str = ""
    
    # 样本不足回退：在协作模式下允许“回到采集方案编辑并重采”
    recollect_round = 0
    platform_row_distribution: Dict[str, int] = {}
    selected_platforms: List[str] = []
    while True:
        if skip_data_collect and existing_data_path:
            # 用户选择使用现有数据，跳过 data_num 和 data_collect
            if debug:
                console.print(f"[bold yellow]⏭️ 跳过数据采集，使用现有数据:[/bold yellow] {existing_data_path}")

            # 将现有路径解析为可直接分析的 CSV
            save_path = _resolve_to_csv_path(existing_data_path)

            if not search_plan.get("eventIntroduction"):
                search_plan["eventIntroduction"] = user_query
            selected_platforms = _to_clean_str_list(suggested_collect_plan.get("platforms"), max_items=12) or ["existing_data"]
            platform_row_distribution = {"existing_data": _count_csv_rows(save_path)}

            _append_ndjson_log(
                run_id="event_analysis_skip_collect",
                hypothesis_id="H27_skip_data_collect",
                location="workflow/event_analysis_pipeline.py:skip_data_collect",
                message="跳过数据采集阶段，使用现有数据",
                data={"existing_data_path": existing_data_path},
            )

            if debug:
                console.print(f"[green]✅ 使用现有数据，save_path={save_path}[/green]")
        else:
            # ============ 3) 数量分配（data_num）- 仅在需要采集数据时执行 ============
            if debug:
                console.print("[bold]Step3: data_num[/bold]")
            _progress_step("Step3: data_num")

            platforms = suggested_collect_plan.get("platforms") or ["微博"]
            selected_platforms = _to_clean_str_list(platforms, max_items=12) or ["微博"]
            return_count = _safe_int(suggested_collect_plan.get("return_count"), default_threshold)
            return_count = max(1, min(return_count, 10000))
            # 并发参数优先级：显式环境变量 > 当前采集方案（历史经验） > 默认值
            data_num_workers = max(
                1,
                min(
                    _safe_int(
                        os.environ.get("SONA_DATA_NUM_MAX_WORKERS", suggested_collect_plan.get("data_num_workers")),
                        4,
                    ),
                    8,
                ),
            )
            data_collect_workers = max(
                1,
                min(
                    _safe_int(
                        os.environ.get("SONA_DATA_COLLECT_MAX_WORKERS", suggested_collect_plan.get("data_collect_workers")),
                        3,
                    ),
                    8,
                ),
            )
            analysis_workers = max(
                1,
                min(
                    _safe_int(
                        os.environ.get("SONA_ANALYSIS_MAX_WORKERS", suggested_collect_plan.get("analysis_workers")),
                        2,
                    ),
                    8,
                ),
            )
            os.environ["SONA_DATA_NUM_MAX_WORKERS"] = str(data_num_workers)
            os.environ["SONA_DATA_COLLECT_MAX_WORKERS"] = str(data_collect_workers)
            os.environ["SONA_ANALYSIS_MAX_WORKERS"] = str(analysis_workers)

            boolean_strategy = str(suggested_collect_plan.get("boolean_strategy") or "")
            boolean_mode = "AND" if boolean_strategy.upper().startswith("AND") else "OR"
            search_words_for_collect, sw_level = _pick_search_words_for_round(
                base_words=search_plan.get("searchWords", []),
                user_query=user_query,
                round_idx=1,
            )
            runtime_harness.record(
                "collect_search_words",
                {
                    "round_idx": 1,
                    "level": sw_level,
                    "strict_mode": _event_query_strict_enabled(),
                    "search_words": search_words_for_collect[:16],
                },
            )
            words_for_num, keyword_mode_for_num = build_data_num_search_words(search_plan, search_words_for_collect)
            if not words_for_num:
                raise ValueError("检索词为空：请检查 searchWords 或 netinsightAdvancedQuery")
            tool_search_words = words_for_num

            platform_save_paths = []
            platform_row_distribution = {}
            last_collect_error = ""
            time_range_base = str(suggested_collect_plan.get("time_range") or "")

        def _run_data_collect_for_platform(
            platform: str,
            search_matrix: Dict[str, int],
            time_range_used: str,
            query_for_retry: str,
        ) -> tuple[bool, str]:
            nonlocal last_collect_error
            if debug:
                console.print(f"[dim]平台={platform} -> data_collect[/dim]")
            _progress_advance()
            _progress_step(f"Step4: data_collect ({platform})")

            collect_json, data_collect_elapsed = _invoke_tool_with_timing(
                data_collect,
                {
                    "searchMatrix": json.dumps(search_matrix, ensure_ascii=False),
                    "timeRange": time_range_used,
                    "platform": platform,
                },
            )
            _append_ndjson_log(
                run_id="event_analysis_timing",
                hypothesis_id="H17_step_timing_data_collect",
                location="workflow/event_analysis_pipeline.py:after_data_collect",
                message="data_collect 耗时",
                data={"elapsed_sec": data_collect_elapsed, "platform": platform},
            )

            collect_error = str(collect_json.get("error") or "").strip()
            save_path_raw = str(collect_json.get("save_path") or "")
            resolved_collect_path = ""
            try:
                resolved_collect_path = _resolve_to_csv_path(save_path_raw)
            except Exception:
                resolved_collect_path = ""
            collected_rows = (
                _count_csv_rows(resolved_collect_path)
                if resolved_collect_path and Path(resolved_collect_path).exists()
                else 0
            )
            if collect_error or not resolved_collect_path or collected_rows <= 0:
                try:
                    retry_days = max(10, _infer_default_time_range_days(user_query) + 7)
                    retry_time_range = _build_default_time_range(retry_days)
                    retry_threshold = max(max(search_matrix.values()) if search_matrix else 0, 1200)
                    for multiplier in (1, 2):
                        retry_matrix = {query_for_retry: retry_threshold * multiplier}
                        retry_json, _retry_elapsed = _invoke_tool_with_timing(
                            data_collect,
                            {
                                "searchMatrix": json.dumps(retry_matrix, ensure_ascii=False),
                                "timeRange": retry_time_range,
                                "platform": platform,
                            },
                        )
                        retry_err = str(retry_json.get("error") or "").strip()
                        retry_raw = str(retry_json.get("save_path") or "")
                        retry_path = _resolve_to_csv_path(retry_raw) if retry_raw else ""
                        retry_rows = _count_csv_rows(retry_path) if retry_path and Path(retry_path).exists() else 0
                        if retry_path and retry_rows > 0 and not retry_err:
                            resolved_collect_path = retry_path
                            collected_rows = retry_rows
                            collect_error = ""
                            break
                except Exception:
                    pass

            if resolved_collect_path and Path(resolved_collect_path).exists() and collected_rows > 0 and not collect_error:
                platform_save_paths.append(resolved_collect_path)
                platform_row_distribution[platform] = int(collected_rows)
                _append_ndjson_log(
                    run_id="event_analysis_data_collect",
                    hypothesis_id="H26_data_collect_result_path",
                    location="workflow/event_analysis_pipeline.py:after_data_collect_path_validate",
                    message="data_collect 返回路径校验结果",
                    data={
                        "task_id": task_id,
                        "save_path": resolved_collect_path,
                        "save_path_exists": True,
                        "platform": platform,
                        "rows": collected_rows,
                    },
                )
                if debug:
                    console.print(f"[green]✅ 平台采集完成[/green] {platform} rows={collected_rows}")
                return True, ""
            last_collect_error = collect_error or last_collect_error or "data_collect failed"
            _append_ndjson_log(
                run_id="event_analysis_fallback",
                hypothesis_id="H32_data_collect_fallback_to_existing_csv",
                location="workflow/event_analysis_pipeline.py:data_collect_fallback",
                message="平台采集失败（将继续尝试其他平台）",
                data={"platform": platform, "error": str(last_collect_error)[:400]},
            )
            return False, last_collect_error

        multi_platform = len(selected_platforms) > 1
        if multi_platform:
            _append_ndjson_log(
                run_id="event_analysis_before_data_num",
                hypothesis_id="H3_tool_args_built",
                location="workflow/event_analysis_pipeline.py:before_data_num",
                message="构建 data_num（多平台按比例分配 threshold）",
                data={
                    "platforms": selected_platforms,
                    "threshold(return_count)": return_count,
                    "keyword_mode": keyword_mode_for_num,
                    "words_for_num": words_for_num[:1],
                    "boolean_mode": boolean_mode,
                },
            )
            matrix_json, data_num_elapsed = _invoke_tool_with_timing(
                data_num,
                {
                    "searchWords": json.dumps(words_for_num, ensure_ascii=False),
                    "timeRange": time_range_base,
                    "threshold": return_count,
                    "platform": "微博",
                    "keywordMode": keyword_mode_for_num,
                    "platforms": json.dumps(selected_platforms, ensure_ascii=False),
                    "allocateByPlatform": True,
                },
            )
            _append_ndjson_log(
                run_id="event_analysis_timing",
                hypothesis_id="H16_step_timing_data_num",
                location="workflow/event_analysis_pipeline.py:after_data_num",
                message="data_num 耗时（多平台分配）",
                data={"elapsed_sec": data_num_elapsed, "platform_allocation": matrix_json.get("platform_allocation")},
            )
            qs = str(matrix_json.get("query_string") or words_for_num[0])
            pa_raw = matrix_json.get("platform_allocation")
            pa: Dict[str, int] = {}
            if isinstance(pa_raw, dict):
                for k, v in pa_raw.items():
                    try:
                        pa[str(k)] = max(0, int(v))
                    except Exception:
                        continue
            if not pa or sum(pa.values()) <= 0:
                n = max(1, len(selected_platforms))
                base, rem = divmod(return_count, n)
                for i, pl in enumerate(selected_platforms):
                    pa[pl] = base + (1 if i < rem else 0)
                _append_ndjson_log(
                    run_id="event_analysis_fallback",
                    hypothesis_id="H46_platform_allocation_fallback_equal",
                    location="workflow/event_analysis_pipeline.py:platform_allocation_fallback",
                    message="data_num 未返回 platform_allocation，已均分 threshold",
                    data={"pa": pa},
                )

            for platform in selected_platforms:
                cap = int(pa.get(platform, 0) or 0)
                if cap < 1:
                    continue
                sm = _sanitize_search_matrix({qs: cap}, cap)
                time_range_used = str(matrix_json.get("time_range") or time_range_base)
                if not sm:
                    sm = {qs: cap}
                _run_data_collect_for_platform(platform, sm, time_range_used, qs)
        else:
            platform = selected_platforms[0]
            _append_ndjson_log(
                run_id="event_analysis_before_data_num",
                hypothesis_id="H3_tool_args_built",
                location="workflow/event_analysis_pipeline.py:before_data_num",
                message="构建 data_num 工具输入参数",
                data={
                    "platform": platform,
                    "threshold(return_count)": return_count,
                    "keyword_mode": keyword_mode_for_num,
                    "tool_search_words_count": len(tool_search_words),
                    "boolean_mode": boolean_mode,
                    "data_num_workers": data_num_workers,
                    "data_collect_workers": data_collect_workers,
                    "analysis_workers": analysis_workers,
                },
            )
            matrix_json, data_num_elapsed = _invoke_tool_with_timing(
                data_num,
                {
                    "searchWords": json.dumps(words_for_num, ensure_ascii=False),
                    "timeRange": time_range_base,
                    "threshold": return_count,
                    "platform": platform,
                    "keywordMode": keyword_mode_for_num,
                    "platforms": "",
                    "allocateByPlatform": False,
                },
            )
            _append_ndjson_log(
                run_id="event_analysis_timing",
                hypothesis_id="H16_step_timing_data_num",
                location="workflow/event_analysis_pipeline.py:after_data_num",
                message="data_num 耗时",
                data={"elapsed_sec": data_num_elapsed, "search_words_count": len(tool_search_words), "platform": platform},
            )
            search_matrix_raw = matrix_json.get("search_matrix")
            search_matrix = _sanitize_search_matrix(search_matrix_raw, return_count)
            time_range_used = str(matrix_json.get("time_range") or time_range_base)
            qs = str(matrix_json.get("query_string") or words_for_num[0])
            if not search_matrix:
                fallback_matrix = _build_uniform_search_matrix(tool_search_words, return_count)
                if fallback_matrix:
                    search_matrix = fallback_matrix
                    time_range_used = time_range_base
                else:
                    raise ValueError(str(matrix_json.get("error") or "data_num 未返回可用 search_matrix"))
            _run_data_collect_for_platform(platform, search_matrix, time_range_used, qs)

        if platform_save_paths:
            if len(platform_save_paths) == 1:
                save_path = platform_save_paths[0]
            else:
                ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                merged_path = str(process_dir / f"netinsight_多平台_汇总_{ts}.csv")
                save_path = merge_netinsight_csv_by_content(csv_paths=platform_save_paths, out_path=merged_path)
            if debug:
                console.print(
                    f"[green]✅ 多平台采集合并完成[/green] platforms={selected_platforms} save_path={save_path}"
                )
        else:
            # 全平台均失败：最后再尝试历史回退（与单平台一致的兜底）
            if not _allow_history_fallback():
                raise ValueError(
                    f"所有平台 data_collect 失败：{selected_platforms}；last_error={last_collect_error[:200]}；"
                    "且已关闭历史回退（SONA_ALLOW_HISTORY_FALLBACK=false）。"
                )
            fallback_candidates = _find_recent_reusable_csv(current_task_id=task_id, limit=8)
            fallback_save_path = ""
            for candidate in fallback_candidates:
                try:
                    fallback_save_path = _resolve_to_csv_path(candidate)
                    if fallback_save_path and Path(fallback_save_path).exists():
                        break
                except Exception:
                    continue
            if fallback_save_path:
                save_path = fallback_save_path
                skip_data_collect = True
                if debug:
                    console.print(f"[yellow]⚠️ 已回退使用历史数据[/yellow] save_path={save_path}")
            else:
                raise ValueError(f"所有平台 data_collect 失败，且无可复用历史数据。last_error={last_collect_error}")
        if debug and save_path:
            console.print(f"[green]✅ 数据采集完成[/green] save_path={save_path}")

        # 若样本过小，给出明确告警（避免把低样本直接当结论）
        min_samples = max(20, min(_safe_int(os.environ.get("SONA_MIN_SAMPLE_WARN", "80"), 80), 1000))
        sample_rows = _count_csv_rows(save_path) if save_path else 0
        if sample_rows > 0 and save_path:
            _save_collect_manifest(
                process_dir=process_dir,
                user_query=user_query,
                save_path=save_path,
                rows=sample_rows,
                time_range=str(suggested_collect_plan.get("time_range") or search_plan.get("timeRange") or ""),
                search_words=search_plan.get("searchWords", []),
            )
        if sample_rows and sample_rows < min_samples:
            console.print(
                f"[yellow]⚠️ 当前样本量仅 {sample_rows} 条，低于建议阈值 {min_samples}；建议扩大时间范围或提高 return_count 后重跑。[/yellow]"
            )
            _append_ndjson_log(
                run_id="event_analysis_data_quality",
                hypothesis_id="H42_low_sample_warning",
                location="workflow/event_analysis_pipeline.py:low_sample_warning",
                message="样本量低于建议阈值，已提示用户",
                data={"sample_rows": sample_rows, "min_samples": min_samples, "save_path": save_path},
            )
            # 自动尝试扩窗重采（仅在本轮为新采集场景）
            if (not skip_data_collect) and (not existing_data_path):
                try:
                    hard_min_samples = max(20, min(_safe_int(os.environ.get("SONA_MIN_SAMPLE_HARD", "70"), 70), 2000))
                    max_retry_rounds = max(
                        1, min(_safe_int(os.environ.get("SONA_LOW_SAMPLE_RETRY_ROUNDS", "2"), 2), 4)
                    )
                    base_days = _infer_default_time_range_days(user_query)
                    retry_threshold_base = max(
                        _safe_int(suggested_collect_plan.get("return_count"), 2000),
                        hard_min_samples * 6,
                    )
                    for round_idx in range(1, max_retry_rounds + 1):
                        if sample_rows >= hard_min_samples:
                            break
                        retry_days = max(10, base_days + 7 * round_idx)
                        retry_time_range = _build_default_time_range(retry_days)
                        retry_threshold = int(retry_threshold_base * (1 + 0.5 * (round_idx - 1)))
                        _sw_retry, _level_retry = _pick_search_words_for_round(
                            base_words=search_plan.get("searchWords", []),
                            user_query=user_query,
                            round_idx=round_idx,
                        )
                        runtime_harness.record(
                            "collect_search_words",
                            {
                                "round_idx": round_idx,
                                "level": _level_retry,
                                "strict_mode": _event_query_strict_enabled(),
                                "search_words": (_sw_retry or [])[:16],
                            },
                        )
                        _wn_retry, _ = build_data_num_search_words(search_plan, _sw_retry)
                        retry_matrix = _build_uniform_search_matrix(_wn_retry or _sw_retry, retry_threshold)
                        retry_platforms = suggested_collect_plan.get("platforms") or ["微博"]
                        retry_platform = str(retry_platforms[0]) if retry_platforms else "微博"
                        retry_collect = _invoke_tool_to_json(
                            data_collect,
                            {
                                "searchMatrix": json.dumps(retry_matrix, ensure_ascii=False),
                                "timeRange": retry_time_range,
                                "platform": retry_platform,
                            },
                        )
                        retry_path = _resolve_to_csv_path(str(retry_collect.get("save_path") or ""))
                        retry_rows = _count_csv_rows(retry_path) if retry_path else 0
                        old_rows = sample_rows
                        if retry_path and retry_rows > sample_rows:
                            save_path = retry_path
                            sample_rows = retry_rows
                            console.print(
                                f"[green]♻️ 第 {round_idx} 轮扩窗重采：{retry_rows} 条（原 {old_rows} 条）[/green]"
                            )
                        _append_ndjson_log(
                            run_id="event_analysis_data_quality",
                            hypothesis_id="H44_low_sample_auto_retry_collect",
                            location="workflow/event_analysis_pipeline.py:low_sample_retry_collect",
                            message="低样本扩窗重采尝试完成",
                            data={
                                "round": round_idx,
                                "retry_time_range": retry_time_range,
                                "retry_threshold": retry_threshold,
                                "retry_rows": retry_rows,
                                "retry_path": retry_path,
                                "sample_rows_after_round": sample_rows,
                                "hard_min_samples": hard_min_samples,
                            },
                        )
                    if sample_rows < hard_min_samples:
                        console.print(
                            f"[yellow]⚠️ 重采后样本仍偏低：{sample_rows}（目标≥{hard_min_samples}）。建议手动扩展检索词后再跑。[/yellow]"
                        )
                except Exception as e:
                    _append_ndjson_log(
                        run_id="event_analysis_data_quality",
                        hypothesis_id="H44_low_sample_auto_retry_collect",
                        location="workflow/event_analysis_pipeline.py:low_sample_retry_collect_exception",
                        message="低样本扩窗重采失败，已跳过",
                        data={"error": str(e)},
                    )

        # 低样本硬中止：避免低质量报告落盘
        min_samples_hard_fail = max(
            20, min(_safe_int(os.environ.get("SONA_MIN_SAMPLE_HARD_FAIL", "200"), 200), 5000)
        )
        if sample_rows < min_samples_hard_fail:
            fail_msg = (
                f"当前样本量仅 {sample_rows} 条（阈值 {min_samples_hard_fail}），"
                "请先补采样本。"
            )
            console.print(f"[red]⛔ {fail_msg}[/red]")
            _append_ndjson_log(
                run_id="event_analysis_data_quality",
                hypothesis_id="H46_low_sample_hard_fail",
                location="workflow/event_analysis_pipeline.py:low_sample_hard_fail",
                message="样本量低于硬阈值，已中止报告生成",
                data={
                    "sample_rows": sample_rows,
                    "min_samples_hard_fail": min_samples_hard_fail,
                    "save_path": save_path,
                },
            )

            can_recollect = collab_enabled and (not skip_data_collect) and (not existing_data_path)
            if can_recollect:
                recollect_round += 1
                console.print(f"[yellow]可选择回到采集方案编辑并补采（第 {recollect_round} 次）。[/yellow]")
                _pretty_print_dict("当前采集方案（可编辑后重采）", suggested_collect_plan)
                updated_plan, accepted = _edit_collect_plan_interactively(suggested_collect_plan)
                if not accepted:
                    raise ValueError(fail_msg)
                suggested_collect_plan = updated_plan
                _save_experience_item(
                    task_id=task_id,
                    user_query=user_query,
                    search_plan=search_plan,
                    collect_plan={
                        "keyword_combination_mode": suggested_collect_plan.get("keyword_combination_mode"),
                        "boolean_strategy": suggested_collect_plan.get("boolean_strategy"),
                        "keywords_join_with": suggested_collect_plan.get("keywords_join_with"),
                        "platforms": suggested_collect_plan.get("platforms"),
                        "time_range": suggested_collect_plan.get("time_range"),
                        "return_count": suggested_collect_plan.get("return_count"),
                        "searchWords_preview": suggested_collect_plan.get("searchWords_preview"),
                    },
                )
                # 继续 while True：重新跑 data_num/data_collect
                continue

            raise ValueError(fail_msg)

        # 采集/质量门槛通过，退出补采循环
        break

    # ============ 5) dataset_summary ============
    if debug:
        console.print("[bold]Step5: dataset_summary[/bold]")
    _progress_step("Step5: dataset_summary")

    ds_json = _invoke_tool_to_json(dataset_summary, {"save_path": save_path})
    dataset_summary_path = str(ds_json.get("result_file_path") or "")
    if not dataset_summary_path or not Path(dataset_summary_path).exists():
        raise ValueError("dataset_summary 未返回有效 result_file_path")

    # ============ 6) 统计与阶段分析 ============
    # ============ 6.1) keyword_stats（可选，失败可跳过） ============
    if debug:
        console.print("[bold]Step6.1: keyword_stats (optional)[/bold]")

    try:
        keyword_json = _invoke_tool_to_json(
            keyword_stats,
            {
                "dataFilePath": save_path,
                "top_n": 200,
                "min_len": 2,
            },
        )
        keyword_stats_path = str(keyword_json.get("result_file_path") or "")
        if debug and keyword_stats_path:
            console.print(f"[green]✅ 关键词统计完成[/green] result_file_path={keyword_stats_path}")
        top_keywords = _load_top_keywords(keyword_stats_path, max_items=80)
        relevance = _topic_relevance_metrics(
            user_query=user_query,
            search_words=_to_clean_str_list(search_plan.get("searchWords"), max_items=12),
            top_keywords=top_keywords,
        )
        min_topic_coverage = max(
            0.05,
            min(_safe_float(os.environ.get("SONA_TOPIC_RELEVANCE_MIN_COVERAGE", "0.08"), 0.08), 0.9),
        )
        runtime_harness.record(
            "topic_relevance_quality",
            {
                "coverage": relevance.get("coverage", 0.0),
                "coverage_phrase": relevance.get("coverage_phrase", 0.0),
                "composite": relevance.get("composite", 0.0),
                "overlap_count": relevance.get("overlap_count", 0),
                "anchor_count": relevance.get("anchor_count", 0),
                "top_keywords_count": len(top_keywords),
                "min_coverage": min_topic_coverage,
                "overlap_terms": relevance.get("overlap_terms", []),
                "phrase_hits": relevance.get("phrase_hits", []),
            },
        )
        guard_score = float(relevance.get("composite", relevance.get("coverage", 0.0)) or 0.0)
        if guard_score < min_topic_coverage:
            detail = (
                f"topic_relevance_guard 失败：score={relevance.get('composite', relevance.get('coverage'))} < {min_topic_coverage}; "
                f"coverage={relevance.get('coverage')} phrase={relevance.get('coverage_phrase')} "
                f"hits={','.join(relevance.get('phrase_hits', [])[:6]) or 'none'} "
                f"overlap_terms={','.join(relevance.get('overlap_terms', [])[:6]) or 'none'}"
            )
            _append_ndjson_log(
                run_id="event_analysis_quality_guard",
                hypothesis_id="H48_topic_relevance_guard_fail",
                location="workflow/event_analysis_pipeline.py:topic_relevance_guard",
                message="主题相关性低于阈值，终止报告生成",
                data={
                    "score": relevance.get("composite", relevance.get("coverage")),
                    "coverage": relevance.get("coverage"),
                    "coverage_phrase": relevance.get("coverage_phrase"),
                    "min_coverage": min_topic_coverage,
                    "overlap_terms": relevance.get("overlap_terms", []),
                    "phrase_hits": relevance.get("phrase_hits", []),
                    "top_keywords_preview": top_keywords[:15],
                },
            )
            try:
                if enable_progress and progress is not None and progress_started:
                    progress.stop()
            except Exception:
                pass
            # 交互会话：允许用户显式选择继续（避免“数据已采集却被硬退回”）。
            allow_override = str(os.environ.get("SONA_TOPIC_GUARD_ALLOW_OVERRIDE", "true")).strip().lower() in (
                "1",
                "true",
                "yes",
                "y",
                "on",
            )
            if collab_enabled and allow_override:
                choice = _prompt_text_timeout(
                    f"{detail}\n是否仍继续生成报告？输入 yes 继续 / no 终止",
                    timeout_sec=max(25, min(collab_timeout_sec, 90)),
                    default_text="yes",
                ).strip().lower()
                if choice in {"y", "yes", "继续", "是"}:
                    _append_ndjson_log(
                        run_id="event_analysis_quality_guard",
                        hypothesis_id="H49_topic_guard_user_override_continue",
                        location="workflow/event_analysis_pipeline.py:topic_relevance_guard",
                        message="topic_relevance_guard 低于阈值，但用户选择继续生成报告",
                        data={"detail": detail},
                    )
                    runtime_harness.record(
                        "topic_relevance_override",
                        {"continued": True, "detail": detail},
                    )
                else:
                    runtime_harness.finalize()
                    raise ValueError(detail)
            else:
                runtime_harness.finalize()
                raise ValueError(detail)
    except Exception as e:
        if "topic_relevance_guard 失败" in str(e):
            raise
        if debug:
            console.print("[yellow]⚠️ keyword_stats 执行失败，已跳过，不影响后续流程[/yellow]")
        _append_ndjson_log(
            run_id="event_analysis_keyword_stats",
            hypothesis_id="H34_keyword_stats_optional_skip_on_error",
            location="workflow/event_analysis_pipeline.py:keyword_stats_optional",
            message="keyword_stats 执行失败，已按可选步骤跳过",
            data={"error": str(e)},
        )

    # ============ 6.2) region_stats（可选，失败可跳过） ============
    if debug:
        console.print("[bold]Step6.2: region_stats (optional)[/bold]")

    try:
        region_json = _invoke_tool_to_json(
            region_stats,
            {
                "dataFilePath": save_path,
                "top_n": 10,
            },
        )
        region_stats_path = str(region_json.get("result_file_path") or "")
        if debug and region_stats_path:
            console.print(f"[green]✅ 地域统计完成[/green] result_file_path={region_stats_path}")
    except Exception as e:
        if debug:
            console.print("[yellow]⚠️ region_stats 执行失败，已跳过，不影响后续流程[/yellow]")
        _append_ndjson_log(
            run_id="event_analysis_region_stats",
            hypothesis_id="H34_region_stats_optional_skip_on_error",
            location="workflow/event_analysis_pipeline.py:region_stats_optional",
            message="region_stats 执行失败，已按可选步骤跳过",
            data={"error": str(e)},
        )

    # ============ 6.3) author_stats（可选，失败可跳过） ============
    if debug:
        console.print("[bold]Step6.3: author_stats (optional)[/bold]")

    try:
        author_json = _invoke_tool_to_json(
            author_stats,
            {
                "dataFilePath": save_path,
                "top_n": 10,
            },
        )
        author_stats_path = str(author_json.get("result_file_path") or "")
        if debug and author_stats_path:
            console.print(f"[green]✅ 作者统计完成[/green] result_file_path={author_stats_path}")
    except Exception as e:
        if debug:
            console.print("[yellow]⚠️ author_stats 执行失败，已跳过，不影响后续流程[/yellow]")
        _append_ndjson_log(
            run_id="event_analysis_author_stats",
            hypothesis_id="H35_author_stats_optional_skip_on_error",
            location="workflow/event_analysis_pipeline.py:author_stats_optional",
            message="author_stats 执行失败，已按可选步骤跳过",
            data={"error": str(e)},
        )

    # ============ 6.4) timeline（顺序执行） ============
    timeline_enabled = _analysis_stage_enabled("timeline")
    sentiment_enabled = _analysis_stage_enabled("sentiment")
    if debug:
        console.print(f"[bold]Step6.4: analysis_timeline ({'on' if timeline_enabled else 'off'})[/bold]")
        console.print(f"[bold]Step7: analysis_sentiment ({'on' if sentiment_enabled else 'off'})[/bold]")
    _progress_advance()
    _progress_step("Step6.4-7: timeline + sentiment")

    analysis_start = time.time()
    single_timing: Dict[str, float] = {"timeline_sec": 0.0, "sentiment_sec": 0.0}
    timeline_json: Dict[str, Any] = {}
    sentiment_json: Dict[str, Any] = {}
    reused_flags = {"timeline": False, "sentiment": False}

    preferred_task_id = ""
    if isinstance(best_exp, dict):
        preferred_task_id = str(best_exp.get("task_id") or "").strip()

    # 先尝试复用历史分析，节省 token 与时延
    if (not fresh_start) and _analysis_reuse_enabled("timeline"):
        reused_timeline = _find_reusable_analysis_result(
            kind="timeline",
            save_path=save_path,
            current_task_id=task_id,
            preferred_task_id=preferred_task_id,
        )
        if reused_timeline:
            timeline_json = reused_timeline
            reused_flags["timeline"] = True
            if debug:
                console.print(f"[green]♻️ 复用历史 timeline 分析[/green] from_task={reused_timeline.get('_reused_from_task_id', '')}")

    if (not fresh_start) and _analysis_reuse_enabled("sentiment"):
        reused_sentiment = _find_reusable_analysis_result(
            kind="sentiment",
            save_path=save_path,
            current_task_id=task_id,
            preferred_task_id=preferred_task_id,
        )
        if reused_sentiment:
            sentiment_json = reused_sentiment
            reused_flags["sentiment"] = True
            if debug:
                console.print(f"[green]♻️ 复用历史 sentiment 分析[/green] from_task={reused_sentiment.get('_reused_from_task_id', '')}")

    # 先 timeline
    timeline_timeout_sec = max(30, min(_safe_int(os.environ.get("SONA_TIMELINE_TIMEOUT_SEC", "240"), 240), 3600))
    sentiment_timeout_sec = max(30, min(_safe_int(os.environ.get("SONA_SENTIMENT_TIMEOUT_SEC", "300"), 300), 3600))

    if not timeline_enabled:
        timeline_json = _build_skipped_analysis_payload("timeline", "SONA_ANALYSIS_ENABLE_TIMELINE=false")
    elif not reused_flags["timeline"]:
        t0 = time.time()
        # 时间线：传入事件锚点，降低“有时间但无关热点”混入概率
        anchor_terms_for_timeline, _ = _pick_search_words_for_round(
            base_words=search_plan.get("searchWords", []),
            user_query=user_query,
            round_idx=1,
        )
        timeline_json = _invoke_tool_to_json_with_timeout(
            analysis_timeline,
            {
                "eventIntroduction": search_plan["eventIntroduction"],
                "dataFilePath": save_path,
                "eventAnchorTerms": anchor_terms_for_timeline[:6],
            },
            timeout_sec=timeline_timeout_sec,
            tool_name="analysis_timeline",
        )
        if str(timeline_json.get("error", "") or "").strip():
            timeline_json = {
                "error": str(timeline_json.get("error", "") or "analysis_timeline 执行失败"),
                "timeline": [],
                "summary": "",
                "result_file_path": "",
            }
        single_timing["timeline_sec"] = round(time.time() - t0, 3)
    else:
        if debug:
            console.print("[green]♻️ timeline 已复用历史结果[/green]")

    # 再 sentiment（失败则用 CSV 情感列兜底）
    if not sentiment_enabled:
        sentiment_json = _build_skipped_analysis_payload("sentiment", "SONA_ANALYSIS_ENABLE_SENTIMENT=false")
    elif not reused_flags["sentiment"]:
        from workflow.runner import run_sentiment_stage as _run_sentiment_stage

        sentiment_json, sentiment_elapsed = _run_sentiment_stage(
            user_query=user_query,
            search_plan=search_plan,
            save_path=save_path,
            debug=debug,
            sentiment_timeout_sec=sentiment_timeout_sec,
            analysis_sentiment_tool=analysis_sentiment,
            invoke_tool_with_timeout=_invoke_tool_to_json_with_timeout,
            fallback_from_csv=_fallback_sentiment_from_csv,
            append_log=_append_ndjson_log,
        )
        single_timing["sentiment_sec"] = sentiment_elapsed
    else:
        if debug:
            console.print("[green]♻️ sentiment 已复用历史结果[/green]")

    # #region debug_log_H15_step_timing_parallel_analysis
    _append_ndjson_log(
        run_id="event_analysis_timing",
        hypothesis_id="H15_step_timing_parallel_analysis",
        location="workflow/event_analysis_pipeline.py:after_parallel_analysis",
        message="分析耗时（顺序执行）",
        data={
            "elapsed_sec": round(time.time() - analysis_start, 3),
            "timeline_sec": single_timing["timeline_sec"],
            "sentiment_sec": single_timing["sentiment_sec"],
        },
    )
    # #endregion debug_log_H15_step_timing_parallel_analysis

    timeline_path = _ensure_analysis_result_file(process_dir=process_dir, kind="timeline", result_json=timeline_json)
    sentiment_path = _ensure_analysis_result_file(process_dir=process_dir, kind="sentiment", result_json=sentiment_json)
    # #region debug_log_H25_analysis_result_paths
    _append_ndjson_log(
        run_id="event_analysis_parallel_analysis",
        hypothesis_id="H25_analysis_result_paths",
        location="workflow/event_analysis_pipeline.py:after_analysis_path_resolve",
        message="analysis 结果文件路径解析完成（含 fallback）",
        data={
            "timeline_path": timeline_path,
            "timeline_exists": Path(timeline_path).exists(),
            "sentiment_path": sentiment_path,
            "sentiment_exists": Path(sentiment_path).exists(),
            "timeline_has_error": bool(timeline_json.get("error")),
            "sentiment_has_error": bool(sentiment_json.get("error")),
        },
    )
    # #endregion debug_log_H25_analysis_result_paths
    sentiment_stats = sentiment_json.get("statistics") if isinstance(sentiment_json.get("statistics"), dict) else {}
    runtime_harness.record(
        "sentiment_quality",
        {
            "skipped": bool(sentiment_json.get("skipped", False)),
            "source": str(sentiment_stats.get("sentiment_source", "") or ""),
            "total": int(sentiment_stats.get("total", 0) or 0),
            "positive_count": int(sentiment_stats.get("positive_count", 0) or 0),
            "negative_count": int(sentiment_stats.get("negative_count", 0) or 0),
            "neutral_count": int(sentiment_stats.get("neutral_count", 0) or 0),
            "fallback_used": str(sentiment_stats.get("sentiment_source", "") or "") == "existing_column_fallback",
        },
    )

    # ============ 6.5) channel（平台占比，生成饼图数据） ============
    if debug:
        console.print("[bold]Step6.5: channel_distribution (optional)[/bold]")
    try:
        calc_source = ""
        channel_counts: Dict[str, int] = {}
        csv_channel_counts = _count_channels_from_csv(save_path)
        if csv_channel_counts:
            channel_counts = {str(k): max(0, int(v)) for k, v in csv_channel_counts.items()}
            calc_source = "csv_groupby_platform"
        elif platform_row_distribution:
            channel_counts = {str(k): max(0, int(v)) for k, v in platform_row_distribution.items()}
            calc_source = "platform_row_distribution"
        elif selected_platforms:
            # 当未拿到平台分布明细时，回退为均分占比（仅用于展示，不影响分析结论）
            fallback_total = max(1, _count_csv_rows(save_path))
            n = max(1, len(selected_platforms))
            base, rem = divmod(fallback_total, n)
            for idx, platform in enumerate(selected_platforms):
                channel_counts[str(platform)] = int(base + (1 if idx < rem else 0))
            calc_source = "fallback_even_split"
        total_count = sum(v for v in channel_counts.values() if v > 0)
        channel_items = []
        for platform, count in sorted(channel_counts.items(), key=lambda x: x[1], reverse=True):
            pct = round((count / total_count) * 100, 2) if total_count > 0 else 0.0
            channel_items.append({"platform": platform, "count": int(count), "percentage": pct})
        mermaid_lines = ["pie title 各渠道声量占比（按采集条数）"]
        for row in channel_items:
            mermaid_lines.append(f'    "{row["platform"]}" : {row["count"]}')
        channel_payload = {
            "total_count": total_count,
            "items": channel_items,
            "chart_type": "pie",
            "mermaid_pie": "\n".join(mermaid_lines),
            "created_at": datetime.now().isoformat(sep=" "),
            "calculation_source": calc_source,
        }
        channel_path = process_dir / "channel_distribution.json"
        with open(channel_path, "w", encoding="utf-8", errors="replace") as f:
            json.dump(channel_payload, f, ensure_ascii=False, indent=2)
        runtime_harness.record(
            "channel_distribution",
            {"total_count": total_count, "channels": channel_items[:8], "path": str(channel_path)},
        )
    except Exception as e:
        _append_ndjson_log(
            run_id="event_analysis_channel",
            hypothesis_id="H51_channel_distribution_optional",
            location="workflow/event_analysis_pipeline.py:channel_distribution_optional",
            message="渠道占比分析失败，已跳过",
            data={"error": str(e)},
        )

    # Wiki/OPRAG 知识增强统一放到 Step10（10.2/10.3）阶段执行，保持显示顺序与执行顺序一致。

    # ============ 8) 初步解读（interpretation.json） ============
    # ============ 6.6) volume_stats（可选，失败可跳过） ============
    if debug:
        console.print("[bold]Step6.6: volume_stats (optional)[/bold]")

    try:
        volume_json = _invoke_tool_to_json(
            volume_stats,
            {
                "dataFilePath": save_path,
            },
        )
        volume_stats_path = str(volume_json.get("result_file_path") or "")
        if debug and volume_stats_path:
            console.print(f"[green]✅ 声量统计完成[/green] result_file_path={volume_stats_path}")
    except Exception as e:
        if debug:
            console.print("[yellow]⚠️ volume_stats 执行失败，已跳过，不影响后续流程[/yellow]")
        _append_ndjson_log(
            run_id="event_analysis_volume_stats",
            hypothesis_id="H36_volume_stats_optional_skip_on_error",
            location="workflow/event_analysis_pipeline.py:volume_stats_optional",
            message="volume_stats 执行失败，已按可选步骤跳过",
            data={"error": str(e)},
        )

    # ============ 6.7) user_portrait（可选，失败可跳过） ============
    if debug:
        console.print("[bold]Step6.7: user_portrait (optional)[/bold]")
    try:
        portrait_json = _invoke_tool_to_json(
            user_portrait,
            {
                "dataFilePath": save_path,
                "sentimentResultPath": sentiment_path,
            },
        )
        portrait_path = str(portrait_json.get("result_file_path") or "")
        if debug and portrait_path:
            console.print(f"[green]✅ 用户画像完成[/green] result_file_path={portrait_path}")
    except Exception as e:
        if debug:
            console.print("[yellow]⚠️ user_portrait 执行失败，已跳过，不影响后续流程[/yellow]")
        _append_ndjson_log(
            run_id="event_analysis_user_portrait",
            hypothesis_id="H41_user_portrait_optional_skip_on_error",
            location="workflow/event_analysis_pipeline.py:user_portrait_optional",
            message="user_portrait 执行失败，已按可选步骤跳过",
            data={"error": str(e)},
        )

    if debug:
        console.print("[bold]Step8: generate_interpretation[/bold]")

    interp_json = _invoke_tool_to_json(
        generate_interpretation,
        {
            "eventIntroduction": search_plan["eventIntroduction"],
            "timelineResultPath": timeline_path,
            "sentimentResultPath": sentiment_path,
            "datasetSummaryPath": dataset_summary_path,
        },
    )
    interpretation_path = str(interp_json.get("result_file_path") or "")
    interpretation = interp_json.get("interpretation") or {}
    if not interpretation_path or not Path(interpretation_path).exists():
        fallback_interpretation = {
            "narrative_summary": str(
                (timeline_json.get("summary") or "")
                if isinstance(timeline_json, dict) else ""
            )[:800] or "自动回退：未获得结构化 interpretation，已基于现有分析结果继续流程。",
            "key_events": [],
            "key_risks": [],
            "event_type": _infer_event_type_from_text(search_plan.get("eventIntroduction", user_query)),
            "domain": _infer_domain_from_text(search_plan.get("eventIntroduction", user_query)),
            "stage": _infer_stage_from_text(str(timeline_json.get("summary", ""))),
            "indicators_dimensions": ["count", "sentiment", "actor", "attention", "quality"],
            # fallback 场景下不强行注入固定理论，避免报告模板化重复
            "theory_names": [],
        }
        fallback_payload = {
            "interpretation": fallback_interpretation,
            "generated_at": datetime.now().isoformat(sep=" "),
            "error": interp_json.get("error", "generate_interpretation 未返回有效 result_file_path"),
            "fallback": True,
        }
        fallback_path = process_dir / f"interpretation_fallback_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        with open(fallback_path, "w", encoding="utf-8", errors="replace") as f:
            json.dump(fallback_payload, f, ensure_ascii=False, indent=2)
        interpretation_path = str(fallback_path)
        interpretation = fallback_interpretation
        # #region debug_log_H30_interpretation_fallback
        _append_ndjson_log(
            run_id="event_analysis_fallback",
            hypothesis_id="H30_interpretation_fallback",
            location="workflow/event_analysis_pipeline.py:interpretation_fallback",
            message="generate_interpretation 失败，已使用 fallback interpretation 继续流程",
            data={"fallback_path": interpretation_path, "tool_error": interp_json.get("error", "")},
        )
        # #endregion debug_log_H30_interpretation_fallback

    # ============ 9) 微博智搜预览 + 用户协同研判输入（可选） ============
    if debug:
        console.print("[bold]Step9: weibo_aisearch + user_judgement[/bold]")
    weibo_ref_json: Dict[str, Any] = {}
    weibo_ref_path = process_dir / "weibo_aisearch_reference.json"
    enable_weibo_ref = str(os.environ.get("SONA_REFERENCE_ENABLE_WEIBO_AISEARCH", "true")).strip().lower() in (
        "1",
        "true",
        "yes",
        "y",
        "on",
    )
    if enable_weibo_ref:
        try:
            weibo_topic = str(search_plan.get("eventIntroduction") or user_query).strip() or user_query
            weibo_ref_json = _invoke_tool_to_json(
                weibo_aisearch,
                {"query": weibo_topic, "limit": 12},
            )
            with open(weibo_ref_path, "w", encoding="utf-8", errors="replace") as f:
                json.dump(weibo_ref_json, f, ensure_ascii=False, indent=2)
            if collab_enabled:
                rows = weibo_ref_json.get("results")
                if isinstance(rows, list) and rows:
                    preview_lines = []
                    for row in rows[:5]:
                        if not isinstance(row, dict):
                            continue
                        title = str(row.get("title") or row.get("name") or row.get("content") or "").strip()
                        if title:
                            preview_lines.append(f"- {title[:120]}")
                    if preview_lines:
                        console.print("[dim]微博智搜预览（辅助你输入观点研判）：[/dim]")
                        console.print(f"[dim]{chr(10).join(preview_lines)}[/dim]")
        except Exception as e:
            _append_ndjson_log(
                run_id="event_analysis_reference",
                hypothesis_id="H50_weibo_aisearch_prefetch_optional",
                location="workflow/event_analysis_pipeline.py:weibo_aisearch_prefetch",
                message="Step9 微博智搜预览失败，已跳过",
                data={"error": str(e)},
            )

    user_judgement_text = str(os.environ.get("SONA_EVENT_USER_JUDGEMENT", "") or "").strip()
    if collab_enabled and not user_judgement_text:
        user_judgement_text = _prompt_text_timeout(
            "可选：主研判输入（影响报告核心叙事/优先级；45s 无响应跳过）",
            timeout_sec=max(collab_timeout_sec, 25),
            default_text="",
        )

    user_focus_keywords = _fallback_search_words_from_query(user_judgement_text, max_words=8) if user_judgement_text else []
    user_judgement_payload = {
        "has_input": bool(user_judgement_text),
        "mode": collab_mode,
        "source": "env" if str(os.environ.get("SONA_EVENT_USER_JUDGEMENT", "") or "").strip() else ("interactive" if user_judgement_text else "none"),
        "user_judgement": user_judgement_text,
        "focus_keywords": user_focus_keywords,
        "difference_note": "本字段用于主研判（优先级/结论取向）；Step10.4 的 expert_note 用于补充专业背景、证据线索或反例校验，可自动复用本输入。",
        "weibo_aisearch_ref_path": str(weibo_ref_path) if weibo_ref_json else "",
        "weibo_aisearch_ref_count": int((weibo_ref_json or {}).get("count") or 0) if isinstance(weibo_ref_json, dict) else 0,
        "context_priority": {
            "user_judgement_weight": _safe_float(os.environ.get("SONA_USER_JUDGEMENT_WEIGHT", "0.65"), 0.65),
            "rag_reference_weight": _safe_float(os.environ.get("SONA_RAG_REFERENCE_WEIGHT", "0.35"), 0.35),
            "note": "用户研判优先，RAG/Wiki/OPRAG 作为补充校验。",
        },
        "created_at": datetime.now().isoformat(sep=" "),
    }
    user_judgement_path = process_dir / "user_judgement_input.json"
    with open(user_judgement_path, "w", encoding="utf-8", errors="replace") as f:
        json.dump(user_judgement_payload, f, ensure_ascii=False, indent=2)
    if user_judgement_text and isinstance(interpretation, dict):
        interpretation["user_focus"] = user_judgement_text
        interpretation["user_focus_keywords"] = user_focus_keywords

    _append_ndjson_log(
        run_id="event_analysis_collab_mode",
        hypothesis_id="H39_user_judgement_input",
        location="workflow/event_analysis_pipeline.py:user_judgement_input",
        message="用户协同研判输入已处理",
        data={
            "has_input": bool(user_judgement_text),
            "focus_keywords": user_focus_keywords[:6],
            "path": str(user_judgement_path),
        },
    )

    # ============ 10) 事件参考资料与知识增强（10.1~10.4） ============

    # ============ 10.1) Graph RAG 增强（可选，默认关闭） ============
    if debug:
        console.print("[bold]Step10.1: graph_rag_query (enrich)[/bold]")

    graph_rag_enabled = _is_graph_rag_enabled()
    # #region debug_log_H11_graph_rag_switch
    _append_ndjson_log(
        run_id="event_analysis_graph_rag",
        hypothesis_id="H11_graph_rag_switch",
        location="workflow/event_analysis_pipeline.py:graph_rag_switch",
        message="Graph RAG 开关判定",
        data={"enabled": graph_rag_enabled},
    )
    # #endregion debug_log_H11_graph_rag_switch

    event_type_raw = _normalize_opt_str(interpretation.get("event_type"))
    domain_raw = _normalize_opt_str(interpretation.get("domain"))
    stage_raw = _normalize_opt_str(interpretation.get("stage"))
    seed_text = (
        f"{search_plan.get('eventIntroduction', '')} "
        f"{timeline_json.get('summary', '')} "
        f"{user_judgement_text}"
    )
    event_type = event_type_raw or _infer_event_type_from_text(seed_text)
    domain = domain_raw or _infer_domain_from_text(seed_text)
    stage = stage_raw or _infer_stage_from_text(seed_text)
    theory_names = interpretation.get("theory_names") or []
    indicators_dimensions = interpretation.get("indicators_dimensions") or []

    _append_ndjson_log(
        run_id="event_analysis_graph_rag",
        hypothesis_id="H37_graph_rag_input_infer",
        location="workflow/event_analysis_pipeline.py:graph_rag_input_prepare",
        message="Graph RAG 输入参数已准备（含空值推断）",
        data={
            "event_type_raw": event_type_raw,
            "domain_raw": domain_raw,
            "stage_raw": stage_raw,
            "event_type_final": event_type,
            "domain_final": domain,
            "stage_final": stage,
        },
    )

    if graph_rag_enabled:
        try:
            graph_rag_start = time.time()
            max_workers = max(1, min(_safe_int(os.environ.get("SONA_GRAPH_RAG_MAX_WORKERS", "4"), 4), 8))

            # similar_cases + theory + indicators 并发查询
            with ThreadPoolExecutor(max_workers=max_workers) as pool:
                futures: Dict[str, Any] = {}
                futures["similar_cases"] = pool.submit(
                    _invoke_tool_to_json,
                    graph_rag_query,
                    {
                        "query_type": "similar_cases",
                        "event_type": event_type,
                        "domain": domain,
                        "stage": stage,
                        "limit": 5,
                    },
                )

                theory_keys: List[str] = []
                if isinstance(theory_names, list):
                    for i, tn in enumerate(theory_names[:3]):
                        if not tn:
                            continue
                        key = f"theory_{i}"
                        theory_keys.append(key)
                        futures[key] = pool.submit(
                            _invoke_tool_to_json,
                            graph_rag_query,
                            {"query_type": "theory", "theory_name": str(tn), "limit": 5},
                        )

                indicator_keys: List[str] = []
                if isinstance(indicators_dimensions, list):
                    for i, dim in enumerate(indicators_dimensions[:3]):
                        if not dim:
                            continue
                        key = f"indicator_{i}"
                        indicator_keys.append(key)
                        futures[key] = pool.submit(
                            _invoke_tool_to_json,
                            graph_rag_query,
                            {"query_type": "indicators", "dimension": str(dim), "limit": 10},
                        )

                similar_json = futures["similar_cases"].result()
                theories = [futures[k].result() for k in theory_keys]
                indicators = [futures[k].result() for k in indicator_keys]

            # #region debug_log_H18_step_timing_graph_rag
            _append_ndjson_log(
                run_id="event_analysis_timing",
                hypothesis_id="H18_step_timing_graph_rag",
                location="workflow/event_analysis_pipeline.py:after_graph_rag_parallel",
                message="Graph RAG 并发查询耗时",
                data={"elapsed_sec": round(time.time() - graph_rag_start, 3), "max_workers": max_workers},
            )
            # #endregion debug_log_H18_step_timing_graph_rag

            def _extract_errors(block: Any) -> List[str]:
                errs: List[str] = []
                if isinstance(block, dict):
                    e = str(block.get("error", "") or "").strip()
                    if e:
                        errs.append(e)
                    rs = block.get("results")
                    if isinstance(rs, list):
                        for it in rs:
                            if isinstance(it, dict):
                                ie = str(it.get("error", "") or "").strip()
                                if ie:
                                    errs.append(ie)
                return errs

            def _has_effective_results(block: Any) -> bool:
                if not isinstance(block, dict):
                    return False
                rs = block.get("results")
                if not isinstance(rs, list):
                    return False
                for it in rs:
                    if isinstance(it, dict):
                        if str(it.get("error", "") or "").strip():
                            continue
                        # 只要有标题/名称/描述之一，视为有效增强结果
                        if any(str(it.get(k, "") or "").strip() for k in ("title", "name", "description", "source")):
                            return True
                    elif it:
                        return True
                return False

            all_error_msgs: List[str] = []
            all_error_msgs.extend(_extract_errors(similar_json))
            for t in theories:
                all_error_msgs.extend(_extract_errors(t))
            for i in indicators:
                all_error_msgs.extend(_extract_errors(i))
            dedup_errors = []
            seen_err = set()
            for msg in all_error_msgs:
                if msg in seen_err:
                    continue
                seen_err.add(msg)
                dedup_errors.append(msg)

            useful = _has_effective_results(similar_json) or any(_has_effective_results(t) for t in theories) or any(
                _has_effective_results(i) for i in indicators
            )

            graph_rag_enrichment = {
                "status": "enabled_success" if useful else "enabled_but_empty",
                "reason": "" if useful else "Graph RAG 已执行，但未检索到可用于增强报告的结构化结果。",
                "errors": dedup_errors[:6] if dedup_errors else [],
                "similar_cases": similar_json,
                "theories": theories,
                "indicators": indicators,
                "input": {
                    "event_type": event_type,
                    "domain": domain,
                    "stage": stage,
                    "theory_names": theory_names[:3] if isinstance(theory_names, list) else [],
                    "indicators_dimensions": indicators_dimensions[:3] if isinstance(indicators_dimensions, list) else [],
                },
            }
        except Exception as e:
            graph_rag_enrichment = {
                "status": "enabled_but_failed_skip",
                "error": str(e),
                "input": {
                    "event_type": event_type,
                    "domain": domain,
                    "stage": stage,
                },
            }
            # #region debug_log_H12_graph_rag_skip_on_error
            _append_ndjson_log(
                run_id="event_analysis_graph_rag",
                hypothesis_id="H12_graph_rag_skip_on_error",
                location="workflow/event_analysis_pipeline.py:graph_rag_exception",
                message="Graph RAG 执行失败并已跳过",
                data={"error": str(e)},
            )
            # #endregion debug_log_H12_graph_rag_skip_on_error
    else:
        graph_rag_enrichment = {
            "status": "disabled_skip",
            "reason": "SONA_ENABLE_GRAPH_RAG 未开启，已跳过。",
            "input": {
                "event_type": event_type,
                "domain": domain,
                "stage": stage,
            },
        }

    # 协同采纳：允许用户决定 Graph RAG 召回结果是否采纳/裁剪
    if graph_rag_enabled and isinstance(graph_rag_enrichment, dict):
        status_text = str(graph_rag_enrichment.get("status", "") or "").strip()
        similar_before = _graph_valid_result_count(graph_rag_enrichment.get("similar_cases"))
        theory_before = 0
        indicator_before = 0
        theories_block = graph_rag_enrichment.get("theories")
        indicators_block = graph_rag_enrichment.get("indicators")
        if isinstance(theories_block, list):
            theory_before = sum(_graph_valid_result_count(x) for x in theories_block if isinstance(x, dict))
        if isinstance(indicators_block, list):
            indicator_before = sum(_graph_valid_result_count(x) for x in indicators_block if isinstance(x, dict))

        decision_mode = str(os.environ.get("SONA_GRAPH_RAG_ADOPTION", "") or "").strip().lower()
        if decision_mode not in {"all", "top", "none"}:
            decision_mode = ""

        if collab_enabled and not decision_mode and status_text.startswith("enabled"):
            total_hits = similar_before + theory_before + indicator_before
            if total_hits > 0:
                if debug:
                    console.print(
                        f"[dim]Graph RAG 召回预览: similar={similar_before}, theory={theory_before}, indicators={indicator_before}[/dim]"
                    )
                choice = _prompt_text_timeout(
                    "Graph RAG 召回是否采纳？输入 all(全部) / top(仅保留高分) / none(不采纳)",
                    timeout_sec=max(collab_timeout_sec, 20),
                    default_text="all",
                ).strip().lower()
                if choice in {"all", "top", "none"}:
                    decision_mode = choice

        if not decision_mode:
            decision_mode = str(os.environ.get("SONA_GRAPH_RAG_ADOPTION_DEFAULT", "all") or "").strip().lower()
            if decision_mode not in {"all", "top", "none"}:
                decision_mode = "all"

        top_similar = max(1, min(_safe_int(os.environ.get("SONA_GRAPH_RAG_TOP_SIMILAR", "2"), 2), 10))
        top_theory = max(1, min(_safe_int(os.environ.get("SONA_GRAPH_RAG_TOP_THEORY", "2"), 2), 10))
        top_indicator = max(1, min(_safe_int(os.environ.get("SONA_GRAPH_RAG_TOP_INDICATOR", "3"), 3), 15))

        if status_text.startswith("enabled"):
            if decision_mode == "none":
                graph_rag_enrichment["status"] = "enabled_user_rejected"
                graph_rag_enrichment["reason"] = "用户选择不采纳 Graph RAG 召回结果。"
                graph_rag_enrichment["similar_cases"] = _graph_trim_block(graph_rag_enrichment.get("similar_cases"), 0)
                graph_rag_enrichment["theories"] = [
                    _graph_trim_block(x, 0) for x in (theories_block if isinstance(theories_block, list) else [])
                ]
                graph_rag_enrichment["indicators"] = [
                    _graph_trim_block(x, 0) for x in (indicators_block if isinstance(indicators_block, list) else [])
                ]
            elif decision_mode == "top":
                graph_rag_enrichment["similar_cases"] = _graph_trim_block(graph_rag_enrichment.get("similar_cases"), top_similar)
                graph_rag_enrichment["theories"] = [
                    _graph_trim_block(x, top_theory) for x in (theories_block if isinstance(theories_block, list) else [])
                ]
                graph_rag_enrichment["indicators"] = [
                    _graph_trim_block(x, top_indicator) for x in (indicators_block if isinstance(indicators_block, list) else [])
                ]

        similar_after = _graph_valid_result_count(graph_rag_enrichment.get("similar_cases"))
        theory_after = 0
        indicator_after = 0
        if isinstance(graph_rag_enrichment.get("theories"), list):
            theory_after = sum(_graph_valid_result_count(x) for x in graph_rag_enrichment.get("theories") if isinstance(x, dict))
        if isinstance(graph_rag_enrichment.get("indicators"), list):
            indicator_after = sum(_graph_valid_result_count(x) for x in graph_rag_enrichment.get("indicators") if isinstance(x, dict))

        graph_rag_enrichment["user_decision"] = {
            "mode": decision_mode,
            "before": {"similar_cases": similar_before, "theories": theory_before, "indicators": indicator_before},
            "after": {"similar_cases": similar_after, "theories": theory_after, "indicators": indicator_after},
            "collab_mode": collab_mode,
            "created_at": datetime.now().isoformat(sep=" "),
        }

        _append_ndjson_log(
            run_id="event_analysis_graph_rag",
            hypothesis_id="H40_graph_rag_user_decision",
            location="workflow/event_analysis_pipeline.py:graph_rag_user_decision",
            message="Graph RAG 召回采纳策略已落地",
            data=graph_rag_enrichment.get("user_decision") if isinstance(graph_rag_enrichment.get("user_decision"), dict) else {},
        )

    out_path = process_dir / "graph_rag_enrichment.json"
    with open(out_path, "w", encoding="utf-8", errors="replace") as f:
        json.dump(graph_rag_enrichment, f, ensure_ascii=False, indent=2)

    # ============ 10.2) Wiki KB 召回快照（可选） ============
    if debug:
        console.print("[bold]Step10.2: wiki_snapshot (optional)[/bold]")
    try:
        wiki_query = _build_reference_query(user_query=user_query, search_plan=search_plan)
        wiki_query = wiki_query or (f"{search_plan.get('eventIntroduction', user_query)}".strip() or str(user_query or "").strip())
        wiki_out = answer_wiki_query(
            wiki_query,
            topk=6,
            style="teach",
            project_root=_ROOT,
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
                project_root=_ROOT,
            )
            if isinstance(fallback_out, dict):
                fallback_sources = fallback_out.get("sources")
                old_sources = wiki_out.get("sources") if isinstance(wiki_out, dict) else []
                if isinstance(fallback_sources, list) and len(fallback_sources) > (len(old_sources) if isinstance(old_sources, list) else 0):
                    wiki_out = fallback_out
        wiki_snapshot_path = process_dir / "wiki_qa_snapshot.json"
        with open(wiki_snapshot_path, "w", encoding="utf-8", errors="replace") as f:
            json.dump(wiki_out, f, ensure_ascii=False, indent=2)
        try:
            sources = wiki_out.get("sources") if isinstance(wiki_out, dict) else []
            meta = wiki_out.get("_wiki_meta") if isinstance(wiki_out, dict) else {}
            retrieved_count = (
                int(meta.get("retrieved_count", 0))
                if isinstance(meta, dict)
                else (len(sources) if isinstance(sources, list) else 0)
            )
            llm_used = bool(meta.get("llm_used")) if isinstance(meta, dict) else False
            preview_lines = [
                f"wiki_query: {wiki_query[:160]}",
                f"retrieved_count: {retrieved_count}",
                f"llm_used: {llm_used}",
            ]
            if isinstance(sources, list) and sources:
                preview_lines.append("top_sources:")
                for row in sources[:4]:
                    if not isinstance(row, dict):
                        continue
                    title = str(row.get("title", "") or "").strip()
                    path = str(row.get("path", "") or "").strip()
                    score = row.get("score", 0)
                    preview_lines.append(f"- {title} (score={score}) | {path}")
            _write_text_file(process_dir / "wiki_recall_preview.txt", "\n".join(preview_lines))
            if debug:
                console.print("[dim]Wiki KB 召回预览（用于核验报告引用）[/dim]")
                console.print(f"[dim]{chr(10).join(preview_lines)}[/dim]")
        except Exception:
            pass
    except Exception as e:
        _append_ndjson_log(
            run_id="event_analysis_wiki_kb",
            hypothesis_id="H44_wiki_snapshot_optional",
            location="workflow/event_analysis_pipeline.py:wiki_snapshot",
            message="Wiki KB 召回快照构建失败，已跳过",
            data={"error": str(e)},
        )

    # ============ 10.3) OPRAG 知识快照（可选） ============
    if debug:
        console.print("[bold]Step10.3: oprag_snapshot (optional)[/bold]")
    try:
        oprag_query = (
            f"方法论 理论 传播机制 历史对比 事件复盘 {search_plan.get('eventIntroduction', user_query)}"
        ).strip()
        oprag_primary = _invoke_tool_to_json(
            load_sentiment_knowledge,
            {"keyword": oprag_query},
        )
        oprag_ref = _invoke_tool_to_json(
            search_reference_insights,
            {"query": oprag_query, "limit": 10},
        )
        oprag_snapshot = {
            "query": oprag_query,
            "knowledge": oprag_primary,
            "references": oprag_ref,
            "created_at": datetime.now().isoformat(sep=" "),
        }
        oprag_snapshot_path = process_dir / OPRAG_KNOWLEDGE_SNAPSHOT_FILENAME
        with open(oprag_snapshot_path, "w", encoding="utf-8", errors="replace") as f:
            json.dump(oprag_snapshot, f, ensure_ascii=False, indent=2)
        preview = _preview_oprag_snapshot(oprag_snapshot)
        if preview:
            _write_text_file(process_dir / OPRAG_RECALL_PREVIEW_FILENAME, preview)
            if debug:
                console.print("[dim]OPRAG 召回预览（用于核验报告引用）[/dim]")
                console.print(f"[dim]{preview}[/dim]")
    except Exception as e:
        _append_ndjson_log(
            run_id="event_analysis_oprag",
            hypothesis_id="H43_oprag_snapshot_optional",
            location="workflow/event_analysis_pipeline.py:oprag_snapshot",
            message="OPRAG 快照构建失败，已跳过",
            data={"error": str(e)},
        )

    # ============ 10.4) 事件参考资料检索（reference_insights） ============
    if debug:
        console.print("[bold]Step10.4: reference_insights (optional)[/bold]")
    try:
        ref_query = _build_reference_query(user_query=user_query, search_plan=search_plan)
        ref_json_raw = _invoke_tool_to_json(
            search_reference_insights,
            {"query": ref_query, "limit": 12},
        )
        ref_json = _filter_reference_hits(
            ref_json_raw,
            user_query=user_query,
            search_words=_to_clean_str_list(search_plan.get("searchWords"), max_items=10),
            min_keep=3,
        )
        filtered_meta = ref_json.get("_filtered") if isinstance(ref_json.get("_filtered"), dict) else {}
        runtime_harness.record(
            "reference_recall_quality",
            {
                "reference_query": ref_query,
                "raw_count": int(ref_json_raw.get("count") or 0) if isinstance(ref_json_raw, dict) else 0,
                "filtered_count": int(ref_json.get("count") or 0),
                "dropped_count": int(filtered_meta.get("dropped", 0) or 0),
            },
        )
        ref_path = process_dir / "reference_insights.json"
        with open(ref_path, "w", encoding="utf-8", errors="replace") as f:
            json.dump(ref_json, f, ensure_ascii=False, indent=2)

        link_json = _invoke_tool_to_json(
            build_event_reference_links,
            {"topic": search_plan.get("eventIntroduction", user_query)},
        )
        link_path = process_dir / "reference_links.json"
        with open(link_path, "w", encoding="utf-8", errors="replace") as f:
            json.dump(link_json, f, ensure_ascii=False, indent=2)

        expert_note = str(os.environ.get("SONA_EVENT_EXPERT_NOTE", "") or "").strip()
        if collab_enabled and not expert_note:
            expert_note = _prompt_text_timeout(
                "可选：专家补充说明（专业背景/证据线索/反例校验；可复用主研判；45s 无响应跳过）",
                timeout_sec=max(collab_timeout_sec, 25),
                default_text="",
            )
        derived = False
        if (not expert_note) and user_judgement_text:
            expert_note = user_judgement_text
            derived = True
        expert_note_path = process_dir / "user_expert_notes.json"
        expert_note_payload = {
            "has_input": bool(expert_note),
            "source": (
                "env"
                if str(os.environ.get("SONA_EVENT_EXPERT_NOTE", "") or "").strip()
                else (
                    "derived_from_user_judgement"
                    if derived
                    else ("interactive" if expert_note else "none")
                )
            ),
            "expert_note": expert_note,
            "derived_from_user_judgement": derived,
            "difference_note": "本字段用于专家补充（证据线索/专业背景/反例校验）；为空时可自动复用 Step9 主研判以避免重复输入。",
            "created_at": datetime.now().isoformat(sep=" "),
        }
        with open(expert_note_path, "w", encoding="utf-8", errors="replace") as f:
            json.dump(expert_note_payload, f, ensure_ascii=False, indent=2)

        _append_ndjson_log(
            run_id="event_analysis_reference",
            hypothesis_id="H36_reference_insights_collected",
            location="workflow/event_analysis_pipeline.py:reference_insights",
            message="舆情智库参考检索已完成并写入过程文件",
            data={
                "reference_insights_path": str(ref_path),
                "reference_query": ref_query,
                "reference_links_path": str(link_path),
                "reference_count": int(ref_json.get("count") or 0),
                "links_count": int(link_json.get("count") or 0),
                "weibo_ref_path": str(weibo_ref_path) if enable_weibo_ref else "",
                "weibo_ref_count": int((weibo_ref_json or {}).get("count") or 0) if enable_weibo_ref else 0,
                "expert_note_path": str(expert_note_path),
                "expert_note_len": len(expert_note),
            },
        )
    except Exception as e:
        if debug:
            console.print("[yellow]⚠️ reference_insights 执行失败，已跳过，不影响后续流程[/yellow]")
        _append_ndjson_log(
            run_id="event_analysis_reference",
            hypothesis_id="H36_reference_insights_collected",
            location="workflow/event_analysis_pipeline.py:reference_insights_exception",
            message="舆情智库参考检索失败，已跳过",
            data={"error": str(e)},
        )

    # ============ 11) 报告生成（report_html） ============
    if debug:
        console.print("[bold]Step11: report_html[/bold]")
    _progress_advance()
    _progress_step("Step11: report_html")

    report_json = _invoke_tool_to_json(
        report_html,
        {
            "eventIntroduction": search_plan["eventIntroduction"],
            "analysisResultsDir": str(process_dir),
            "report_length": effective_report_length,
        },
    )
    html_file_path = str(report_json.get("html_file_path") or "")
    file_url = str(report_json.get("file_url") or "")

    if not html_file_path and file_url:
        html_file_path = file_url

    if sys.stdout.isatty():
        try:
            open_url = ""
            if html_file_path:
                try:
                    open_url = Path(html_file_path).expanduser().resolve().as_uri()
                except Exception:
                    open_url = file_url
            else:
                open_url = file_url
            if open_url:
                webbrowser.open(open_url)
        except Exception:
            pass

    final_msg = f"已完成舆情事件分析工作流。报告：{file_url or html_file_path}"
    runtime_harness.finalize()
    session_manager.add_message(task_id, "assistant", final_msg)

    console.print()
    console.print(f"[green]✅ {final_msg}[/green]")
    try:
        if enable_progress and progress is not None and progress_started:
            _progress_advance()
            progress.stop()
    except Exception:
        pass
    return file_url or html_file_path
