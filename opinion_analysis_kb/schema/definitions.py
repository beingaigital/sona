"""知识类型定义（Schema v2.0）

定义舆情分析知识库的8种核心知识类型：
- Methodology: 方法论、理论、模型、分析框架
- Case: 舆情事件案例
- DomainPlaybook: 领域应对指南
- Actor: 主体、机构、媒体、KOL
- RiskPattern: 风险模式
- ResponseTactic: 回应策略
- Evidence: 证据片段（新增）
- Scenario: 舆情场景（新增）

所有类型共享统一元数据字段。
"""

from __future__ import annotations

from typing import List, Optional
from datetime import datetime
from pydantic import BaseModel, Field, field_validator


class BaseKnowledge(BaseModel):
    """所有知识类型的基类，包含统一元数据字段"""
    id: str = Field(..., description="唯一标识")
    type: str = Field(..., description="知识类型")
    domain: Optional[str] = Field(None, description="领域: health/transport/panda/...")
    tags: List[str] = Field(default_factory=list, description="标签列表")
    source_path: Optional[str] = Field(None, description="原始markdown文件路径")
    source_url: Optional[str] = Field(None, description="原始来源URL")
    evidence_refs: List[str] = Field(default_factory=list, description="证据引用ID列表")
    confidence: float = Field(0.8, ge=0.0, le=1.0, description="置信度，范围 0.0-1.0")
    status: str = Field("candidate", description="状态: raw/candidate/approved/deprecated")
    created_at: str = Field(default_factory=lambda: datetime.now().isoformat(), description="创建时间")
    updated_at: str = Field(default_factory=lambda: datetime.now().isoformat(), description="更新时间")
    last_verified_at: Optional[str] = Field(None, description="最后验证时间")

    @field_validator("confidence", mode="before")
    @classmethod
    def normalize_confidence(cls, value: object) -> float:
        """兼容 high/medium/low 旧格式，并统一转换为 0-1 数值。"""
        if value is None or value == "":
            return 0.8
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            confidence_map = {
                "high": 0.9,
                "medium": 0.6,
                "low": 0.3,
            }
            normalized = value.strip().lower()
            if normalized in confidence_map:
                return confidence_map[normalized]
            try:
                return float(normalized)
            except ValueError as exc:
                raise ValueError(f"Invalid confidence value: {value}") from exc
        raise TypeError(f"Unsupported confidence type: {type(value).__name__}")


class Methodology(BaseKnowledge):
    """方法论/理论/模型"""
    type: str = "Methodology"
    name: str = Field(..., description="名称")
    description: str = Field(..., description="描述")
    keywords: List[str] = Field(default_factory=list, description="关键词")
    source: Optional[str] = Field(None, description="来源链接或引用")


class Case(BaseKnowledge):
    """舆情事件案例"""
    type: str = "Case"
    title: str = Field(..., description="标题")
    subdomain: Optional[str] = Field(None, description="子领域")
    scenario: Optional[str] = Field(None, description="场景ID")
    event_type: Optional[str] = Field(None, description="事件类型")
    summary: Optional[str] = Field(None, description="摘要")
    actors: List[str] = Field(default_factory=list, description="涉及主体ID列表")
    platforms: List[str] = Field(default_factory=list, description="涉及平台")
    timeline: Optional[str] = Field(None, description="时间线")
    trigger_points: List[str] = Field(default_factory=list, description="触发点")
    key_claims: List[str] = Field(default_factory=list, description="核心诉求/质疑点")
    public_sentiment: Optional[str] = Field(None, description="公众情绪结构")
    risk_patterns: List[str] = Field(default_factory=list, description="风险模式ID列表")
    response_tactics: List[str] = Field(default_factory=list, description="回应策略ID列表")
    official_response: Optional[str] = Field(None, description="官方回应")
    escalation_path: Optional[str] = Field(None, description="升级路径")
    turning_points: List[str] = Field(default_factory=list, description="转折点")
    resolution: Optional[str] = Field(None, description="解决结果")
    lessons_learned: List[str] = Field(default_factory=list, description="经验教训")
    severity: str = Field("medium", description="严重程度: high/medium/low")
    impact_scope: Optional[str] = Field(None, description="影响范围")
    start_date: Optional[str] = Field(None, description="开始日期")
    end_date: Optional[str] = Field(None, description="结束日期")


class DomainPlaybook(BaseKnowledge):
    """领域应对指南"""
    type: str = "DomainPlaybook"
    subdomains: List[str] = Field(default_factory=list, description="子领域列表")
    scenario_type: Optional[str] = Field(None, description="场景类型")
    risk_level: str = Field("medium", description="风险等级")
    response_stages: List[dict] = Field(default_factory=list, description="分阶段应对")
    stakeholders: List[str] = Field(default_factory=list, description="利益相关方")
    recommended_tactics: List[str] = Field(default_factory=list, description="推荐策略ID列表")
    do_and_dont: Optional[dict] = Field(None, description="可以做/不可以做")
    example_cases: List[str] = Field(default_factory=list, description="示例案例ID列表")
    evaluation_metrics: List[str] = Field(default_factory=list, description="评估指标")
    references: List[str] = Field(default_factory=list, description="参考资料")


