"""知识库 Schema 模块（v2.0）

包含8种核心知识类型：
- Methodology: 方法论
- Case: 案例
- DomainPlaybook: 领域应对指南
- Actor: 主体
- RiskPattern: 风险模式
- ResponseTactic: 回应策略
- Evidence: 证据片段（新增）
- Scenario: 舆情场景（新增）

所有类型共享统一元数据字段。
"""

from .definitions import (
    BaseKnowledge,
    Methodology,
    Case,
    DomainPlaybook,
    Actor,
    RiskPattern,
    ResponseTactic,
    Evidence,
    Scenario,
    SCHEMA_TYPES,
    generate_id,
    validate_and_parse,
)

from .compiler import (
    parse_markdown,
    extract_sections,
    classify_by_content,
    markdown_to_structured,
    compile_wiki_directory,
)

__all__ = [
    "BaseKnowledge",
    "Methodology",
    "Case",
    "DomainPlaybook",
    "Actor",
    "RiskPattern",
    "ResponseTactic",
    "Evidence",
    "Scenario",
    "SCHEMA_TYPES",
    "generate_id",
    "validate_and_parse",
    "parse_markdown",
    "extract_sections",
    "classify_by_content",
    "markdown_to_structured",
    "compile_wiki_directory",
]
