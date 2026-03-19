"""事件时间线分析工具：分析舆情数据中的时间线信息。"""

from __future__ import annotations

import csv
import re
import json as json_module
from pathlib import Path
from typing import Any, Dict, List, Optional

from datetime import datetime
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.tools import tool

from model.factory import get_tools_model
from utils.path import ensure_task_dirs
from utils.prompt_loader import get_analysis_timeline_prompt
from utils.task_context import get_task_id
from utils.path import get_task_process_dir

# 时间相关词表（用于初步筛选包含时间信息的内容）
TIME_KEYWORDS = [
    "年", "月", "日", "时", "分", "秒",
    "今天", "昨天", "前天", "明天", "后天",
    "周一", "周二", "周三", "周四", "周五", "周六", "周日",
    "星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日",
    "上午", "下午", "晚上", "凌晨", "中午",
    "月初", "月中", "月底", "年初", "年中", "年底",
    "去年", "今年", "明年", "前年", "后年",
    "上个月", "这个月", "下个月",
    "上周", "本周", "下周",
    "发布", "公布", "宣布", "启动", "上线", "推出",
    "发生", "爆发", "开始", "结束", "完成",
    "首次", "首次", "首次", "首次", "首次",
]

# 时间正则匹配表（用于提取具体的时间描述）
TIME_PATTERNS = [
    # 标准日期格式：2024-01-01, 2024/01/01, 2024.01.01
    r'\d{4}[-/.]\d{1,2}[-/.]\d{1,2}',
    # 年月日：2024年1月1日, 2024年01月01日
    r'\d{4}年\d{1,2}月\d{1,2}日',
    # 月日：1月1日, 01月01日
    r'\d{1,2}月\d{1,2}日',
    # 相对时间：今天、昨天、前天、明天、后天
    r'(今天|昨天|前天|明天|后天)',
    # 相对时间：X天前、X天前、X周前、X个月前、X年前
    r'\d+(天|周|个月|年)前',
    # 相对时间：X小时后、X天后、X周后、X个月后、X年后
    r'\d+(小时|天|周|个月|年)后',
    # 时间点：X点、X时、X:XX
    r'\d{1,2}[点时]',
    r'\d{1,2}:\d{2}',
    # 时间段：X月X日-X月X日、X年X月-X年X月
    r'\d{1,2}月\d{1,2}日[-至到]\d{1,2}月\d{1,2}日',
    r'\d{4}年\d{1,2}月[-至到]\d{4}年\d{1,2}月',
    # 事件时间：X月X日发布、X月X日上线
    r'\d{1,2}月\d{1,2}日(发布|公布|宣布|启动|上线|推出|发生|爆发)',
    # 时间描述：X日上午、X日下午、X日晚上
    r'\d{1,2}日(上午|下午|晚上|凌晨|中午)',
]


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


def _identify_columns(data: List[Dict[str, Any]]) -> tuple[Optional[str], Optional[str]]:
    """识别内容列和发布时间列"""
    if not data:
        return None, None
    
    # 可能的列名
    content_candidates = ["内容", "content", "正文", "text", "摘要", "abstract"]
    time_candidates = ["发布时间", "发布时间戳", "time", "timeBak", "发布时间", "发布", "时间"]
    
    # 获取所有列名
    columns = list(data[0].keys())
    
    # 查找内容列
    content_col = None
    for col in columns:
        if any(candidate in col for candidate in content_candidates):
            content_col = col
            break
    
    # 查找发布时间列
    time_col = None
    for col in columns:
        if any(candidate in col for candidate in time_candidates):
            time_col = col
            break
    
    return content_col, time_col


def _filter_by_time_keywords(data: List[Dict[str, Any]], content_col: str) -> List[Dict[str, Any]]:
    """使用词表筛选包含时间信息的内容"""
    if not content_col:
        return []
    
    filtered = []
    for row in data:
        content = str(row.get(content_col, "")).strip()
        if not content:
            continue
        
        # 检查是否包含时间关键词
        if any(keyword in content for keyword in TIME_KEYWORDS):
            filtered.append(row)
    
    return filtered


def _extract_time_descriptions(data: List[Dict[str, Any]], content_col: str) -> List[Dict[str, Any]]:
    """使用正则表达式提取包含时间描述的内容"""
    if not content_col:
        return []
    
    extracted = []
    for row in data:
        content = str(row.get(content_col, "")).strip()
        if not content:
            continue
        
        # 检查是否匹配时间正则表达式
        for pattern in TIME_PATTERNS:
            if re.search(pattern, content):
                extracted.append(row)
                break
    
    return extracted


def _prepare_reference_materials(
    data: List[Dict[str, Any]],
    content_col: str,
    time_col: str
) -> str:
    """准备参考资料：将内容和发布时间拼接"""
    materials = []
    
    for row in data:
        content = str(row.get(content_col, "")).strip()
        time_str = str(row.get(time_col, "")).strip()
        
        if not content:
            continue
        
        # 拼接内容和时间
        material = f"内容：{content}"
        if time_str:
            material += f"\n发布时间：{time_str}"
        
        materials.append(material)
    
    return "\n\n".join(materials)


