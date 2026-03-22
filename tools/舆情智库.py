"""
舆情智库工具：为舆情分析提供方法论支持

在生成舆情分析报告或提供分析角度时，自动加载相关的理论指导和框架。
"""

import json
from pathlib import Path
from typing import Any, Dict, Optional
from langchain_core.tools import tool

# 舆情智库路径
SKILL_DIR = Path.home() / ".openclaw/skills/舆情智库"
REFERENCES_DIR = SKILL_DIR / "references"
PROJECT_ROOT = Path(__file__).resolve().parent.parent
LOCAL_REFERENCES_DIR = PROJECT_ROOT / "舆情深度分析" / "references"
LOCAL_METHOD_DIR = PROJECT_ROOT / "舆情深度分析"


def _find_reference_file(candidates: list[str]) -> Optional[Path]:
    """在多个候选目录/文件名中查找第一个存在的参考文件。"""
    search_dirs = [REFERENCES_DIR, LOCAL_REFERENCES_DIR, LOCAL_METHOD_DIR]
    for name in candidates:
        for d in search_dirs:
            p = d / name
            if p.exists() and p.is_file():
                return p
    return None


@tool
def get_sentiment_analysis_framework(topic: Optional[str] = None) -> str:
    """
    获取舆情分析框架和核心维度。
    
    用于在进行舆情分析时获取方法论指导，自动注入到分析提示词中。
    
    Args:
        topic: 可选，特定的分析主题（如"企业危机"、"政策舆情"等）
    
    Returns:
        舆情分析框架和方法论指导
    """
    framework = """
【舆情分析核心框架】

一、舆情基本要素
- 主体：网民、KOL、媒体、机构
- 客体：事件/议题/品牌/政策
- 渠道：微博、短视频平台、论坛、私域社群等
- 情绪：积极/中性/消极 + 细分（愤怒、焦虑、讽刺等）
- 主体行为：转发、评论、跟帖、二创、线下行动

二、核心分析维度
- 量：声量、增速、峰值、平台分布
- 质：情感极性、话题焦点、信息真实性
- 人：关键意见领袖、关键节点用户、受众画像
- 场：主要平台、话语场风格（理性、撕裂、娱乐化）
- 效：对品牌/政策/行为的实际影响（搜索量、销量、投诉量等）

三、舆情生命周期阶段
- 潜伏期：信息量少但敏感度高
- 萌芽期：意见领袖介入、帖文量开始增长
- 爆发期：媒体跟进、热度达到峰值
- 衰退期：事件解决或新热点出现、舆情衰减

四、分析框架建议
1. 事件脉络：潜伏期→萌芽期→爆发期→衰退期
2. 回应观察：回应处置梳理、趋势变化、传播平台变化、情绪变化、话题变化
3. 总结复盘：话语分析、议题泛化趋势、舆论推手分析、叙事手段分析
"""
    return framework


@tool
def get_sentiment_theories() -> str:
    """
    获取舆情规律的理论基础。
    
    包含沉默螺旋、议程设置、蝴蝶效应等经典理论及其网络表现。
    
    Returns:
        舆情理论规律及其应用
    """
    # 从 reference 文件加载完整理论（优先 ~/.openclaw，其次项目内舆情深度分析目录）
    theory_file = _find_reference_file(["舆情分析方法论.md"])
    
    if theory_file and theory_file.exists():
        content = theory_file.read_text(encoding='utf-8')
        # 提取理论规律部分
        if "舆情规律的理论基础" in content:
            start = content.find("舆情规律的理论基础")
            # 找到下一个大标题或结束
            end = content.find("##", start + 20)
            if end == -1:
                end = len(content)
            return content[start:end]
    
    # Fallback: 返回简要理论列表
    return """
【舆情规律理论基础】

1. 沉默螺旋规律 - 群体压力下的意见趋同
   网络表现：网民通过点赞、转发等行为无形施加压力，使异议声音被淹没

2. 议程设置规律 - 媒介与公众的互动博弈
   网络表现：热点事件经微博、论坛等平台发酵后，传统媒体跟进报道放大舆情

3. 蝴蝶效应规律 - 初始微扰的指数级放大
   网络表现：谣言或不实信息通过转发、评论几何级扩散

4. 生命周期规律 - 舆情的阶段性演变
   阶段：初始期→扩散期→消退期

5. 博弈均衡规律 - 政府与网民的策略互动
   网络表现：政府公开信息可双赢，封锁信息会激发逆反心理

6. 社会燃烧规律 - 矛盾累积的临界点爆发
   治理启示：需建立舆情萌芽期预警机制
"""


