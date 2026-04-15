"""情感倾向分析工具：对内容列做与关键词一致的清洗后，使用 qwen-plus 打 0-10 分并汇总。"""

from __future__ import annotations

import csv
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import time

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.tools import tool

from model.factory import get_sentiment_model
from tools.keyword_stats import CONTENT_COLUMN_KEYWORDS, _identify_content_columns
from utils.content_text import clean_text_like_keyword_stats
from utils.path import ensure_task_dirs, get_task_process_dir
from utils.prompt_loader import get_analysis_sentiment_prompt
from utils.task_context import get_task_id

_BATCH_SIZE: int = 12
_MAX_CHARS_PER_TEXT: int = 2800

# 并发配置（通过环境变量可调）
import os  # noqa: E402
import random  # noqa: E402
import threading  # noqa: E402
from concurrent.futures import ThreadPoolExecutor, as_completed  # noqa: E402

def _env_int(name: str, default: int, low: int, high: int) -> int:
    raw = str(os.environ.get(name, str(default))).strip()
    try:
        val = int(raw)
    except Exception:
        val = default
    return max(low, min(high, val))

_SENTIMENT_BATCH_PARALLEL_WORKERS: int = _env_int("SONA_SENTIMENT_BATCH_PARALLEL_WORKERS", 2, 1, 8)
_SENTIMENT_BATCH_JITTER_MS: int = _env_int("SONA_SENTIMENT_BATCH_JITTER_MS", 100, 0, 1000)
_SENTIMENT_DYNAMIC_BATCH_SIZE: int = _env_int("SONA_SENTIMENT_BATCH_SIZE", 40, 4, 64)
_SENTIMENT_BATCH_RETRIES: int = _env_int("SONA_SENTIMENT_BATCH_RETRIES", 1, 0, 3)

_SCORE_SYSTEM_PROMPT = """你是舆情情感分析助手。结合「事件背景」，对每条文本给出整数情感分 score，范围 0～10：
- 0 为最负面，10 为最正面
- 0～3：负面；4～6：中立；7～10：正面

必须只输出一个 JSON 对象，不要 markdown 代码块，格式严格为：
{"items":[{"row":<整数行号>,"score":<0到10的整数>}, ...]}

要求：
- items 长度与输入条数一致，每个 row 与输入中的 row 一致
- score 必须为整数，不得输出小数或文字说明
"""


class _RequestMetrics:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.started = 0
        self.succeeded = 0
        self.failed = 0
        self.active = 0
        self.max_active = 0

    def on_start(self) -> None:
        with self._lock:
            self.started += 1
            self.active += 1
            if self.active > self.max_active:
                self.max_active = self.active

    def on_end(self, success: bool) -> None:
        with self._lock:
            self.active = max(0, self.active - 1)
            if success:
                self.succeeded += 1
            else:
                self.failed += 1

    def summary(self, *, elapsed_sec: float, rows_scored: int) -> Dict[str, Any]:
        elapsed = max(0.001, elapsed_sec)
        qps = rows_scored / elapsed
        qpm = qps * 60.0
        rpm = self.started / elapsed * 60.0
        return {
            "elapsed_sec": round(elapsed_sec, 3),
            "rows_scored": rows_scored,
            "requests_started": self.started,
            "requests_succeeded": self.succeeded,
            "requests_failed": self.failed,
            "qps": round(qps, 3),
            "qpm": round(qpm, 2),
            "rpm": round(rpm, 2),
            "max_concurrent_connections": self.max_active,
        }





def _read_csv_data(file_path: str) -> List[Dict[str, Any]]:
    file = Path(file_path)
    if not file.exists():
        raise FileNotFoundError(f"数据文件不存在: {file_path}")
    data: List[Dict[str, Any]] = []
    # 兼容常见中文编码，避免“乱码”导致表头识别失败
    encodings_to_try: List[str] = ["utf-8-sig", "utf-8", "gb18030", "gbk"]
    last_error: Optional[Exception] = None
    for enc in encodings_to_try:
        try:
            with open(file, "r", encoding=enc, errors="strict") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    data.append(row)
            break
        except Exception as e:
            data = []
            last_error = e
            continue
    if not data and last_error is not None:
        # 最后一次失败也读不到，回退到宽容模式读取，避免完全失败
        with open(file, "r", encoding="utf-8-sig", errors="replace") as f:
            reader = csv.DictReader(f)
            for row in reader:
                data.append(row)
    return data