def _generate_result_filename(retryContext: Optional[str] = None) -> str:
    """生成结果文件名，如果是重试则添加后缀"""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    base_name = f"timeline_analysis_{timestamp}"
    
    # 如果是重试，需要检查已存在的文件并添加后缀
    if retryContext:
        task_id = get_task_id()
        if task_id:
            process_dir = get_task_process_dir(task_id)
            if process_dir.exists():
                # 查找所有 timeline_analysis_ 开头的 JSON 文件
                existing_files = list(process_dir.glob("timeline_analysis_*.json"))
                if existing_files:
                    # 提取已有的后缀编号
                    suffix_nums = []
                    for file in existing_files:
                        # 匹配 timeline_analysis_时间戳_数字.json 或 timeline_analysis_时间戳.json 格式
                        match = re.search(r"timeline_analysis_\d{8}_\d{6}_(\d+)\.json", file.name)
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
def analysis_timeline(
    eventIntroduction: str,
    dataFilePath: str,
    retryContext: Optional[str] = None
) -> str:
    """
    描述：分析事件时间线。根据提供的事件基础介绍和数据文件，从舆情数据中提取时间相关信息，生成事件时间线。只有当热点事件可能包含时间线（跨度比较长）时才使用本工具。
    使用时机：当需要分析热点事件的时间线信息，且事件可能跨越较长时间段时调用本工具。
    输入：
    - eventIntroduction（必填）：事件基础介绍，由 extract_search_terms 工具生成，用于告知模型事件背景，避免分析跑偏。
    - dataFilePath（必填）：数据文件位置，数据爬取后保存的CSV文件路径，需要从data_collect工具返回的JSON结果中提取。
    - retryContext（可选，默认None）：重试机制参数。第一次调用时不使用，当后续用户有调整意见时，填入之前的结果及修改建议，格式为JSON字符串，例如 '{"previous_result": "...", "suggestions": "..."}'。
    输出：JSON字符串，包含以下字段：
    - timeline：事件时间线信息，按时间顺序排列的关键事件节点
    - summary：时间线摘要
    - result_file_path：结果文件保存路径（保存在任务的过程文件夹中，JSON格式）
    注意：如果是多次调用（重试），文件名会自动添加后缀（_1, _2等）。
    """
    
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
            "timeline": [],
            "summary": "",
            "result_file_path": ""
        }, ensure_ascii=False)
    
    if not all_data:
        return json_module.dumps({
            "error": "数据文件为空",
            "timeline": [],
            "summary": "",
            "result_file_path": ""
        }, ensure_ascii=False)
    
    # 识别内容列和发布时间列
    content_col, time_col = _identify_columns(all_data)
    
    if not content_col:
        return json_module.dumps({
            "error": "无法识别内容列，请确保CSV文件包含'内容'或'content'列",
            "timeline": [],
            "summary": "",
            "result_file_path": ""
        }, ensure_ascii=False)
    
    # 使用词表筛选包含时间信息的内容
    time_keyword_data = _filter_by_time_keywords(all_data, content_col)
    
    # 使用正则表达式进一步提取包含时间描述的内容
    time_pattern_data = _extract_time_descriptions(time_keyword_data, content_col)
    
    # 如果没有匹配到时间信息，使用所有数据
    if not time_pattern_data:
        time_pattern_data = time_keyword_data if time_keyword_data else all_data[:100]  # 限制数量
    
    # 准备参考资料
    reference_materials = _prepare_reference_materials(
        time_pattern_data,
        content_col,
        time_col or ""
    )
    
    # 获取分析模型和prompt
    try:
        model = get_tools_model()
        prompt_template = get_analysis_timeline_prompt()
    except Exception as e:
        return json_module.dumps({
            "error": f"获取分析模型失败: {str(e)}",
            "timeline": [],
            "summary": "",
            "result_file_path": ""
        }, ensure_ascii=False)
    
    # 构建提示词
    # 处理重试上下文：如果是第一次调用，显示"无"；如果有重试上下文，显示具体内容
    retry_section = "无（首次分析）" if not previous_result else previous_result
    suggestions_section = "无" if not suggestions else suggestions
    
    prompt = prompt_template.format(
        event_introduction=eventIntroduction,
        reference_materials=reference_materials[:5000] if len(reference_materials) > 5000 else reference_materials,  # 限制长度
        previous_result=retry_section,
        suggestions=suggestions_section
    )
    
    # 调用模型进行分析
    try:
        messages = [
            SystemMessage(content="你是一个专业的事件时间线分析专家。"),
            HumanMessage(content=prompt)
        ]
        response = model.invoke(messages)
        result_text = response.content if hasattr(response, 'content') else str(response)
    except Exception as e:
        return json_module.dumps({
            "error": f"模型分析失败: {str(e)}",
            "timeline": [],
            "summary": "",
            "result_file_path": ""
        }, ensure_ascii=False)
    
    # 尝试解析JSON结果
    try:
        # 尝试提取JSON部分
        json_match = re.search(r'\{[\s\S]*\}', result_text)
        if json_match:
            result_json = json_module.loads(json_match.group())
        else:
            # 如果不是JSON格式，尝试直接解析
            result_json = json_module.loads(result_text)
    except Exception:
        # 如果解析失败，返回原始文本
        return json_module.dumps({
            "error": "模型返回结果格式不正确",
            "raw_result": result_text,
            "timeline": [],
            "summary": "",
            "result_file_path": ""
        }, ensure_ascii=False)
    
    # 验证返回结果格式
    if not isinstance(result_json, dict):
        return json_module.dumps({
            "error": "模型返回结果格式不正确",
            "raw_result": result_text,
            "timeline": [],
            "summary": "",
            "result_file_path": ""
        }, ensure_ascii=False)
    
    # 确保包含必需字段
    result = {
        "timeline": result_json.get("timeline", []),
        "summary": result_json.get("summary", ""),
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
            # 保存失败不影响返回结果，但记录错误
            result["save_error"] = f"保存结果文件失败: {str(e)}"
            result["result_file_path"] = ""  # 保存失败时设置为空字符串
    else:
        result["save_error"] = "未找到任务ID，无法保存结果文件"
        result["result_file_path"] = ""  # 无任务ID时设置为空字符串
    
    return json_module.dumps(result, ensure_ascii=False)
