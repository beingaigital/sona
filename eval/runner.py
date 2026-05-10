"""Evaluation runner for Sona harness with AutoScorer integration."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any
from datetime import datetime


def run_evaluation(
    project_root: Path,
    target: Optional[str] = None,
    stage: Optional[str] = None,
    case_id: Optional[str] = None,
    suite: Optional[str] = None,
    mode: Optional[str] = None,
    output_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """
    Run evaluation harness with AutoScorer.

    Args:
        project_root: Project root directory path
        target: Filter by target type (workflow/tool/wiki)
        stage: Filter by stage name
        case_id: Run only one case by case id
        suite: Suite selector (reserved for future)
        mode: Filter by fixture mode (live/replay)
        output_dir: Directory to save evaluation results

    Returns:
        dict: Evaluation summary
    """
    cases_dir = project_root / "eval" / "golden_cases"
    
    # Setup output directory
    if output_dir is None:
        output_dir = project_root / "eval" / "results"
    output_dir.mkdir(parents=True, exist_ok=True)

    # 1. Load all cases
    cases = _load_cases(cases_dir)

    # 2. Apply filters
    filtered_cases = _filter_cases(cases, target, stage, case_id, mode)

    # 3. Evaluate each case
    results = []
    for case in filtered_cases:
        try:
            if case.get("mode") == "live":
                report_content, report_data = _generate_report(case["query"])
            else:
                report_content, report_data = _load_cached_report(project_root, case["case_id"])

            # Use AutoScorer for evaluation
            score_result = _evaluate_with_scorer(
                report_content=report_content,
                report_data=report_data,
                case=case,
                output_dir=output_dir
            )
            results.append(score_result)
        except Exception as exc:
            error_result = {
                "case_id": case.get("case_id", "unknown"),
                "domain": case.get("domain", "unknown"),
                "score": 0,
                "max_score": 100,
                "percentage": 0,
                "status": "error",
                "summary": f"Evaluation failed: {exc}",
                "recommendations": [],
                "breakdown": {},
                "issues": [str(exc)],
                "timestamp": datetime.now().isoformat()
            }
            results.append(error_result)
            
            # Save error result
            _save_result(output_dir, error_result)

    # 4. Summarize and return
    summary = _summarize_results(results)
    
    # 5. Save summary report
    _save_summary(output_dir, summary)
    
    return summary


def _load_cases(cases_dir: Path) -> List[Dict[str, Any]]:
    """Scan directory and load all case files."""
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
            print(f"[WARN] Skip invalid case file {case_file}: {exc}")

    return cases


def _filter_cases(
    cases: List[Dict[str, Any]],
    target: Optional[str],
    stage: Optional[str],
    case_id: Optional[str],
    mode: Optional[str],
) -> List[Dict[str, Any]]:
    """Apply filter conditions."""
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


def _generate_report(query: str) -> Tuple[str, Dict[str, Any]]:
    """
    Generate report using Sona tool.
    
    Returns:
        Tuple of (report_content, report_data)
    """
    # TODO: Replace with actual Sona integration
    content = f"""[Mock Report] 舆情分析报告：{query}

摘要：
本报告针对「{query}」进行舆情分析，梳理了事件发展脉络和舆论关注点。

事件回顾：
事件起因于近期网络热议，涉及多个利益相关方，引发广泛社会关注。

舆情分析：
1. 主流媒体观点：客观报道，关注事件进展
2. 社交媒体情绪：呈现多元化态势
3. 专家意见：存在不同立场的观点交锋

趋势判断：
预计事件将持续发酵，建议密切关注后续发展。

