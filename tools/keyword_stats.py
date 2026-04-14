"""关键词统计工具：对采集到的舆情数据进行关键词 TopN 统计。"""

from __future__ import annotations

import csv
import json as json_module
import contextlib
import io
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from langchain_core.tools import tool

from utils.content_text import clean_text_like_keyword_stats
from utils.path import get_config_path, get_project_root, get_task_process_dir
from utils.task_context import get_task_id


CONTENT_COLUMN_KEYWORDS: Tuple[str, ...] = (
    "content",
    "contents",
    "内容",
    "正文",
    "摘要",
    "ocr",
    "segment",
)

DEFAULT_ALLOWED_POS_PREFIXES: Tuple[str, ...] = ("n", "v", "a", "nr", "ns", "nt")


@dataclass(frozen=True)
class KeywordStatsResult:
    """关键词统计结果（用于结构化输出）。"""

    content_columns: List[str]
    top_keywords: List[Dict[str, Any]]
    total_rows: int


def _load_stopwords() -> Set[str]:
    """
    从 stopwords.txt 加载停用词集合；找不到则返回空集合。

    优先路径：
    - config/stopwords.txt（项目内默认）
    - configs/stopwords.txt（兼容部分历史命名）
    """
    candidates = [
        get_config_path("stopwords.txt"),
        get_project_root() / "configs" / "stopwords.txt",
    ]
    stopwords_path = next((p for p in candidates if p.exists()), None)
    if stopwords_path is None:
        return set()
    try:
        with open(stopwords_path, "r", encoding="utf-8", errors="replace") as f:
            return {line.strip() for line in f if line.strip()}
    except Exception:
        return set()


def _read_csv_rows(file_path: str) -> List[Dict[str, Any]]:
    file = Path(file_path)
    if not file.exists():
        raise FileNotFoundError(f"数据文件不存在: {file_path}")

    rows: List[Dict[str, Any]] = []
    encodings_to_try: List[str] = ["utf-8-sig", "utf-8", "gb18030", "gbk"]
    last_error: Optional[Exception] = None
    for enc in encodings_to_try:
        try:
            with open(file, "r", encoding=enc, errors="strict") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    rows.append(row)
            break
        except Exception as e:
            rows = []
            last_error = e
            continue
    if not rows and last_error is not None:
        with open(file, "r", encoding="utf-8-sig", errors="replace") as f:
            reader = csv.DictReader(f)
            for row in reader:
                rows.append(row)
    return rows


def _identify_content_columns(fieldnames: Sequence[str]) -> List[str]:
    """
    自动识别内容列：列名包含 content/contents/内容/正文/摘要/ocr/segment（不区分大小写）。
    """
    matched: List[str] = []
    for name in fieldnames:
        name_lower = name.lower()
        if any(k in name_lower for k in ("content", "contents", "ocr", "segment")):
            matched.append(name)
            continue
        if any(k in name for k in ("内容", "正文", "摘要")):
            matched.append(name)
    return matched


def _flatten_text(rows: Sequence[Dict[str, Any]], columns: Sequence[str]) -> str:
    """
    将命中的多列全部摊平，用空格拼成一个超长字符串（全量语料）。
    """
    parts: List[str] = []
    for row in rows:
        for col in columns:
            val = row.get(col, "")
            if val is None:
                continue
            text = str(val).strip()
            if text:
                parts.append(text)
    return " ".join(parts)


def _tokenize_with_jieba(
    text: str,
    *,
    stopwords: Set[str],
    min_len: int,
    allowed_pos_prefixes: Tuple[str, ...],
) -> Iterable[str]:
    try:
        # jieba 在首次初始化时会向 stdout/stderr 输出一些“Loading/Buiding...”信息，
        # 这里临时重定向，避免污染 Sona 终端输出。
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            import jieba.posseg as pseg  # type: ignore
    except Exception:
        return []

    tokens: List[str] = []
    # 进一步兜底：切词时也做同样的 stdout/stderr 重定向。
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        for word, flag in pseg.cut(text):
            w = (word or "").strip()
            if not w:
                continue
            if len(w) < min_len:
                continue
            if w in stopwords:
                continue
            if flag and any(str(flag).startswith(prefix) for prefix in allowed_pos_prefixes):
                tokens.append(w)
    return tokens


def _tokenize_fallback(
    text: str,
    *,
    stopwords: Set[str],
    min_len: int,
) -> Iterable[str]:
    cleaned = clean_text_like_keyword_stats(text)
    for tok in cleaned.split():
        t = tok.strip()
        if not t:
            continue
        if len(t) < min_len:
            continue
        if t in stopwords:
            continue
        yield t


