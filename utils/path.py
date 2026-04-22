"""统一项目路径，供配置、数据等模块确定文件位置。"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

# 项目根目录
_PROJECT_ROOT: Path | None = None


def get_project_root() -> Path:
    """返回项目根目录（sona 包所在目录）。"""
    global _PROJECT_ROOT
    if _PROJECT_ROOT is None:
        _PROJECT_ROOT = Path(__file__).resolve().parent.parent
    return _PROJECT_ROOT


# 历史目录名为中文，曾用全仓库字符串替换迁移；此处用码位拼接避免再被误批量改写。
_LEGACY_OPINION_KB_DIR = "".join(chr(c) for c in (0x8206, 0x60C5, 0x6DF1, 0x5EA6, 0x5206, 0x6790))


def get_opinion_analysis_kb_root(project_root: Path | None = None) -> Path:
    """
    项目根下「舆情分析知识库」根目录（英文文件夹 ``opinion_analysis_kb``，内含 references/ 等）。

    若仍存在旧的中文目录名（见 ``_LEGACY_OPINION_KB_DIR``），则优先新目录，否则回退旧目录。
    """
    root = project_root if project_root is not None else get_project_root()
    new_p = root / "opinion_analysis_kb"
    legacy = root / _LEGACY_OPINION_KB_DIR
    if new_p.is_dir():
        return new_p
    if legacy.is_dir():
        return legacy
    return new_p


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


def _clean_event_label(text: str, max_len: int = 18) -> str:
    s = str(text or "").strip()
    if not s:
        return "事件"
    # 保留中英文数字，去掉其它符号
    s = re.sub(r"[^\w\u4e00-\u9fff]+", "", s)
    # 去掉常见指令词
    for w in ("请帮我", "帮我", "请", "分析", "舆情", "事件", "报告", "一下", "给出", "概述"):
        s = s.replace(w, "")
    s = s.strip() or "事件"
    return s[:max_len]


def ensure_task_readable_alias(task_id: str, event_text: str) -> Path:
    """
    在 sandbox 下创建“中文可读别名目录”（优先软链接到真实 task 目录）。
    例：20260414高铁骂熊孩子舆情事件分析_任务ab12cd34
    """
    sandbox = get_sandbox_dir()
    task_dir = get_task_dir(task_id)
    date_prefix = datetime.now().strftime("%Y%m%d")
    label = _clean_event_label(event_text)
    alias_name = f"{date_prefix}{label}舆情事件分析_任务{task_id[:8]}"
    alias_path = sandbox / alias_name

    # 已存在则直接返回
    if alias_path.exists():
        return alias_path

    try:
        # 优先创建软链接，避免重复占用空间
        alias_path.symlink_to(task_dir, target_is_directory=True)
        return alias_path
    except Exception:
        # 回退：创建目录并写入指向说明
        alias_path.mkdir(parents=True, exist_ok=True)
        pointer = alias_path / "README_任务目录指引.txt"
        pointer.write_text(
            f"该目录为可读别名。\n真实任务目录：{task_dir}\n任务ID：{task_id}\n",
            encoding="utf-8",
        )
        return alias_path


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
