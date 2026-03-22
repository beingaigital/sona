"""HTML报告生成工具：根据分析结果生成HTML报告。"""

from __future__ import annotations

import json
import os
import re
import html
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.tools import tool

from model.factory import get_report_model
from utils.path import ensure_task_dirs, get_task_result_dir
from utils.prompt_loader import get_report_html_prompt
from utils.task_context import get_task_id
from utils.methodology_loader import load_methodology_for_report


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


def _needs_quality_retry(html_content: str) -> bool:
    """
    质量兜底：当报告过短或未覆盖关键方法论章节时，触发一次重试。
    """
    if not html_content:
        return True
    text = html_content.strip()
    if len(text) < 2200:
        return True
    required_terms = [
        "舆情分析核心维度",
        "舆情生命周期",
        "理论规律",
        "回应观察",
        "总结复盘",
    ]
    matched = sum(1 for t in required_terms if t in text)
    return matched < 3


def _build_fallback_html(
    *,
    event_introduction: str,
    analysis_results_text: str,
    methodology_content: str,
    model_error: str,
) -> str:
    """
    当模型不可用时，生成一个可直接打开的静态兜底报告。
    """
    title = "舆情分析报告（Fallback）"
    intro = html.escape(event_introduction or "未提供事件介绍")
    analysis_block = html.escape((analysis_results_text or "无分析结果")[:20000])
    methodology_block = html.escape((methodology_content or "无方法论内容")[:12000])
    error_block = html.escape(model_error or "未知错误")
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>{title}</title>
  <style>
    :root {{
      --bg: #f5f7fb;
      --card: #ffffff;
      --text: #1f2937;
      --muted: #6b7280;
      --line: #e5e7eb;
      --accent: #1d4ed8;
    }}
    body {{
      margin: 0;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif;
      background: linear-gradient(180deg, #f9fbff 0%, var(--bg) 100%);
      color: var(--text);
    }}
    .wrap {{
      max-width: 1100px;
      margin: 28px auto;
      padding: 0 16px 28px;
    }}
    .card {{
      background: var(--card);
      border: 1px solid var(--line);
      border-radius: 14px;
      padding: 18px 20px;
      margin-bottom: 14px;
      box-shadow: 0 6px 18px rgba(15, 23, 42, 0.05);
    }}
    h1 {{ margin: 0 0 8px; color: var(--accent); font-size: 28px; }}
    h2 {{ margin: 0 0 10px; font-size: 18px; }}
    .meta {{ color: var(--muted); font-size: 13px; margin-bottom: 10px; }}
    pre {{
      margin: 0;
      white-space: pre-wrap;
      word-break: break-word;
      line-height: 1.6;
      font-size: 13px;
      background: #f8fafc;
      border: 1px solid #e2e8f0;
      border-radius: 10px;
      padding: 12px;
    }}
    .warn {{
      color: #991b1b;
      background: #fef2f2;
      border: 1px solid #fecaca;
      border-radius: 10px;
      padding: 10px 12px;
      font-size: 13px;
    }}
  </style>
</head>
<body>
  <div class="wrap">
    <div class="card">
      <h1>{title}</h1>
      <div class="meta">生成时间：{generated_at}</div>
      <div class="warn">报告模型调用失败，已启用兜底报告。错误信息：{error_block}</div>
    </div>

    <div class="card">
      <h2>事件基础介绍</h2>
      <pre>{intro}</pre>
    </div>

    <div class="card">
      <h2>分析结果原始摘要</h2>
      <pre>{analysis_block}</pre>
    </div>

    <div class="card">
      <h2>舆情智库方法论参考</h2>
      <pre>{methodology_block}</pre>
    </div>
  </div>
</body>
</html>"""


def _get_file_url(file_path: Path) -> str:
    """
    获取文件的 file:// URL。
    
    Args:
        file_path: 文件路径
        
    Returns:
        file:// URL 字符串
    """
    # 使用 pathlib 的 URI 转换，自动处理中文/空格等字符编码，避免 macOS 打开 file:// 报 -43
    abs_path = file_path.resolve()
    try:
        return abs_path.as_uri()
    except Exception:
        if os.name == "nt":
            url_path = str(abs_path).replace("\\", "/")
            return f"file:///{url_path}"
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
    
    # 读取舆情智库方法论
    methodology_content = load_methodology_for_report(topic=eventIntroduction)
    
    # 构建提示词
    analysis_results_text = ""
    for json_file in json_files:
        analysis_results_text += f"\n## 文件: {json_file['filename']}\n"
        analysis_results_text += json_module.dumps(json_file['content'], ensure_ascii=False, indent=2)
        analysis_results_text += "\n"
    
    # 格式化prompt（包含方法论）
    prompt = prompt_template.format(
        event_introduction=eventIntroduction,
        analysis_results=analysis_results_text,
        methodology=methodology_content
    )
    
    # 调用模型生成HTML
    model_error = ""
    try:
        messages = [
            SystemMessage(content="你是一个专业的HTML报告生成专家，擅长创建美观、交互式的舆情分析报告。"),
            HumanMessage(content=prompt)
        ]
        response = model.invoke(messages)
        html_content = response.content if hasattr(response, 'content') else str(response)

        # 质量兜底：过于浅层时进行一次强化重试
        if _needs_quality_retry(str(html_content)):
            retry_prompt = (
                prompt
                + "\n\n【强制质量要求】\n"
                + "1) 不能只做数据描述，必须给出研判结论、风险研判、回应建议。\n"
                + "2) 必须完整覆盖：舆情分析核心维度、舆情生命周期阶段、理论规律分析、回应观察与分析、总结复盘。\n"
                + "3) 每个章节至少包含1条“数据证据 -> 结论”的推理链。\n"
                + "4) 明确引用并吸收“舆情智库方法论指导”中的术语与框架。\n"
            )
            retry_messages = [
                SystemMessage(content="你是资深舆情研究员，同时是可视化报告专家。"),
                HumanMessage(content=retry_prompt),
            ]
            retry_resp = model.invoke(retry_messages)
            retry_html = retry_resp.content if hasattr(retry_resp, "content") else str(retry_resp)
            if retry_html and len(str(retry_html).strip()) >= len(str(html_content).strip()):
                html_content = retry_html
    except Exception as e:
        model_error = f"模型生成HTML失败: {str(e)}"
        html_content = _build_fallback_html(
            event_introduction=eventIntroduction,
            analysis_results_text=analysis_results_text,
            methodology_content=methodology_content,
            model_error=model_error,
        )
    
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
    if model_error:
        result["warning"] = model_error
    
    return json_module.dumps(result, ensure_ascii=False)
