"""从 .env 加载环境变量（原 config/settings 逻辑），供 model/factory 等使用。"""

from __future__ import annotations

import os
from typing import Optional
from dotenv import load_dotenv

from utils.path import get_project_root


def _load_dotenv() -> None:
    try:
        root = get_project_root()
        env_file = root / ".env"
        if env_file.exists():
            load_dotenv(env_file)
        else:
            load_dotenv()
    except ImportError:
        pass


_env_config: Optional["EnvConfig"] = None


class EnvConfig:
    """从 .env 读取的环境配置"""

    def __init__(self) -> None:
        _load_dotenv()
        # 统一使用 APIKEY（按 openai / gemini / qwen / deepseek 顺序）
        self.OPENAI_APIKEY: Optional[str] = os.environ.get("OPENAI_APIKEY") or None
        self.GEMINI_APIKEY: Optional[str] = os.environ.get("GEMINI_APIKEY") or None
        self.QWEN_APIKEY: Optional[str] = os.environ.get("QWEN_APIKEY") or None
        self.DASHSCOPE_APIKEY: Optional[str] = os.environ.get("DASHSCOPE_APIKEY") or None
        self.DEEPSEEK_APIKEY: Optional[str] = os.environ.get("DEEPSEEK_APIKEY") or None
        self.KIMI_APIKEY: Optional[str] = os.environ.get("KIMI_APIKEY") or None
        self.BOCHA_API_KEY: Optional[str] = os.environ.get("BOCHA_API_KEY") or None
        self.NETINSIGHT_USER: Optional[str] = os.environ.get("NETINSIGHT_USER") or None
        self.NETINSIGHT_PASS: Optional[str] = os.environ.get("NETINSIGHT_PASS") or None

    def get_api_key(self, env_var_name: str) -> Optional[str]:
        """根据配置中的 api_key_env 取对应 API Key。"""
        return os.environ.get(env_var_name) or getattr(self, env_var_name, None)


def get_env_config() -> EnvConfig:
    """获取单例 EnvConfig，首次调用时加载 .env。"""
    global _env_config
    if _env_config is None:
        _env_config = EnvConfig()
    return _env_config


def reload_env_config() -> EnvConfig:
    """丢弃缓存并重新从当前 ``os.environ`` 构建配置（供在 ``load_dotenv(override=True)`` 之后使用）。"""
    global _env_config
    _env_config = None
    return get_env_config()
