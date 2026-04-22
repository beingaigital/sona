"""NetInsight 多文件采集结果合并（从 CLI 迁移）。"""

from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path
from typing import Dict, List


def merge_netinsight_csv_by_content(*, csv_paths: List[str], out_path: str) -> str:
    """
    将多个 data_collect 输出 CSV 合并，并按「内容」列去重（若缺失则退化为整行去重）。
    返回 out_path。
    """
    paths = [str(p) for p in (csv_paths or []) if str(p).strip()]
    paths = [p for p in paths if Path(p).expanduser().exists()]
    if not paths:
        return out_path

    out_p = Path(out_path).expanduser()
    out_p.parent.mkdir(parents=True, exist_ok=True)

    all_rows: List[Dict[str, str]] = []
    fieldnames: List[str] = []
    for p in paths:
        try:
            with open(p, "r", encoding="utf-8", errors="replace", newline="") as f:
                reader = csv.DictReader(f)
                if not fieldnames and reader.fieldnames:
                    fieldnames = list(reader.fieldnames)
                for row in reader:
                    if isinstance(row, dict):
                        all_rows.append({str(k): str(v) for k, v in row.items()})
        except Exception:
            continue

    if not fieldnames:
        keys: set[str] = set()
        for r in all_rows:
            keys.update(r.keys())
        fieldnames = sorted(keys)

    seen: set[str] = set()
    deduped: List[Dict[str, str]] = []
    for row in all_rows:
        content = str(row.get("内容", "") or "").strip()
        key_src = content if content else json.dumps(row, ensure_ascii=False, sort_keys=True)
        h = hashlib.md5(key_src.encode("utf-8", errors="ignore")).hexdigest()
        if h in seen:
            continue
        seen.add(h)
        deduped.append(row)

    with open(out_p, "w", encoding="utf-8", errors="replace", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in deduped:
            writer.writerow(row)
    return str(out_p)
