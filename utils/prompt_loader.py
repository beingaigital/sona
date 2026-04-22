"""从 config/prompt.yaml 加载映射，prompt 文件从 sona/prompt 目录读取；并支持将工具注册表载入 system_prompt。"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

import yaml

from utils.path import get_config_path, get_prompt_dir
from utils.policy_docs import format_harness_policy_for_prompt


def _load_prompt_yaml() -> Dict[str, Any]:
    """读取 config/prompt.yaml"""
    path = get_config_path("prompt.yaml")
    if not path.exists():
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _resolve_value(value: Any, prompt_dir: Path) -> str:
    """若 value 为相对路径则从 prompt 目录读取文件内容，否则返回字符串"""
    if value is None:
        return ""
    s = str(value).strip()
    if not s:
        return ""
    if "/" in s or "\\" in s or not s.startswith("http"):
        file_path = (prompt_dir / s).resolve()
        if file_path.is_file():
            with open(file_path, "r", encoding="utf-8") as f:
                return f.read().strip()
        return s
    return s


def get_prompt_config() -> Dict[str, str]:
    """
    加载 prompt 配置。从 config/prompt.yaml 读取映射，值为文件路径时
    返回示例：{"system_prompt": "你是舆情分析专家..."}
    """
    raw = _load_prompt_yaml()
    prompt_dir = get_prompt_dir()
    result: Dict[str, str] = {}
    for key, value in raw.items():
        if isinstance(value, str) or value is None:
            result[key] = _resolve_value(value, prompt_dir)
        elif isinstance(value, dict):
            result[key] = str(value)
        else:
            result[key] = str(value)
    return result


def get_system_prompt() -> str:
    """获取 system_prompt 正文，供 agent 使用"""
    return get_prompt_config().get("system_prompt", "").strip()


def get_extract_search_terms_prompt() -> str:
    """获取「从 query 提取搜索词」任务使用的 prompt，供 extract 模型使用"""
    return get_prompt_config().get("extract_search_terms_prompt", "").strip()


def get_analysis_timeline_prompt() -> str:
    """获取「事件时间线分析」任务使用的 prompt，供 analysis 模型使用"""
    return get_prompt_config().get("analysis_timeline_prompt", "").strip()


def get_analysis_sentiment_prompt() -> str:
    """获取「情感倾向分析」任务使用的 prompt，供 analysis 模型使用"""
    return get_prompt_config().get("analysis_sentiment_prompt", "").strip()


def get_report_html_prompt() -> str:
    """获取「HTML报告生成」任务使用的 prompt，供 report 模型使用"""
    return get_prompt_config().get("report_html_prompt", "").strip()


def get_report_html_template_basename() -> str:
    """
    固定 HTML 报告模板文件名（位于 prompt/ 下）。
    注意：不能走 get_prompt_config()，否则会把 .html 文件内容当作「配置字符串」读入。
    """
    raw = _load_prompt_yaml()
    v = raw.get("report_html_template")
    if v is None:
        return ""
    return str(v).strip()


def get_interpretation_prompt() -> str:
    """获取「解释与研判 JSON」任务使用的 prompt，供 interpretation 模型使用"""
    return get_prompt_config().get("interpretation_prompt", "").strip()


def format_tool_registry_for_prompt(tools: List[Any]) -> str:
    """根据当前注册的工具列表生成「可用的工具列表及描述」段落，用于拼接到 system_prompt"""
    if not tools:
        return ""
    lines = ["## 当前可用工具", ""]
    for t in tools:
        name = getattr(t, "name", None) or getattr(t, "__class__", type(t)).__name__
        desc = getattr(t, "description", "") or ""
        desc_one = desc.strip().replace("\n", " ").strip()[:400]
        lines.append(f"- **{name}**：{desc_one}")
    return "\n".join(lines)


def get_system_prompt_with_tools(tools: List[Any]) -> str:
    """获取 system_prompt 正文，并追加系统策略与当前工具注册表信息。"""
    base = get_system_prompt()
    policy = format_harness_policy_for_prompt()
    section = format_tool_registry_for_prompt(tools)
    parts = [base.strip()]
    if policy:
        parts.append(policy.strip())
    if section:
        parts.append(section.strip())
    return "\n\n".join([p for p in parts if p]).strip()