class Actor(BaseKnowledge):
    """主体/机构/媒体/KOL"""
    type: str = "Actor"
    name: str = Field(..., description="名称")
    aliases: List[str] = Field(default_factory=list, description="别名列表")
    actor_type: str = Field("organization", description="类型: organization/media/kol/person/group")
    description: Optional[str] = Field(None, description="描述")
    domain_relevance: List[str] = Field(default_factory=list, description="相关领域")
    region: Optional[str] = Field(None, description="地域")
    platforms: List[str] = Field(default_factory=list, description="活跃平台")
    influence_level: str = Field("medium", description="影响力: high/medium/low")
    credibility: str = Field("medium", description="可信度: high/medium/low")


class RiskPattern(BaseKnowledge):
    """风险模式"""
    type: str = "RiskPattern"
    name: str = Field(..., description="名称")
    description: str = Field(..., description="描述")
    risk_category: Optional[str] = Field(None, description="风险类别")
    applicable_scenarios: List[str] = Field(default_factory=list, description="适用场景ID列表")
    indicators: List[str] = Field(default_factory=list, description="风险指标")
    trigger_conditions: List[str] = Field(default_factory=list, description="触发条件")
    early_warning_signals: List[str] = Field(default_factory=list, description="早期预警信号")
    typical_escalation_path: Optional[str] = Field(None, description="典型升级路径")
    related_emotions: List[str] = Field(default_factory=list, description="相关情绪")
    severity: str = Field("medium", description="严重程度: high/medium/low")
    recommended_tactics: List[str] = Field(default_factory=list, description="推荐策略ID列表")
    example_cases: List[str] = Field(default_factory=list, description="示例案例ID列表")


class ResponseTactic(BaseKnowledge):
    """回应策略"""
    type: str = "ResponseTactic"
    name: str = Field(..., description="名称")
    description: str = Field(..., description="描述")
    tactic_type: Optional[str] = Field(None, description="策略类型")
    applicable_scenarios: List[str] = Field(default_factory=list, description="适用场景ID列表")
    applicable_risk_patterns: List[str] = Field(default_factory=list, description="适用风险模式ID列表")
    applicable_actors: List[str] = Field(default_factory=list, description="适用主体类型")
    stage: Optional[str] = Field(None, description="适用阶段")
    preconditions: List[str] = Field(default_factory=list, description="前置条件")
    actions: List[str] = Field(default_factory=list, description="行动清单")
    message_templates: List[str] = Field(default_factory=list, description="话术模板")
    do_and_dont: Optional[dict] = Field(None, description="可以做/不可以做")
    success_metrics: List[str] = Field(default_factory=list, description="成功指标")
    failure_modes: List[str] = Field(default_factory=list, description="失败模式")
    example_cases: List[str] = Field(default_factory=list, description="示例案例ID列表")


class Evidence(BaseKnowledge):
    """证据片段（新增）"""
    type: str = "Evidence"
    source_doc_id: Optional[str] = Field(None, description="来源文档ID")
    chunk_text: str = Field(..., description="证据文本片段")
    chunk_summary: Optional[str] = Field(None, description="片段摘要")
    published_at: Optional[str] = Field(None, description="发布时间")
    author: Optional[str] = Field(None, description="作者")
    reliability: str = Field("medium", description="可靠性: high/medium/low")


class Scenario(BaseKnowledge):
    """舆情场景（新增）"""
    type: str = "Scenario"
    name: str = Field(..., description="场景名称")
    description: Optional[str] = Field(None, description="场景描述")
    typical_triggers: List[str] = Field(default_factory=list, description="典型触发因素")
    typical_risk_patterns: List[str] = Field(default_factory=list, description="典型风险模式ID列表")
    recommended_tactics: List[str] = Field(default_factory=list, description="推荐策略ID列表")
    example_cases: List[str] = Field(default_factory=list, description="示例案例ID列表")


# 类型映射
SCHEMA_TYPES = {
    "Methodology": Methodology,
    "Case": Case,
    "DomainPlaybook": DomainPlaybook,
    "Actor": Actor,
    "RiskPattern": RiskPattern,
    "ResponseTactic": ResponseTactic,
    "Evidence": Evidence,
    "Scenario": Scenario,
}


def generate_id(prefix: str = "kb") -> str:
    """生成唯一ID"""
    import uuid
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


def validate_and_parse(data: dict) -> BaseKnowledge:
    """验证数据并解析为对应的模型"""
    data_type = data.get("type")
    if data_type not in SCHEMA_TYPES:
        raise ValueError(f"Unknown type: {data_type}")
    
    model_class = SCHEMA_TYPES[data_type]
    return model_class(**data)