def _identify_sentiment_column(data: List[Dict[str, Any]]) -> Optional[str]:
    if not data:
        return None
    sentiment_candidates = (
        "情感",
        "情感倾向",
        "情感分析",
        "情感分类",
        "情感标签",
        "sentiment",
        "emotion",
    )
    for col in data[0].keys():
        col_lower = str(col).lower()
        if any(key in col_lower for key in ("sentiment", "emotion")):
            return col
        if any(key in str(col) for key in ("情感", "倾向")):
            return col
        if any(key in str(col) for key in sentiment_candidates):
            return col
    return None


def _normalize_sentiment_label(value: Any) -> Optional[str]:
    raw = str(value or "").strip().lower()
    if not raw:
        return None
    mapping = {
        "正面": "正面",
        "积极": "正面",
        "positive": "正面",
        "pos": "正面",
        "1": "正面",
        "负面": "负面",
        "消极": "负面",
        "negative": "负面",
        "neg": "负面",
        "-1": "负面",
        "中性": "中立",
        "中立": "中立",
        "neutral": "中立",
        "0": "中立",
    }
    if raw in mapping:
        return mapping[raw]
    if raw in {"p", "n"}:
        return "正面" if raw == "p" else "负面"
    return None


def _label_to_score(label: str) -> int:
    if label == "正面":
        return 8
    if label == "负面":
        return 2
    return 5


def _should_use_existing_sentiment(
    data: List[Dict[str, Any]],
    sentiment_col: Optional[str],
) -> bool:
    if not sentiment_col or not data:
        return False
    non_empty = 0
    recognizable = 0
    for row in data:
        raw = row.get(sentiment_col, "")
        if str(raw or "").strip():
            non_empty += 1
            if _normalize_sentiment_label(raw) is not None:
                recognizable += 1
    if non_empty == 0:
        return False
    ratio = recognizable / non_empty
    min_non_empty = min(len(data), 20)
    return non_empty >= min_non_empty and ratio >= 0.6


def _to_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    s = str(value).strip().lower()
    if not s:
        return default
    if s in {"1", "true", "yes", "y", "on"}:
        return True
    if s in {"0", "false", "no", "n", "off"}:
        return False
    return default


def _row_raw_content_text(row: Dict[str, Any], content_columns: List[str]) -> str:
    parts: List[str] = []
    for col in content_columns:
        val = row.get(col, "")
        if val is None:
            continue
        text = str(val).strip()
        if text:
            parts.append(text)
    return " ".join(parts)


def _row_cleaned_content(row: Dict[str, Any], content_columns: List[str]) -> str:
    return clean_text_like_keyword_stats(_row_raw_content_text(row, content_columns))


def _clamp_score(value: Any) -> int:
    try:
        if isinstance(value, bool):
            return 5
        if isinstance(value, str):
            value = value.strip()
            m = re.search(r"-?\d+", value)
            if m:
                value = int(m.group())
            else:
                return 5
        n = int(round(float(value)))
        return max(0, min(10, n))
    except (TypeError, ValueError):
        return 5


def _label_from_score(score: int) -> str:
    if score <= 3:
        return "负面"
    if score <= 6:
        return "中立"
    return "正面"


def _parse_score_json(text: str) -> Dict[str, Any]:
    json_match = re.search(r"\{[\s\S]*\}", text)
    raw = json_match.group() if json_match else text
    return json.loads(raw)


def _score_batch(
    model: Any,
    *,
    event_introduction: str,
    batch: List[Tuple[int, str]],
    metrics: Optional[_RequestMetrics] = None,
) -> Dict[int, int]:
    payload = [{"row": idx, "text": txt[:_MAX_CHARS_PER_TEXT]} for idx, txt in batch]
    human = (
        f"事件背景：\n{event_introduction}\n\n"
        f"请为下列每条文本打分（JSON 中的 row 必须与下列 row 一致）：\n"
        f"{json.dumps(payload, ensure_ascii=False)}"
    )
    messages = [
        SystemMessage(content=_SCORE_SYSTEM_PROMPT),
        HumanMessage(content=human),
    ]
    last_error: Optional[Exception] = None
    response: Any = None
    for attempt in range(_SENTIMENT_BATCH_RETRIES + 1):
        if metrics:
            metrics.on_start()
        try:
            response = model.invoke(messages)
            if metrics:
                metrics.on_end(True)
            last_error = None
            break
        except Exception as e:
            last_error = e
            if metrics:
                metrics.on_end(False)
            if attempt < _SENTIMENT_BATCH_RETRIES:
                # 指数退避，缓解瞬时限流
                threading.Event().wait(min(1.5, 0.25 * (2**attempt)))
            continue
    if response is None and last_error is not None:
        return {idx: 5 for idx, _ in batch}
    result_text = response.content if hasattr(response, "content") else str(response)
    out: Dict[int, int] = {}
    try:
        obj = _parse_score_json(result_text)
        items = obj.get("items")
        if not isinstance(items, list):
            raise ValueError("missing items")
        for it in items:
            if not isinstance(it, dict):
                continue
            r = it.get("row")
            s = it.get("score")
            if r is None:
                continue
            try:
                ri = int(r)
            except (TypeError, ValueError):
                continue
            out[ri] = _clamp_score(s)
    except Exception:
        pass
    for idx, _ in batch:
        if idx not in out:
            out[idx] = 5
    return out


