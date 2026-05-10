"""评分报告 HTML 生成器"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional
from datetime import datetime


def generate_eval_html_report(
    eval_result: Dict[str, Any],
    task_id: str,
    report_html_path: str,
    output_dir: Path,
) -> str:
    """
    生成 HTML 格式的评分报告
    
    Args:
        eval_result: 评分结果字典
        task_id: 任务 ID
        report_html_path: 原始舆情报告的路径
        output_dir: 输出目录
        
    Returns:
        评分报告 HTML 文件路径
    """
    
    case_id = eval_result.get("case_id", "unknown")
    score = eval_result.get("score", 0)
    max_score = eval_result.get("max_score", 100)
    status = eval_result.get("status", "unknown")
    breakdown = eval_result.get("breakdown", {})
    issues = eval_result.get("issues", [])
    
    # 计算百分比
    score_percent = (score / max_score * 100) if max_score > 0 else 0
    
    # 状态样式
    status_class = "passed" if status == "passed" else "failed"
    status_text = "通过" if status == "passed" else "未通过"
    
    # 生成详细评分表格
    breakdown_rows = ""
    for dimension, details in breakdown.items():
        dim_score = details.get("score", 0)
        dim_max = details.get("max_score", 0)
        dim_percent = (dim_score / dim_max * 100) if dim_max > 0 else 0
        
        # 根据得分设置颜色
        if dim_percent >= 80:
            score_color = "#28a745"
        elif dim_percent >= 60:
            score_color = "#ffc107"
        else:
            score_color = "#dc3545"
        
        breakdown_rows += f"""
        <tr>
            <td>{dimension}</td>
            <td>{details.get('description', '')}</td>
            <td style="color: {score_color}; font-weight: bold;">{dim_score}/{dim_max}</td>
            <td>{dim_percent:.1f}%</td>
        </tr>
        """
    
    # 生成问题列表
    issues_html = ""
    if issues:
        issues_html = "<h3>发现的问题</h3><ul>"
        for issue in issues:
            issues_html += f"<li>{issue}</li>"
        issues_html += "</ul>"
    else:
        issues_html = "<p style='color: #28a745;'>✓ 未发现明显问题</p>"
    
    # HTML 模板
    html_content = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>舆情分析报告质量评估 - {case_id}</title>
    <style>
        * {{
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }}
        
        body {{
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
            line-height: 1.6;
            color: #333;
            background: #f5f5f5;
            padding: 20px;
        }}
        
        .container {{
            max-width: 900px;
            margin: 0 auto;
            background: white;
            border-radius: 8px;
            box-shadow: 0 2px 8px rgba(0,0,0,0.1);
            padding: 30px;
        }}
        
        h1 {{
            color: #2c3e50;
            border-bottom: 3px solid #3498db;
            padding-bottom: 15px;
            margin-bottom: 25px;
        }}
        
        h2 {{
            color: #34495e;
            margin: 25px 0 15px 0;
            font-size: 1.3em;
        }}
        
        h3 {{
            color: #555;
            margin: 20px 0 10px 0;
            font-size: 1.1em;
        }}
        
        .summary {{
            background: #f8f9fa;
            border-radius: 6px;
            padding: 20px;
            margin-bottom: 25px;
        }}
        
        .score-display {{
            text-align: center;
            margin: 20px 0;
        }}
        
        .score-number {{
            font-size: 4em;
            font-weight: bold;
            color: {'#28a745' if score_percent >= 60 else '#dc3545'};
        }}
        
        .score-max {{
            font-size: 1.5em;
            color: #666;
        }}
        
        .status-badge {{
            display: inline-block;
            padding: 8px 20px;
            border-radius: 20px;
            font-weight: bold;
            font-size: 1.1em;
            margin-top: 10px;
        }}
        
        .status-badge.passed {{
            background: #d4edda;
            color: #155724;
        }}
        
        .status-badge.failed {{
            background: #f8d7da;
            color: #721c24;
        }}
        
        table {{
            width: 100%;
            border-collapse: collapse;
            margin: 15px 0;
        }}
        
        th, td {{
            padding: 12px;
            text-align: left;
            border-bottom: 1px solid #ddd;
        }}
        
        th {{
            background: #3498db;
            color: white;
            font-weight: 600;
        }}
        
        tr:hover {{
            background: #f5f5f5;
        }}
        
        .info-box {{
            background: #e3f2fd;
            border-left: 4px solid #2196f3;
            padding: 15px;
            margin: 15px 0;
            border-radius: 4px;
        }}
        
        .warning-box {{
            background: #fff3cd;
            border-left: 4px solid #ffc107;
            padding: 15px;
            margin: 15px 0;
            border-radius: 4px;
        }}
        
        .error-box {{
            background: #f8d7da;
            border-left: 4px solid #dc3545;
            padding: 15px;
            margin: 15px 0;
            border-radius: 4px;
        }}
        
        ul {{
            margin: 10px 0 10px 20px;
        }}
        
        li {{
            margin: 5px 0;
        }}
        
        .footer {{
            margin-top: 30px;
            padding-top: 20px;
            border-top: 1px solid #ddd;
            color: #666;
            font-size: 0.9em;
        }}
        
        .progress-bar {{
            width: 100%;
            height: 20px;
            background: #e9ecef;
            border-radius: 10px;
            overflow: hidden;
            margin: 10px 0;
        }}
        
        .progress-fill {{
            height: 100%;
            background: {'#28a745' if score_percent >= 60 else '#dc3545'};
            width: {score_percent}%;
            transition: width 0.3s ease;
        }}
    </style>
</head>
<body>
    <div class="container">
        <h1>📊 舆情分析报告质量评估</h1>
        
        <div class="summary">
            <div class="info-box">
                <strong>案例 ID:</strong> {case_id}<br>
                <strong>任务 ID:</strong> {task_id}<br>
                <strong>评估时间:</strong> {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
            </div>
            
            <div class="score-display">
                <div class="score-number">{score}</div>
                <div class="score-max">/ {max_score} 分</div>
                <div class="progress-bar">
                    <div class="progress-fill"></div>
                </div>
                <span class="status-badge {status_class}">{status_text}</span>
            </div>
        </div>
        
        <h2>📋 详细评分</h2>
        <table>
            <thead>
                <tr>
                    <th>维度</th>
                    <th>说明</th>
                    <th>得分</th>
                    <th>百分比</th>
                </tr>
            </thead>
            <tbody>
                {breakdown_rows}
            </tbody>
        </table>
        
        <h2>🔍 质量检查</h2>
        {issues_html}
        
        <h2>📄 相关文件</h2>
        <ul>
            <li><strong>舆情报告:</strong> <a href="file://{report_html_path}" target="_blank">{report_html_path}</a></li>
            <li><strong>评分报告:</strong> 当前文件</li>
        </ul>
        
        <div class="footer">
            <p>本报告由 Sona 舆情分析系统自动生成</p>
            <p>评估标准基于 Golden Cases 框架</p>
        </div>
    </div>
</body>
</html>"""
    
    # 保存文件
    eval_report_path = output_dir / "eval_report.html"
    eval_report_path.write_text(html_content, encoding='utf-8')
    
    return str(eval_report_path)


