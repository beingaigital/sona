"""舆情事件分析工作流（4.1）：以可交互 debug 形式落地搜索方案确认与结构化产物生成。"""

from __future__ import annotations

import json
import os
import sys
import webbrowser
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
import select
import time
from typing import Any, Dict, List, Optional, Tuple
from concurrent.futures import ThreadPoolExecutor

from rich.console import Console
from rich.prompt import Prompt

from tools import (
    extract_search_terms,
    data_num,
    data_collect,
    analysis_timeline,
    analysis_sentiment,
    dataset_summary,
    generate_interpretation,
    graph_rag_query,
    report_html,
)
from utils.path import ensure_task_dirs, get_sandbox_dir
from utils.task_context import set_task_id
from utils.session_manager import SessionManager


console = Console()

LOG_PATH = "/Users/biaowenhuang/Documents/sona-master/.cursor/debug.log"
EXPERIENCE_PATH = "/Users/biaowenhuang/Documents/sona-master/memory/LTM/search_plan_experience.jsonl"


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

    payload: Dict[str, Any] = {
        "id": f"log_{int(time.time() * 1000)}_{abs(hash((hypothesis_id, location, message))) % 10_000_000}",
        "timestamp": int(time.time() * 1000),
        "runId": run_id,
        "hypothesisId": hypothesis_id,
        "location": location,
        "message": message,
        "data": data or {},
    }
    try:
        with open(LOG_PATH, "a", encoding="utf-8", errors="replace") as f:
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except Exception:
        # 不让日志失败影响主流程
        return


def _prompt_yes_no_timeout(question: str, timeout_sec: int = 20, default_yes: bool = True) -> bool:
    """
    以 y/n 方式询问，并提供超时：timeout 后默认继续（默认 y）。
    """

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
    if ans_l.startswith("y"):
        return True
    if ans_l.startswith("n"):
        return False
    return default_yes


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
    start, end = [x.strip() for x in time_range.split(";", maxsplit=1)]
    if not start or not end:
        return False
    from datetime import datetime as dt

    for value in (start, end):
        ok = False
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S", "%Y-%m-%d"):
            try:
                dt.strptime(value, fmt)
                ok = True
                break
            except Exception:
                continue
        if not ok:
            return False
    return True


def _build_default_time_range(days: int = 30) -> str:
    """
    生成默认时间范围：昨天 23:59:59 往前 days 天。
    """
    end = datetime.now().replace(hour=23, minute=59, second=59, microsecond=0) - timedelta(days=1)
    start = end - timedelta(days=days)
    return f"{start.strftime('%Y-%m-%d %H:%M:%S')};{end.strftime('%Y-%m-%d %H:%M:%S')}"


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


def _safe_int(value: Any, default: int) -> int:
    try:
        v = int(value)
        return v
    except Exception:
        return default


def _allow_history_fallback() -> bool:
    v = os.environ.get("SONA_ALLOW_HISTORY_FALLBACK", "false").strip().lower()
    return v in ("1", "true", "yes", "y", "on")


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


def _normalize_opt_str(value: Any) -> Optional[str]:
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    return s


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
    tokens = {t.strip() for t in cleaned.split() if t.strip()}
    return tokens


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
            location="cli/event_analysis_workflow.py:save_experience",
            message="历史经验已写入本地 LTM",
            data={"task_id": task_id, "path": EXPERIENCE_PATH},
        )
        # #endregion debug_log_H14_experience_saved
    except Exception:
        # #region debug_log_H14_experience_save_failed
        _append_ndjson_log(
            run_id="event_analysis_experience",
            hypothesis_id="H14_experience_save_failed",
            location="cli/event_analysis_workflow.py:save_experience",
            message="历史经验写入失败",
            data={"task_id": task_id, "path": EXPERIENCE_PATH},
        )
        # #endregion debug_log_H14_experience_save_failed
        return


