"""网页搜索工具，使用BoCha AI Search API"""

from __future__ import annotations

import json
import os
from typing import Any, Dict

import requests

from utils.env_loader import get_env_config

# BoCha AI Search API 配置
BOCHA_API_URL = "https://api.bocha.cn/v1/web-search"
BOCHA_API_KEY_ENV = "BOCHA_API_KEY"


def _get_api_key() -> str:
    """从环境变量获取 BoCha API Key"""
    env = get_env_config()
    api_key = env.get_api_key(BOCHA_API_KEY_ENV) or os.environ.get(BOCHA_API_KEY_ENV)
    if not api_key:
        raise ValueError(f"缺少 {BOCHA_API_KEY_ENV}，请在 .env 中配置")
    return api_key


def bocha_ai_search(
    query: str,
    summary: bool = True,
    count: int = 20,
    freshness: str = "oneWeek",
) -> Dict[str, Any]:
    """
    调用 BoCha AI Search API 进行网页搜索

    Args:
        query: 搜索查询关键词
        summary: 是否返回摘要，默认 True
        count: 返回结果数量，默认 10
        freshness: 搜索指定时间范围内的网页，默认 "noLimit"。
            可选值：
            - "noLimit": 不限（默认，推荐使用）
            - "oneDay": 一天内
            - "oneWeek": 一周内
            - "oneMonth": 一个月内
            - "oneYear": 一年内
            - "YYYY-MM-DD..YYYY-MM-DD": 搜索日期范围，例如 "2025-01-01..2025-04-06"
            - "YYYY-MM-DD": 搜索指定日期，例如 "2025-04-06"

    Returns:
        API 返回的 JSON 结果字典
    """
    # 获取 API Key
    api_key = _get_api_key()
    
    # 构建请求
    payload = json.dumps({
        "query": query,
        "summary": summary,
        "count": count,
        "freshness": freshness
    })
    
    headers = {
        'Authorization': f'Bearer {api_key}',
        'Content-Type': 'application/json'
    }
    
    # 发送请求
    try:
        response = requests.post(BOCHA_API_URL, headers=headers, data=payload, timeout=30)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        error_msg = f"搜索请求失败: {str(e)}"
        if hasattr(e, 'response') and e.response is not None:
            try:
                error_detail = e.response.json()
                error_msg += f" - {json.dumps(error_detail, ensure_ascii=False)}"
            except:
                error_msg += f" - {e.response.text}"
        return {"error": error_msg}
    except json.JSONDecodeError as e:
        return {"error": f"响应解析失败: {str(e)}"}