def generate_summary_report(
    all_results: List[Dict[str, Any]],
    output_path: Path,
) -> str:
    """
    生成批量评测的汇总报告
    
    Args:
        all_results: 所有案例的评分结果列表
        output_path: 输出文件路径
        
    Returns:
        汇总报告 HTML 文件路径
    """
    total_cases = len(all_results)
    passed_cases = sum(1 for r in all_results if r.get("status") == "passed")
    failed_cases = total_cases - passed_cases
    total_score = sum(r.get("score", 0) for r in all_results)
    avg_score = total_score / total_cases if total_cases > 0 else 0
    
    # 生成案例列表
    case_rows = ""
    for result in all_results:
        case_id = result.get("case_id", "unknown")
        score = result.get("score", 0)
        max_score = result.get("max_score", 100)
        status = result.get("status", "unknown")
        domain = result.get("domain", "unknown")
        
        status_class = "passed" if status == "passed" else "failed"
        status_text = "通过" if status == "passed" else "失败"
        
        case_rows += f"""
        <tr class="{status_class}">
            <td>{case_id}</td>
            <td>{domain}</td>
            <td>{score}/{max_score}</td>
            <td><span class="badge {status_class}">{status_text}</span></td>
        </tr>
        """
    
    html_content = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>批量评测汇总报告</title>
    <style>
        body {{
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            line-height: 1.6;
            color: #333;
            background: #f5f5f5;
            padding: 20px;
        }}
        .container {{
            max-width: 1000px;
            margin: 0 auto;
            background: white;
            border-radius: 8px;
            box-shadow: 0 2px 8px rgba(0,0,0,0.1);
            padding: 30px;
        }}
        h1 {{
            color: #2c3e50;
            border-bottom: 3px solid #3498db;
            padding-bottom: 15px;
        }}
        .stats {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 20px;
            margin: 25px 0;
        }}
        .stat-card {{
            background: #f8f9fa;
            border-radius: 8px;
            padding: 20px;
            text-align: center;
        }}
        .stat-value {{
            font-size: 2.5em;
            font-weight: bold;
            color: #3498db;
        }}
        .stat-label {{
            color: #666;
            margin-top: 5px;
        }}
        table {{
            width: 100%;
            border-collapse: collapse;
            margin: 20px 0;
        }}
        th, td {{
            padding: 12px;
            text-align: left;
            border-bottom: 1px solid #ddd;
        }}
        th {{
            background: #3498db;
            color: white;
        }}
        tr:hover {{
            background: #f5f5f5;
        }}
        .badge {{
            padding: 4px 12px;
            border-radius: 12px;
            font-size: 0.85em;
            font-weight: bold;
        }}
        .badge.passed {{
            background: #d4edda;
            color: #155724;
        }}
        .badge.failed {{
            background: #f8d7da;
            color: #721c24;
        }}
        tr.passed td {{
            background: rgba(212, 237, 218, 0.3);
        }}
        tr.failed td {{
            background: rgba(248, 215, 218, 0.3);
        }}
    </style>
