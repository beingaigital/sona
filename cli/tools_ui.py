"""工具列表显示：显示所有可用工具及其用途。"""

from __future__ import annotations

from rich.console import Console
from agent.reactagent import AGENT_TOOLS

console = Console()


def _extract_description(docstring: str) -> str:
    """从 docstring 中提取描述部分"""
    if not docstring:
        return "无描述"
    
    # 查找 "描述：" 的位置
    desc_start = docstring.find("描述：")
    if desc_start == -1:
        return "无描述"
    
    # 从 "描述：" 后面开始提取
    desc_text = docstring[desc_start + 3:].strip()
    
    # 查找下一个部分（使用时机、输入、输出等）的开始位置
    next_sections = ['使用时机：', '输入：', '输出：', '注意：']
    for section in next_sections:
        pos = desc_text.find(section)
        if pos != -1:
            desc_text = desc_text[:pos].strip()
            break
    
    # 清理描述：移除多余的空格和换行
    description = ' '.join(desc_text.split())
    return description if description else "无描述"


def show_tools_list() -> None:
    """显示所有可用工具及其用途"""
    console.print()
    console.print("[bold cyan]可用工具列表：[/bold cyan]")
    console.print()
    
    for tool in AGENT_TOOLS:
        # 获取工具名称
        tool_name = tool.name if hasattr(tool, 'name') else str(tool)
        
        # 获取工具描述
        description = ""
        if hasattr(tool, 'description'):
            description = tool.description
        elif hasattr(tool, '__doc__') and tool.__doc__:
            description = _extract_description(tool.__doc__)
        
        if not description:
            description = "无描述"
        
        console.print(f"  [cyan]•[/cyan] [bold]{tool_name}[/bold]")
        console.print(f"    [dim]{description}[/dim]")
        console.print()
    
    console.print()