def _score_batch_worker(
    *, event_introduction: str, batch: List[Tuple[int, str]], metrics: Optional[_RequestMetrics] = None
) -> Dict[int, int]:
    # 轻微抖动，降低并发瞬时触发限流
    if _SENTIMENT_BATCH_JITTER_MS > 0:
        try:
            time_to_sleep = random.random() * (_SENTIMENT_BATCH_JITTER_MS / 1000.0)
            if time_to_sleep > 0:
                threading.Event().wait(time_to_sleep)
        except Exception:
            pass
    # 每个线程内独立获取模型实例（model.factory 内部已做线程局部缓存）
    model = get_sentiment_model()
    try:
        return _score_batch(model, event_introduction=event_introduction, batch=batch, metrics=metrics)
    except Exception:
        # 兜底：该批次统一给 5 分，保证流程不中断
        return {idx: 5 for idx, _ in batch}


def _build_statistics(
    total: int,
    scores_by_row: Dict[int, Optional[int]],
) -> Dict[str, Any]:
    positive_count = 0
    negative_count = 0
    neutral_count = 0
    analyzed_scores: List[int] = []

    for i in range(total):
        s = scores_by_row.get(i)
        if s is None:
            neutral_count += 1
            continue
        analyzed_scores.append(s)
        lab = _label_from_score(s)
        if lab == "正面":
            positive_count += 1
        elif lab == "负面":
            negative_count += 1
        else:
            neutral_count += 1

    avg_score: Optional[float] = None
    if analyzed_scores:
        avg_score = round(sum(analyzed_scores) / len(analyzed_scores), 4)

    return {
        "total": total,
        "positive_count": positive_count,
        "negative_count": negative_count,
        "neutral_count": neutral_count,
        "positive_ratio": round(positive_count / total, 4) if total else 0.0,
        "negative_ratio": round(negative_count / total, 4) if total else 0.0,
        "neutral_ratio": round(neutral_count / total, 4) if total else 0.0,
        "avg_score_analyzed": avg_score,
        "score_scale": "0-10（0 最低，10 最高；0-3 负面，4-6 中立，7-10 正面）",
    }


def _extract_contents_by_label(
    row_meta: List[Dict[str, Any]],
    label: str,
    limit: int = 10,
) -> List[str]:
    texts = [m["cleaned"] for m in row_meta if m.get("label") == label and m.get("cleaned")]
    texts.sort(key=len, reverse=True)
    return texts[:limit]


def _fallback_summary_from_contents(contents: List[str], max_items: int = 3) -> List[str]:
    out: List[str] = []
    seen = set()
    for c in contents:
        s = re.sub(r"\s+", " ", str(c or "")).strip()
        if not s:
            continue
        key = s[:80]
        if key in seen:
            continue
        seen.add(key)
        out.append((s[:48] + "...") if len(s) > 48 else s)
        if len(out) >= max_items:
            break
    return out


def _generate_result_filename(retryContext: Optional[str] = None) -> str:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    base_name = f"sentiment_analysis_{timestamp}"
    if retryContext:
        task_id = get_task_id()
        if task_id:
            process_dir = get_task_process_dir(task_id)
            if process_dir.exists():
                existing_files = list(process_dir.glob("sentiment_analysis_*.json"))
                if existing_files:
                    suffix_nums: List[int] = []
                    for file in existing_files:
                        match = re.search(r"sentiment_analysis_\d{8}_\d{6}_(\d+)\.json", file.name)
                        if match:
                            suffix_nums.append(int(match.group(1)))
                    if not suffix_nums:
                        return f"{base_name}_1.json"
                    return f"{base_name}_{max(suffix_nums) + 1}.json"
                return f"{base_name}_1.json"
    return f"{base_name}.json"


