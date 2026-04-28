"""情感倾向分析工具：对内容列做与关键词一致的清洗后，使用 qwen-plus 打 0-10 分并汇总。"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import time

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.tools import tool

from model.factory import get_sentiment_model
from tools._csv_io import read_csv_rows_all
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

_SENTIMENT_BATCH_PARALLEL_WORKERS: int = _env_int("SONA_SENTIMENT_BATCH_PARALLEL_WORKERS", 4, 1, 8)
_SENTIMENT_BATCH_JITTER_MS: int = _env_int("SONA_SENTIMENT_BATCH_JITTER_MS", 100, 0, 1000)
_SENTIMENT_DYNAMIC_BATCH_SIZE: int = _env_int("SONA_SENTIMENT_BATCH_SIZE", 40, 4, 64)
_SENTIMENT_BATCH_RETRIES: int = _env_int("SONA_SENTIMENT_BATCH_RETRIES", 1, 0, 3)
_SENTIMENT_BATCH_TIMEOUT_SEC: int = _env_int("SONA_SENTIMENT_BATCH_TIMEOUT_SEC", 25, 5, 120)
_SENTIMENT_MAX_WALLTIME_SEC: int = _env_int("SONA_SENTIMENT_MAX_WALLTIME_SEC", 120, 15, 1800)
_SENTIMENT_MAX_ROWS: int = _env_int("SONA_SENTIMENT_MAX_ROWS", 2000, 50, 200000)
_SENTIMENT_EXAMPLES_MAX_CHARS: int = _env_int("SONA_SENTIMENT_EXAMPLES_MAX_CHARS", 4000, 500, 20000)

_SCORE_SYSTEM_PROMPT = """你是舆情情感分析助手。结合「事件背景」，对每条文本给出整数情感分 score，范围 0～10：
- 0 为最负面，10 为最正面
- 0～3：负面；4～6：中立；7～10：正面

必须只输出一个 JSON 对象，不要 markdown 代码块，格式严格为：
{"items":[{"row":<整数行号>,"score":<0到10的整数>}, ...]}

