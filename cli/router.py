"""
意图识别与路由层：根据用户 Query 智能决定执行路径。

功能：
1. 意图识别：判断是"舆情事件分析"还是简单查询/搜索
2. 数据检测：检查 sandbox 目录是否已有相关数据
3. 路由决策：决定调用 event_analysis_workflow 还是 reactagent
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from rich.console import Console

from utils.session_manager import get_session_manager
from utils.path import get_project_root, get_sandbox_dir

console = Console()

# 舆情事件分析相关的关键词模式
EVENT_ANALYSIS_PATTERNS = [
    # 中文关键词
    r"舆情分析",
    r"舆论分析",
    r"舆情报告",
    r"网络舆论",
    r"社会心态",
    r"舆论规律",
    r"舆情规律",
    r"危机分析",
    r"舆论应对",
    r"传播分析",
    r"舆论态势",
    r"舆情监测",
    r"舆论研判",
    r"事件分析",
    r"315晚会",
    r"央视315",
    r"315曝光",
    # 分析类动词短语 - 更宽松的匹配
    r".*分析.*报告",
    r".*分析.*事件",
    r".*事件.*分析",
    r".*生成.*报告",
    r".*分析.*数据",
    r".*舆论.*观察",
    r".*舆情.*观察",
    r".*舆情.*事件",
    r".*舆论.*事件",
    r"分析.*舆情",
    r"分析.*舆论",
    r"帮我分析",
    r"帮我生成",
    r".*高铁.*熊孩子.*",
    # 英文关键词
    r"sentiment.*analysis",
    r"public.*opinion",
    r"media.*analysis",
    r"crisis.*analysis",
]

# 轻量“事件概述/经过”意图：只需提取搜索词与事件简介，不必走完整报告流程
EVENT_BRIEF_PATTERNS = [
    r"事件经过",
    r"经过概述",
    r"事件概述",
    r"简要概述",
    r"简述",
    r"发生了什么",
    r"来龙去脉",
    r"先说说.*经过",
    r"先概述",
]

# 热点发现/态势感知意图模式
HOT_DISCOVERY_PATTERNS = [
    r"热点",
    r"热搜",
    r"态势感知",
    r"趋势感知",
    r"今天.*(发生|热点|舆情)",
    r"最近.*(发生|热点|舆情)",
    r"当前.*(热点|舆情)",
    r"不知道.*(热点|舆情)",
    r"帮我看.*(热点|热搜|舆情)",
    r"发现.*(热点|事件)",
]

# 需要重新搜索/采集数据的意图模式（而不是使用现有数据）
RE_SEARCH_PATTERNS = [
    r"搜索.*",
    r"采集.*",
    r"抓取.*",
    r"获取.*数据",
    r"查一下.*",
    r"帮我搜",
    r"去搜索",
    r"重新.*分析",
    r"再.*分析",
]

# 数据文件的后缀名
DATA_FILE_EXTENSIONS = [".json", ".csv", ".xlsx", ".parquet"]


@dataclass
class IntentResult:
    """意图识别结果"""
    intent: str  # "event_analysis" | "hotspot_discovery" | "simple_search" | "general_query"
    confidence: float  # 0.0 - 1.0
    reasoning: str  # 识别理由
    keywords: List[str]  # 识别出的关键词
    suggested_action: str  # 建议的行动


@dataclass
class DataDetectionResult:
    """数据检测结果"""
    has_data: bool  # 是否有现成数据
    data_paths: List[str]  # 数据文件路径列表
    relevance_scores: List[float]  # 与 query 的相关性得分
    task_ids: List[str]  # 对应的 task_id
    reasoning: str  # 检测理由


class IntentRecognizer:
    """意图识别器：分析用户 Query 的意图"""

    def __init__(self):
        self.patterns = [re.compile(p, re.IGNORECASE) for p in EVENT_ANALYSIS_PATTERNS]
        self.brief_patterns = [re.compile(p, re.IGNORECASE) for p in EVENT_BRIEF_PATTERNS]
        self.hot_patterns = [re.compile(p, re.IGNORECASE) for p in HOT_DISCOVERY_PATTERNS]
        self.re_search_patterns = [re.compile(p, re.IGNORECASE) for p in RE_SEARCH_PATTERNS]

    def recognize(self, query: str) -> IntentResult:
        """
        识别用户 Query 的意图

        Args:
            query: 用户查询

        Returns:
            IntentResult: 意图识别结果
        """
        query_lower = query.lower()

        # 提取匹配的关键词
        matched_keywords = []
        for pattern in self.patterns:
            match = pattern.search(query)
            if match:
                matched_keywords.append(match.group(0))

        brief_keywords = []
        for pattern in self.brief_patterns:
            match = pattern.search(query)
            if match:
                brief_keywords.append(match.group(0))

        # 检查是否明确要求重新搜索
        is_re_search = any(p.search(query) for p in self.re_search_patterns)

        hot_keywords = []
        for pattern in self.hot_patterns:
            match = pattern.search(query)
            if match:
                hot_keywords.append(match.group(0))

        # 判断意图类型
        if brief_keywords and not matched_keywords:
            confidence = min(0.72 + 0.08 * len(brief_keywords), 0.95)
            intent = "event_brief"
            reasoning = f"检测到事件概述意图关键词: {', '.join(brief_keywords[:3])}"
        elif hot_keywords and not matched_keywords:
            confidence = min(0.7 + 0.08 * len(hot_keywords), 0.93)
            intent = "hotspot_discovery"
            reasoning = f"检测到热点发现关键词: {', '.join(hot_keywords[:3])}"
        elif matched_keywords:
            # 有舆情分析相关关键词
            if is_re_search:
                # 用户明确要求重新搜索/采集
                confidence = 0.9
                intent = "event_analysis"
                reasoning = f"检测到舆情分析关键词，但用户明确要求重新搜索"
            else:
                confidence = min(0.7 + 0.1 * len(matched_keywords), 0.95)
                intent = "event_analysis"
                reasoning = f"检测到舆情分析关键词: {', '.join(matched_keywords[:3])}"
        else:
            # 没有明确关键词，默认为一般查询
            confidence = 0.5
            intent = "general_query"
            reasoning = "未检测到特定意图，使用通用查询模式"

        return IntentResult(
            intent=intent,
            confidence=confidence,
            reasoning=reasoning,
            keywords=brief_keywords or matched_keywords or hot_keywords,
            suggested_action=(
                "run_event_analysis_workflow"
                if intent == "event_analysis"
                else (
                    "run_event_brief_workflow"
                    if intent == "event_brief"
                    else ("run_hottopics_workflow" if intent == "hotspot_discovery" else "run_reactagent")
                )
            ),
        )


@dataclass
class RoutePolicy:
    """路由策略偏好。"""

    preference: str = "平衡"
    prefer_confirm: bool = True
    auto_retry: bool = True
    max_retry: int = 2
    report_length: str = "中篇"
    source: str = "default"


class PolicyLoader:
    """加载 USER/SOUL/AGENT/MEMORY 策略文档，提供轻量路由偏好。"""

    def __init__(self, project_root: Optional[str] = None):
        self.project_root = Path(project_root) if project_root else get_project_root()
        self.user_path = self.project_root / "USER.md"
        self.soul_path = self.project_root / "SOUL.md"
        self.agent_path = self.project_root / "AGENT.md"
        self.memory_path = self.project_root / "MEMORY.md"
        self._policy = self._load_policy()

    @staticmethod
    def _safe_read(path: Path) -> str:
        try:
            return path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            return ""

    @staticmethod
    def _extract_scalar(text: str, key: str) -> Optional[str]:
        if not text:
            return None
        # 支持 "key: value" 或 "- key: value"
        pattern = re.compile(rf"^\s*-?\s*{re.escape(key)}\s*:\s*(.+?)\s*$", re.IGNORECASE | re.MULTILINE)
        m = pattern.search(text)
        if not m:
            return None
        value = m.group(1).strip().strip("`").strip()
        # 模板行(含 | 枚举)不当作真实配置
        if "|" in value:
            return None
        return value

    @staticmethod
    def _to_bool(value: Optional[str], default: bool) -> bool:
        if value is None:
            return default
        v = value.strip().lower()
        if v in ("true", "1", "yes", "y", "on", "是"):
            return True
        if v in ("false", "0", "no", "n", "off", "否"):
            return False
        return default

    @staticmethod
    def _to_int(value: Optional[str], default: int) -> int:
        if value is None:
            return default
        try:
            return int(value)
        except Exception:
            return default

    def _load_policy(self) -> RoutePolicy:
        user_text = self._safe_read(self.user_path)
        soul_text = self._safe_read(self.soul_path)
        agent_text = self._safe_read(self.agent_path)
        memory_text = self._safe_read(self.memory_path)

        preference = self._extract_scalar(user_text, "preference") or "平衡"
        prefer_confirm = self._to_bool(self._extract_scalar(user_text, "prefer_confirm"), True)
        auto_retry = self._to_bool(self._extract_scalar(user_text, "auto_retry"), True)
        max_retry = self._to_int(self._extract_scalar(user_text, "max_retry"), 2)
        report_length = self._extract_scalar(user_text, "report_length") or "中篇"

        # 若系统约束强调“必须确认”，强制 prefer_confirm=true
        if "必须请求确认的场景" in agent_text or "安全优先" in soul_text:
            prefer_confirm = True
        # 若 memory 文档强调快速实现阶段，则偏成本效率
        if "阶段1：基于文件的LTM" in memory_text and preference == "平衡":
            preference = "成本优先"

        return RoutePolicy(
            preference=preference,
            prefer_confirm=prefer_confirm,
            auto_retry=auto_retry,
            max_retry=max_retry,
            report_length=report_length,
            source="USER.md/SOUL.md/AGENT.md/MEMORY.md",
        )

    def get_policy(self) -> RoutePolicy:
        return self._policy


class DataDetector:
    """数据检测器：检查 sandbox 目录是否已有相关数据"""

    def __init__(self, sandbox_base: Optional[str] = None):
        self.sandbox_base = Path(sandbox_base) if sandbox_base else get_sandbox_dir()

    def detect(self, query: str, intent: str) -> DataDetectionResult:
        """
        检测是否有现成数据可用

        Args:
            query: 用户查询
            intent: 识别的意图

        Returns:
            DataDetectionResult: 数据检测结果
        """
        # 如果意图不是事件分析，不需要检测数据
        if intent != "event_analysis":
            return DataDetectionResult(
                has_data=False,
                data_paths=[],
                relevance_scores=[],
                task_ids=[],
                reasoning="非舆情事件分析意图，无需检测数据"
            )

        # 提取查询中的关键实体（用于匹配）
        query_keywords = self._extract_keywords(query)

        # 搜索相关数据
        data_paths = []
        relevance_scores = []
        task_ids = []

        if not self.sandbox_base.exists():
            return DataDetectionResult(
                has_data=False,
                data_paths=[],
                relevance_scores=[],
                task_ids=[],
                reasoning="Sandbox 目录不存在"
            )

        # 遍历 sandbox 下的所有 task 目录
        for task_dir in self.sandbox_base.iterdir():
            if not task_dir.is_dir():
                continue

            task_id = task_dir.name

            # 检查是否有数据文件
            result_files = self._find_data_files(task_dir, query_keywords)

            if result_files:
                # 计算相关性得分
                score = self._calculate_relevance(query, query_keywords, task_dir, result_files)
                if score > 0.22:  # 阈值过滤（放宽，避免漏掉同类历史事件）
                    data_paths.extend(result_files)
                    relevance_scores.extend([score] * len(result_files))
                    task_ids.extend([task_id] * len(result_files))

        if data_paths:
            # 按相关性排序
            sorted_data = sorted(
                zip(data_paths, relevance_scores, task_ids),
                key=lambda x: x[1],
                reverse=True
            )
            data_paths, relevance_scores, task_ids = zip(*sorted_data)
            data_paths = list(data_paths)
            relevance_scores = list(relevance_scores)
            task_ids = list(task_ids)

            return DataDetectionResult(
                has_data=True,
                data_paths=data_paths,
                relevance_scores=relevance_scores,
                task_ids=task_ids,
                reasoning=f"检测到 {len(data_paths)} 个相关数据文件，最高相关度: {max(relevance_scores):.2f}"
            )
        else:
            return DataDetectionResult(
                has_data=False,
                data_paths=[],
                relevance_scores=[],
                task_ids=[],
                reasoning="未检测到相关数据文件，需要重新采集"
            )

    def _extract_keywords(self, query: str) -> List[str]:
        """从查询中提取关键词"""
        stop_words = {
            "的", "了", "和", "是", "在", "我", "有", "这", "那", "个",
            "帮", "请", "给", "对", "进行", "一下", "帮我", "请帮",
            "舆情", "舆论", "分析", "报告", "事件", "热点", "态势感知",
        }
        normalized = re.sub(r"[^\w\u4e00-\u9fff]+", " ", query or "").strip().lower()
        segments = re.findall(r"[a-z0-9]+|[\u4e00-\u9fff]{2,}", normalized)

        keywords: List[str] = []
        seen: set[str] = set()

        def add_kw(value: str) -> None:
            v = (value or "").strip().lower()
            if len(v) < 2:
                return
            if v in stop_words:
                return
            if v in seen:
                return
            seen.add(v)
            keywords.append(v)

        suffixes = [
            "舆情分析", "舆情研判", "舆情", "分析报告",
            "报告", "分析", "事件", "热点", "态势感知",
        ]
        for seg in segments:
            add_kw(seg)
            for sfx in suffixes:
                if seg.endswith(sfx) and len(seg) > len(sfx) + 1:
                    add_kw(seg[: -len(sfx)])
            mix = re.match(r"(\d{2,4})([\u4e00-\u9fff]{1,8})", seg)
            if mix:
                add_kw(mix.group(1))
                add_kw(mix.group(2))
                add_kw(mix.group(0))

        for num in re.findall(r"\d{2,4}", normalized):
            add_kw(num)

        # 对超长中文短语做实体化切分，提升“分析…” vs “分析一下…”这类近似 query 的复用命中率
        simplified = normalized
        for marker in ("分析一下", "帮我分析", "请帮分析", "分析", "事件", "相关", "舆情", "舆论", "报告", "一下"):
            simplified = simplified.replace(marker, " ")
        for seg in re.findall(r"[\u4e00-\u9fff]{2,}", simplified):
            add_kw(seg)
            # 生成少量 2~4 字片段，帮助匹配人名/事件核心词
            max_n = min(4, len(seg))
            for n in range(2, max_n + 1):
                for i in range(0, len(seg) - n + 1):
                    add_kw(seg[i : i + n])

        return keywords[:24]

    def _find_data_files(self, task_dir: Path, keywords: List[str]) -> List[str]:
        """在 task 目录中查找可复用数据文件（优先可直接分析的 CSV）。"""
        candidate_files: List[Path] = []

        # 检查结果文件和过程文件目录
        for subdir_name in ["结果文件", "过程文件", "data", "results"]:
            subdir = task_dir / subdir_name
            if not subdir.exists() or not subdir.is_dir():
                continue
            for ext in DATA_FILE_EXTENSIONS:
                for f in subdir.rglob(f"*{ext}"):
                    if not f.is_file():
                        continue
                    # 排除临时文件
                    lname = f.name.lower()
                    if "tmp" in lname or "temp" in lname:
                        continue
                    candidate_files.append(f)

        # 根目录下的数据文件（避免重复遍历）
        for f in task_dir.iterdir():
            if f.is_file() and f.suffix in DATA_FILE_EXTENSIONS:
                candidate_files.append(f)

        # 从 dataset_summary*.json 反解 CSV（优先）
        resolved_csv_from_json: List[str] = []
        direct_csv: List[str] = []
        other_files: List[str] = []

        for p in candidate_files:
            if p.suffix.lower() == ".json" and "dataset_summary" in p.name:
                csv_path = self._extract_csv_from_dataset_summary(p)
                if csv_path:
                    resolved_csv_from_json.append(csv_path)

            if p.suffix.lower() == ".csv":
                direct_csv.append(str(p))
            else:
                other_files.append(str(p))

        # CSV 优先返回，JSON/XLSX 作为兜底
        ordered = resolved_csv_from_json + direct_csv + other_files
        deduped: List[str] = []
        seen: set[str] = set()
        for path in ordered:
            if path in seen:
                continue
            seen.add(path)
            deduped.append(path)
        return deduped

    def _extract_csv_from_dataset_summary(self, summary_json_path: Path) -> Optional[str]:
        """从 dataset_summary*.json 中提取 save_path（CSV）。"""
        try:
            with open(summary_json_path, "r", encoding="utf-8", errors="replace") as f:
                obj = json.load(f)
            if not isinstance(obj, dict):
                return None
            save_path = obj.get("save_path")
            if isinstance(save_path, str) and save_path.strip():
                p = Path(save_path.strip())
                if p.exists() and p.is_file() and p.suffix.lower() == ".csv":
                    return str(p)
        except Exception:
            return None
        return None

    @staticmethod
    def _normalize_text(text: str) -> str:
        return re.sub(r"[^\w\u4e00-\u9fff]+", "", (text or "").lower())

    @staticmethod
    def _char_ngrams(text: str, n: int = 2) -> set[str]:
        s = (text or "").strip()
        if not s:
            return set()
        if len(s) <= n:
            return {s}
        return {s[i : i + n] for i in range(0, len(s) - n + 1)}

    def _char_ngram_similarity(self, a: str, b: str, n: int = 2) -> float:
        sa = self._char_ngrams(a, n=n)
        sb = self._char_ngrams(b, n=n)
        if not sa or not sb:
            return 0.0
        inter = len(sa & sb)
        union = len(sa | sb)
        return float(inter) / float(union) if union else 0.0

    def _calculate_relevance(
        self,
        query: str,
        keywords: List[str],
        task_dir: Path,
        result_files: List[str],
    ) -> float:
        """计算数据与查询的相关性"""
        # 读取 session 信息获取描述
        task_id = task_dir.name
        session_manager = get_session_manager()
        session_data = session_manager.load_session(task_id)

        # 获取会话描述和初始查询
        description = ""
        initial_query = ""
        if session_data:
            description = str(session_data.get("description", "") or "")
            initial_query = str(session_data.get("initial_query", "") or "")

        # 文件路径上下文（即使目录名是 task_id，也可通过文件名补充信号）
        file_context = " ".join(Path(p).name for p in result_files[:8])

        # 合并文本进行匹配
        context_text = f"{description} {initial_query} {file_context}".lower().strip()
        if not context_text:
            return 0.0

        # 计算关键词匹配数
        keyword_score = 0.0
        if keywords:
            matched = sum(1 for kw in keywords if kw and kw.lower() in context_text)
            keyword_score = matched / max(1, len(keywords))

        # 额外加入字符级相似度，提升对“词序变化/黏连短语”的鲁棒性
        norm_query = self._normalize_text(query)
        norm_desc = self._normalize_text(description)
        norm_init = self._normalize_text(initial_query)
        norm_files = self._normalize_text(file_context)
        norm_context = self._normalize_text(context_text)
        char_score = max(
            self._char_ngram_similarity(norm_query, norm_context, n=2),
            self._char_ngram_similarity(norm_query, norm_desc, n=2),
            self._char_ngram_similarity(norm_query, norm_init, n=2),
            self._char_ngram_similarity(norm_query, norm_files, n=2),
        )

        # 中文场景下关键词抽取常不稳定，提升字符相似度权重
        score = char_score if not keywords else (0.45 * keyword_score + 0.55 * char_score)

        # 数字锚点（如 315/2024）额外加权
        digit_hits = 0
        for num in re.findall(r"\d{2,4}", query or ""):
            if num and num in context_text:
                digit_hits += 1
        if digit_hits:
            score += min(0.18, 0.06 * digit_hits)

        # 时间因子：越新的数据相关性越高
        mtime = task_dir.stat().st_mtime
        age_days = (time.time() - mtime) / (24 * 3600)
        if age_days < 1:
            score *= 1.2  # 一天内的数据加分
        elif age_days < 7:
            score *= 1.1  # 一周内的数据轻微加分
        elif age_days > 30:
            score *= 0.8  # 一个月前的数据降权

        return min(max(score, 0.0), 1.0)


class IntentRouter:
    """
    意图路由器：综合意图识别和数据检测，决定执行路径
    """

    def __init__(self):
        self.intent_recognizer = IntentRecognizer()
        self.data_detector = DataDetector()
        self.policy_loader = PolicyLoader()

    def route(self, query: str, task_id: Optional[str] = None) -> Tuple[str, Any]:
        """
        路由决策

        Args:
            query: 用户查询
            task_id: 当前任务 ID（可选）

        Returns:
            Tuple[str, Any]: (路由决策, 附加数据)
            - 路由决策: "event_analysis_workflow" | "event_analysis_with_existing_data" | "event_brief_workflow" | "hottopics_workflow" | "reactagent"
            - 附加数据: dict 包含 intent_result, data_detection_result 等
        """
        # Step 1: 意图识别
        intent_result = self.intent_recognizer.recognize(query)

        console.print(f"[dim]🔍 意图识别: {intent_result.intent} (置信度: {intent_result.confidence:.2f})[/dim]")
        console.print(f"[dim]   {intent_result.reasoning}[/dim]")

        # Step 2: 数据检测（仅对舆情事件分析意图）
        data_result = self.data_detector.detect(query, intent_result.intent)

        console.print(f"[dim]📊 数据检测: {'有现成数据' if data_result.has_data else '需要重新采集'}[/dim]")
        if data_result.has_data:
            console.print(f"[dim]   {data_result.reasoning}[/dim]")

        policy = self.policy_loader.get_policy()
        console.print(
            f"[dim]🧭 路由策略: preference={policy.preference}, prefer_confirm={policy.prefer_confirm}, auto_retry={policy.auto_retry}[/dim]"
        )

        # Step 3: 路由决策
        additional_data = {
            "intent_result": intent_result,
            "data_result": data_result,
            "route_policy": {
                "preference": policy.preference,
                "prefer_confirm": policy.prefer_confirm,
                "auto_retry": policy.auto_retry,
                "max_retry": policy.max_retry,
                "report_length": policy.report_length,
                "source": policy.source,
            },
        }

        if intent_result.intent == "hotspot_discovery":
            return "hottopics_workflow", additional_data

        if intent_result.intent == "event_brief":
            return "event_brief_workflow", additional_data

        if intent_result.intent == "event_analysis":
            if data_result.has_data:
                # 有现成数据时，根据偏好决定路径倾向
                pref = (policy.preference or "").strip()
                if pref in ("覆盖优先", "深挖优先"):
                    return "event_analysis_workflow", additional_data
                return "event_analysis_with_existing_data", additional_data
            else:
                # 没有现成数据，需要完整流程
                return "event_analysis_workflow", additional_data
        else:
            # 一般查询，使用 reactagent
            return "reactagent", additional_data


# 全局单例
_router: Optional[IntentRouter] = None


def get_router() -> IntentRouter:
    """获取路由器单例"""
    global _router
    if _router is None:
        _router = IntentRouter()
    return _router


def route_query(query: str, task_id: Optional[str] = None) -> Tuple[str, Any]:
    """
    便捷函数：对用户 Query 进行路由

    Args:
        query: 用户查询
        task_id: 当前任务 ID（可选）

    Returns:
        Tuple[str, Any]: (路由决策, 附加数据)
    """
    router = get_router()
    return router.route(query, task_id)
