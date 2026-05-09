"""统一项目路径，供配置、数据等模块确定文件位置。"""

from __future__ import annotations

from pathlib import Path

# 项目根目录
_PROJECT_ROOT: Path | None = None


def get_project_root() -> Path:
    """返回项目根目录（sona 包所在目录）。"""
    global _PROJECT_ROOT
    if _PROJECT_ROOT is None:
        _PROJECT_ROOT = Path(__file__).resolve().parent.parent
    return _PROJECT_ROOT


def get_opinion_analysis_kb_root(project_root: Path | None = None) -> Path:
    """垂类知识库根目录：`<项目根>/opinion_analysis_kb`（Wiki/编译产物与 domains 的上层）。"""
    base = project_root if project_root is not None else get_project_root()
    return Path(base) / "opinion_analysis_kb"


def get_config_dir() -> Path:
    """配置目录：项目根/config。"""
    return get_project_root() / "config"


def get_prompt_dir() -> Path:
    """Prompt 目录：项目根/prompt（存放 prompt.yaml 及各类 prompt 文本）。"""
    return get_project_root() / "prompt"


def get_config_path(name: str) -> Path:
    """指定配置文件名在 config 目录下的完整路径。如 get_config_path('model.yaml')。"""
    return get_config_dir() / name


def get_sandbox_dir() -> Path:
    """沙箱根目录：项目根/sandbox，用于按任务 ID 隔离运行产物。"""
    return get_project_root() / "sandbox"


def get_task_dir(task_id: str) -> Path:
    """指定任务 ID 的目录：sandbox/task_id。"""
    return get_sandbox_dir() / task_id


def get_task_process_dir(task_id: str) -> Path:
    """指定任务的过程文件目录：sandbox/task_id/过程文件。"""
    return get_task_dir(task_id) / "过程文件"


def get_task_result_dir(task_id: str) -> Path:
    """指定任务的结果文件目录：sandbox/task_id/结果文件。"""
    return get_task_dir(task_id) / "结果文件"


def ensure_task_dirs(task_id: str) -> Path:
    """确保任务目录、过程文件目录和结果文件目录存在，返回过程文件目录。"""
    process_dir = get_task_process_dir(task_id)
    result_dir = get_task_result_dir(task_id)
    process_dir.mkdir(parents=True, exist_ok=True)
    result_dir.mkdir(parents=True, exist_ok=True)
    return process_dir


def get_memory_dir() -> Path:
    """Memory 目录：项目根/memory，用于存储会话记忆。"""
    return get_project_root() / "memory"


def get_stm_dir() -> Path:
    """STM（短期记忆）目录：项目根/memory/STM，用于存储会话数据。"""
    return get_memory_dir() / "STM"


def ensure_memory_dirs() -> Path:
    """确保 memory 和 STM 目录存在，返回 STM 目录。"""
    stm_dir = get_stm_dir()
    stm_dir.mkdir(parents=True, exist_ok=True)
    return stm_dir


def ensure_task_readable_alias(task_id: str, user_query: str) -> None:
    """
    在 sandbox 下为任务目录创建可读别名（相对路径符号链接或占位文件）。
    任意失败静默忽略，不影响主流程。
    """
    import re
    from datetime import datetime

    if not task_id:
        return
    sandbox = get_sandbox_dir()
    tgt = sandbox / task_id
    if not tgt.is_dir():
        return
    q = (user_query or "").strip()
    slug = re.sub(r"[^\w\u4e00-\u9fff]+", "_", q[:48]).strip("_") or "task"
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    alias = sandbox / f"{stamp}_{slug}"
    if alias.exists():
        return
    try:
        alias.symlink_to(task_id, target_is_directory=True)
    except OSError:
        try:
            alias.mkdir(parents=True, exist_ok=True)
            marker = alias / ".sona_task_id.txt"
            marker.write_text(task_id, encoding="utf-8")
        except Exception:
            return
