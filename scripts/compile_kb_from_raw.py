#!/usr/bin/env python3
"""
从 opinion_analysis_kb/references/raw 增量编译到 wiki/sources（维护 index/log）。

对应老师口径：在 raw 里新增文件后，可在终端运行本脚本；或在 Cursor 里让助手执行：
  python scripts/compile_kb_from_raw.py

底层调用 tools.oprag.build_reference_wiki（需配置可用的 tools 模型与 API，见 config/.env）。

用法：
  python scripts/compile_kb_from_raw.py              # 默认最多处理 80 个未编译源
  python scripts/compile_kb_from_raw.py --limit 200
  python scripts/compile_kb_from_raw.py --force      # 强制重编译（已有 wiki 对应页也会重做）
  python scripts/compile_kb_from_raw.py --list-raw   # 仅列出 raw 下 .md / .txt，不调用模型
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _list_raw_text_files(raw_dir: Path) -> list[Path]:
    out: list[Path] = []
    for suf in (".md", ".txt"):
        out.extend(sorted(raw_dir.rglob(f"*{suf}")))
    return [p for p in out if p.is_file()]


def main() -> int:
    parser = argparse.ArgumentParser(description="Compile KB from opinion_analysis_kb/references/raw")
    parser.add_argument("--limit", type=int, default=80, help="本次最多处理的源文件数（传给 build_reference_wiki）")
    parser.add_argument("--force", action="store_true", help="强制重编译已有产物")
    parser.add_argument("--list-raw", action="store_true", help="只列出 raw 下的文本文件")
    args = parser.parse_args()

    root = _project_root()
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

    raw_dir = root / "opinion_analysis_kb" / "references" / "raw"
    if not raw_dir.is_dir():
        print(f"[ERROR] raw 目录不存在: {raw_dir}", file=sys.stderr)
        return 2

    if args.list_raw:
        files = _list_raw_text_files(raw_dir)
        for p in files:
            print(p.relative_to(raw_dir).as_posix())
        print(f"# total: {len(files)} files under raw", file=sys.stderr)
        return 0

    from tools.oprag import build_reference_wiki

    payload = {"limit": max(1, min(args.limit, 500)), "force": bool(args.force)}
    raw = build_reference_wiki.invoke(payload)
    print(raw)
    try:
        obj = json.loads(raw)
        return 0 if obj.get("ok") else 1
    except json.JSONDecodeError:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
