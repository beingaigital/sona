"""舆情事件分析工作流（4.1）：以可交互 debug 形式落地搜索方案确认与结构化产物生成。"""

from __future__ import annotations

import json
import os
import sys
import webbrowser
import re
import hashlib
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


def _prompt_text_timeout(question: str, timeout_sec: int = 35, default_text: str = "") -> str:
    """
    询问自由文本输入，timeout 后返回默认值。
    """
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


def _collab_timeout(default_sec: int = 20) -> int:
    try:
        v = int(str(os.environ.get("SONA_EVENT_COLLAB_TIMEOUT_SEC", default_sec)).strip())
        return max(8, min(v, 180))
    except Exception:
        return default_sec


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
    with ThreadPoolExecutor(max_workers=1) as pool:
        fut = pool.submit(_invoke_tool_to_json, tool_obj, payload)
        try:
            return fut.result(timeout=sec)
        except FuturesTimeoutError:
            return {
                "error": f"{tool_name} 超时（>{sec}s）",
                "result_file_path": "",
            }
        except Exception as e:
            return {
                "error": f"{tool_name} 执行异常: {str(e)}",
                "result_file_path": "",
            }


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


def _normalize_search_words_for_collection(words: List[str], user_query: str) -> List[str]:
    """
    采集前关键词增强：
    - 保留原词
    - 去掉“舆情分析/事件分析/报告”等后缀，生成更可检索短语
    - 结合 query 提取候选词，避免单个超长词导致 data_num/data_collect 命中低
    """
    base = [str(w or "").strip() for w in words if str(w or "").strip()]
    q_words = _fallback_search_words_from_query(user_query, max_words=10)
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
    merged = base + extra + q_words
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
    - 否则使用较短窗口（默认 10 天），减少突发事件的数据污染
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

    # 默认 10 天
    return 10


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


def _write_text_file(path: Path, text: str) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8", errors="replace") as f:
            f.write(str(text or ""))
    except Exception:
        return


