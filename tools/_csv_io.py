"""Shared CSV helpers for tools (encoding fallback, streaming sample + row count)."""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

DEFAULT_ENCODINGS: Tuple[str, ...] = ("utf-8-sig", "utf-8", "gb18030", "gbk")


def read_csv_rows_all(
    file_path: str | Path,
    *,
    encodings: Sequence[str] = DEFAULT_ENCODINGS,
) -> List[Dict[str, Any]]:
    """Read entire CSV as list of row dicts; try encodings until one succeeds (same strategy as legacy tools)."""
    file = Path(file_path)
    if not file.exists():
        raise FileNotFoundError(f"数据文件不存在: {file_path}")

    rows: List[Dict[str, Any]] = []
    last_error: Optional[Exception] = None
    for enc in encodings:
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


def read_csv_fieldnames_sample_and_total(
    save_path: str | Path,
    *,
    sample_limit: int = 200,
    encodings: Sequence[str] = DEFAULT_ENCODINGS,
) -> Tuple[List[str], List[Dict[str, str]], int]:
    """
    Read CSV fieldnames, up to `sample_limit` rows (string values), and total row count.

    Tries strict decoding per encoding; falls back to utf-8-sig replace for stubborn files.
    """
    csv_path = Path(save_path)
    if not csv_path.exists():
        raise FileNotFoundError(f"CSV 文件不存在: {save_path}")

    last_error: Optional[Exception] = None
    for enc in encodings:
        try:
            with open(csv_path, "r", encoding=enc, errors="strict") as f:
                reader = csv.DictReader(f)
                if not reader.fieldnames:
                    return [], [], 0
                fieldnames = list(reader.fieldnames)
                rows: List[Dict[str, str]] = []
                row_count = 0
                for row in reader:
                    row_count += 1
                    if len(rows) < sample_limit:
                        rows.append({k: (v or "") for k, v in row.items()})
                return fieldnames, rows, row_count
        except Exception as e:
            last_error = e
            continue

    with open(csv_path, "r", encoding="utf-8-sig", errors="replace") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            return [], [], 0
        fieldnames = list(reader.fieldnames)
        rows = []
        row_count = 0
        for row in reader:
            row_count += 1
            if len(rows) < sample_limit:
                rows.append({k: (v or "") for k, v in row.items()})
        if last_error is not None and row_count == 0:
            raise last_error
        return fieldnames, rows, row_count