</head>
<body>
    <div class="container">
        <h1>📊 批量评测汇总报告</h1>
        
        <div class="stats">
            <div class="stat-card">
                <div class="stat-value">{total_cases}</div>
                <div class="stat-label">总案例数</div>
            </div>
            <div class="stat-card">
                <div class="stat-value" style="color: #28a745;">{passed_cases}</div>
                <div class="stat-label">通过</div>
            </div>
            <div class="stat-card">
                <div class="stat-value" style="color: #dc3545;">{failed_cases}</div>
                <div class="stat-label">失败</div>
            </div>
            <div class="stat-card">
                <div class="stat-value" style="color: #ffc107;">{avg_score:.1f}</div>
                <div class="stat-label">平均分</div>
            </div>
        </div>
        
        <h2>详细结果</h2>
        <table>
            <thead>
                <tr>
                    <th>案例 ID</th>
                    <th>领域</th>
                    <th>得分</th>
                    <th>状态</th>
                </tr>
            </thead>
            <tbody>
                {case_rows}
            </tbody>
        </table>
        
        <div style="margin-top: 30px; padding-top: 20px; border-top: 1px solid #ddd; color: #666;">
            <p>生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</p>
        </div>
    </div>
</body>
</html>"""
    
    output_path.write_text(html_content, encoding='utf-8')
    return str(output_path)