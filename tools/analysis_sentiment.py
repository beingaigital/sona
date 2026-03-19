"""情感倾向分析工具：分析舆情数据中的情感倾向信息。"""

from __future__ import annotations

import csv
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.tools import tool

from model.factory import get_tools_model
from utils.path import ensure_task_dirs, get_task_process_dir
from utils.prompt_loader import get_analysis_sentiment_prompt
from utils.task_context import get_task_id


def _read_csv_data(file_path: str) -> List[Dict[str, Any]]:
    """读取CSV文件数据"""
    file = Path(file_path)
    if not file.exists():
        raise FileNotFoundError(f"数据文件不存在: {file_path}")
    
    data = []
    with open(file, 'r', encoding='utf-8-sig', errors='replace') as f:
        reader = csv.DictReader(f)
        for row in reader:
            data.append(row)
    
    return data


def _identify_sentiment_column(data: List[Dict[str, Any]]) -> Optional[str]:
    """识别情感列"""
    if not data:
        return None
    
    # 可能的情感列名
    sentiment_candidates = [
        "情感", "情感倾向", "情感分析", "sentiment", "emotion",
        "情感分类", "情感标签", "情感类型"
    ]
    
    # 获取所有列名
    columns = list(data[0].keys())
    
    # 查找情感列
    for col in columns:
        if any(candidate in col for candidate in sentiment_candidates):
            return col
    
    return None


def _identify_content_column(data: List[Dict[str, Any]]) -> Optional[str]:
    """识别内容列"""
    if not data:
        return None
    
    # 可能的内容列名
    content_candidates = ["内容", "content", "正文", "text", "摘要", "abstract"]
    
    # 获取所有列名
    columns = list(data[0].keys())
    
    # 查找内容列
    for col in columns:
        if any(candidate in col for candidate in content_candidates):
            return col
    
    return None


def _normalize_sentiment(sentiment: str) -> str:
    """标准化情感值"""
    sentiment = str(sentiment).strip()
    
    # 映射到标准值
    sentiment_map = {
        "正面": "正面",
        "积极": "正面",
        "positive": "正面",
        "1": "正面",
        "负面": "负面",
        "消极": "负面",
        "negative": "负面",
        "-1": "负面",
        "中性": "中性",
        "neutral": "中性",
        "0": "中性",
    }
    
    return sentiment_map.get(sentiment.lower(), sentiment)


def _calculate_sentiment_statistics(data: List[Dict[str, Any]], sentiment_col: str) -> Dict[str, Any]:
    """计算情感统计信息"""
    total = len(data)
    if total == 0:
        return {
            "total": 0,
            "positive_count": 0,
            "negative_count": 0,
            "neutral_count": 0,
            "positive_ratio": 0.0,
            "negative_ratio": 0.0,
            "neutral_ratio": 0.0
        }
    
    positive_count = 0
    negative_count = 0
    neutral_count = 0
    
    for row in data:
        sentiment = _normalize_sentiment(row.get(sentiment_col, ""))
        if sentiment == "正面":
            positive_count += 1
        elif sentiment == "负面":
            negative_count += 1
        else:
            neutral_count += 1
    
    return {
        "total": total,
        "positive_count": positive_count,
        "negative_count": negative_count,
        "neutral_count": neutral_count,
        "positive_ratio": round(positive_count / total, 4) if total > 0 else 0.0,
        "negative_ratio": round(negative_count / total, 4) if total > 0 else 0.0,
        "neutral_ratio": round(neutral_count / total, 4) if total > 0 else 0.0
    }


def _extract_content_by_sentiment(
    data: List[Dict[str, Any]],
    sentiment_col: str,
    content_col: str,
    sentiment_type: str,
    limit: int = 10
) -> List[str]:
    """按情感类型提取内容，按字数排序"""
    if not content_col:
        return []
    
    contents = []
    for row in data:
        sentiment = _normalize_sentiment(row.get(sentiment_col, ""))
        if sentiment == sentiment_type:
            content = str(row.get(content_col, "")).strip()
            if content:
                contents.append(content)
    
    # 按字数排序（从长到短）
    contents.sort(key=len, reverse=True)
    
    return contents[:limit]