def _preview_yqzk_snapshot(snapshot: Dict[str, Any]) -> str:
    """
    将 yqzk 快照提炼成可读预览，用于控制台与日志。
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


def run_event_analysis_workflow(
    user_query: str,
    task_id: str,
    session_manager: SessionManager,
    *,
    debug: bool = False,
    default_threshold: int = 2000,
    existing_data_path: Optional[str] = None,
    skip_data_collect: bool = False,
    force_fresh_start: Optional[bool] = None,
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
    
    Returns:
        report_html 生成的 `file_url`（若为空则返回 html 文件路径）。
    """

    # 关键：让 tools/* 能读取 task_id 写入过程目录
    set_task_id(task_id)
    process_dir = ensure_task_dirs(task_id)
    # 进度条：区分“卡死”vs“在跑”（每个工具调用仍同步，但可见当前步骤与耗时）
    progress = _make_progress()
    progress_task_id: Optional[int] = None
    progress_total_steps = 7
    progress_started = False

    def _progress_start_if_needed(first_desc: str) -> None:
        nonlocal progress_task_id, progress_started
        if progress_started:
            return
        progress_started = True
        progress.start()
        progress_task_id = progress.add_task(first_desc, total=progress_total_steps)

    def _progress_step(desc: str) -> None:
        if not debug:
            return
        _progress_start_if_needed(desc)
        if progress_task_id is not None:
            progress.update(progress_task_id, description=desc)

    def _progress_advance() -> None:
        if progress_task_id is not None:
            progress.advance(progress_task_id, 1)
    # 生成可读别名目录（时间+事件+任务），便于在 sandbox 中人工识别
    try:
        ensure_task_readable_alias(task_id, user_query)
    except Exception:
        pass

    collab_mode = _event_collab_mode()
    interactive_session = _is_interactive_session()
    collab_enabled = collab_mode != "auto" and interactive_session
    fresh_start = _force_fresh_start_enabled() if force_fresh_start is None else bool(force_fresh_start)
    if fresh_start:
        existing_data_path = None
        skip_data_collect = False
    # 第九步需要更充分的人工输入时间：默认 45s，可用 SONA_EVENT_COLLAB_TIMEOUT_SEC 覆盖
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
        location="cli/event_analysis_workflow.py:startup",
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
        location="cli/event_analysis_workflow.py:experience_lookup",
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
                            location="cli/event_analysis_workflow.py:auto_reuse_history_data",
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
        _progress_step("Step1: extract_search_terms")
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
            "timeRange": _normalize_time_range_input(str(plan_json.get("timeRange", "") or "")),
        }
        _progress_advance()

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
            fallback_days = _infer_default_time_range_days(user_query)
            fallback_time_range = _build_default_time_range(fallback_days)
            search_plan["timeRange"] = fallback_time_range
            # #region debug_log_H29_time_range_fallback
            _append_ndjson_log(
                run_id="event_analysis_fallback",
                hypothesis_id="H29_time_range_fallback",
                location="cli/event_analysis_workflow.py:extract_time_range_fallback",
                message="extract_search_terms 返回非法 timeRange，已回退默认时间范围",
                data={"fallback_time_range": fallback_time_range, "fallback_days": fallback_days},
            )
            # #endregion debug_log_H29_time_range_fallback

        # ============ 2) 提出建议的搜索采集方案并等待 y/n（20s 无响应默认继续） ============
        # 该"采集方案"是针对 extract_search_terms 的扩展描述，最终仍映射到现有 data_num / data_collect 能力。
        # 其中 boolean 与关键词 ; 语义需要在真实运行中与 API 行为对齐（后续你看 debug log 我们再校准）。
        keyword_count = max(1, len(search_plan["searchWords"]))
        auto_data_num_workers = max(2, min(keyword_count, 8))
        auto_data_collect_workers = max(1, min(keyword_count, 8))
        auto_analysis_workers = max(2, min(keyword_count, 4))  # 未来可扩展更多分析节点
        suggested_collect_plan = {
            "keyword_combination_mode": "逐词检索并合并（当前实现）",
            "boolean_strategy": "OR（当前实现：各词分别检索再合并）",
            "keywords_join_with": ";",
            "platforms": ["微博"],
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
            "platforms": suggested_collect_plan.get("platforms") or ["微博"],
            "time_range": _normalize_time_range_input(str(suggested_collect_plan.get("time_range") or search_plan["timeRange"])) or search_plan["timeRange"],
            "return_count": max(200, min(_safe_int(suggested_collect_plan.get("return_count"), 2000), 10000)),
            "data_num_workers": max(1, min(_safe_int(suggested_collect_plan.get("data_num_workers"), 4), 8)),
            "data_collect_workers": max(1, min(_safe_int(suggested_collect_plan.get("data_collect_workers"), 3), 8)),
            "analysis_workers": max(1, min(_safe_int(suggested_collect_plan.get("analysis_workers"), 2), 8)),
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

    if collab_enabled:
        accept = _prompt_yes_no_timeout(
            "是否接受上述搜索采集方案？(y 执行 / n 修改后再确认)",
            timeout_sec=collab_timeout_sec,
            default_yes=True,
        )
    else:
        accept = True

    # #region debug_log_H2_timeout_or_user_choice
    _append_ndjson_log(
        run_id="event_analysis_pre_confirm",
        hypothesis_id="H2_timeout_or_user_choice",
        location="cli/event_analysis_workflow.py:confirm_choice",
        message="用户对采集方案的 y/n 决策结果记录",
        data={"accept": accept, "timeout_sec": collab_timeout_sec if collab_enabled else 0, "collab_enabled": collab_enabled},
    )
    # #endregion debug_log_H2_timeout_or_user_choice

    # 若用户选择 n，则允许编辑"平台、返回条数、时间范围、布尔策略"等（仍先通过 y 再执行）
    if collab_enabled and not accept:
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
            "修改 timeRange（形如 YYYY-MM-DD;YYYY-MM-DD；不填则默认）",
            default=_time_range_to_user_date_range(str(suggested_collect_plan["time_range"])),
        ).strip() or _time_range_to_user_date_range(str(suggested_collect_plan["time_range"]))
        normalized_time_range = _normalize_time_range_input(time_range_in)
        if not _validate_time_range(normalized_time_range):
            console.print("[red]修改后的 timeRange 格式不合法，已忽略本次 timeRange 修改[/red]")
        else:
            suggested_collect_plan["time_range"] = normalized_time_range

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
            timeout_sec=collab_timeout_sec,
            default_yes=True,
        )

        # #region debug_log_H2_timeout_or_user_choice_after_edit
        _append_ndjson_log(
            run_id="event_analysis_pre_confirm",
            hypothesis_id="H2_timeout_or_user_choice_after_edit",
            location="cli/event_analysis_workflow.py:confirm_choice_after_edit",
            message="用户对编辑后采集方案的 y/n 决策结果记录",
            data={"accept": accept, "timeout_sec": collab_timeout_sec},
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
        _progress_step("Step3: data_num")

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
                8,
            ),
        )
        os.environ["SONA_DATA_NUM_MAX_WORKERS"] = str(data_num_workers)
        os.environ["SONA_DATA_COLLECT_MAX_WORKERS"] = str(data_collect_workers)
        os.environ["SONA_ANALYSIS_MAX_WORKERS"] = str(analysis_workers)

        # 根据布尔策略组装关键词
        boolean_strategy = str(suggested_collect_plan.get("boolean_strategy") or "")
        boolean_mode = "AND" if boolean_strategy.upper().startswith("AND") else "OR"
        search_words_for_collect = _normalize_search_words_for_collection(search_plan["searchWords"], user_query)
        if boolean_mode == "AND":
            tool_search_words: List[str] = [";".join(search_words_for_collect)]
        else:
            tool_search_words = search_words_for_collect

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
        search_matrix = _sanitize_search_matrix(search_matrix_raw, return_count)
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
            _progress_advance()
            _progress_step("Step4: data_collect")

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

            collected_rows = _count_csv_rows(resolved_collect_path) if resolved_collect_path and Path(resolved_collect_path).exists() else 0
            if collect_error or not resolved_collect_path or not Path(resolved_collect_path).exists() or collected_rows <= 0:
                # 先做“当前任务内自动重试”，避免在关闭历史回退时直接失败
                retry_collect_path = ""
                retry_collect_rows = 0
                retry_collect_error = ""
                try:
                    retry_days = max(14, _infer_default_time_range_days(user_query) + 7)
                    retry_time_range = _build_default_time_range(retry_days)
                    retry_threshold = max(_safe_int(suggested_collect_plan.get("return_count"), 2000), 1200)
                    retry_words = _normalize_search_words_for_collection(
                        search_plan.get("searchWords", []),
                        user_query,
                    )
                    retry_matrix = _build_uniform_search_matrix(retry_words, retry_threshold)
                    if retry_matrix:
                        retry_json: Dict[str, Any] = {}
                        retry_elapsed = 0.0
                        # 两档重试：先用扩窗，再用更大阈值
                        for multiplier in (1, 2):
                            matrix_try = _build_uniform_search_matrix(retry_words, retry_threshold * multiplier)
                            retry_json, retry_elapsed = _invoke_tool_with_timing(
                                data_collect,
                                {
                                    "searchMatrix": json.dumps(matrix_try, ensure_ascii=False),
                                    "timeRange": retry_time_range,
                                    "platform": platform,
                                },
                            )
                            retry_collect_error = str(retry_json.get("error") or "").strip()
                            retry_save_raw = str(retry_json.get("save_path") or "")
                            retry_collect_path = _resolve_to_csv_path(retry_save_raw) if retry_save_raw else ""
                            retry_collect_rows = _count_csv_rows(retry_collect_path) if retry_collect_path and Path(retry_collect_path).exists() else 0
                            if retry_collect_path and retry_collect_rows > 0:
                                break
                        _append_ndjson_log(
                            run_id="event_analysis_data_collect",
                            hypothesis_id="H45_data_collect_retry_without_history_fallback",
                            location="cli/event_analysis_workflow.py:data_collect_retry_no_history",
                            message="data_collect 首次失败后已执行本任务自动重试采集",
                            data={
                                "retry_elapsed_sec": retry_elapsed,
                                "retry_time_range": retry_time_range,
                                "retry_threshold": retry_threshold,
                                "retry_words": retry_words[:8],
                                "retry_rows": retry_collect_rows,
                                "retry_error": retry_collect_error[:300],
                            },
                        )
                except Exception as e:
                    _append_ndjson_log(
                        run_id="event_analysis_data_collect",
                        hypothesis_id="H45_data_collect_retry_without_history_fallback",
                        location="cli/event_analysis_workflow.py:data_collect_retry_no_history_exception",
                        message="data_collect 自动重试异常",
                        data={"error": str(e)},
                    )

                if retry_collect_path and retry_collect_rows > 0:
                    save_path = retry_collect_path
                    collect_error = ""
                    resolved_collect_path = retry_collect_path
                    collected_rows = retry_collect_rows
                    if debug:
                        console.print(
                            f"[green]♻️ data_collect 自动重试成功[/green] rows={retry_collect_rows} save_path={save_path}"
                        )
                else:
                    collect_error = collect_error or retry_collect_error

            if collect_error or not resolved_collect_path or collected_rows <= 0:
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
            _progress_advance()

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
            location="cli/event_analysis_workflow.py:low_sample_warning",
            message="样本量低于建议阈值，已提示用户",
            data={"sample_rows": sample_rows, "min_samples": min_samples, "save_path": save_path},
        )
        # 自动尝试扩窗重采（仅在本轮为新采集场景）
        if (not skip_data_collect) and (not existing_data_path):
            try:
                hard_min_samples = max(20, min(_safe_int(os.environ.get("SONA_MIN_SAMPLE_HARD", "70"), 70), 2000))
                max_retry_rounds = max(1, min(_safe_int(os.environ.get("SONA_LOW_SAMPLE_RETRY_ROUNDS", "2"), 2), 4))
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
                    retry_matrix = _build_uniform_search_matrix(search_plan.get("searchWords", []), retry_threshold)
                    retry_collect = _invoke_tool_to_json(
                        data_collect,
                        {
                            "searchMatrix": json.dumps(retry_matrix, ensure_ascii=False),
                            "timeRange": retry_time_range,
                            "platform": "微博",
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
                        location="cli/event_analysis_workflow.py:low_sample_retry_collect",
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
                    location="cli/event_analysis_workflow.py:low_sample_retry_collect_exception",
                    message="低样本扩窗重采失败，已跳过",
                    data={"error": str(e)},
                )

    # 低样本硬中止：避免低质量报告落盘
    min_samples_hard_fail = max(20, min(_safe_int(os.environ.get("SONA_MIN_SAMPLE_HARD_FAIL", "200"), 200), 5000))
    if sample_rows < min_samples_hard_fail:
        fail_msg = (
            f"当前样本量仅 {sample_rows} 条（阈值 {min_samples_hard_fail}），"
            "请先补采样本。"
        )
        console.print(f"[red]⛔ {fail_msg}[/red]")
        _append_ndjson_log(
            run_id="event_analysis_data_quality",
            hypothesis_id="H46_low_sample_hard_fail",
            location="cli/event_analysis_workflow.py:low_sample_hard_fail",
            message="样本量低于硬阈值，已中止报告生成",
            data={
                "sample_rows": sample_rows,
                "min_samples_hard_fail": min_samples_hard_fail,
                "save_path": save_path,
            },
        )
        raise ValueError(fail_msg)

    # ============ 6) dataset_summary ============
    if debug:
        console.print("[bold]Step6: dataset_summary[/bold]")
    _progress_step("Step5: dataset_summary")

    ds_json = _invoke_tool_to_json(dataset_summary, {"save_path": save_path})
    dataset_summary_path = str(ds_json.get("result_file_path") or "")
    if not dataset_summary_path or not Path(dataset_summary_path).exists():
        raise ValueError("dataset_summary 未返回有效 result_file_path")

    # ============ 6.5) keyword_stats（可选，失败可跳过） ============
    if debug:
        console.print("[bold]Step6.5: keyword_stats (optional)[/bold]")

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
    except Exception as e:
        if debug:
            console.print("[yellow]⚠️ keyword_stats 执行失败，已跳过，不影响后续流程[/yellow]")
        _append_ndjson_log(
            run_id="event_analysis_keyword_stats",
            hypothesis_id="H34_keyword_stats_optional_skip_on_error",
            location="cli/event_analysis_workflow.py:keyword_stats_optional",
            message="keyword_stats 执行失败，已按可选步骤跳过",
            data={"error": str(e)},
        )

    # ============ 6.6) region_stats（可选，失败可跳过） ============
    if debug:
        console.print("[bold]Step6.6: region_stats (optional)[/bold]")

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
            location="cli/event_analysis_workflow.py:region_stats_optional",
            message="region_stats 执行失败，已按可选步骤跳过",
            data={"error": str(e)},
        )

    # ============ 6.7) author_stats（可选，失败可跳过） ============
    if debug:
        console.print("[bold]Step6.7: author_stats (optional)[/bold]")

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
            location="cli/event_analysis_workflow.py:author_stats_optional",
            message="author_stats 执行失败，已按可选步骤跳过",
            data={"error": str(e)},
        )

    # ============ 7) 舆情分析（timeline + sentiment，顺序执行） ============
    if debug:
        console.print("[bold]Step7: analysis_timeline[/bold]")
        console.print("[bold]Step8: analysis_sentiment[/bold]")
    _progress_advance()
    _progress_step("Step6: timeline + sentiment")

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

    if not reused_flags["timeline"]:
        t0 = time.time()
        timeline_json = _invoke_tool_to_json_with_timeout(
            analysis_timeline,
            {"eventIntroduction": search_plan["eventIntroduction"], "dataFilePath": save_path},
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
    if not reused_flags["sentiment"]:
        t0 = time.time()
        force_sentiment_rerun = _should_force_sentiment_rerun(user_query)
        sentiment_json = _invoke_tool_to_json_with_timeout(
            analysis_sentiment,
            {
                "eventIntroduction": search_plan["eventIntroduction"],
                "dataFilePath": save_path,
                # 默认优先复用抓取数据情感列，仅在用户明确要求时全量重判。
                "preferExistingSentimentColumn": (not force_sentiment_rerun),
            },
            timeout_sec=sentiment_timeout_sec,
            tool_name="analysis_sentiment",
        )
        if str(sentiment_json.get("error", "") or "").strip():
            sentiment_json = {
                "error": str(sentiment_json.get("error", "") or "analysis_sentiment 执行失败"),
                "statistics": {},
                "positive_summary": [],
                "negative_summary": [],
                "result_file_path": "",
            }
        single_timing["sentiment_sec"] = round(time.time() - t0, 3)

        if str(sentiment_json.get("error", "") or "").strip() and save_path:
            fallback_json = _fallback_sentiment_from_csv(save_path)
            if not str(fallback_json.get("error", "") or "").strip():
                sentiment_json = fallback_json
                _append_ndjson_log(
                    run_id="event_analysis_sentiment",
                    hypothesis_id="H36_sentiment_fallback_from_existing_column",
                    location="cli/event_analysis_workflow.py:sentiment_fallback_existing_column",
                    message="analysis_sentiment 失败，已用 CSV 情感列生成兜底统计",
                    data={"data_file_path": save_path},
                )
    else:
        if debug:
            console.print("[green]♻️ sentiment 已复用历史结果[/green]")

    # #region debug_log_H15_step_timing_parallel_analysis
    _append_ndjson_log(
        run_id="event_analysis_timing",
        hypothesis_id="H15_step_timing_parallel_analysis",
        location="cli/event_analysis_workflow.py:after_parallel_analysis",
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

    # ============ 7.4) 舆情智库知识快照（报告/研判/复盘统一参考） ============
    try:
        yqzk_query = f"方法论 历史对比 事件复盘 {search_plan.get('eventIntroduction', user_query)}".strip()
        yqzk_primary = _invoke_tool_to_json(
            load_sentiment_knowledge,
            {"keyword": yqzk_query},
        )
        yqzk_ref = _invoke_tool_to_json(
            search_reference_insights,
            {"query": yqzk_query, "limit": 10},
        )
        yqzk_snapshot = {
            "query": yqzk_query,
            "knowledge": yqzk_primary,
            "references": yqzk_ref,
            "created_at": datetime.now().isoformat(sep=" "),
        }
        yqzk_snapshot_path = process_dir / "yqzk_knowledge_snapshot.json"
        with open(yqzk_snapshot_path, "w", encoding="utf-8", errors="replace") as f:
            json.dump(yqzk_snapshot, f, ensure_ascii=False, indent=2)
        preview = _preview_yqzk_snapshot(yqzk_snapshot)
        if preview:
            _write_text_file(process_dir / "yqzk_recall_preview.txt", preview)
            if debug:
                console.print("[dim]yqzk 召回预览（用于核验报告引用）[/dim]")
                console.print(f"[dim]{preview}[/dim]")
    except Exception as e:
        _append_ndjson_log(
            run_id="event_analysis_yqzk",
            hypothesis_id="H43_yqzk_snapshot_optional",
            location="cli/event_analysis_workflow.py:yqzk_snapshot",
            message="yqzk 快照构建失败，已跳过",
            data={"error": str(e)},
        )

    # ============ 8) 初步解读（interpretation.json） ============
    # ============ 7.5) 声量分析（可选，失败可跳过） ============
    if debug:
        console.print("[bold]Step7.5: volume_stats (optional)[/bold]")

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
            location="cli/event_analysis_workflow.py:volume_stats_optional",
            message="volume_stats 执行失败，已按可选步骤跳过",
            data={"error": str(e)},
        )

    # ============ 8.5) 用户画像（可选，失败可跳过） ============
    if debug:
        console.print("[bold]Step8.5: user_portrait (optional)[/bold]")
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
            location="cli/event_analysis_workflow.py:user_portrait_optional",
            message="user_portrait 执行失败，已按可选步骤跳过",
            data={"error": str(e)},
        )

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
            location="cli/event_analysis_workflow.py:interpretation_fallback",
            message="generate_interpretation 失败，已使用 fallback interpretation 继续流程",
            data={"fallback_path": interpretation_path, "tool_error": interp_json.get("error", "")},
        )
        # #endregion debug_log_H30_interpretation_fallback

    # ============ 9.2) 用户协同研判输入（可选） ============
    user_judgement_text = str(os.environ.get("SONA_EVENT_USER_JUDGEMENT", "") or "").strip()
    if collab_enabled and not user_judgement_text:
        user_judgement_text = _prompt_text_timeout(
            "可选：请输入你对该事件的研判重点（将写入报告参考）",
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
        location="cli/event_analysis_workflow.py:user_judgement_input",
        message="用户协同研判输入已处理",
        data={
            "has_input": bool(user_judgement_text),
            "focus_keywords": user_focus_keywords[:6],
            "path": str(user_judgement_path),
        },
    )

    # ============ 9.5) 事件参考资料检索（舆情智库） ============
    if debug:
        console.print("[bold]Step9.5: reference_insights (optional)[/bold]")

    try:
        ref_query = f"{user_query} {search_plan.get('eventIntroduction', '')}".strip()
        ref_json = _invoke_tool_to_json(
            search_reference_insights,
            {"query": ref_query, "limit": 8},
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
            weibo_topic = str(search_plan.get("eventIntroduction") or user_query).strip() or user_query
            weibo_ref_json = _invoke_tool_to_json(
                weibo_aisearch,
                {"query": weibo_topic, "limit": 12},
            )
            with open(weibo_ref_path, "w", encoding="utf-8", errors="replace") as f:
                json.dump(weibo_ref_json, f, ensure_ascii=False, indent=2)

        expert_note = str(os.environ.get("SONA_EVENT_EXPERT_NOTE", "") or "").strip()
        if collab_enabled and not expert_note:
            expert_note = _prompt_text_timeout(
                "可选：补充你的专家研判（将作为参考材料进入报告）",
                timeout_sec=max(collab_timeout_sec, 25),
                default_text="",
            )
        expert_note_path = process_dir / "user_expert_notes.json"
        expert_note_payload = {
            "has_input": bool(expert_note),
            "source": "env" if str(os.environ.get("SONA_EVENT_EXPERT_NOTE", "") or "").strip() else ("interactive" if expert_note else "none"),
            "expert_note": expert_note,
            "created_at": datetime.now().isoformat(sep=" "),
        }
        with open(expert_note_path, "w", encoding="utf-8", errors="replace") as f:
            json.dump(expert_note_payload, f, ensure_ascii=False, indent=2)

        _append_ndjson_log(
            run_id="event_analysis_reference",
            hypothesis_id="H36_reference_insights_collected",
            location="cli/event_analysis_workflow.py:reference_insights",
            message="舆情智库参考检索已完成并写入过程文件",
            data={
                "reference_insights_path": str(ref_path),
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
            location="cli/event_analysis_workflow.py:reference_insights_exception",
            message="舆情智库参考检索失败，已跳过",
            data={"error": str(e)},
        )

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
        location="cli/event_analysis_workflow.py:graph_rag_input_prepare",
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
                location="cli/event_analysis_workflow.py:after_graph_rag_parallel",
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
            location="cli/event_analysis_workflow.py:graph_rag_user_decision",
            message="Graph RAG 召回采纳策略已落地",
            data=graph_rag_enrichment.get("user_decision") if isinstance(graph_rag_enrichment.get("user_decision"), dict) else {},
        )

    out_path = process_dir / "graph_rag_enrichment.json"
    with open(out_path, "w", encoding="utf-8", errors="replace") as f:
        json.dump(graph_rag_enrichment, f, ensure_ascii=False, indent=2)

    # ============ 10) 报告生成（report_html） ============
    if debug:
        console.print("[bold]Step11: report_html[/bold]")
    _progress_advance()
    _progress_step("Step7: report_html")

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
    try:
        if progress_started:
            _progress_advance()
            progress.stop()
    except Exception:
        pass
    return file_url or html_file_path


def run_full_report_mode(
    *,
    user_query: str,
    task_id: str,
    session_manager: SessionManager,
    debug: bool = True,
    existing_data_path: Optional[str] = None,
    skip_data_collect: bool = False,
    force_fresh_start: Optional[bool] = None,
) -> str:
    """
    可复用节点：完整报告模式（供 Agent full_report 模式复用）。
    """
    return run_event_analysis_workflow(
        user_query=user_query,
        task_id=task_id,
        session_manager=session_manager,
        debug=debug,
        existing_data_path=existing_data_path,
        skip_data_collect=skip_data_collect,
        force_fresh_start=force_fresh_start,
    )


def run_brief_mode(user_query: str) -> Dict[str, Any]:
    """
    可复用节点：轻量概述模式（仅提取事件简介/关键词/时间范围）。
    """
    raw = extract_search_terms.invoke({"query": user_query})
    raw_text = raw if isinstance(raw, str) else str(raw)
    try:
        parsed = json.loads(raw_text)
        if isinstance(parsed, dict):
            # 简要模式也补一层 yqzk 参考，提升历史对比与方法论一致性
            try:
                ref = _invoke_tool_to_json(search_reference_insights, {"query": user_query, "limit": 3})
                parsed["yqzk_reference"] = ref
            except Exception:
                pass
            return parsed
    except Exception:
        pass
    return {
        "eventIntroduction": "",
        "searchWords": [],
        "timeRange": "",
        "raw": raw_text,
    }
