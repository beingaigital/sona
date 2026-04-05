"""解释与研判工具：生成 interpretation.json，用于报告叙事骨架与 Graph RAG 参数提取。"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional, List

from langchain_core.tools import tool

from model.factory import get_tools_model
from utils.path import ensure_task_dirs
from utils.prompt_loader import get_interpretation_prompt
from utils.methodology_loader import load_methodology_for_report
from utils.task_context import get_task_id


def _read_json_file(path: str) -> Dict[str, Any]:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"JSON 文件不存在: {path}")
    with open(p, "r", encoding="utf-8", errors="replace") as f:
        content = json.load(f)
    if not isinstance(content, dict):
        raise ValueError(f"JSON 不是对象: {path}")
    return content


def _extract_json_from_text(text: str) -> Dict[str, Any]:
    """
    从模型返回文本中提取 JSON 对象。
    """

    if not text:
        raise ValueError("模型返回为空")

    # 优先提取第一个 { ... } 区块
    match = re.search(r"\{[\s\S]*\}", text.strip())
    if not match:
        raise ValueError("未找到 JSON 对象片段")

    parsed = json.loads(match.group())
    if not isinstance(parsed, dict):
        raise ValueError("JSON 解析结果不是对象")
    return parsed


def _truncate_methodology(text: str, max_chars: int = 3800) -> str:
    s = (text or "").strip()
    if len(s) <= max_chars:
        return s
    return s[:max_chars] + "..."


@dataclass(frozen=True)
class InterpretationOutput:
    narrative_summary: str
    key_events: List[str]
    key_risks: List[str]
    event_type: Optional[str]
    domain: Optional[str]
    stage: Optional[str]
    indicators_dimensions: List[str]
    theory_names: List[str]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "narrative_summary": self.narrative_summary,
            "key_events": self.key_events,
            "key_risks": self.key_risks,
            "event_type": self.event_type or "",
            "domain": self.domain or "",
            "stage": self.stage or "",
            "indicators_dimensions": self.indicators_dimensions,
            "theory_names": self.theory_names,
        }


@tool
def generate_interpretation(
    eventIntroduction: str,
    timelineResultPath: str,
    sentimentResultPath: str,
    datasetSummaryPath: str,
) -> str:
    """
    描述：生成 interpretation.json（解释与研判）。
    使用时机：在获得时间线分析与情感倾向分析后，用于最终报告的叙事骨架，
    并产出 Graph RAG 所需的 event_type/domain/stage/dimensions/theory_names。
    输入：
    - eventIntroduction：事件基础介绍
    - timelineResultPath：analysis_timeline 工具保存的 JSON 文件路径
    - sentimentResultPath：analysis_sentiment 工具保存的 JSON 文件路径
    - datasetSummaryPath：dataset_summary 工具保存的 JSON 文件路径
    输出：JSON 字符串，包含 result_file_path 与解释结果
    """

    task_id = get_task_id()
    if not task_id:
        return json.dumps(
            {"error": "未找到任务ID，请确保在任务上下文中调用", "result_file_path": "", "interpretation": {}},
            ensure_ascii=False,
        )

    try:
        timeline_json = _read_json_file(timelineResultPath)
        sentiment_json = _read_json_file(sentimentResultPath)
        dataset_json = _read_json_file(datasetSummaryPath)
    except Exception as e:
        return json.dumps(
            {"error": f"读取输入 JSON 失败: {str(e)}", "result_file_path": "", "interpretation": {}},
            ensure_ascii=False,
        )

    prompt_template = get_interpretation_prompt()
    if not prompt_template:
        return json.dumps(
            {"error": "缺少 interpretation_prompt 配置", "result_file_path": "", "interpretation": {}},
            ensure_ascii=False,
        )

    # 给 LLM 的输入：只传必要段落，避免过长
    timeline_short = {
        "summary": timeline_json.get("summary", ""),
        "timeline": timeline_json.get("timeline", []),
    }
    sentiment_short = {
        "statistics": sentiment_json.get("statistics", {}),
        "positive_summary": sentiment_json.get("positive_summary", []),
        "negative_summary": sentiment_json.get("negative_summary", []),
    }
    dataset_short = dataset_json.get("dataset_summary", {}) or dataset_json

    prompt = prompt_template.format(
        event_introduction=eventIntroduction,
        timeline_result_json=json.dumps(timeline_short, ensure_ascii=False, indent=2),
        sentiment_result_json=json.dumps(sentiment_short, ensure_ascii=False, indent=2),
        dataset_summary_json=json.dumps(dataset_short, ensure_ascii=False, indent=2),
        methodology_json=json.dumps(
            {"reference": _truncate_methodology(load_methodology_for_report(topic=eventIntroduction), 3800)},
            ensure_ascii=False,
            indent=2,
        ),
    )

    model = get_tools_model()
    try:
        from langchain_core.messages import SystemMessage, HumanMessage

        messages = [
            SystemMessage(content="你是一个专业的舆情分析研判专家，输出必须是严格 JSON。"),
            HumanMessage(content=prompt),
        ]
        response = model.invoke(messages)
        result_text = getattr(response, "content", None) or str(response)
        parsed = _extract_json_from_text(result_text)
    except Exception as e:
        return json.dumps(
            {"error": f"模型生成 interpretation 失败: {str(e)}", "result_file_path": "", "interpretation": {}},
            ensure_ascii=False,
        )

    try:
        narrative_summary = str(parsed.get("narrative_summary", "") or "").strip()
        key_events = parsed.get("key_events") or []
        key_risks = parsed.get("key_risks") or []
        indicators_dimensions = parsed.get("indicators_dimensions") or []
        theory_names = parsed.get("theory_names") or []

        event_type = parsed.get("event_type") or ""
        domain = parsed.get("domain") or ""
        stage = parsed.get("stage") or ""

        # 基础校验
        if not narrative_summary:
            raise ValueError("缺少 narrative_summary")
        if not indicators_dimensions:
            raise ValueError("缺少 indicators_dimensions")

        output = InterpretationOutput(
            narrative_summary=narrative_summary,
            key_events=[str(x) for x in key_events][:5],
            key_risks=[str(x) for x in key_risks][:5],
            event_type=str(event_type) if event_type else None,
            domain=str(domain) if domain else None,
            stage=str(stage) if stage else None,
            indicators_dimensions=[str(x) for x in indicators_dimensions][:6],
            theory_names=[str(x) for x in theory_names][:3],
        )
        interpretation_dict = output.to_dict()
    except Exception as e:
        return json.dumps(
            {"error": f"interpretation 结果校验失败: {str(e)}", "result_file_path": "", "interpretation": {}},
            ensure_ascii=False,
        )

    # 保存到过程文件夹：写入固定文件名，便于后续工作流稳定引用
    try:
        process_dir = ensure_task_dirs(task_id)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_path_ts = process_dir / f"interpretation_{timestamp}.json"
        out_path = process_dir / "interpretation.json"
        payload = {"interpretation": interpretation_dict, "generated_at": datetime.now().isoformat(sep=" ")}
        with open(out_path_ts, "w", encoding="utf-8", errors="replace") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
    except Exception as e:
        return json.dumps(
            {"error": f"保存 interpretation.json 失败: {str(e)}", "result_file_path": "", "interpretation": interpretation_dict},
            ensure_ascii=False,
        )

    # 写入固定文件名（覆盖）
    try:
        with open(out_path, "w", encoding="utf-8", errors="replace") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
    except Exception:
        # 固定文件失败不影响 timestamp 输出
        out_path = out_path_ts

    return json.dumps(
        {"result_file_path": str(out_path), "interpretation": interpretation_dict},
        ensure_ascii=False,
    )