def _save_result_json(task_id: str, payload: Dict[str, Any]) -> str:
    process_dir = get_task_process_dir(task_id)
    process_dir.mkdir(parents=True, exist_ok=True)
    out_path = process_dir / "keyword_stats.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json_module.dump(payload, f, ensure_ascii=False, indent=2)
    return str(out_path)


@tool
def keyword_stats(
    dataFilePath: str,
    top_n: int = 20,
    min_len: int = 2,
    contentColumns: Optional[List[str]] = None,
) -> str:
    """
    描述：关键词统计识别。对采集到的CSV数据进行内容列识别、语料拼接、分词与过滤、停用词过滤，并统计 TopN 关键词词频。
    使用时机：数据采集（data_collect）完成后、生成报告（report_html）之前，可用于补充“关键词热度/关注点”维度。
    输入：
    - dataFilePath（必填）：数据文件位置，CSV 文件路径（来自 data_collect 返回的 JSON 中的 save_path 字段）。
    - top_n（可选，默认20）：TopN 数量。
    - min_len（可选，默认2）：最小词长度（中文按字符长度计）。
    输出：JSON 字符串，包含以下字段：
    - content_columns：命中的内容列名列表
    - top_keywords：Top 关键词列表（元素包含 word/count）
    - total_rows：数据行数
    - result_file_path：结果 JSON 文件保存路径（任务过程文件夹）
    """
    task_id = get_task_id()
    if not task_id:
        return json_module.dumps(
            {
                "error": "未找到任务ID，请确保在Agent上下文中调用",
                "content_columns": [],
                "top_keywords": [],
                "total_rows": 0,
                "result_file_path": "",
            },
            ensure_ascii=False,
        )

    try:
        rows = _read_csv_rows(dataFilePath)
    except Exception as e:
        return json_module.dumps(
            {
                "error": f"读取数据文件失败: {str(e)}",
                "content_columns": [],
                "top_keywords": [],
                "total_rows": 0,
                "result_file_path": "",
            },
            ensure_ascii=False,
        )

    if not rows:
        return json_module.dumps(
            {
                "error": "数据文件为空",
                "content_columns": [],
                "top_keywords": [],
                "total_rows": 0,
                "result_file_path": "",
            },
            ensure_ascii=False,
        )

    fieldnames = list(rows[0].keys())
    content_columns: List[str] = []
    if contentColumns:
        normalized_headers = {str(h).strip() for h in fieldnames}
        specified = [c for c in (str(x).strip() for x in contentColumns) if c in normalized_headers]
        if specified:
            content_columns = specified
    if not content_columns:
        content_columns = _identify_content_columns(fieldnames)
    if not content_columns:
        return json_module.dumps(
            {
                "error": f"无法识别内容列（需要列名包含: {', '.join(CONTENT_COLUMN_KEYWORDS)}）",
                "content_columns": [],
                "top_keywords": [],
                "total_rows": len(rows),
                "result_file_path": "",
            },
            ensure_ascii=False,
        )

    stopwords = _load_stopwords()
    corpus_text = _flatten_text(rows, content_columns)

    tokens = list(
        _tokenize_with_jieba(
            corpus_text,
            stopwords=stopwords,
            min_len=min_len,
            allowed_pos_prefixes=DEFAULT_ALLOWED_POS_PREFIXES,
        )
    )
    if not tokens:
        tokens = list(_tokenize_fallback(corpus_text, stopwords=stopwords, min_len=min_len))

    counter = Counter(tokens)
    top_keywords = [{"word": w, "count": c} for w, c in counter.most_common(int(top_n))]

    full_payload: Dict[str, Any] = {
        "content_columns": content_columns,
        "top_keywords": top_keywords,
        "total_rows": len(rows),
    }
    result_file_path = _save_result_json(task_id, full_payload)
    full_payload["result_file_path"] = result_file_path

    # 终端/交互展示：保持简洁（详细结果请看保存文件）
    preview_n = min(5, len(top_keywords))
    summary_payload: Dict[str, Any] = {
        "message": f"关键词统计完成：共 {len(rows)} 行，命中 {len(content_columns)} 个内容列，Top{top_n} 已写入过程文件。",
        "content_columns": content_columns,
        "top_keywords_preview": top_keywords[:preview_n],
        "total_rows": len(rows),
        "result_file_path": result_file_path,
    }

    return json_module.dumps(summary_payload, ensure_ascii=False)