建议：
1. 加强信息监测
2. 及时回应公众关切
3. 做好风险预案
"""
    data = {
        "event_title": query,
        "time_range": "近一周",
        "data_volume": 1000,
        "sentiment": "neutral"
    }
    return content, data


def _load_cached_report(project_root: Path, case_id: str) -> Tuple[str, Dict[str, Any]]:
    """Load historical report from cache."""
    cache_dir = project_root / "eval" / "cache"
    content_file = cache_dir / f"{case_id}_report.txt"
    data_file = cache_dir / f"{case_id}_data.json"

    content = ""
    data = {}

    if content_file.exists():
        content = content_file.read_text(encoding="utf-8")
    
    if data_file.exists():
        try:
            with open(data_file, "r", encoding="utf-8") as f:
                data = json.load(f)
        except json.JSONDecodeError:
            pass

    return content, data


def _evaluate_with_scorer(
    report_content: str,
    report_data: Dict[str, Any],
    case: Dict[str, Any],
    output_dir: Path
) -> Dict[str, Any]:
    """
    使用 AutoScorer 评估报告
    """
    try:
        from eval.scorer import AutoScorer
    except ImportError:
        # Fallback to basic scoring if scorer not available
        return _evaluate_report_basic(report_content, case)
    
    # Initialize scorer with custom weights if provided
    weights = case.get("scoring_weights")
    scorer = AutoScorer(weights)
    
    # Run scoring
    result = scorer.score(
        report_content=report_content,
        report_data=report_data,
        case_config=case
    )
    
    # Convert to dict and add metadata
    result_dict = result.to_dict()
    result_dict["timestamp"] = datetime.now().isoformat()
    result_dict["report_preview"] = report_content[:200] + "..." if len(report_content) > 200 else report_content
    
    # Save individual result
    _save_result(output_dir, result_dict)
    
    return result_dict


def _evaluate_report_basic(report: str, case: Dict[str, Any]) -> Dict[str, Any]:
    """
    基础评分（当 AutoScorer 不可用时使用）
    """
    rubric = case.get("rubric", {})
    total_score = 0
    breakdown = {}
    issues = []

    # 1. Completeness score
    completeness_config = rubric.get("completeness", {})
    expected_points = case.get("expected_key_points", [])
    completeness_score = _score_completeness(report, expected_points, completeness_config)
    breakdown["completeness"] = {
        "score": completeness_score,
        "max_score": completeness_config.get("max_score", 0),
        "description": completeness_config.get("description", "Completeness")
    }
    total_score += completeness_score

    # 2. Accuracy score
    accuracy_config = rubric.get("accuracy", {})
    red_lines = case.get("red_lines", [])
    accuracy_score, accuracy_issues = _score_accuracy(report, red_lines, accuracy_config)
    breakdown["accuracy"] = {
        "score": accuracy_score,
        "max_score": accuracy_config.get("max_score", 0),
        "description": accuracy_config.get("description", "Accuracy")
    }
    total_score += accuracy_score
    issues.extend(accuracy_issues)

    # 3. Structure score
    structure_config = rubric.get("structure", {})
    structure_score, structure_issues = _score_structure(report, structure_config)
    breakdown["structure"] = {
        "score": structure_score,
        "max_score": structure_config.get("max_score", 0),
        "description": structure_config.get("description", "Structure")
    }
    total_score += structure_score
    issues.extend(structure_issues)

    # 4. Insight score
    insight_config = rubric.get("insight", {})
    insight_score, insight_issues = _score_insight(report, insight_config)
    breakdown["insight"] = {
        "score": insight_score,
        "max_score": insight_config.get("max_score", 0),
        "description": insight_config.get("description", "Insight")
    }
    total_score += insight_score
    issues.extend(insight_issues)

    max_score = sum(r.get("max_score", 0) for r in rubric.values())
    status = "passed" if total_score >= (max_score * 0.6) else "failed"

    return {
        "case_id": case["case_id"],
        "domain": case.get("domain", "unknown"),
        "score": total_score,
        "max_score": max_score,
        "percentage": round((total_score / max_score * 100), 2) if max_score > 0 else 0,
        "breakdown": breakdown,
        "status": status,
        "summary": f"Basic scoring: {status} with {total_score}/{max_score} points",
        "recommendations": [],
        "issues": issues,
        "timestamp": datetime.now().isoformat()
    }


def _score_completeness(report: str, expected_points: List[str], config: Dict[str, Any]) -> int:
    """Score: Coverage of key points. Uses keyword matching."""
    if not expected_points:
        return config.get("max_score", 0)

    max_score = config.get("max_score", 0)
    point_score = max_score / len(expected_points) if expected_points else 0
    score = 0

    for point in expected_points:
        keywords = _extract_keywords(point)
        matched = sum(1 for kw in keywords if kw.lower() in report.lower())

        if matched >= len(keywords) * 0.5:
            score += point_score
        elif matched > 0:
            score += point_score * 0.5

    return int(score)


def _score_accuracy(report: str, red_lines: List[str], config: Dict[str, Any]) -> Tuple[int, List[str]]:
    """Score: Factual accuracy. Detect red line violations."""
    max_score = config.get("max_score", 0)
    score = max_score
    issues = []

    if not red_lines:
        return score, issues

    penalty = max_score / len(red_lines) if red_lines else 0

    for line in red_lines:
        forbidden_keywords = _extract_keywords(line)
        matched = sum(1 for kw in forbidden_keywords if kw.lower() in report.lower())

        if matched >= len(forbidden_keywords) * 0.3:
            score -= penalty
            issues.append(f"Red line violated: {line}")

    return max(0, int(score)), issues


def _score_structure(report: str, config: Dict[str, Any]) -> Tuple[int, List[str]]:
    """Score: Report structure completeness. Check required sections."""
    max_score = config.get("max_score", 0)
    score = max_score
    issues = []

    required_sections = ["摘要", "事件", "舆情", "趋势", "建议"]
    section_score = max_score / len(required_sections) if required_sections else 0

    for section in required_sections:
        if section not in report:
            score -= section_score
            issues.append(f"Missing required section: {section}")

    return max(0, int(score)), issues


def _score_insight(report: str, config: Dict[str, Any]) -> Tuple[int, List[str]]:
    """Score: Depth of insight. Check for prediction/warning content."""
    max_score = config.get("max_score", 0)
    issues = []

    insight_keywords = ["预测", "预警", "风险", "趋势", "建议", "展望", "可能", "或将"]
    matched = sum(1 for kw in insight_keywords if kw in report)

    if matched >= 2:
        return max_score, issues
    else:
        issues.append("Missing trend prediction or risk warning content")
        return 0, issues


def _extract_keywords(text: str) -> List[str]:
    """Extract keywords from text."""
    stop_words = {"的", "了", "是", "在", "和", "与", "或", "有", "被", "将", "要", "及", "等"}
    words = re.findall(r"[\u4e00-\u9fa5]{2,}|[a-zA-Z]+", text)
    return [w for w in words if w not in stop_words and len(w) >= 2]


def _save_result(output_dir: Path, result: Dict[str, Any]) -> None:
    """Save individual evaluation result to JSON file."""
    case_id = result.get("case_id", "unknown")
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"{case_id}_{timestamp}.json"
    
    filepath = output_dir / filename
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    
    print(f"[INFO] Saved evaluation result: {filepath}")


def _save_summary(output_dir: Path, summary: Dict[str, Any]) -> None:
    """Save evaluation summary to JSON file."""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"summary_{timestamp}.json"
    
    filepath = output_dir / filename
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    
    print(f"[INFO] Saved evaluation summary: {filepath}")


def _summarize_results(results: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Summarize evaluation results."""
    if not results:
        return {
            "total_cases": 0,
            "run_cases": 0,
            "passed_cases": 0,
            "failed_cases": 0,
            "error_cases": 0,
            "average_score": 0.0,
            "average_percentage": 0.0,
            "details": []
        }

    total_cases = len(results)
    passed_cases = sum(1 for r in results if r["status"] == "passed")
    failed_cases = sum(1 for r in results if r["status"] == "failed")
    error_cases = sum(1 for r in results if r["status"] == "error")

    valid_scores = [r["score"] for r in results if r["status"] != "error"]
    valid_percentages = [r.get("percentage", 0) for r in results if r["status"] != "error"]
    
    average_score = sum(valid_scores) / len(valid_scores) if valid_scores else 0.0
    average_percentage = sum(valid_percentages) / len(valid_percentages) if valid_percentages else 0.0

    return {
        "total_cases": total_cases,
        "run_cases": total_cases - error_cases,
        "passed_cases": passed_cases,
        "failed_cases": failed_cases,
        "error_cases": error_cases,
        "average_score": round(average_score, 2),
        "average_percentage": round(average_percentage, 2),
        "timestamp": datetime.now().isoformat(),
        "details": results
    }
