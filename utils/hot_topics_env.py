"""热点流程（hottopics）与 Sona 统一环境：加载 .env 并映射 API Key 到 INSIGHT_/QUERY_ 变量。"""

from __future__ import annotations

import os
from typing import Optional

import yaml

from utils.env_loader import get_env_config
from utils.path import get_config_path, get_project_root


def _set_if_absent(key: str, value: Optional[str]) -> None:
    if value and key not in os.environ:
        os.environ[key] = value


def prepare_hot_topics_environment() -> None:
    """
    在导入或运行 tools.hottopics 之前调用。

    1. 通过 EnvConfig 加载项目根目录 .env（与主 Agent 一致）。
    2. 将 Sona 使用的变量名映射到 hottopics 内 InsightNode / ForumNode 所需的
       INSIGHT_ENGINE_*、QUERY_ENGINE_*（OpenAI 兼容接口）。

    优先级（仅当对应 INSIGHT_/QUERY_ 未显式设置时填充）：
    - KIMI_API_KEY / KIMI_APIKEY → Moonshot
    - OPENAI_API_KEY / OPENAI_APIKEY → OpenAI 官方
    - DEEPSEEK_APIKEY → DeepSeek
    """
    env = get_env_config()

    kimi = os.environ.get("KIMI_API_KEY") or os.environ.get("KIMI_APIKEY")
    openai = os.environ.get("OPENAI_API_KEY") or os.environ.get("OPENAI_APIKEY")
    deepseek = os.environ.get("DEEPSEEK_APIKEY") or os.environ.get("DEEPSEEK_API_KEY")

    moonshot_base = "https://api.moonshot.cn/v1"
    moonshot_model = os.environ.get("KIMI_MODEL_NAME") or os.environ.get("KIMI_MODEL") or "moonshot-v1-8k"

    # 优先：从 config/model.yaml 读取 tools profile（在 hottopics 里使用 ChatOpenAI，所以需要 OpenAI compatible base_url）
    if not os.environ.get("INSIGHT_ENGINE_API_KEY"):
        model_cfg_path = get_config_path("model.yaml")
        if model_cfg_path.exists():
            try:
                with open(model_cfg_path, "r", encoding="utf-8") as f:
                    model_cfg = yaml.safe_load(f) or {}

                tools_block = model_cfg.get("tools") if isinstance(model_cfg.get("tools"), dict) else None
                if not tools_block:
                    tools_block = model_cfg.get("main") if isinstance(model_cfg.get("main"), dict) else None

                if tools_block:
                    provider = str(tools_block.get("provider") or "").lower()
                    api_key_env = str(tools_block.get("api_key_env") or "").strip()
                    api_key = env.get_api_key(api_key_env) if api_key_env else None

                    # 仅对 OpenAI-Compatible 的 provider 使用 ChatOpenAI
                    if provider in {"qwen", "openai", "deepseek", "dashscope", "kimi"} and api_key:
                        base_url = str(tools_block.get("base_url") or "").strip()
                        model_name = str(tools_block.get("model") or "").strip()

                        _set_if_absent("INSIGHT_ENGINE_API_KEY", api_key)

                        # 强制：Qwen coding plan 必须使用指定 base_url，确保走 coding plan 额度
                        if provider == "qwen":
                            _set_if_absent("INSIGHT_ENGINE_BASE_URL", "https://coding.dashscope.aliyuncs.com/v1")
                        elif base_url:
                            _set_if_absent("INSIGHT_ENGINE_BASE_URL", base_url)

                        if model_name:
                            _set_if_absent("INSIGHT_ENGINE_MODEL_NAME", model_name)
            except Exception:
                # 读配置失败时走下方的简单兜底逻辑
                pass

    # 兜底：如果仍未设置，再用 KIMI/OPENAI/DEEPSEEK 的旧映射规则
    if not os.environ.get("INSIGHT_ENGINE_API_KEY"):
        if kimi:
            _set_if_absent("INSIGHT_ENGINE_API_KEY", kimi)
            _set_if_absent("INSIGHT_ENGINE_BASE_URL", os.environ.get("KIMI_BASE_URL") or moonshot_base)
            _set_if_absent("INSIGHT_ENGINE_MODEL_NAME", moonshot_model)
        elif openai:
            _set_if_absent("INSIGHT_ENGINE_API_KEY", openai)
            _set_if_absent("INSIGHT_ENGINE_BASE_URL", os.environ.get("OPENAI_BASE_URL") or "https://api.openai.com/v1")
            _set_if_absent(
                "INSIGHT_ENGINE_MODEL_NAME",
                os.environ.get("OPENAI_MODEL") or os.environ.get("INSIGHT_ENGINE_MODEL_NAME") or "gpt-4o-mini",
            )
        elif deepseek:
            _set_if_absent("INSIGHT_ENGINE_API_KEY", deepseek)
            _set_if_absent("INSIGHT_ENGINE_BASE_URL", "https://api.deepseek.com/v1")
            _set_if_absent(
                "INSIGHT_ENGINE_MODEL_NAME",
                os.environ.get("DEEPSEEK_MODEL") or "deepseek-chat",
            )

    # QUERY_NODE 使用同一套 key/base_url/model（避免用户重复配置）
    if not os.environ.get("QUERY_ENGINE_API_KEY") and os.environ.get("INSIGHT_ENGINE_API_KEY"):
        _set_if_absent("QUERY_ENGINE_API_KEY", os.environ["INSIGHT_ENGINE_API_KEY"])
        _set_if_absent("QUERY_ENGINE_BASE_URL", os.environ.get("INSIGHT_ENGINE_BASE_URL", moonshot_base))
        _set_if_absent("QUERY_ENGINE_MODEL_NAME", os.environ.get("INSIGHT_ENGINE_MODEL_NAME", moonshot_model))

    # 若用户只配置了 QWEN_APIKEY（但 model.yaml 不可读），仍强制 base_url 为 coding plan
    if os.environ.get("QWEN_APIKEY") and not os.environ.get("INSIGHT_ENGINE_BASE_URL"):
        _set_if_absent("INSIGHT_ENGINE_BASE_URL", "https://coding.dashscope.aliyuncs.com/v1")
    if os.environ.get("QWEN_APIKEY") and not os.environ.get("QUERY_ENGINE_BASE_URL"):
        _set_if_absent("QUERY_ENGINE_BASE_URL", "https://coding.dashscope.aliyuncs.com/v1")


def ensure_hot_topics_cwd() -> None:
    """将工作目录设为项目根，保证 output_langgraph、data_langgraph 写在仓库根目录。"""
    root = get_project_root()
    try:
        os.chdir(root)
    except OSError:
        pass
