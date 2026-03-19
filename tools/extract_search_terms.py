"""从 query 中提取事件简介、搜索关键词以及事件范围，先进行初步搜索获取相关资料，再生成检索配置。"""

from __future__ import annotations

import json
from datetime import datetime, timedelta

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.tools import tool

from mcps.web_search import bocha_ai_search
from model.factory import get_tools_model
from utils.date_utils import get_today_str, get_yesterday_end
from utils.prompt_loader import get_extract_search_terms_prompt


def _extract_related_materials(search_result: dict) -> str:
    """
    从搜索结果中提取相关资料
    
    Args:
        search_result: 搜索结果字典
    
    Returns:
        拼接后的相关资料文本
    """
    materials = []
    
    # 检查响应结构
    if search_result.get("code") != 200:
        return "搜索失败，无法获取相关资料"
    
    data = search_result.get("data", {})
    web_pages = data.get("webPages", {})
    value = web_pages.get("value", [])
    
    if not value:
        return "未找到相关搜索结果"
    
    # 提取所有 summary 和 datePublished
    for item in value:
        summary = item.get("summary", "").strip()
        date_published = item.get("datePublished", "").strip()
        
        if summary:
            material = f"摘要：{summary}"
            if date_published:
                material += f"\n发布时间：{date_published}"
            materials.append(material)
    
    if not materials:
        return "搜索结果中未包含摘要信息"
    
    return "\n\n".join(materials)


@tool
def extract_search_terms(query: str) -> str:
    """
    描述：从用户自然语言 query 中提取用于舆情检索的搜索关键词和配置，包括事件介绍、检索词、时间范围等。
    使用时机：当用户提出舆情分析、热点追踪、话题监测等需求时，应优先调用本工具，从用户的描述中提炼出检索配置，再基于这些配置调用搜索等工具。
    输入：query（必填），即用户的原始提问或需求描述，例如「最近一周某品牌在社交平台上的负面声音」等。
    输出：一个 JSON 字符串，格式为 {
        "eventIntroduction": "事件介绍...",
        "searchWords": ["关键词1", "关键词2"等],
        "timeRange": "2026-01-01 00:00:00;2026-01-31 23:59:59"
    }。
    """
    # 进行初步搜索
    try:
        search_result = bocha_ai_search(
            query=query,
            summary=True,
            count=20,
            freshness="oneWeek"
        )
        
        # 提取相关资料
        related_materials = _extract_related_materials(search_result)
        
    except Exception as e:
        # 如果搜索失败，使用默认值
        related_materials = f"搜索失败：{str(e)}"
    
    # 获取当前时间和昨天最后时间（用于指导模型计算时间范围）
    current_time = get_today_str("%Y年%m月%d日")
    yesterday_end = get_yesterday_end()
    yesterday_end_str = yesterday_end.strftime("%Y-%m-%d %H:%M:%S")
    
    # 构建输入内容
    prompt = get_extract_search_terms_prompt()
    if not prompt:
        # 如果 prompt 不存在，返回默认配置（时间范围需要模型生成）
        yesterday_end = get_yesterday_end()
        default_time_range = f"{(yesterday_end - timedelta(days=30)).strftime('%Y-%m-%d %H:%M:%S')};{yesterday_end.strftime('%Y-%m-%d %H:%M:%S')}"
        return json.dumps({
            "eventIntroduction": "",
            "searchWords": [],
            "timeRange": default_time_range
        }, ensure_ascii=False)
    
    # 构建用户输入，包含 query、相关资料、当前时间等信息
    user_content = f"""用户查询：{query.strip() or "未提供内容"}

    相关资料：
    {related_materials}

    当前时间：{current_time}
    搜索时间范围的结束时间应为：{yesterday_end_str}（昨天最后一刻）

    请仔细阅读相关资料，识别事件是什么时候发生的，然后根据事件发生时间计算合适的搜索时间范围。"""
    
    # 4. 调用模型生成配置
    llm = get_tools_model()
    messages = [
        SystemMessage(content=prompt),
        HumanMessage(content=user_content),
    ]
    
    try:
        response = llm.invoke(messages)
        content = getattr(response, "content", "") or str(response)
        result = (content or "").strip()
        
        # 尝试解析 JSON，如果失败则返回默认值
        try:
            parsed = json.loads(result)
            # 确保包含所有必需字段
            if not isinstance(parsed, dict):
                raise ValueError("返回的不是字典格式")
            
            # 设置默认值（如果模型没有生成时间范围，使用默认值）
            if "timeRange" not in parsed or not parsed.get("timeRange"):
                yesterday_end = get_yesterday_end()
                default_time_range = f"{(yesterday_end - timedelta(days=30)).strftime('%Y-%m-%d %H:%M:%S')};{yesterday_end.strftime('%Y-%m-%d %H:%M:%S')}"
                parsed["timeRange"] = default_time_range
            
            # 移除不需要的字段
            parsed.pop("num", None)
            parsed.pop("groupName", None)
            
            return json.dumps(parsed, ensure_ascii=False)
        except (json.JSONDecodeError, ValueError):
            # 如果解析失败，返回默认配置
            yesterday_end = get_yesterday_end()
            default_time_range = f"{(yesterday_end - timedelta(days=30)).strftime('%Y-%m-%d %H:%M:%S')};{yesterday_end.strftime('%Y-%m-%d %H:%M:%S')}"
            return json.dumps({
                "eventIntroduction": result[:200] if result else "",
                "searchWords": [],
                "timeRange": default_time_range
            }, ensure_ascii=False)
    except Exception as e:
        # 如果模型调用失败，返回默认配置
        yesterday_end = get_yesterday_end()
        default_time_range = f"{(yesterday_end - timedelta(days=30)).strftime('%Y-%m-%d %H:%M:%S')};{yesterday_end.strftime('%Y-%m-%d %H:%M:%S')}"
        return json.dumps({
            "eventIntroduction": f"处理失败：{str(e)}",
            "searchWords": [],
            "timeRange": default_time_range
        }, ensure_ascii=False)