def _generate_result_filename(retryContext: Optional[str] = None) -> str:
    """生成结果文件名，如果是重试则添加后缀"""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    base_name = f"sentiment_analysis_{timestamp}"
    
    # 如果是重试，需要检查已存在的文件并添加后缀
    if retryContext:
        task_id = get_task_id()
        if task_id:
            process_dir = get_task_process_dir(task_id)
            if process_dir.exists():
                # 查找所有 sentiment_analysis_ 开头的 JSON 文件
                existing_files = list(process_dir.glob("sentiment_analysis_*.json"))
                if existing_files:
                    # 提取已有的后缀编号
                    suffix_nums = []
                    for file in existing_files:
                        # 匹配 sentiment_analysis_时间戳_数字.json 格式
                        match = re.search(r"sentiment_analysis_\d{8}_\d{6}_(\d+)\.json", file.name)
                        if match:
                            suffix_nums.append(int(match.group(1)))
                    # 如果没有找到带后缀的，说明是第一次重试，使用 _1
                    if not suffix_nums:
                        return f"{base_name}_1.json"
                    # 否则使用最大编号 + 1
                    return f"{base_name}_{max(suffix_nums) + 1}.json"
                else:
                    # 如果没找到任何文件，说明是第一次重试，使用 _1
                    return f"{base_name}_1.json"
    
    return f"{base_name}.json"