要求：
- items 长度与输入条数一致，每个 row 与输入中的 row 一致
- score 必须为整数，不得输出小数或文字说明
"""


def _load_sentiment_examples_text() -> str:
    use_examples = str(os.environ.get("SONA_SENTIMENT_USE_EXAMPLES", "0")).strip().lower() in {
        "1",
        "true",
        "yes",
        "y",
        "on",
    }
    if not use_examples:
        return ""
    custom_path = str(os.environ.get("SONA_SENTIMENT_EXAMPLES_PATH", "")).strip()
    if custom_path:
        p = Path(custom_path).expanduser()
    else:
        p = Path(__file__).resolve().parents[1] / "prompt" / "sentiment_examples_zh_v1.md"
    if not p.exists() or not p.is_file():
        return ""
    try:
        text = p.read_text(encoding="utf-8")
    except Exception:
        return ""
    text = text.strip()
    if not text:
        return ""
    if len(text) > _SENTIMENT_EXAMPLES_MAX_CHARS:
        text = text[:_SENTIMENT_EXAMPLES_MAX_CHARS] + "\n...\n"
    return text


_SENTIMENT_EXAMPLES_TEXT: str = _load_sentiment_examples_text()


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


def _score_to_coarse_label(score: Optional[int]) -> Optional[str]:
    if score is None:
        return None
    try:
        s = int(score)
    except Exception:
        return None
    if s <= 3:
        return "负面"
    if s <= 6:
        return "中立"
    return "正面"


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
    system_prompt = _SCORE_SYSTEM_PROMPT
    if _SENTIMENT_EXAMPLES_TEXT:
        system_prompt += (
            "\n\n以下是中文舆情情感标注示例与规则，请参考其口径完成打分：\n"
            + _SENTIMENT_EXAMPLES_TEXT
        )
    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=human),
    ]
    last_error: Optional[Exception] = None
    response: Any = None
    for attempt in range(_SENTIMENT_BATCH_RETRIES + 1):
        if metrics:
            metrics.on_start()
        try:
            # Hard timeout guard: prevent one batch from hanging forever.
            _pool = ThreadPoolExecutor(max_workers=1)
            _fut = _pool.submit(model.invoke, messages)
            try:
                response = _fut.result(timeout=max(1, _SENTIMENT_BATCH_TIMEOUT_SEC))
            finally:
                # 关键：超时后不等待线程，避免单批次“表面超时，实际阻塞”。
                _pool.shutdown(wait=False, cancel_futures=True)
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
    # 关键：获取模型本身也可能卡住，因此做硬超时兜底。
    try:
        _pool = ThreadPoolExecutor(max_workers=1)
        _fut = _pool.submit(get_sentiment_model)
        try:
            model = _fut.result(timeout=max(1, _SENTIMENT_BATCH_TIMEOUT_SEC))
        finally:
            _pool.shutdown(wait=False, cancel_futures=True)
    except Exception:
        return {idx: 5 for idx, _ in batch}
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


def _compute_agreement_with_existing(
    data: List[Dict[str, Any]],
    *,
    sentiment_col: Optional[str],
    scores_by_row: Dict[int, Optional[int]],
) -> Dict[str, Any]:
    if not sentiment_col:
        return {
            "available": False,
            "compared_rows": 0,
            "agreement_rows": 0,
            "agreement_rate": None,
        }

    compared = 0
    agreed = 0
    for i, row in enumerate(data):
        existing = _normalize_sentiment_label(row.get(sentiment_col, ""))
        new_label = _score_to_coarse_label(scores_by_row.get(i))
        if existing is None or new_label is None:
            continue
        compared += 1
        if existing == new_label:
            agreed += 1

    return {
        "available": compared > 0,
        "compared_rows": compared,
        "agreement_rows": agreed,
        "agreement_rate": round(agreed / compared, 4) if compared > 0 else None,
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
        all_data = read_csv_rows_all(dataFilePath)
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
    # 稳定性优先策略：
    # - 数据里存在“情感/倾向”列时，默认优先复用该列（避免 LLM 卡死/超时导致整段流程无结果）
    # - 若确需强制重判，可显式设置环境变量 SONA_SENTIMENT_ALLOW_LLM_REJUDGE_WITH_EXISTING_COLUMN=1
    allow_llm_rejudge_with_existing = str(
        os.environ.get("SONA_SENTIMENT_ALLOW_LLM_REJUDGE_WITH_EXISTING_COLUMN", "0")
    ).strip().lower() in {"1", "true", "yes", "y"}
    if sentiment_col and not allow_llm_rejudge_with_existing:
        use_existing_sentiment = True
    else:
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

    def _build_scores_from_existing_sentiment() -> None:
        nonlocal scores_by_row
        if not sentiment_col:
            return
        for i, row in enumerate(all_data):
            cleaned = _row_cleaned_content(row, content_columns)
            raw_label = row.get(sentiment_col, "")
            norm_label = _normalize_sentiment_label(raw_label)
            if norm_label is None:
                scores_by_row[i] = None if not cleaned else 5
            else:
                scores_by_row[i] = _label_to_score(norm_label)

    # 快速抽样重判（可选）：在“已有情感列但不可信”场景，用 LLM 对抽样重判，避免全量耗时。
    # - 默认关闭；开启方式：SONA_SENTIMENT_FAST_SAMPLE=1 且允许重判
    # - 抽样规模：max(min(n*rate, max_rows), min_rows)
    fast_sample_enabled = str(os.environ.get("SONA_SENTIMENT_FAST_SAMPLE", "0")).strip().lower() in {
        "1",
        "true",
        "yes",
        "y",
        "on",
    }
    sample_rate_raw = str(os.environ.get("SONA_SENTIMENT_FAST_SAMPLE_RATE", "0.1")).strip()
    sample_max_raw = str(os.environ.get("SONA_SENTIMENT_FAST_SAMPLE_MAX_ROWS", "220")).strip()
    sample_min_raw = str(os.environ.get("SONA_SENTIMENT_FAST_SAMPLE_MIN_ROWS", "60")).strip()
    try:
        sample_rate = float(sample_rate_raw)
    except Exception:
        sample_rate = 0.1
    try:
        sample_max_rows = int(sample_max_raw)
    except Exception:
        sample_max_rows = 220
    try:
        sample_min_rows = int(sample_min_raw)
    except Exception:
        sample_min_rows = 60
    sample_rate = max(0.01, min(sample_rate, 0.5))
    sample_max_rows = max(40, min(sample_max_rows, 1200))
    sample_min_rows = max(20, min(sample_min_rows, 400))

    use_llm_sample = bool(
        fast_sample_enabled
        and allow_llm_rejudge_with_existing
        and sentiment_col
        and use_existing_sentiment  # 原本会直接复用情感列；改为抽样重判以提高准确性
    )

    if use_existing_sentiment and sentiment_col and (not use_llm_sample):
        _build_scores_from_existing_sentiment()
    else:
        # LLM 重判路径（仅在用户明确要求重跑，或无可用情感列时执行）
        try:
            # 关键：获取模型实例也可能卡死，加入硬超时。
            _pool = ThreadPoolExecutor(max_workers=1)
            _fut = _pool.submit(get_sentiment_model)
            try:
                score_model = _fut.result(timeout=max(1, _SENTIMENT_BATCH_TIMEOUT_SEC))
            finally:
                _pool.shutdown(wait=False, cancel_futures=True)
        except Exception as e:
            # 关键兜底：LLM 不可用时，只要数据里有“情感”列，就强制回退复用情感列
            if sentiment_col:
                use_existing_sentiment = True
                _build_scores_from_existing_sentiment()
            else:
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

        # 抽样重判：仅取部分样本做 LLM 打分，统计用样本估计（速度优先）
        if use_llm_sample and to_score:
            import random

            target = int(round(len(to_score) * sample_rate))
            target = max(sample_min_rows, min(target, sample_max_rows, len(to_score)))
            random.seed(17)
            to_score = random.sample(to_score, k=target) if len(to_score) > target else to_score

        # Cap total rows to score to keep walltime bounded (can be tuned via env).
        if len(to_score) > _SENTIMENT_MAX_ROWS:
            to_score = to_score[:_SENTIMENT_MAX_ROWS]

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
                if (time.perf_counter() - scoring_started_at) > float(_SENTIMENT_MAX_WALLTIME_SEC):
                    # Stop early to avoid long stalls; fill remaining with neutral score.
                    for idx, _txt in chunk:
                        scores_by_row[idx] = 5
                    continue
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

        # 关键兜底：LLM 打分若整体不可用/疑似超时（无成功请求），且存在“情感”列，则强制回退情感列
        try:
            succ = int(metrics.requests_succeeded or 0)
            started = int(metrics.requests_started or 0)
        except Exception:
            succ, started = 0, 0
        if sentiment_col and (started > 0 and succ <= 0):
            use_existing_sentiment = True
            scores_by_row = {}
            _build_scores_from_existing_sentiment()

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

    # 注意：若为抽样重判，则 scores_by_row 只覆盖部分行；统计应基于已打分样本估计比例
    if use_llm_sample and not use_existing_sentiment:
        sampled_scores = {i: s for i, s in scores_by_row.items() if s is not None}
        # 若极端情况下无有效样本，则退回中立
        if not sampled_scores:
            sampled_scores = {0: 5}
        statistics = _build_statistics(len(sampled_scores), sampled_scores)
        statistics["total"] = n
        statistics["sampling"] = {
            "enabled": True,
            "sampled_rows": len(sampled_scores),
            "sample_rate": round(len(sampled_scores) / max(1, n), 4),
            "note": "情感分布为抽样估计（为提升速度，未对全量逐条重判）",
        }
    else:
        statistics = _build_statistics(n, scores_by_row)
    statistics["content_columns"] = content_columns
    statistics["batching"] = {
        "batch_size": _SENTIMENT_DYNAMIC_BATCH_SIZE,
        "batch_count": len(chunks),
        "parallel_workers": parallel_workers,
        "retry_per_batch": _SENTIMENT_BATCH_RETRIES,
        "batch_timeout_sec": _SENTIMENT_BATCH_TIMEOUT_SEC,
        "max_walltime_sec": _SENTIMENT_MAX_WALLTIME_SEC,
        "max_rows": _SENTIMENT_MAX_ROWS,
        "max_chars_per_text": _MAX_CHARS_PER_TEXT,
        "mode": (
            "existing_sentiment_column"
            if use_existing_sentiment
            else ("llm_sampling" if use_llm_sample else "llm_scoring")
        ),
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
    if use_existing_sentiment:
        # 若 preferExistingSentimentColumn=False 但最终回退到情感列，需要显式标注 fallback
        statistics["sentiment_source"] = "existing_column_fallback" if not prefer_existing_sentiment else "existing_column"
        statistics["fallback_used"] = (not prefer_existing_sentiment) or (bool(sentiment_col) and not _to_bool(preferExistingSentimentColumn, default=True))
    else:
        statistics["sentiment_source"] = "llm_scoring"
    llm_rows = len(to_score) if not use_existing_sentiment else 0
    statistics["llm_coverage"] = round(llm_rows / n, 4) if n else 0.0
    req_started = int(statistics.get("concurrency_metrics", {}).get("requests_started", 0) or 0)
    req_succeeded = int(statistics.get("concurrency_metrics", {}).get("requests_succeeded", 0) or 0)
    statistics["parse_success_rate"] = (round(req_succeeded / req_started, 4) if req_started > 0 else 1.0)
    statistics["agreement_with_existing"] = _compute_agreement_with_existing(
        all_data, sentiment_col=sentiment_col, scores_by_row=scores_by_row
    )

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
            # 总结提示词加载失败：仍然保证返回可用统计（用简单摘要兜底）
            positive_summary = _fallback_summary_from_contents(positive_contents, max_items=3)
            negative_summary = _fallback_summary_from_contents(negative_contents, max_items=3)
            parsed = {"positive_summary": positive_summary, "negative_summary": negative_summary}
        else:
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
            except Exception:
                # 模型总结失败：仍然可用（用简单摘要兜底）
                positive_summary = _fallback_summary_from_contents(positive_contents, max_items=3)
                negative_summary = _fallback_summary_from_contents(negative_contents, max_items=3)
                parsed = {"positive_summary": positive_summary, "negative_summary": negative_summary}
            else:
                try:
                    json_match = re.search(r"\{[\s\S]*\}", result_text)
                    parsed = json_module.loads(json_match.group() if json_match else result_text)
                except Exception:
                    positive_summary = _fallback_summary_from_contents(positive_contents, max_items=3)
                    negative_summary = _fallback_summary_from_contents(negative_contents, max_items=3)
                    parsed = {
                        "positive_summary": positive_summary,
                        "negative_summary": negative_summary,
                        "raw_result": result_text,
                    }

                if not isinstance(parsed, dict):
                    positive_summary = _fallback_summary_from_contents(positive_contents, max_items=3)
                    negative_summary = _fallback_summary_from_contents(negative_contents, max_items=3)
                    parsed = {
                        "positive_summary": positive_summary,
                        "negative_summary": negative_summary,
                        "raw_result": result_text,
                    }

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