@tool
def get_sentiment_case_template(case_type: str = "社会事件") -> str:
    """
    获取舆情分析报告模板。
    
    Args:
        case_type: 案例类型，"社会事件"或"商业事件"
    
    Returns:
        分析报告模板
    """
    if "商业" in case_type:
        return """
【商业事件舆情分析模板】

一、行业背景
二、事件梳理
   - 事件脉络：
     * 萌芽期：联系行业背景与宏观政策
     * 发酵期：分析多方主体如何参与事件
     * 爆发期：导火索、热度高峰期分析
     * 延续期：行业未来走向判断
三、品牌观察
   - 宣发策略分析
   - 品牌总热度分析
   - 品牌渠道热度分析（小红书、微博、抖音、新闻app、问答平台、论坛）
   - 品牌宣发成效
   - 舆论聚焦话题分析
   - SWOT前景观察分析
"""
    else:
        return """
【社会事件舆情分析模板】

一、事件脉络
   - 潜伏期
   - 萌芽期
   - 爆发期
   - 衰退期

二、回应观察
   - 回应处置梳理——回应分析
   - 趋势变化——舆情演变
   - 传播平台变化——各渠道对比
   - 情绪变化——舆论态度对比
   - 话题变化——回应前/回应后对比

三、总结复盘
   - 视觉化传播+情感化表达的话语分析
   - 舆论关注焦点偏移，议题泛化趋势分析
   - 舆论推手分析（KOL、情感类KOL、竞争对手话语）
   - 叙事手段分析（如分析此事件中对立叙事如何形成）
"""


@tool
def get_youth_sentiment_insight() -> str:
    """
    获取中国青年网民社会心态分析洞察。
    
    Returns:
        青年网民心态分析要点
    """
    insight_file = _find_reference_file(["青年网民心态.md", "中国青年网民社会心态调查报告（2024）.md"])
    
    if insight_file and insight_file.exists():
        content = insight_file.read_text(encoding='utf-8')
        # 返回前3000字符作为概要
        return content[:5000] + "\n\n[...详细内容见 references/青年网民心态.md...]"
    
    return "青年网民心态报告文件未找到"


@tool
def load_sentiment_knowledge(keyword: str) -> str:
    """
    根据关键词加载舆情知识库。
    
    可以在分析时根据具体需求加载相关的理论和方法论。
    
    Args:
        keyword: 关键词，如"框架"、"理论"、"案例"、"青年"等
    
    Returns:
        相关的舆情知识
    """
    keyword_map = {
        "框架": get_sentiment_analysis_framework(),
        "理论": get_sentiment_theories(),
        "社会事件": get_sentiment_case_template("社会事件"),
        "商业事件": get_sentiment_case_template("商业事件"),
        "青年": get_youth_sentiment_insight(),
    }
    
    # 模糊匹配
    for key, value in keyword_map.items():
        if key in keyword:
            return value
    
    # 默认返回框架
    return get_sentiment_analysis_framework()


# 为 LangChain 工具注册
sentiment_analysis_framework = get_sentiment_analysis_framework
sentiment_theories = get_sentiment_theories
sentiment_case_template = get_sentiment_case_template
youth_sentiment_insight = get_youth_sentiment_insight
load_sentiment_knowledge = load_sentiment_knowledge


if __name__ == "__main__":
    # 测试调用
    print("=== 框架测试 ===")
    print(get_sentiment_analysis_framework()[:500])
    print("\n=== 理论测试 ===")
    print(get_sentiment_theories()[:500])
