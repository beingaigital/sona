"""Schema 配置文件"""

from pathlib import Path

# 知识库根目录：opinion_analysis_kb/
KB_ROOT = Path(__file__).resolve().parent.parent

# 原始 wiki 文档目录
DEFAULT_INPUT_DIR = KB_ROOT / "opinion_analysis_kb" / "references" / "wiki"

# 编译后的结构化数据输出目录
DEFAULT_OUTPUT_DIR = KB_ROOT / "compiled"

# 支持的知识类型
SUPPORTED_TYPES = [
    "Methodology",
    "Case",
    "DomainPlaybook",
    "Actor",
    "RiskPattern",
    "ResponseTactic",
    "Evidence",
    "Scenario",
]

# 置信度取值范围
CONFIDENCE_MIN = 0.0
CONFIDENCE_MAX = 1.0

# 旧版置信度文本映射
CONFIDENCE_LEVEL_MAP = {
    "high": 0.9,
    "medium": 0.6,
    "low": 0.3,
}

# 状态类型
STATUS_TYPES = ["raw", "candidate", "approved", "deprecated"]

# 领域列表
DOMAINS = ["health", "transport", "panda", "general"]

# 主体类型
ACTOR_TYPES = ["organization", "media", "kol", "person", "group"]

# 严重程度级别
SEVERITY_LEVELS = ["high", "medium", "low"]

# 回应阶段
RESPONSE_STAGES = ["0-6h", "6-24h", "1-3d", "3-7d", "review"]