@tool
def analysis_sentiment(
    eventIntroduction: str,
    dataFilePath: str,
    retryContext: Optional[str] = None,
    preferExistingSentimentColumn: Optional[bool] = None,
    contentColumns: Optional[List[str]] = None,
) -> str:
    """
    描述：分析情感倾向。自动识别与关键词工具一致的内容列，先做相同规则的文本清洗，再使用通义 qwen-plus
    对每条文本打 0～10 分（0-3 负面，4-6 中立，7-10 正面），统计占比并总结主要观点。
    说明：默认优先复用数据内已有情感列；如需全量重判，请将 preferExistingSentimentColumn 设为 false。
    """
    import json as json_module

    previous_result = None
    suggestions = None
    if retryContext:
        try:
            retry_data = json_module.loads(retryContext) if isinstance(retryContext, str) else retryContext
            previous_result = retry_data.get("previous_result")
            suggestions = retry_data.get("suggestions")
        except Exception:
            pass

    try:
        all_data = _read_csv_data(dataFilePath)
    except Exception as e:
        return json_module.dumps(
            {
                "error": f"读取数据文件失败: {str(e)}",
                "statistics": {},
                "positive_summary": [],
                "negative_summary": [],
                "result_file_path": "",
            },
            ensure_ascii=False,
        )

    if not all_data:
        return json_module.dumps(
            {
                "error": "数据文件为空",
                "statistics": {},
                "positive_summary": [],
                "negative_summary": [],
                "result_file_path": "",
            },
            ensure_ascii=False,
        )

    fieldnames = list(all_data[0].keys())
    # 如果外部强制指定内容列，优先使用（仅保留存在于文件表头的列）
    forced_cols: List[str] = []
    if contentColumns:
        normalized = [str(c or "").strip() for c in contentColumns if str(c or "").strip()]
        header_set = {str(h) for h in fieldnames}
        forced_cols = [c for c in normalized if c in header_set]
    content_columns = forced_cols if forced_cols else _identify_content_columns(fieldnames)
    if not content_columns:
        return json_module.dumps(
            {
                "error": (
                    "无法识别内容列，请确保列名包含: "
                    + ", ".join(CONTENT_COLUMN_KEYWORDS)
                ),
                "statistics": {},
                "positive_summary": [],
                "negative_summary": [],
                "result_file_path": "",
            },
            ensure_ascii=False,
        )

    n = len(all_data)
    sentiment_col = _identify_sentiment_column(all_data)
    prefer_existing_sentiment = _to_bool(preferExistingSentimentColumn, default=True)
    use_existing_sentiment = prefer_existing_sentiment and _should_use_existing_sentiment(all_data, sentiment_col)

    summary_model: Any = None
    scoring_model_name = ""
    scoring_profile = ""
    scores_by_row: Dict[int, Optional[int]] = {}
    row_meta: List[Dict[str, Any]] = []
    row_scores_brief: List[Dict[str, Any]] = []
    to_score: List[Tuple[int, str]] = []
    metrics = _RequestMetrics()
    chunks: List[List[Tuple[int, str]]] = []
    parallel_workers = 1
    scoring_elapsed = 0.0
    score_model: Any = None

    if use_existing_sentiment and sentiment_col:
        for i, row in enumerate(all_data):
            cleaned = _row_cleaned_content(row, content_columns)
            raw_label = row.get(sentiment_col, "")
            norm_label = _normalize_sentiment_label(raw_label)
            if norm_label is None:
                scores_by_row[i] = None if not cleaned else 5
            else:
                scores_by_row[i] = _label_to_score(norm_label)
    else:
        # LLM 重判路径（仅在用户明确要求重跑，或无可用情感列时执行）
        try:
            score_model = get_sentiment_model()
        except Exception as e:
            return json_module.dumps(
                {
                    "error": f"获取情感分析模型失败: {str(e)}",
                    "statistics": {},
                    "positive_summary": [],
                    "negative_summary": [],
                    "result_file_path": "",
                },
                ensure_ascii=False,
            )

        for i, row in enumerate(all_data):
            cleaned = _row_cleaned_content(row, content_columns)
            if not cleaned:
                scores_by_row[i] = None
                continue
            to_score.append((i, cleaned))

        # 组批
        scoring_started_at = time.perf_counter()
        chunks = [
            to_score[start : start + _SENTIMENT_DYNAMIC_BATCH_SIZE]
            for start in range(0, len(to_score), _SENTIMENT_DYNAMIC_BATCH_SIZE)
        ]

        # 并发批次打分（按批次级别并发，线程安全且可控），不足时退回串行
        parallel_workers = min(_SENTIMENT_BATCH_PARALLEL_WORKERS, len(chunks)) if chunks else 1
        if parallel_workers <= 1:
            for chunk in chunks:
                part = _score_batch(score_model, event_introduction=eventIntroduction, batch=chunk, metrics=metrics)
                for idx, _txt in chunk:
                    scores_by_row[idx] = part.get(idx, 5)
        else:
            with ThreadPoolExecutor(max_workers=parallel_workers) as executor:
                futures = {}
                for chunk in chunks:
                    fut = executor.submit(_score_batch_worker, event_introduction=eventIntroduction, batch=chunk, metrics=metrics)
                    futures[fut] = chunk
                for fut in as_completed(list(futures.keys())):
                    chunk = futures.get(fut) or []
                    try:
                        part = fut.result()
                    except Exception:
                        part = {idx: 5 for idx, _txt in chunk}
                    for idx, _txt in chunk:
                        scores_by_row[idx] = part.get(idx, 5)
        scoring_elapsed = time.perf_counter() - scoring_started_at

    for i, row in enumerate(all_data):
        cleaned = _row_cleaned_content(row, content_columns)
        s = scores_by_row.get(i)
        if s is None:
            label = "中立"
        else:
            label = _label_from_score(s)
        row_meta.append({"cleaned": cleaned, "label": label, "score": s})
        preview = (cleaned[:200] + "...") if len(cleaned) > 200 else cleaned
        row_scores_brief.append({"row_index": i, "score": s, "label": label, "text_preview": preview})

    statistics = _build_statistics(n, scores_by_row)
    statistics["content_columns"] = content_columns
    statistics["batching"] = {
        "batch_size": _SENTIMENT_DYNAMIC_BATCH_SIZE,
        "batch_count": len(chunks),
        "parallel_workers": parallel_workers,
        "retry_per_batch": _SENTIMENT_BATCH_RETRIES,
        "max_chars_per_text": _MAX_CHARS_PER_TEXT,
        "mode": "existing_sentiment_column" if use_existing_sentiment else "llm_scoring",
        "sentiment_column": sentiment_col or "",
    }
    statistics["concurrency_metrics"] = (
        metrics.summary(elapsed_sec=scoring_elapsed, rows_scored=len(to_score))
        if not use_existing_sentiment
        else {
            "elapsed_sec": 0.0,
            "rows_scored": 0,
            "requests_started": 0,
            "requests_succeeded": 0,
            "requests_failed": 0,
            "qps": 0.0,
            "qpm": 0.0,
            "rpm": 0.0,
            "max_concurrent_connections": 0,
        }
    )
    statistics["sentiment_source"] = "existing_column" if use_existing_sentiment else "llm_scoring"

    positive_contents = _extract_contents_by_label(row_meta, "正面", limit=10)
    negative_contents = _extract_contents_by_label(row_meta, "负面", limit=10)

    positive_ratio = statistics.get("positive_ratio", 0.0)
    negative_ratio = statistics.get("negative_ratio", 0.0)
    need_positive = positive_ratio > 0.1 and negative_ratio < 0.6
    need_negative = negative_ratio > 0.1 and positive_ratio < 0.6
    if abs(positive_ratio - negative_ratio) < 0.2:
        need_positive = positive_ratio > 0.1
        need_negative = negative_ratio > 0.1

    if use_existing_sentiment:
        positive_summary = _fallback_summary_from_contents(positive_contents, max_items=3)
        negative_summary = _fallback_summary_from_contents(negative_contents, max_items=3)
        parsed = {"positive_summary": positive_summary, "negative_summary": negative_summary}
    else:
        try:
            prompt_template = get_analysis_sentiment_prompt()
        except Exception as e:
            return json_module.dumps(
                {
                    "error": f"加载情感总结提示词失败: {str(e)}",
                    "statistics": statistics,
                    "positive_summary": [],
                    "negative_summary": [],
                    "content_columns": content_columns,
                    "row_scores": row_scores_brief,
                    "result_file_path": "",
                },
                ensure_ascii=False,
            )

        retry_section = "无（首次分析）" if not previous_result else str(previous_result)
        suggestions_section = "无" if not suggestions else str(suggestions)

        prompt = prompt_template.format(
            event_introduction=eventIntroduction,
            statistics=json_module.dumps(statistics, ensure_ascii=False, indent=2),
            positive_contents="\n\n".join(positive_contents) if need_positive and positive_contents else "无",
            negative_contents="\n\n".join(negative_contents) if need_negative and negative_contents else "无",
            need_positive="是" if need_positive else "否",
            need_negative="是" if need_negative else "否",
            previous_result=retry_section,
            suggestions=suggestions_section,
        )

        try:
            summary_messages = [
                SystemMessage(content="你是一个专业的情感倾向分析专家。"),
                HumanMessage(content=prompt),
            ]
            summary_resp = score_model.invoke(summary_messages)
            result_text = summary_resp.content if hasattr(summary_resp, "content") else str(summary_resp)
        except Exception as e:
            return json_module.dumps(
                {
                    "error": f"模型总结失败: {str(e)}",
                    "statistics": statistics,
                    "positive_summary": [],
                    "negative_summary": [],
                    "content_columns": content_columns,
                    "row_scores": row_scores_brief,
                    "result_file_path": "",
                },
                ensure_ascii=False,
            )

        try:
            json_match = re.search(r"\{[\s\S]*\}", result_text)
            parsed = json_module.loads(json_match.group() if json_match else result_text)
        except Exception:
            return json_module.dumps(
                {
                    "error": "模型返回总结格式不正确",
                    "raw_result": result_text,
                    "statistics": statistics,
                    "positive_summary": [],
                    "negative_summary": [],
                    "content_columns": content_columns,
                    "row_scores": row_scores_brief,
                    "result_file_path": "",
                },
                ensure_ascii=False,
            )

        if not isinstance(parsed, dict):
            return json_module.dumps(
                {
                    "error": "模型返回总结格式不正确",
                    "raw_result": result_text,
                    "statistics": statistics,
                    "positive_summary": [],
                    "negative_summary": [],
                    "content_columns": content_columns,
                    "row_scores": row_scores_brief,
                    "result_file_path": "",
                },
                ensure_ascii=False,
            )

    full_result: Dict[str, Any] = {
        "statistics": statistics,
        "positive_summary": parsed.get("positive_summary", []),
        "negative_summary": parsed.get("negative_summary", []),
        "content_columns": content_columns,
        "row_scores": row_scores_brief,
        "scoring_model": "existing_sentiment_column" if use_existing_sentiment else "qwen-plus",
        "scoring_profile": "existing_column" if use_existing_sentiment else "sentiment",
        "raw_summary": None,
    }

    task_id = get_task_id()
    if task_id:
        try:
            process_dir = ensure_task_dirs(task_id)
            filename = _generate_result_filename(retryContext)
            result_file = process_dir / filename
            full_result["result_file_path"] = str(result_file)
            with open(result_file, "w", encoding="utf-8", errors="replace") as f:
                json_module.dump(full_result, f, ensure_ascii=False, indent=2)
        except Exception as e:
            full_result["save_error"] = f"保存结果文件失败: {str(e)}"
            full_result["result_file_path"] = ""
    else:
        full_result["save_error"] = "未找到任务ID，无法保存结果文件"
        full_result["result_file_path"] = ""

    # 终端/交互展示：保持简洁（详细打分与样本请看保存文件）
    result_file_path = str(full_result.get("result_file_path") or "")
    summary_message = (
        f"情感打分完成：共 {n} 行，命中 {len(content_columns)} 个内容列；"
        f"QPS={statistics.get('concurrency_metrics', {}).get('qps', 0)}，"
        f"RPM={statistics.get('concurrency_metrics', {}).get('rpm', 0)}，"
        f"最大并发连接={statistics.get('concurrency_metrics', {}).get('max_concurrent_connections', 0)}。"
    )
    summary_payload: Dict[str, Any] = {
        "message": summary_message,
        "statistics": statistics,
        "content_columns": content_columns,
        "positive_summary": full_result.get("positive_summary", []),
        "negative_summary": full_result.get("negative_summary", []),
        "scoring_model": full_result.get("scoring_model", ""),
        "scoring_profile": full_result.get("scoring_profile", ""),
        "result_file_path": result_file_path,
    }
    if full_result.get("save_error"):
        summary_payload["save_error"] = full_result.get("save_error")

    return json_module.dumps(summary_payload, ensure_ascii=False)
