"""启动 HTTP API（封装 uvicorn，见 docs/api_design.md）。"""

from __future__ import annotations

import os

import uvicorn

from utils.path import get_project_root


def run_serve(*, host: str, port: int, reload: bool) -> None:
    """
    以项目根为工作目录启动 FastAPI，确保 ``api.server:app`` 可导入。

    环境变量（可选）：``SONA_API_HOST``、``SONA_API_PORT`` 作为 Typer 未覆盖时的默认值由调用方处理。
    """
    os.chdir(get_project_root())
    uvicorn.run(
        "api.server:app",
        host=host,
        port=port,
        reload=reload,
    )
