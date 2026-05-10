原始要求
- 建立 `eval/golden_cases/`。
- 收集 15-30 个样例，包括健康、交通、大熊猫、消费、文旅、教育、政务。
- 每个样例包括 query、期望报告要点、错误红线、参考材料路径。

验收：

- 至少 15 个样例能被 `scripts/eval_runner.py` 批量读取。
- 每个样例都能对应到一个明确评分 rubric。



# Eval Runner 升级指引文档

## 1. 项目背景

本项目是一个**舆情监控工具**，需要建立评测体系（Golden Cases）来检验工具生成的舆情报告质量。

当前状态：
- ✅ `scripts/eval_runner.py` - 命令行入口已存在
- ❌ `tests/evals/runner.py` - 核心评测逻辑缺失，需要实现
- ❌ `eval/golden_cases/` - 测试样例目录缺失，需要建立并填充15-30个样例

## 2. 现有代码分析

### 2.1 入口脚本 `scripts/eval_runner.py`

```python
"""CLI wrapper for Day1 evaluation harness."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _build_parser() -&gt; argparse.ArgumentParser:
   parser = argparse.ArgumentParser(description="Run evaluation harness cases.")
   parser.add_argument("--target", choices=["workflow", "tool", "wiki"], help="Filter by target type.")
   parser.add_argument("--stage", help="Filter by stage name.")
   parser.add_argument("--case", dest="case_id", help="Run only one case by case id.")
   parser.add_argument("--suite", help="Suite selector (reserved for Day2).")
   parser.add_argument("--mode", choices=["live", "replay"], help="Filter by fixture mode.")
   return parser


def main() -&gt; int:
   parser = _build_parser()
   args = parser.parse_args()

   project_root = Path(__file__).resolve().parents[1]
   if str(project_root) not in sys.path:
       sys.path.insert(0, str(project_root))

   try:
       from tests.evals.runner import run_evaluation
   except Exception as exc:  # pragma: no cover - import guard
       print(f"[ERROR] failed to import eval runner: {exc}")
       return 2

   summary = run_evaluation(
       project_root=project_root,
       target=args.target,
       stage=args.stage,
       case_id=args.case_id,
       suite=args.suite,
       mode=args.mode,
   )
   print(json.dumps(summary, ensure_ascii=False, indent=2))
   return 0


if __name__ == "__main__":
   raise SystemExit(main())

项目结构：
project_root/
├── scripts/
│   └── eval_runner.py          # 已有入口
├── tests/
│   └── evals/
│       ├── __init__.py
│       └── runner.py           # 【需实现】核心评测逻辑
├── eval/
│   └── golden_cases/           # 【需建立】测试样例目录
│       ├── case_01_health.json
│       ├── case_02_traffic.json
│       ├── case_03_panda.json
│       ├── case_04_consumption.json
│       ├── case_05_tourism.json
│       ├── case_06_education.json
│       ├── case_07_government.json
│       └── ... (共15-30个)
└── data/
    └── references/             # 【需建立】参考材料存放目录
        ├── health/
        ├── traffic/
        └── ...

3.2 样例 JSON 格式规范
每个样例文件必须包含以下字段：
{
  "case_id": "case_01_health",
  "domain": "health",
  "query": "2024年流感疫苗接种争议事件舆情分析",
  "description": "简要说明这个测试样例的背景和目的",
  
  "expected_key_points": [
    "争议焦点梳理：安全性 vs 有效性讨论",
    "专家观点汇总：至少包含2种不同立场",
    "官方回应时间线：卫健委、疾控中心的声明",
    "社交媒体情绪分布：微博、抖音平台差异"
  ],
  
  "red_lines": [
    "严禁编造不存在的医学专家姓名或言论",
    "严禁虚构官方机构未发布的声明",
    "严禁将不同年份的事件混淆"
  ],
  
  "reference_materials": [
    "data/references/health/flu_vaccine_2024_news.json",
    "data/references/health/cdc_statement_20240115.txt"
  ],
  
  "rubric": {
    "completeness": {
      "description": "要点覆盖度",
      "max_score": 30,
      "criteria": "每个期望要点10分，部分覆盖5分，未覆盖0分"
    },
    "accuracy": {
      "description": "事实准确性",
      "max_score": 40,
      "criteria": "每条红线触碰扣20分，扣完为止"
    },
    "structure": {
      "description": "报告结构完整性",
      "max_score": 20,
      "criteria": "需包含摘要、事件回顾、舆情分析、趋势判断、建议五部分，每部分4分"
    },
    "insight": {
      "description": "洞察深度",
      "max_score": 10,
      "criteria": "是否有趋势预测或风险预警，有则满分，无则0分"
    }
  },

  3.3 核心评测逻辑 tests/evals/runner.py
3.3.1 完整实现代码  
  "target": "tool",
  "stage": "day1",
  "mode": "live"
}

"""Evaluation runner for Day1 harness."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Optional


def run_evaluation(
    project_root: Path,
    target: Optional[str] = None,
    stage: Optional[str] = None,
    case_id: Optional[str] = None,
    suite: Optional[str] = None,
    mode: Optional[str] = None,
) -> dict:
    """
    运行评测流程。
    
    Args:
        project_root: 项目根目录路径
        target: 过滤条件，只评测指定target类型的样例
        stage: 过滤条件，只评测指定stage的样例
        case_id: 过滤条件，只评测指定样例
        suite: 预留参数，Day2使用
        mode: 过滤条件，"live"实时调用，"replay"回放历史
    
    Returns:
        dict: 评测结果汇总
    """
    cases_dir = project_root / "eval" / "golden_cases"
    
    # 1. 加载所有样例
    cases = _load_cases(cases_dir)
    
    # 2. 应用过滤条件
    filtered_cases = _filter_cases(cases, target, stage, case_id, mode)
    
    # 3. 对每个样例执行评测
    results = []
    for case in filtered_cases:
        try:
            if case.get("mode") == "live":
                report = _generate_report(case["query"])
            else:
                report = _load_cached_report(project_root, case["case_id"])
            
            score_result = _evaluate_report(report, case)
            results.append(score_result)
        except Exception as exc:
            results.append({
                "case_id": case.get("case_id", "unknown"),
                "domain": case.get("domain", "unknown"),
                "score": 0,
                "max_score": 100,
                "breakdown": {},
                "status": "error",
                "issues": [f"评测执行失败: {exc}"]
            })
    
    # 4. 汇总并返回
    return _summarize_results(results)


def _load_cases(cases_dir: Path) -> list[dict]:
    """扫描目录加载所有样例文件。"""
    cases = []
    if not cases_dir.exists():
        return cases
    
    for case_file in sorted(cases_dir.glob("case_*.json")):
        try:
            with open(case_file, "r", encoding="utf-8") as f:
                case = json.load(f)
                case["_source_file"] = str(case_file)
                cases.append(case)
        except (json.JSONDecodeError, IOError) as exc:
            print(f"[WARN] 跳过无效样例文件 {case_file}: {exc}")
    
    return cases


def _filter_cases(
    cases: list[dict],
    target: Optional[str],
    stage: Optional[str],
    case_id: Optional[str],
    mode: Optional[str],
) -> list[dict]:
    """应用过滤条件。"""
    filtered = cases
    
    if case_id:
        filtered = [c for c in filtered if c.get("case_id") == case_id]
    if target:
        filtered = [c for c in filtered if c.get("target") == target]
    if stage:
        filtered = [c for c in filtered if c.get("stage") == stage]
    if mode:
        filtered = [c for c in filtered if c.get("mode") == mode]
    
    return filtered


def _generate_report(query: str) -> str:
    """
    调用舆情工具生成报告。
    
    Day1: 返回模拟报告用于测试框架
    Day2: 替换为真实工具调用
    """
    # TODO: 替换为真实工具调用
    return f"[模拟报告] 关于「{query}」的舆情分析报告\n\n摘要：...\n事件回顾：...\n舆情分析：...\n趋势判断：...\n建议：..."


def _load_cached_report(project_root: Path, case_id: str) -> str:
    """从缓存加载历史报告。"""
    cache_dir = project_root / "eval" / "cache"
    cache_file = cache_dir / f"{case_id}_report.txt"
    
    if cache_file.exists():
        with open(cache_file, "r", encoding="utf-8") as f:
            return f.read()
    
    return ""


def _evaluate_report(report: str, case: dict) -> dict:
    """
    根据样例标准对报告进行评分。
    Day1采用基于规则的简化评分。
    """
    rubric = case.get("rubric", {})
    total_score = 0
    breakdown = {}
    issues = []
    
    # 1. 完整性评分
    completeness_config = rubric.get("completeness", {})
    expected_points = case.get("expected_key_points", [])
    completeness_score = _score_completeness(report, expected_points, completeness_config)
    breakdown["completeness"] = completeness_score
    total_score += completeness_score
    
    # 2. 准确性评分
    accuracy_config = rubric.get("accuracy", {})
    red_lines = case.get("red_lines", [])
    accuracy_score, accuracy_issues = _score_accuracy(report, red_lines, accuracy_config)
    breakdown["accuracy"] = accuracy_score
    total_score += accuracy_score
    issues.extend(accuracy_issues)
    
    # 3. 结构评分
    structure_config = rubric.get("structure", {})
    structure_score, structure_issues = _score_structure(report, structure_config)
    breakdown["structure"] = structure_score
    total_score += structure_score
    issues.extend(structure_issues)
    
    # 4. 洞察评分
    insight_config = rubric.get("insight", {})
    insight_score, insight_issues = _score_insight(report, insight_config)
    breakdown["insight"] = insight_score
    total_score += insight_score
    issues.extend(insight_issues)
    
    max_score = sum(r.get("max_score", 0) for r in rubric.values())
    status = "passed" if total_score >= (max_score * 0.6) else "failed"
    
    return {
        "case_id": case["case_id"],
        "domain": case.get("domain", "unknown"),
        "score": total_score,
        "max_score": max_score,
        "breakdown": breakdown,
        "status": status,
        "issues": issues
    }


def _score_completeness(report: str, expected_points: list[str], config: dict) -> int:
    """评分：要点覆盖度。通过关键词匹配判断。"""
    if not expected_points:
        return config.get("max_score", 0)
    
    max_score = config.get("max_score", 0)
    point_score = max_score / len(expected_points) if expected_points else 0
    score = 0
    
    for point in expected_points:
        # 提取关键词（取前5个实词作为关键词）
        keywords = _extract_keywords(point)
        matched = sum(1 for kw in keywords if kw.lower() in report.lower())
        
        if matched >= len(keywords) * 0.5:
            score += point_score
        elif matched > 0:
            score += point_score * 0.5
    
    return int(score)


def _score_accuracy(report: str, red_lines: list[str], config: dict) -> tuple[int, list[str]]:
    """评分：事实准确性。检测红线触碰。"""
    max_score = config.get("max_score", 0)
    score = max_score
    issues = []
    
    if not red_lines:
        return score, issues
    
    penalty = max_score / len(red_lines) if red_lines else 0
    
    for line in red_lines:
        # 提取禁止内容关键词
        forbidden_keywords = _extract_keywords(line)
        matched = sum(1 for kw in forbidden_keywords if kw.lower() in report.lower())
        
        # 简单规则：如果报告中出现红线描述的具体内容，判定为触碰
        if matched >= len(forbidden_keywords) * 0.3:
            score -= penalty
            issues.append(f"触碰红线: {line}")
    
    return max(0, int(score)), issues


def _score_structure(report: str, config: dict) -> tuple[int, list[str]]:
    """评分：报告结构完整性。检查必要章节是否存在。"""
    max_score = config.get("max_score", 0)
    score = max_score
    issues = []
    
    required_sections = ["摘要", "事件", "舆情", "趋势", "建议"]
    section_score = max_score / len(required_sections) if required_sections else 0
    
    for section in required_sections:
        if section not in report:
            score -= section_score
            issues.append(f"缺少必要章节: {section}")
    
    return max(0, int(score)), issues


def _score_insight(report: str, config: dict) -> tuple[int, list[str]]:
    """评分：洞察深度。检查是否有预测/预警相关内容。"""
    max_score = config.get("max_score", 0)
    issues = []
    
    insight_keywords = ["预测", "预警", "风险", "趋势", "建议", "展望", "可能", "或将"]
    matched = sum(1 for kw in insight_keywords if kw in report)
    
    if matched >= 2:
        return max_score, issues
    else:
        issues.append("缺少趋势预测或风险预警内容")
        return 0, issues


def _extract_keywords(text: str) -> list[str]:
    """提取文本中的关键词（简单实现：过滤掉常见虚词）。"""
    stop_words = {"的", "了", "是", "在", "和", "与", "或", "有", "被", "将", "要", "及", "等", "至少", "每个", "包含", "需", "是否"}
    words = re.findall(r"[\u4e00-\u9fa5]{2,}|[a-zA-Z]+", text)
    return [w for w in words if w not in stop_words and len(w) >= 2]


def _summarize_results(results: list[dict]) -> dict:
    """汇总评测结果。"""
    if not results:
        return {
            "total_cases": 0,
            "run_cases": 0,
            "passed_cases": 0,
            "failed_cases": 0,
            "average_score": 0.0,
            "details": []
        }
    
    total_cases = len(results)
    passed_cases = sum(1 for r in results if r["status"] == "passed")
    failed_cases = sum(1 for r in results if r["status"] == "failed")
    error_cases = sum(1 for r in results if r["status"] == "error")
    
    valid_scores = [r["score"] for r in results if r["status"] != "error"]
    average_score = sum(valid_scores) / len(valid_scores) if valid_scores else 0.0
    
    return {
        "total_cases": total_cases,
        "run_cases": total_cases - error_cases,
        "passed_cases": passed_cases,
        "failed_cases": failed_cases,
        "error_cases": error_cases,
        "average_score": round(average_score, 2),
        "details": results
    }

    样例模板：
    {
  "case_id": "case_01_health",
  "domain": "health",
  "query": "2024年流感疫苗接种争议事件舆情分析",
  "description": "测试工具对公共卫生争议事件的舆情分析能力，包括多方观点梳理和官方回应追踪",
  "expected_key_points": [
    "争议焦点梳理：安全性与有效性讨论",
    "专家观点汇总：包含支持和质疑两种立场",
    "官方回应时间线：卫健委或疾控中心声明",
    "社交媒体情绪分布：不同平台舆论差异"
  ],
  "red_lines": [
    "严禁编造不存在的医学专家姓名或言论",
    "严禁虚构官方机构未发布的声明",
    "严禁将不同年份的疫苗事件混淆"
  ],
  "reference_materials": [
    "data/references/health/flu_vaccine_2024_news.json"
  ],
  "rubric": {
    "completeness": {
      "description": "要点覆盖度",
      "max_score": 30,
      "criteria": "每个期望要点10分，部分覆盖5分，未覆盖0分"
    },
    "accuracy": {
      "description": "事实准确性",
      "max_score": 40,
      "criteria": "每条红线触碰扣20分，扣完为止"
    },
    "structure": {
      "description": "报告结构完整性",
      "max_score": 20,
      "criteria": "需包含摘要、事件回顾、舆情分析、趋势判断、建议五部分，每部分4分"
    },
    "insight": {
      "description": "洞察深度",
      "max_score": 10,
      "criteria": "是否有趋势预测或风险预警，有则满分，无则0分"
    }
  },
  "target": "tool",
  "stage": "day1",
  "mode": "live"
}