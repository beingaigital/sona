"""Playwright 浏览器缓存目录。

默认使用「项目根/.cache/ms-playwright」，与 `python scripts/install_playwright_browsers.py`
安装的目录一致，避免系统目录 ~/Library/Caches/ms-playwright 里残留旧 revision（如 1208）
而当前 playwright 包要求 chromium-1217 导致 Executable doesn't exist。
"""

from __future__ import annotations

import os
from pathlib import Path

from utils.path import get_project_root


def ensure_playwright_browsers_path() -> None:
    """若未显式设置 PLAYWRIGHT_BROWSERS_PATH，则指向项目内缓存目录。"""
    if os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "").strip():
        return
    root = get_project_root()
    target = root / ".cache" / "ms-playwright"
    try:
        target.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(target.resolve())
