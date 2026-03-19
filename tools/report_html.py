"""HTML报告生成工具：根据分析结果生成HTML报告。"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.tools import tool

from model.factory import get_report_model
from utils.path import ensure_task_dirs, get_task_result_dir
from utils.prompt_loader import get_report_html_prompt
from utils.task_context import get_task_id


def _read_json_files(directory: str) -> List[Dict[str, Any]]:
    """
    读取目录中所有JSON文件。
    
    Args:
        directory: 目录路径
        
    Returns:
        JSON文件列表，每个元素包含文件名和内容
    """
    dir_path = Path(directory)
    if not dir_path.exists():
        raise FileNotFoundError(f"目录不存在: {directory}")
    
    json_files = []
    for json_file in dir_path.glob("*.json"):
        try:
            with open(json_file, 'r', encoding='utf-8') as f:
                content = json.load(f)
                json_files.append({
                    "filename": json_file.name,
                    "content": content
                })
        except Exception as e:
            # 跳过无法读取的文件
            continue
    
    return json_files


def _get_file_url(file_path: Path) -> str:
    """
    获取文件的 file:// URL。
    
    Args:
        file_path: 文件路径
        
    Returns:
        file:// URL 字符串
    """
    # 转换为绝对路径
    abs_path = file_path.resolve()
    # Windows 路径需要特殊处理
    if os.name == 'nt':  # Windows
        # 将反斜杠转换为正斜杠，并添加 file:/// 前缀
        url_path = str(abs_path).replace('\\', '/')
        return f"file:///{url_path}"
    else:
        # Unix/Linux/Mac
        return f"file://{abs_path}"


@tool
def report_html(
    eventIntroduction: str,
    analysisResultsDir: str
) -> str:
    """
    描述：生成HTML报告。根据提供的事件基础介绍和分析结果文件夹，生成美观的HTML舆情分析报告。
    使用时机：当需要生成最终的HTML报告时调用本工具。
    输入：
    - eventIntroduction（必填）：事件基础介绍，由 extract_search_terms 工具生成，用于告知模型事件背景，避免分析跑偏。
    - analysisResultsDir（必填）：分析结果文件夹路径，通常是 sandbox/任务ID/过程文件，包含所有分析结果的JSON文件。
    输出：JSON字符串，包含以下字段：
    - html_file_path：生成的HTML文件路径（保存在任务的结果文件夹中）
    - file_url：本地文件访问地址（file:// 协议，可直接在浏览器中打开）
    """
    import json as json_module
    
    # 获取任务ID
    task_id = get_task_id()
    if not task_id:
        return json_module.dumps({
            "error": "未找到任务ID，请确保在Agent上下文中调用",
            "html_file_path": "",
            "file_url": ""
        }, ensure_ascii=False)
    
    # 读取分析结果文件夹中的所有JSON文件
    try:
        json_files = _read_json_files(analysisResultsDir)
    except Exception as e:
        return json_module.dumps({
            "error": f"读取分析结果文件夹失败: {str(e)}",
            "html_file_path": "",
            "file_url": ""
        }, ensure_ascii=False)
    
    if not json_files:
        return json_module.dumps({
            "error": "分析结果文件夹中没有找到JSON文件",
            "html_file_path": "",
            "file_url": ""
        }, ensure_ascii=False)
    
    # 获取报告模型和prompt
    try:
        model = get_report_model()
        prompt_template = get_report_html_prompt()
    except Exception as e:
        return json_module.dumps({
            "error": f"获取报告模型失败: {str(e)}",
            "html_file_path": "",
            "file_url": ""
        }, ensure_ascii=False)
    
    # 构建提示词
    analysis_results_text = ""
    for json_file in json_files:
        analysis_results_text += f"\n## 文件: {json_file['filename']}\n"
        analysis_results_text += json_module.dumps(json_file['content'], ensure_ascii=False, indent=2)
        analysis_results_text += "\n"
    
    prompt = prompt_template.format(
        event_introduction=eventIntroduction,
        analysis_results=analysis_results_text
    )
    
    # 调用模型生成HTML
    try:
        messages = [
            SystemMessage(content="你是一个专业的HTML报告生成专家，擅长创建美观、交互式的舆情分析报告。"),
            HumanMessage(content=prompt)
        ]
        response = model.invoke(messages)
        html_content = response.content if hasattr(response, 'content') else str(response)
    except Exception as e:
        return json_module.dumps({
            "error": f"模型生成HTML失败: {str(e)}",
            "html_file_path": "",
            "file_url": ""
        }, ensure_ascii=False)
    
    # 清理HTML内容（移除markdown代码块标记）
    html_content = html_content.strip()
    if html_content.startswith("```html"):
        html_content = html_content[7:]
    elif html_content.startswith("```"):
        html_content = html_content[3:]
    if html_content.endswith("```"):
        html_content = html_content[:-3]
    html_content = html_content.strip()
    
    # 确保结果文件夹存在
    result_dir = get_task_result_dir(task_id)
    result_dir.mkdir(parents=True, exist_ok=True)
    
    # 生成HTML文件名
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    html_filename = f"report_{timestamp}.html"
    html_file_path = result_dir / html_filename
    
    # 保存HTML文件
    try:
        with open(html_file_path, 'w', encoding='utf-8') as f:
            f.write(html_content)
    except Exception as e:
        return json_module.dumps({
            "error": f"保存HTML文件失败: {str(e)}",
            "html_file_path": "",
            "file_url": ""
        }, ensure_ascii=False)
    
    # 生成 file:// URL
    file_url = _get_file_url(html_file_path)
    
    # 返回结果（包含HTML文件路径和 file:// URL）
    result = {
        "html_file_path": str(html_file_path),
        "file_url": file_url
    }
    
    return json_module.dumps(result, ensure_ascii=False)