@tool
def analysis_sentiment(
    eventIntroduction: str,
    dataFilePath: str,
    retryContext: Optional[str] = None
) -> str:
    """
    描述：分析情感倾向。根据提供的事件基础介绍和数据文件，从舆情数据中分析情感倾向，统计占比并总结主要观点。一般均可使用本工具。
    使用时机：当需要分析舆情数据的情感倾向时调用本工具。
    输入：
    - eventIntroduction（必填）：事件基础介绍，由 extract_search_terms 工具生成，用于告知模型事件背景，避免分析跑偏。
    - dataFilePath（必填）：数据文件位置，数据爬取后保存的CSV文件路径，需要从data_collect工具返回的JSON结果中提取。
    - retryContext（可选，默认None）：重试机制参数。第一次调用时不使用，当后续用户有调整意见时，填入之前的结果及修改建议，格式为JSON字符串，例如 '{"previous_result": "...", "suggestions": "..."}'。
    输出：JSON字符串，包含以下字段：
    - statistics：情感统计信息（总数、各类型数量、占比）
    - positive_summary：正面观点总结（最多3条，如果负面占主导则可能为空）
    - negative_summary：负面观点总结（最多3条，如果正面占主导则可能为空）
    - result_file_path：结果文件保存路径（保存在任务的过程文件夹中，JSON格式）
    注意：如果是多次调用（重试），文件名会自动添加后缀（_1, _2等）。
    """
    import json as json_module
    
    # 解析重试上下文
    previous_result = None
    suggestions = None
    if retryContext:
        try:
            retry_data = json_module.loads(retryContext) if isinstance(retryContext, str) else retryContext
            previous_result = retry_data.get("previous_result")
            suggestions = retry_data.get("suggestions")
        except Exception:
            pass
    
    # 读取数据文件
    try:
        all_data = _read_csv_data(dataFilePath)
    except Exception as e:
        return json_module.dumps({
            "error": f"读取数据文件失败: {str(e)}",
            "statistics": {},
            "positive_summary": [],
            "negative_summary": [],
            "result_file_path": ""
        }, ensure_ascii=False)
    
    if not all_data:
        return json_module.dumps({
            "error": "数据文件为空",
            "statistics": {},
            "positive_summary": [],
            "negative_summary": [],
            "result_file_path": ""
        }, ensure_ascii=False)
    
    # 识别情感列和内容列
    sentiment_col = _identify_sentiment_column(all_data)
    content_col = _identify_content_column(all_data)
    
    if not sentiment_col:
        return json_module.dumps({
            "error": "无法识别情感列，请确保CSV文件包含'情感'或'sentiment'列",
            "statistics": {},
            "positive_summary": [],
            "negative_summary": [],
            "result_file_path": ""
        }, ensure_ascii=False)
    
    if not content_col:
        return json_module.dumps({
            "error": "无法识别内容列，请确保CSV文件包含'内容'或'content'列",
            "statistics": {},
            "positive_summary": [],
            "negative_summary": [],
            "result_file_path": ""
        }, ensure_ascii=False)
    
    # 计算情感统计
    statistics = _calculate_sentiment_statistics(all_data, sentiment_col)
    
    # 提取正面和负面内容
    positive_contents = _extract_content_by_sentiment(
        all_data, sentiment_col, content_col, "正面", limit=10
    )
    negative_contents = _extract_content_by_sentiment(
        all_data, sentiment_col, content_col, "负面", limit=10
    )
    
    # 判断是否需要分析正面和负面观点
    positive_ratio = statistics.get("positive_ratio", 0.0)
    negative_ratio = statistics.get("negative_ratio", 0.0)
    
    # 灵活判断：如果负面占主导（>60%），则无需正面观点；如果正面占主导（>60%），则无需负面观点
    need_positive = positive_ratio > 0.1 and negative_ratio < 0.6
    need_negative = negative_ratio > 0.1 and positive_ratio < 0.6
    
    # 如果均衡，则都给出
    if abs(positive_ratio - negative_ratio) < 0.2:
        need_positive = positive_ratio > 0.1
        need_negative = negative_ratio > 0.1
    
    # 获取分析模型和prompt
    try:
        model = get_tools_model()
        prompt_template = get_analysis_sentiment_prompt()
    except Exception as e:
        return json_module.dumps({
            "error": f"获取分析模型失败: {str(e)}",
            "statistics": statistics,
            "positive_summary": [],
            "negative_summary": [],
            "result_file_path": ""
        }, ensure_ascii=False)
    
    # 构建提示词
    retry_section = "无（首次分析）" if not previous_result else previous_result
    suggestions_section = "无" if not suggestions else suggestions
    
    prompt = prompt_template.format(
        event_introduction=eventIntroduction,
        statistics=json_module.dumps(statistics, ensure_ascii=False, indent=2),
        positive_contents="\n\n".join(positive_contents[:10]) if need_positive and positive_contents else "无",
        negative_contents="\n\n".join(negative_contents[:10]) if need_negative and negative_contents else "无",
        need_positive="是" if need_positive else "否",
        need_negative="是" if need_negative else "否",
        previous_result=retry_section,
        suggestions=suggestions_section
    )
    
    # 调用模型进行分析
    try:
        messages = [
            SystemMessage(content="你是一个专业的情感倾向分析专家。"),
            HumanMessage(content=prompt)
        ]
        response = model.invoke(messages)
        result_text = response.content if hasattr(response, 'content') else str(response)
    except Exception as e:
        return json_module.dumps({
            "error": f"模型分析失败: {str(e)}",
            "statistics": statistics,
            "positive_summary": [],
            "negative_summary": [],
            "result_file_path": ""
        }, ensure_ascii=False)
    
    # 尝试解析JSON结果
    try:
        # 尝试提取JSON部分
        json_match = re.search(r'\{[\s\S]*\}', result_text)
        if json_match:
            result_json = json_module.loads(json_match.group())
        else:
            result_json = json_module.loads(result_text)
    except Exception:
        return json_module.dumps({
            "error": "模型返回结果格式不正确",
            "raw_result": result_text,
            "statistics": statistics,
            "positive_summary": [],
            "negative_summary": [],
            "result_file_path": ""
        }, ensure_ascii=False)
    
    # 验证返回结果格式
    if not isinstance(result_json, dict):
        return json_module.dumps({
            "error": "模型返回结果格式不正确",
            "raw_result": result_text,
            "statistics": statistics,
            "positive_summary": [],
            "negative_summary": [],
            "result_file_path": ""
        }, ensure_ascii=False)
    
    # 确保包含必需字段
    result = {
        "statistics": statistics,
        "positive_summary": result_json.get("positive_summary", []),
        "negative_summary": result_json.get("negative_summary", []),
        "raw_result": result_text if "error" in result_json else None
    }
    
    # 获取任务ID并保存结果文件
    task_id = get_task_id()
    result_file_path = ""
    
    if task_id:
        try:
            # 确保任务目录存在
            process_dir = ensure_task_dirs(task_id)
            
            # 生成文件名
            filename = _generate_result_filename(retryContext)
            result_file = process_dir / filename
            
            result_file_path = str(result_file)
            
            # 在保存前添加文件路径到结果中
            result["result_file_path"] = result_file_path
            
            # 保存JSON文件
            with open(result_file, 'w', encoding='utf-8', errors='replace') as f:
                json_module.dump(result, f, ensure_ascii=False, indent=2)
        except Exception as e:
            result["save_error"] = f"保存结果文件失败: {str(e)}"
            result["result_file_path"] = ""
    else:
        result["save_error"] = "未找到任务ID，无法保存结果文件"
        result["result_file_path"] = ""
    
    return json_module.dumps(result, ensure_ascii=False)