def _is_graph_rag_enabled() -> bool:
    """
    Graph RAG 开关：默认关闭，显式设置 SONA_ENABLE_GRAPH_RAG=true 才启用。
    """
    v = os.environ.get("SONA_ENABLE_GRAPH_RAG", "").strip().lower()
    return v in ("1", "true", "yes", "y", "on")


def run_event_analysis_workflow(
    user_query: str,
    task_id: str,
    session_manager: SessionManager,
    *,
    debug: bool = False,
    default_threshold: int = 2000,
    existing_data_path: Optional[str] = None,
    skip_data_collect: bool = False,
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
    
    Returns:
        report_html 生成的 `file_url`（若为空则返回 html 文件路径）。
    """

    # 关键：让 tools/* 能读取 task_id 写入过程目录
    set_task_id(task_id)
    process_dir = ensure_task_dirs(task_id)

    if debug:
        console.print(f"[green]🔧 进入 EventAnalysisWorkflow[/green] task_id={task_id}")

    session_manager.add_message(task_id, "user", user_query)
    _set_session_final_query(session_manager, task_id, user_query)

    # ============ 0) 历史经验复用（可跳过 extract） ============
    best_exp = _find_best_experience(user_query)
    # #region debug_log_H9_experience_lookup
    _append_ndjson_log(
        run_id="event_analysis_experience",
        hypothesis_id="H9_experience_lookup",
        location="cli/event_analysis_workflow.py:experience_lookup",
        message="历史经验检索结果",
        data={
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
        use_history = _prompt_yes_no_timeout(
            "是否复用这条历史检索/采集经验？(y 复用 / n 不复用)",
            timeout_sec=20,
            default_yes=False,
        )
        if use_history:
            search_plan = dict(best_exp.get("search_plan") or {})
            suggested_collect_plan = dict(best_exp.get("collect_plan") or {})
            # 与当前 query 绑定，确保 session 描述等仍按本次 query
            search_plan["eventIntroduction"] = str(search_plan.get("eventIntroduction", "") or "")
            search_plan["searchWords"] = _to_clean_str_list(search_plan.get("searchWords"), max_items=12)
            search_plan["timeRange"] = str(search_plan.get("timeRange", "") or "")
            used_experience = True
            # #region debug_log_H10_experience_reused
            _append_ndjson_log(
                run_id="event_analysis_experience",
                hypothesis_id="H10_experience_reused",
                location="cli/event_analysis_workflow.py:experience_reused",
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
        plan_json = _invoke_tool_to_json(extract_search_terms, {"query": user_query})
        # #region debug_log_H13_step_timing_extract
        _append_ndjson_log(
            run_id="event_analysis_timing",
            hypothesis_id="H13_step_timing_extract",
            location="cli/event_analysis_workflow.py:after_extract_search_terms",
            message="extract_search_terms 耗时",
            data={"elapsed_sec": round(time.time() - step1_start, 3)},
        )
        # #endregion debug_log_H13_step_timing_extract
        search_plan = {
            "eventIntroduction": str(plan_json.get("eventIntroduction", "") or ""),
            "searchWords": _to_clean_str_list(plan_json.get("searchWords"), max_items=12),
            "timeRange": str(plan_json.get("timeRange", "") or ""),
        }

        if not search_plan["searchWords"]:
            fallback_words = _fallback_search_words_from_query(user_query)
            if fallback_words:
                search_plan["searchWords"] = fallback_words
                # #region debug_log_H28_search_words_fallback
                _append_ndjson_log(
                    run_id="event_analysis_fallback",
                    hypothesis_id="H28_search_words_fallback",
                    location="cli/event_analysis_workflow.py:extract_search_words_fallback",
                    message="extract_search_terms 返回空 searchWords，已使用 query 兜底关键词",
                    data={"fallback_words": fallback_words[:8]},
                )
                # #endregion debug_log_H28_search_words_fallback
            else:
                raise ValueError("searchWords 为空，且无法从 query 提取兜底关键词")
        if not _validate_time_range(search_plan["timeRange"]):
            fallback_time_range = _build_default_time_range(30)
            search_plan["timeRange"] = fallback_time_range
            # #region debug_log_H29_time_range_fallback
            _append_ndjson_log(
                run_id="event_analysis_fallback",
                hypothesis_id="H29_time_range_fallback",
                location="cli/event_analysis_workflow.py:extract_time_range_fallback",
                message="extract_search_terms 返回非法 timeRange，已回退默认时间范围",
                data={"fallback_time_range": fallback_time_range},
            )
            # #endregion debug_log_H29_time_range_fallback

        # ============ 2) 提出建议的搜索采集方案并等待 y/n（20s 无响应默认继续） ============
        # 该"采集方案"是针对 extract_search_terms 的扩展描述，最终仍映射到现有 data_num / data_collect 能力。
        # 其中 boolean 与关键词 ; 语义需要在真实运行中与 API 行为对齐（后续你看 debug log 我们再校准）。
        keyword_count = max(1, len(search_plan["searchWords"]))
        auto_data_num_workers = max(2, min(keyword_count, 8))
        auto_data_collect_workers = max(1, min(keyword_count, 8))
        auto_analysis_workers = 2  # 仅 timeline + sentiment 两个分析节点
        suggested_collect_plan = {
            "keyword_combination_mode": "逐词检索并合并（当前实现）",
            "boolean_strategy": "OR（当前实现：各词分别检索再合并）",
            "keywords_join_with": ";",
            "platforms": ["微博"],
            "time_range": search_plan["timeRange"],
            "return_count": min(_safe_int(os.environ.get("SONA_RETURN_COUNT", ""), 2000), 10000),
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
                    2,
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
            search_plan["timeRange"] = _build_default_time_range(30)
        suggested_collect_plan = {
            "keyword_combination_mode": str(suggested_collect_plan.get("keyword_combination_mode") or "逐词检索并合并（当前实现）"),
            "boolean_strategy": str(suggested_collect_plan.get("boolean_strategy") or "OR（当前实现：各词分别检索再合并）"),
            "keywords_join_with": ";",
            "platforms": suggested_collect_plan.get("platforms") or ["微博"],
            "time_range": str(suggested_collect_plan.get("time_range") or search_plan["timeRange"]),
            "return_count": max(1, min(_safe_int(suggested_collect_plan.get("return_count"), 2000), 10000)),
            "data_num_workers": max(1, min(_safe_int(suggested_collect_plan.get("data_num_workers"), 4), 8)),
            "data_collect_workers": max(1, min(_safe_int(suggested_collect_plan.get("data_collect_workers"), 3), 8)),
            "analysis_workers": max(1, min(_safe_int(suggested_collect_plan.get("analysis_workers"), 2), 2)),
            "searchWords_preview": search_plan["searchWords"][:10],
        }

    # #region debug_log_H1_search_collect_plan_generated
    _append_ndjson_log(
        run_id="event_analysis_pre_confirm",
        hypothesis_id="H1_search_collect_plan_generated",
        location="cli/event_analysis_workflow.py:after_collect_plan",
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

    accept = _prompt_yes_no_timeout(
        "是否接受上述搜索采集方案？(y 执行 / n 修改后再确认)",
        timeout_sec=20,
        default_yes=True,
    )

    # #region debug_log_H2_timeout_or_user_choice
    _append_ndjson_log(
        run_id="event_analysis_pre_confirm",
        hypothesis_id="H2_timeout_or_user_choice",
        location="cli/event_analysis_workflow.py:confirm_choice",
        message="用户对采集方案的 y/n 决策结果记录",
        data={"accept": accept, "timeout_sec": 20},
    )
    # #endregion debug_log_H2_timeout_or_user_choice

    # 若用户选择 n，则允许编辑"平台、返回条数、时间范围、布尔策略"等（仍先通过 y 再执行）
    if not accept:
        default_platform = suggested_collect_plan["platforms"][0] if suggested_collect_plan["platforms"] else "微博"
        platform_in = Prompt.ask("修改平台（当前实现仅验证：微博；不填则默认）", default=default_platform).strip() or default_platform
        # return_count：最大 10000
        return_count_in = Prompt.ask(
            "修改返回结果条数 return_count（1-10000；不填则默认）",
            default=str(suggested_collect_plan["return_count"]),
        ).strip() or str(suggested_collect_plan["return_count"])
        return_count_in_int = _safe_int(return_count_in, int(suggested_collect_plan["return_count"]))
        return_count_in_int = max(1, min(return_count_in_int, 10000))

        # timeRange
        time_range_in = Prompt.ask(
            "修改 timeRange（形如 YYYY-MM-DD HH:MM:SS;YYYY-MM-DD HH:MM:SS；不填则默认）",
            default=str(suggested_collect_plan["time_range"]),
        ).strip() or str(suggested_collect_plan["time_range"])
        if not _validate_time_range(time_range_in):
            console.print("[red]修改后的 timeRange 格式不合法，已忽略本次 timeRange 修改[/red]")
        else:
            suggested_collect_plan["time_range"] = time_range_in

        # boolean strategy（目前仅影响我们如何拼接 searchWords 给 data_num）
        boolean_in = Prompt.ask(
            "修改布尔策略（OR 或 AND；默认 OR）",
            default=str(suggested_collect_plan["boolean_strategy"]).startswith("AND") and "AND" or "OR",
        ).strip().upper()
        if boolean_in not in ("OR", "AND"):
            boolean_in = "OR"

        suggested_collect_plan["platforms"] = [platform_in]
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
            "修改分析并发（1-2）",
            default=str(suggested_collect_plan.get("analysis_workers", 2)),
        ).strip()
        suggested_collect_plan["data_num_workers"] = max(1, min(_safe_int(data_num_workers_in, 4), 8))
        suggested_collect_plan["data_collect_workers"] = max(1, min(_safe_int(data_collect_workers_in, 3), 8))
        suggested_collect_plan["analysis_workers"] = max(1, min(_safe_int(analysis_workers_in, 2), 2))

        # #region debug_log_H1_search_collect_plan_edited
        _append_ndjson_log(
            run_id="event_analysis_pre_confirm",
            hypothesis_id="H1_search_collect_plan_edited",
            location="cli/event_analysis_workflow.py:edit_collect_plan",
            message="用户在采集方案 n 分支下进行了编辑",
            data={
                "platform": platform_in,
                "return_count": return_count_in_int,
                "boolean": boolean_in,
            },
        )
        # #endregion debug_log_H1_search_collect_plan_edited

        # 再次确认 y/n（仍保留 20s 默认继续）
        accept = _prompt_yes_no_timeout(
            "编辑完成后是否执行？(y 执行 / n 继续修改)",
            timeout_sec=20,
            default_yes=True,
        )

        # #region debug_log_H2_timeout_or_user_choice_after_edit
        _append_ndjson_log(
            run_id="event_analysis_pre_confirm",
            hypothesis_id="H2_timeout_or_user_choice_after_edit",
            location="cli/event_analysis_workflow.py:confirm_choice_after_edit",
            message="用户对编辑后采集方案的 y/n 决策结果记录",
            data={"accept": accept, "timeout_sec": 20},
        )
        # #endregion debug_log_H2_timeout_or_user_choice_after_edit

        if not accept:
            raise RuntimeError("用户未确认采集方案（选择 n），本次执行中止。")

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
    
    if skip_data_collect and existing_data_path:
        # 用户选择使用现有数据，跳过 data_num 和 data_collect
        if debug:
            console.print(f"[bold yellow]⏭️ 跳过数据采集，使用现有数据:[/bold yellow] {existing_data_path}")
        
        # 将现有路径解析为可直接分析的 CSV
        save_path = _resolve_to_csv_path(existing_data_path)
        
        # 从现有数据中提取 eventIntroduction（如果用户 query 中没有明确提供）
        # 尝试从文件名或目录名中推断
        if not search_plan.get("eventIntroduction"):
            # 使用用户 query 作为 eventIntroduction
            search_plan["eventIntroduction"] = user_query
        
        # #region debug_log_H27_skip_data_collect
        _append_ndjson_log(
            run_id="event_analysis_skip_collect",
            hypothesis_id="H27_skip_data_collect",
            location="cli/event_analysis_workflow.py:skip_data_collect",
            message="跳过数据采集阶段，使用现有数据",
            data={"existing_data_path": existing_data_path},
        )
        # #endregion debug_log_H27_skip_data_collect
        
        # 设置 eventIntroduction 用于后续分析
        if not search_plan.get("eventIntroduction"):
            search_plan["eventIntroduction"] = user_query

        if debug:
            console.print(f"[green]✅ 使用现有数据，save_path={save_path}[/green]")
    
    # ============ 3) 数量分配（data_num）- 仅在需要采集数据时执行 ============
    if not skip_data_collect or not existing_data_path:
        # 正常数据采集流程
        if debug:
            console.print("[bold]Step3: data_num[/bold]")

        platforms = suggested_collect_plan.get("platforms") or ["微博"]
        platform = str(platforms[0]) if platforms else "微博"
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
                2,
            ),
        )
        os.environ["SONA_DATA_NUM_MAX_WORKERS"] = str(data_num_workers)
        os.environ["SONA_DATA_COLLECT_MAX_WORKERS"] = str(data_collect_workers)
        os.environ["SONA_ANALYSIS_MAX_WORKERS"] = str(analysis_workers)

        # 根据布尔策略组装关键词
        boolean_strategy = str(suggested_collect_plan.get("boolean_strategy") or "")
        boolean_mode = "AND" if boolean_strategy.upper().startswith("AND") else "OR"
        if boolean_mode == "AND":
            tool_search_words: List[str] = [";".join(search_plan["searchWords"])]
        else:
            tool_search_words = search_plan["searchWords"]

        # #region debug_log_H3_tool_args_built
        _append_ndjson_log(
            run_id="event_analysis_before_data_num",
            hypothesis_id="H3_tool_args_built",
            location="cli/event_analysis_workflow.py:before_data_num",
            message="构建 data_num 工具输入参数",
            data={
                "platform": platform,
                "threshold(return_count)": return_count,
                "tool_search_words_count": len(tool_search_words),
                "boolean_mode": boolean_mode,
                "data_num_workers": data_num_workers,
                "data_collect_workers": data_collect_workers,
                "analysis_workers": analysis_workers,
            },
        )
        # #endregion debug_log_H3_tool_args_built

        matrix_json, data_num_elapsed = _invoke_tool_with_timing(
            data_num,
            {
                "searchWords": json.dumps(tool_search_words, ensure_ascii=False),
                "timeRange": suggested_collect_plan["time_range"],
                "threshold": return_count,
                "platform": platform,
            },
        )
        # #region debug_log_H16_step_timing_data_num
        _append_ndjson_log(
            run_id="event_analysis_timing",
            hypothesis_id="H16_step_timing_data_num",
            location="cli/event_analysis_workflow.py:after_data_num",
            message="data_num 耗时",
            data={"elapsed_sec": data_num_elapsed, "search_words_count": len(tool_search_words)},
        )
        # #endregion debug_log_H16_step_timing_data_num

        search_matrix_raw = matrix_json.get("search_matrix")
        search_matrix = search_matrix_raw if isinstance(search_matrix_raw, dict) else {}
        time_range_used = matrix_json.get("time_range") or suggested_collect_plan["time_range"]

        # data_num 失败时优先降级为“均分采集矩阵”，避免直接回退历史数据
        if not search_matrix:
            data_num_error = str(matrix_json.get("error") or "data_num 未返回可用 search_matrix")
            fallback_matrix = _build_uniform_search_matrix(tool_search_words, return_count)
            if fallback_matrix:
                search_matrix = fallback_matrix
                time_range_used = suggested_collect_plan["time_range"]
                console.print(
                    "[yellow]⚠️ data_num 未返回可用搜索矩阵，已按当前关键词均分数量继续执行 data_collect[/yellow]"
                )
                _append_ndjson_log(
                    run_id="event_analysis_fallback",
                    hypothesis_id="H34_data_num_fallback_to_uniform_matrix",
                    location="cli/event_analysis_workflow.py:data_num_uniform_matrix_fallback",
                    message="data_num 失败，已使用均分矩阵继续 data_collect",
                    data={
                        "task_id": task_id,
                        "data_num_error": data_num_error[:500],
                        "fallback_matrix_size": len(fallback_matrix),
                        "fallback_matrix_preview": list(fallback_matrix.items())[:5],
                    },
                )
            else:
                if not _allow_history_fallback():
                    raise ValueError(
                        f"search_matrix 为空，且已关闭历史回退（SONA_ALLOW_HISTORY_FALLBACK=false）。"
                        f"data_num_error={data_num_error}。"
                        "建议先尝试：SONA_NETINSIGHT_NO_PROXY=true，或 NETINSIGHT_HEADLESS=false 后重试。"
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
                    console.print(
                        "[yellow]⚠️ data_num 未返回可用搜索矩阵，已自动回退复用最近历史数据继续分析[/yellow]"
                    )
                    _append_ndjson_log(
                        run_id="event_analysis_fallback",
                        hypothesis_id="H31_data_num_fallback_to_existing_csv",
                        location="cli/event_analysis_workflow.py:data_num_fallback",
                        message="data_num 失败，已回退使用历史 CSV",
                        data={
                            "task_id": task_id,
                            "data_num_error": data_num_error[:500],
                            "fallback_save_path": save_path,
                        },
                    )
                else:
                    raise ValueError(f"search_matrix 为空，且无可复用历史数据。data_num_error={data_num_error}")

        # ============ 4) 数据采集（data_collect） ============
        if not skip_data_collect:
            if debug:
                console.print("[bold]Step5: data_collect[/bold]")

            collect_json, data_collect_elapsed = _invoke_tool_with_timing(
                data_collect,
                {
                    "searchMatrix": json.dumps(search_matrix, ensure_ascii=False),
                    "timeRange": str(time_range_used),
                    "platform": platform,
                },
            )
            # #region debug_log_H17_step_timing_data_collect
            _append_ndjson_log(
                run_id="event_analysis_timing",
                hypothesis_id="H17_step_timing_data_collect",
                location="cli/event_analysis_workflow.py:after_data_collect",
                message="data_collect 耗时",
                data={"elapsed_sec": data_collect_elapsed, "platform": platform},
            )
            # #endregion debug_log_H17_step_timing_data_collect

            collect_error = str(collect_json.get("error") or "").strip()
            save_path_raw = str(collect_json.get("save_path") or "")
            resolved_collect_path = ""
            try:
                resolved_collect_path = _resolve_to_csv_path(save_path_raw)
            except Exception:
                resolved_collect_path = ""

            if collect_error or not resolved_collect_path or not Path(resolved_collect_path).exists():
                if not _allow_history_fallback():
                    err_msg = collect_error or f"data_collect 未返回有效 save_path: {save_path_raw}"
                    raise ValueError(
                        f"{err_msg}；且已关闭历史回退（SONA_ALLOW_HISTORY_FALLBACK=false）。"
                        "建议先尝试：SONA_NETINSIGHT_NO_PROXY=true，或 NETINSIGHT_HEADLESS=false 后重试。"
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
                    console.print(
                        "[yellow]⚠️ data_collect 失败，已自动回退复用最近历史数据继续分析[/yellow]"
                    )
                    _append_ndjson_log(
                        run_id="event_analysis_fallback",
                        hypothesis_id="H32_data_collect_fallback_to_existing_csv",
                        location="cli/event_analysis_workflow.py:data_collect_fallback",
                        message="data_collect 失败，已回退使用历史 CSV",
                        data={
                            "task_id": task_id,
                            "collect_error": collect_error[:500],
                            "save_path_raw": save_path_raw,
                            "fallback_save_path": save_path,
                        },
                    )
                else:
                    err_msg = collect_error or f"data_collect 未返回有效 save_path: {save_path_raw}"
                    raise ValueError(f"{err_msg}；且无可复用历史数据")
            else:
                save_path = resolved_collect_path

            # #region debug_log_H26_data_collect_result_path
            _append_ndjson_log(
                run_id="event_analysis_data_collect",
                hypothesis_id="H26_data_collect_result_path",
                location="cli/event_analysis_workflow.py:after_data_collect_path_validate",
                message="data_collect 返回路径校验结果",
                data={
                    "task_id": task_id,
                    "save_path": save_path,
                    "save_path_exists": Path(save_path).exists(),
                    "save_path_parent": str(Path(save_path).parent),
                },
            )
            # #endregion debug_log_H26_data_collect_result_path

            if debug:
                console.print(f"[green]✅ 数据采集完成[/green] save_path={save_path}")
        elif debug:
            console.print(f"[green]✅ 已回退使用历史数据[/green] save_path={save_path}")
    else:
        # 跳过数据采集，使用已有数据
        if debug:
            console.print(f"[green]✅ 跳过数据采集，使用已有数据[/green] save_path={save_path}")

    # ============ 6) dataset_summary ============
    if debug:
        console.print("[bold]Step6: dataset_summary[/bold]")

    ds_json = _invoke_tool_to_json(dataset_summary, {"save_path": save_path})
    dataset_summary_path = str(ds_json.get("result_file_path") or "")
    if not dataset_summary_path or not Path(dataset_summary_path).exists():
        raise ValueError("dataset_summary 未返回有效 result_file_path")

    # ============ 7) 舆情分析（timeline + sentiment，并发执行） ============
    if debug:
        console.print("[bold]Step7/8: analysis_timeline + analysis_sentiment (并发)[/bold]")

    analysis_start = time.time()
    single_timing: Dict[str, float] = {"timeline_sec": 0.0, "sentiment_sec": 0.0}
    max_workers = max(1, min(_safe_int(os.environ.get("SONA_ANALYSIS_MAX_WORKERS", "2"), 2), 2))

    def _run_timeline() -> Dict[str, Any]:
        t0 = time.time()
        res = _invoke_tool_to_json(
            analysis_timeline,
            {"eventIntroduction": search_plan["eventIntroduction"], "dataFilePath": save_path},
        )
        single_timing["timeline_sec"] = round(time.time() - t0, 3)
        return res

    def _run_sentiment() -> Dict[str, Any]:
        t0 = time.time()
        res = _invoke_tool_to_json(
            analysis_sentiment,
            {"eventIntroduction": search_plan["eventIntroduction"], "dataFilePath": save_path},
        )
        single_timing["sentiment_sec"] = round(time.time() - t0, 3)
        return res

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        future_timeline = pool.submit(_run_timeline)
        future_sentiment = pool.submit(_run_sentiment)
        try:
            timeline_json = future_timeline.result()
        except Exception as e:
            timeline_json = {"error": f"analysis_timeline 并发执行异常: {str(e)}", "timeline": [], "summary": "", "result_file_path": ""}
            # #region debug_log_H23_timeline_future_exception
            _append_ndjson_log(
                run_id="event_analysis_parallel_analysis",
                hypothesis_id="H23_timeline_future_exception",
                location="cli/event_analysis_workflow.py:timeline_future_exception",
                message="analysis_timeline 并发 future 异常，进入 fallback",
                data={"error": str(e)},
            )
            # #endregion debug_log_H23_timeline_future_exception
        try:
            sentiment_json = future_sentiment.result()
        except Exception as e:
            sentiment_json = {
                "error": f"analysis_sentiment 并发执行异常: {str(e)}",
                "statistics": {},
                "positive_summary": [],
                "negative_summary": [],
                "result_file_path": "",
            }
            # #region debug_log_H24_sentiment_future_exception
            _append_ndjson_log(
                run_id="event_analysis_parallel_analysis",
                hypothesis_id="H24_sentiment_future_exception",
                location="cli/event_analysis_workflow.py:sentiment_future_exception",
                message="analysis_sentiment 并发 future 异常，进入 fallback",
                data={"error": str(e)},
            )
            # #endregion debug_log_H24_sentiment_future_exception
    # #region debug_log_H15_step_timing_parallel_analysis
    _append_ndjson_log(
        run_id="event_analysis_timing",
        hypothesis_id="H15_step_timing_parallel_analysis",
        location="cli/event_analysis_workflow.py:after_parallel_analysis",
        message="并发分析耗时",
        data={
            "elapsed_sec": round(time.time() - analysis_start, 3),
            "max_workers": max_workers,
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
        location="cli/event_analysis_workflow.py:after_analysis_path_resolve",
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

    # ============ 8) 初步解读（interpretation.json） ============
    if debug:
        console.print("[bold]Step9: generate_interpretation[/bold]")

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
            "event_type": "",
            "domain": "",
            "stage": "",
            "indicators_dimensions": ["量", "质", "人", "场", "效"],
            "theory_names": ["沉默螺旋规律", "议程设置规律", "生命周期规律"],
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
            location="cli/event_analysis_workflow.py:interpretation_fallback",
            message="generate_interpretation 失败，已使用 fallback interpretation 继续流程",
            data={"fallback_path": interpretation_path, "tool_error": interp_json.get("error", "")},
        )
        # #endregion debug_log_H30_interpretation_fallback

    # ============ 9) Graph RAG 增强（可选，默认关闭） ============
    if debug:
        console.print("[bold]Step10: graph_rag_query (enrich)[/bold]")

    graph_rag_enabled = _is_graph_rag_enabled()
    # #region debug_log_H11_graph_rag_switch
    _append_ndjson_log(
        run_id="event_analysis_graph_rag",
        hypothesis_id="H11_graph_rag_switch",
        location="cli/event_analysis_workflow.py:graph_rag_switch",
        message="Graph RAG 开关判定",
        data={"enabled": graph_rag_enabled},
    )
    # #endregion debug_log_H11_graph_rag_switch

    event_type = _normalize_opt_str(interpretation.get("event_type"))
    domain = _normalize_opt_str(interpretation.get("domain"))
    stage = _normalize_opt_str(interpretation.get("stage"))
    theory_names = interpretation.get("theory_names") or []
    indicators_dimensions = interpretation.get("indicators_dimensions") or []

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
                location="cli/event_analysis_workflow.py:after_graph_rag_parallel",
                message="Graph RAG 并发查询耗时",
                data={"elapsed_sec": round(time.time() - graph_rag_start, 3), "max_workers": max_workers},
            )
            # #endregion debug_log_H18_step_timing_graph_rag

            graph_rag_enrichment = {
                "status": "enabled_success",
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
                location="cli/event_analysis_workflow.py:graph_rag_exception",
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

    out_path = process_dir / "graph_rag_enrichment.json"
    with open(out_path, "w", encoding="utf-8", errors="replace") as f:
        json.dump(graph_rag_enrichment, f, ensure_ascii=False, indent=2)

    # ============ 10) 报告生成（report_html） ============
    if debug:
        console.print("[bold]Step11: report_html[/bold]")

    report_json = _invoke_tool_to_json(
        report_html,
        {
            "eventIntroduction": search_plan["eventIntroduction"],
            "analysisResultsDir": str(process_dir),
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
    session_manager.add_message(task_id, "assistant", final_msg)

    console.print()
    console.print(f"[green]✅ {final_msg}[/green]")
    return file_url or html_file_path
