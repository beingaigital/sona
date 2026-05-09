#!/usr/bin/env python3
"""
将 Playwright Chromium 安装到项目内 .cache/ms-playwright（与运行时默认一致）。

解决：系统 ~/Library/Caches/ms-playwright 仅有旧 revision（如 chromium-1208），
而当前 playwright 包要求 chromium-1217，导致 NetInsight 登录报 Executable doesn't exist。

用法（在项目根目录）：
  .venv/bin/python scripts/install_playwright_browsers.py
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / ".cache" / "ms-playwright"
TARGET.mkdir(parents=True, exist_ok=True)
os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(TARGET.resolve())

print("PLAYWRIGHT_BROWSERS_PATH=", os.environ["PLAYWRIGHT_BROWSERS_PATH"])
sys.exit(
    subprocess.call(
        [sys.executable, "-m", "playwright", "install", "chromium", "--force"],
        cwd=str(ROOT),
    )
)
