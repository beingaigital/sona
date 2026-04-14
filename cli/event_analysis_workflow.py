"""舆情事件分析工作流（4.1）：以可交互 debug 形式落地搜索方案确认与结构化产物生成。"""

from __future__ import annotations

import json
import os
import sys
import webbrowser
import re
import hashlib
import html
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
import select
import time
from typing import Any, Dict, List, Optional, Tuple
from concurrent.futures import ThreadPoolExecutor, wait

from rich.console import Console
from rich.prompt import Prompt

from tools import (
    extract_search_terms,
    data_num,
    data_collect,
    analysis_timeline,
    analysis_sentiment,
    keyword_stats,
    region_stats,
    author_stats,
    volume_stats,
    dataset_summary,
    generate_interpretation,
    graph_rag_query,
    report_html,
    search_reference_insights,
    build_event_reference_links,
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
    默认开启，可通过 SONA_AUTO_REUSE_HISTORY_DATA=false 关闭。
    """
    v = os.environ.get("SONA_AUTO_REUSE_HISTORY_DATA", "true").strip().lower()
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
    v = str(os.environ.get(key, "true")).strip().lower()
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


def _fetch_weibo_aisearch_reference(topic: str, limit: int = 12) -> Dict[str, Any]:
    """
    尝试抓取微博智搜页面中的可见文本片段，作为外部参考（best effort）。
    """
    query = str(topic or "").strip() or "舆情事件"
    url = "https://s.weibo.com/aisearch?q=" + re.sub(r"\s+", "%20", query) + "&Refer=weibo_aisearch"

    try:
        import requests  # type: ignore
    except Exception as e:
        return {"topic": query, "url": url, "count": 0, "results": [], "error": f"requests 不可用: {str(e)}"}

    timeout_sec = max(5, min(_safe_int(os.environ.get("SONA_REFERENCE_FETCH_TIMEOUT_SEC", "12"), 12), 60))
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        )
    }

    try:
        resp = requests.get(url, headers=headers, timeout=timeout_sec)
        text = resp.text or ""
    except Exception as e:
        return {"topic": query, "url": url, "count": 0, "results": [], "error": f"抓取失败: {str(e)}"}

    # 抓取文本块（微博页面结构经常变化，采用宽松正则兜底）
    blocks = re.findall(r"<p[^>]*class=\"txt\"[^>]*>([\s\S]*?)</p>", text, flags=re.IGNORECASE)
    if not blocks:
        blocks = re.findall(r"<a[^>]*href=\"//weibo\\.com/[^\"#]+\"[^>]*>([\s\S]*?)</a>", text, flags=re.IGNORECASE)

    results: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for b in blocks:
        s = re.sub(r"<[^>]+>", " ", b)
        s = html.unescape(re.sub(r"\s+", " ", s)).strip()
        if len(s) < 12:
            continue
        key = s[:80]
        if key in seen:
            continue
        seen.add(key)
        results.append({"snippet": s[:220] + ("..." if len(s) > 220 else "")})
        if len(results) >= max(1, min(limit, 30)):
            break

    return {
        "topic": query,
        "url": url,
        "count": len(results),
        "results": results,
        "fetched_at": datetime.now().isoformat(sep=" "),
    }


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

    collab_mode = _event_collab_mode()
    interactive_session = _is_interactive_session()
    collab_enabled = collab_mode != "auto" and interactive_session
    collab_timeout_sec = _collab_timeout(24)

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
        },
    )

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
        auto_analysis_workers = max(2, min(keyword_count, 4))  # 未来可扩展更多分析节点
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

    # ============ 6.5) keyword_stats（可选，失败可跳过） ============
    if debug:
        console.print("[bold]Step6.5: keyword_stats (optional)[/bold]")

    try:
        keyword_json = _invoke_tool_to_json(
            keyword_stats,
            {
                "dataFilePath": save_path,
                "top_n": 20,
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

    # ============ 7) 舆情分析（timeline + sentiment，并发执行） ============
    if debug:
        console.print("[bold]Step7/8: analysis_timeline + analysis_sentiment (并发)[/bold]")

    analysis_start = time.time()
    single_timing: Dict[str, float] = {"timeline_sec": 0.0, "sentiment_sec": 0.0}
    timeline_json: Dict[str, Any] = {}
    sentiment_json: Dict[str, Any] = {}
    reused_flags = {"timeline": False, "sentiment": False}

    preferred_task_id = ""
    if isinstance(best_exp, dict):
        preferred_task_id = str(best_exp.get("task_id") or "").strip()

    # 先尝试复用历史分析，节省 token 与时延
    if _analysis_reuse_enabled("timeline"):
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

    if _analysis_reuse_enabled("sentiment"):
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
            {
                "eventIntroduction": search_plan["eventIntroduction"],
                "dataFilePath": save_path,
                # 默认全量重判帖文情感，避免直接复用原始情感列导致结果“固定不变”。
                "preferExistingSentimentColumn": False,
            },
        )
        single_timing["sentiment_sec"] = round(time.time() - t0, 3)
        return res

    pending_jobs: List[str] = []
    if not reused_flags["timeline"]:
        pending_jobs.append("timeline")
    if not reused_flags["sentiment"]:
        pending_jobs.append("sentiment")

    max_workers_env = max(1, min(_safe_int(os.environ.get("SONA_ANALYSIS_MAX_WORKERS", "4"), 4), 8))
    max_workers = max(1, min(max_workers_env, max(1, len(pending_jobs))))
    per_tool_timeout = max(
        30,
        min(_safe_int(os.environ.get("SONA_ANALYSIS_PER_TOOL_TIMEOUT_SEC", "120"), 120), 1800),
    )
    explicit_timeout_raw = str(os.environ.get("SONA_ANALYSIS_TIMEOUT_SEC", "")).strip()
    if explicit_timeout_raw:
        analysis_timeout_sec = max(30, min(_safe_int(explicit_timeout_raw, 240), 3600))
    else:
        analysis_timeout_sec = max(60, min(per_tool_timeout * max(1, len(pending_jobs)) + 60, 3600))

    if debug:
        console.print(
            f"[dim]分析阶段超时保护: total={analysis_timeout_sec}s, per_tool={per_tool_timeout}s, pending={len(pending_jobs)}[/dim]"
        )

    if pending_jobs:
        pool = ThreadPoolExecutor(max_workers=max_workers)
        try:
            futures: Dict[str, Any] = {}
            if "timeline" in pending_jobs:
                futures["timeline"] = pool.submit(_run_timeline)
            if "sentiment" in pending_jobs:
                futures["sentiment"] = pool.submit(_run_sentiment)

            done, not_done = wait(list(futures.values()), timeout=analysis_timeout_sec)

            if "timeline" in futures:
                ft = futures["timeline"]
                if ft in done:
                    try:
                        timeline_json = ft.result()
                    except Exception as e:
                        timeline_json = {
                            "error": f"analysis_timeline 并发执行异常: {str(e)}",
                            "timeline": [],
                            "summary": "",
                            "result_file_path": "",
                        }
                        _append_ndjson_log(
                            run_id="event_analysis_parallel_analysis",
                            hypothesis_id="H23_timeline_future_exception",
                            location="cli/event_analysis_workflow.py:timeline_future_exception",
                            message="analysis_timeline 并发 future 异常，进入 fallback",
                            data={"error": str(e)},
                        )
                else:
                    single_timing["timeline_sec"] = round(time.time() - analysis_start, 3)
                    timeline_json = {
                        "error": f"analysis_timeline 超时（>{analysis_timeout_sec}s），已跳过并继续流程",
                        "timeline": [],
                        "summary": "",
                        "result_file_path": "",
                    }
                    ft.cancel()
                    _append_ndjson_log(
                        run_id="event_analysis_parallel_analysis",
                        hypothesis_id="H33_analysis_future_timeout",
                        location="cli/event_analysis_workflow.py:timeline_future_timeout",
                        message="analysis_timeline 超时，已降级继续流程",
                        data={
                            "timeout_sec": analysis_timeout_sec,
                            "done_count": len(done),
                            "pending_count": len(not_done),
                        },
                    )

            if "sentiment" in futures:
                fs = futures["sentiment"]
                if fs in done:
                    try:
                        sentiment_json = fs.result()
                    except Exception as e:
                        sentiment_json = {
                            "error": f"analysis_sentiment 并发执行异常: {str(e)}",
                            "statistics": {},
                            "positive_summary": [],
                            "negative_summary": [],
                            "result_file_path": "",
                        }
                        _append_ndjson_log(
                            run_id="event_analysis_parallel_analysis",
                            hypothesis_id="H24_sentiment_future_exception",
                            location="cli/event_analysis_workflow.py:sentiment_future_exception",
                            message="analysis_sentiment 并发 future 异常，进入 fallback",
                            data={"error": str(e)},
                        )
                else:
                    single_timing["sentiment_sec"] = round(time.time() - analysis_start, 3)
                    sentiment_json = {
                        "error": f"analysis_sentiment 超时（>{analysis_timeout_sec}s），已跳过并继续流程",
                        "statistics": {},
                        "positive_summary": [],
                        "negative_summary": [],
                        "result_file_path": "",
                    }
                    fs.cancel()
                    _append_ndjson_log(
                        run_id="event_analysis_parallel_analysis",
                        hypothesis_id="H33_analysis_future_timeout",
                        location="cli/event_analysis_workflow.py:sentiment_future_timeout",
                        message="analysis_sentiment 超时，已降级继续流程",
                        data={
                            "timeout_sec": analysis_timeout_sec,
                            "done_count": len(done),
                            "pending_count": len(not_done),
                        },
                    )
        finally:
            # 不等待卡住的子线程，避免主流程在 Step7/8 无期限阻塞。
            pool.shutdown(wait=False, cancel_futures=True)
    else:
        if debug:
            console.print("[green]✅ 分析步骤已全部复用历史结果，无需重跑[/green]")
    # #region debug_log_H15_step_timing_parallel_analysis
    _append_ndjson_log(
        run_id="event_analysis_timing",
        hypothesis_id="H15_step_timing_parallel_analysis",
        location="cli/event_analysis_workflow.py:after_parallel_analysis",
        message="并发分析耗时",
        data={
            "elapsed_sec": round(time.time() - analysis_start, 3),
            "max_workers": max_workers,
            "timeout_sec": analysis_timeout_sec,
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
            weibo_ref_json = _fetch_weibo_aisearch_reference(weibo_topic, limit=12)
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
