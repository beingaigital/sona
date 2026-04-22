"""Core metric computations for evaluation harness (Day3 MVP)."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple


@dataclass(frozen=True)
class CaseExpectations:
    required_fields: Sequence[str]
    thresholds: Dict[str, float]
    max_latency_ms: Optional[float]
    min_sources: Optional[int]
    min_unique_source_titles: Optional[int]
    required_source_fields: Sequence[str]
    required_artifact_fields: Sequence[str]
    compare_min_dimensions: Optional[int]
    case_must_contain_any: Sequence[str]
    case_must_not_contain_any: Sequence[str]
    report_min_references_count: Optional[int]
    report_required_sections: Sequence[str]
    report_required_flags: Sequence[str]
    report_depth_required_flags: Sequence[str]
    report_depth_min_analogous_cases_count: Optional[int]
    report_depth_min_pattern_points_count: Optional[int]
    report_depth_min_theory_frameworks_count: Optional[int]
    sentiment_min_parse_success_rate: Optional[float]
    sentiment_min_llm_coverage: Optional[float]
    sentiment_agreement_warning_min: Optional[float]
    sentiment_agreement_warning_max: Optional[float]
    consistency_warning_budget: Optional[int]


def keywords(text: str) -> List[str]:
    if not text:
        return []
    raw_tokens = re.findall(r"[a-zA-Z0-9]{2,}|[\u4e00-\u9fff]{2,}", text)
    tokens: List[str] = []
    for tok in raw_tokens:
        tok = tok.strip()
        if not tok:
            continue
        tokens.append(tok)
        if re.fullmatch(r"[\u4e00-\u9fff]{4,}", tok):
            max_n = min(4, len(tok))
            for n in range(2, max_n + 1):
                for i in range(0, len(tok) - n + 1):
                    tokens.append(tok[i : i + n])

    deduped: List[str] = []
    seen = set()
    for token in tokens:
        if token in seen:
            continue
        seen.add(token)
        deduped.append(token)
    return deduped[:40]


def safe_div(numerator: float, denominator: float) -> float:
    if denominator <= 0:
        return 0.0
    return numerator / denominator


def extract_source_snippets(output: Dict[str, Any]) -> List[str]:
    sources = output.get("sources")
    snippets: List[str] = []
    if isinstance(sources, list):
        for item in sources:
            if isinstance(item, dict):
                snippets.append(str(item.get("snippet", "")))
                snippets.append(str(item.get("title", "")))
            elif isinstance(item, str):
                snippets.append(item)
    return [s for s in snippets if s]


def traceability_score(answer: str, evidence_snippets: List[str]) -> float:
    if not answer.strip():
        return 0.0
    statements = [s.strip() for s in answer.replace("；", "。").split("。") if len(s.strip()) >= 8]
    if not statements:
        return 0.0
    supported = 0
    for statement in statements:
        tokens = keywords(statement)
        hit = any(
            token and len(token) >= 2 and token in snippet for token in tokens for snippet in evidence_snippets
        )
        if hit:
            supported += 1
    return safe_div(float(supported), float(len(statements)))


def relevance_score(query: str, answer: str) -> float:
    q = set(keywords(query))
    if not q:
        return 0.0
    a = set(keywords(answer))
    return safe_div(float(len(q & a)), float(len(q)))


def structure_completeness(output: Dict[str, Any], required_fields: Sequence[str]) -> float:
    if not required_fields:
        return 1.0
    hit = sum(1 for field in required_fields if field in output and output[field] not in (None, "", []))
    return safe_div(float(hit), float(len(required_fields)))


def parse_expectations(expectations: Dict[str, Any]) -> CaseExpectations:
    required_fields = expectations.get("required_fields")
    required_fields = required_fields if isinstance(required_fields, list) else []

    thresholds = expectations.get("thresholds")
    thresholds = thresholds if isinstance(thresholds, dict) else {}
    threshold_floats: Dict[str, float] = {}
    for k, v in thresholds.items():
        try:
            threshold_floats[str(k)] = float(v)
        except Exception:
            continue

    max_latency_ms = expectations.get("max_latency_ms", thresholds.get("latency_ms"))
    try:
        max_latency_ms_f = float(max_latency_ms) if max_latency_ms is not None else None
    except Exception:
        max_latency_ms_f = None

    min_sources = expectations.get("min_sources")
    try:
        min_sources_i = int(min_sources) if min_sources is not None else None
    except Exception:
        min_sources_i = None

    min_unique_titles = expectations.get("min_unique_source_titles")
    try:
        min_unique_titles_i = int(min_unique_titles) if min_unique_titles is not None else None
    except Exception:
        min_unique_titles_i = None

    required_source_fields = expectations.get("required_source_fields")
    required_source_fields = required_source_fields if isinstance(required_source_fields, list) else []
    required_source_fields = [str(x) for x in required_source_fields if str(x).strip()]

    required_artifact_fields = expectations.get("required_artifact_fields")
    required_artifact_fields = required_artifact_fields if isinstance(required_artifact_fields, list) else []
    required_artifact_fields = [str(x) for x in required_artifact_fields if str(x).strip()]

    compare_cfg = expectations.get("compare") if isinstance(expectations.get("compare"), dict) else {}
    compare_min_dimensions_raw = compare_cfg.get("min_dimensions")
    try:
        compare_min_dimensions = int(compare_min_dimensions_raw) if compare_min_dimensions_raw is not None else None
    except Exception:
        compare_min_dimensions = None

    case_cfg = expectations.get("case_example") if isinstance(expectations.get("case_example"), dict) else {}
    must_contain_any = case_cfg.get("must_contain_any")
    must_contain_any = must_contain_any if isinstance(must_contain_any, list) else []
    must_contain_any = [str(x) for x in must_contain_any if str(x).strip()]

    must_not_contain_any = case_cfg.get("must_not_contain_any")
    must_not_contain_any = must_not_contain_any if isinstance(must_not_contain_any, list) else []
    must_not_contain_any = [str(x) for x in must_not_contain_any if str(x).strip()]

    report_cfg = expectations.get("report") if isinstance(expectations.get("report"), dict) else {}
    report_min_refs_raw = report_cfg.get("min_references_count")
    try:
        report_min_refs = int(report_min_refs_raw) if report_min_refs_raw is not None else None
    except Exception:
        report_min_refs = None

    report_required_sections = report_cfg.get("required_sections")
    report_required_sections = report_required_sections if isinstance(report_required_sections, list) else []
    report_required_sections = [str(x) for x in report_required_sections if str(x).strip()]

    report_required_flags = report_cfg.get("required_flags")
    report_required_flags = report_required_flags if isinstance(report_required_flags, list) else []
    report_required_flags = [str(x) for x in report_required_flags if str(x).strip()]

    report_depth_cfg = expectations.get("report_depth") if isinstance(expectations.get("report_depth"), dict) else {}
    report_depth_required_flags = report_depth_cfg.get("required_flags")
    report_depth_required_flags = report_depth_required_flags if isinstance(report_depth_required_flags, list) else []
    report_depth_required_flags = [str(x) for x in report_depth_required_flags if str(x).strip()]

    min_analogous_cases_raw = report_depth_cfg.get("min_analogous_cases_count")
    try:
        min_analogous_cases_count = int(min_analogous_cases_raw) if min_analogous_cases_raw is not None else None
    except Exception:
        min_analogous_cases_count = None

    min_pattern_points_raw = report_depth_cfg.get("min_pattern_points_count")
    try:
        min_pattern_points_count = int(min_pattern_points_raw) if min_pattern_points_raw is not None else None
    except Exception:
        min_pattern_points_count = None

    min_theory_frameworks_raw = report_depth_cfg.get("min_theory_frameworks_count")
    try:
        min_theory_frameworks_count = (
            int(min_theory_frameworks_raw) if min_theory_frameworks_raw is not None else None
        )
    except Exception:
        min_theory_frameworks_count = None

    sentiment_cfg = expectations.get("sentiment") if isinstance(expectations.get("sentiment"), dict) else {}
    min_parse_success_raw = sentiment_cfg.get("min_parse_success_rate")
    try:
        min_parse_success_rate = float(min_parse_success_raw) if min_parse_success_raw is not None else None
    except Exception:
        min_parse_success_rate = None
    min_llm_coverage_raw = sentiment_cfg.get("min_llm_coverage")
    try:
        min_llm_coverage = float(min_llm_coverage_raw) if min_llm_coverage_raw is not None else None
    except Exception:
        min_llm_coverage = None

    agreement_warn_cfg = (
        sentiment_cfg.get("agreement_warning")
        if isinstance(sentiment_cfg.get("agreement_warning"), dict)
        else {}
    )
    agreement_warn_min_raw = agreement_warn_cfg.get("min")
    try:
        agreement_warn_min = float(agreement_warn_min_raw) if agreement_warn_min_raw is not None else None
    except Exception:
        agreement_warn_min = None
    agreement_warn_max_raw = agreement_warn_cfg.get("max")
    try:
        agreement_warn_max = float(agreement_warn_max_raw) if agreement_warn_max_raw is not None else None
    except Exception:
        agreement_warn_max = None

    consistency_cfg = expectations.get("consistency") if isinstance(expectations.get("consistency"), dict) else {}
    warning_budget_raw = consistency_cfg.get("warning_budget")
    try:
        consistency_warning_budget = int(warning_budget_raw) if warning_budget_raw is not None else None
    except Exception:
        consistency_warning_budget = None

    return CaseExpectations(
        required_fields=required_fields,
        thresholds=threshold_floats,
        max_latency_ms=max_latency_ms_f,
        min_sources=min_sources_i,
        min_unique_source_titles=min_unique_titles_i,
        required_source_fields=required_source_fields,
        required_artifact_fields=required_artifact_fields,
        compare_min_dimensions=compare_min_dimensions,
        case_must_contain_any=must_contain_any,
        case_must_not_contain_any=must_not_contain_any,
        report_min_references_count=report_min_refs,
        report_required_sections=report_required_sections,
        report_required_flags=report_required_flags,
        report_depth_required_flags=report_depth_required_flags,
        report_depth_min_analogous_cases_count=min_analogous_cases_count,
        report_depth_min_pattern_points_count=min_pattern_points_count,
        report_depth_min_theory_frameworks_count=min_theory_frameworks_count,
        sentiment_min_parse_success_rate=min_parse_success_rate,
        sentiment_min_llm_coverage=min_llm_coverage,
        sentiment_agreement_warning_min=agreement_warn_min,
        sentiment_agreement_warning_max=agreement_warn_max,
        consistency_warning_budget=consistency_warning_budget,
    )


def compute_metrics(
    *,
    query: str,
    output: Dict[str, Any],
    latency_ms: int,
    expectations: CaseExpectations,
    project_root: Optional[Path] = None,
) -> Dict[str, Any]:
    answer = str(output.get("answer", ""))
    evidence = extract_source_snippets(output)
    raw_metrics = output.get("metrics") if isinstance(output.get("metrics"), dict) else {}
    metrics: Dict[str, Any] = {
        "latency_ms": float(latency_ms),
        "traceability_score": traceability_score(answer, evidence),
        "relevance_score": relevance_score(query, answer),
        "structure_completeness": structure_completeness(output, expectations.required_fields),
        "fallback_rate": 0.0,
        "section_coverage": float(raw_metrics.get("section_coverage", 0.0) or 0.0),
        "html_parse_success": 1.0 if bool(raw_metrics.get("html_parse_success")) else 0.0,
    }
    agreement_obj = raw_metrics.get("agreement_with_existing")
    if isinstance(agreement_obj, dict):
        # keep as hidden helper field for warning logic (won't affect numeric thresholds)
        metrics["_sentiment_agreement_obj"] = agreement_obj  # type: ignore[assignment]
    consistency = _consistency_signals(output=output, project_root=project_root)
    metrics["lifecycle_consistency"] = 1.0 if consistency.get("lifecycle_consistency_ok", True) else 0.0
    metrics["placeholder_leakage"] = float(consistency.get("placeholder_hits", 0) or 0)
    metrics["metric_source_consistency"] = 1.0 if consistency.get("metric_source_consistency_ok", True) else 0.0
    metrics["claim_consistency"] = 1.0 if consistency.get("claim_consistency_ok", True) else 0.0
    metrics["_consistency_reasons"] = consistency.get("reasons", [])
    return metrics


def hard_fail_reasons(
    *,
    output: Dict[str, Any],
    metrics: Dict[str, Any],
    expectations: CaseExpectations,
    project_root: Optional[Path] = None,
) -> List[str]:
    fail_reasons: List[str] = []

    if expectations.required_fields and metrics.get("structure_completeness", 0.0) < 1.0:
        fail_reasons.append("required fields missing from output")

    max_latency = expectations.max_latency_ms
    if max_latency is not None and metrics.get("latency_ms", 0.0) > float(max_latency):
        fail_reasons.append(f"latency_ms too high: {metrics['latency_ms']:.0f} > {float(max_latency):.0f}")

    for name in ("traceability_score", "structure_completeness"):
        threshold = expectations.thresholds.get(name)
        if threshold is None:
            continue
        if metrics.get(name, 0.0) < float(threshold):
            fail_reasons.append(f"{name} below threshold: {metrics.get(name, 0.0):.3f} < {float(threshold):.3f}")

    min_sources = expectations.min_sources
    if min_sources is not None:
        sources = output.get("sources")
        src_count = len(sources) if isinstance(sources, list) else 0
        if src_count < int(min_sources):
            fail_reasons.append(f"min_sources not met: {src_count} < {int(min_sources)}")

    if expectations.required_source_fields:
        sources = output.get("sources")
        if not isinstance(sources, list):
            fail_reasons.append("sources must be a list when required_source_fields is set")
        else:
            for idx, src in enumerate(sources):
                if not isinstance(src, dict):
                    fail_reasons.append(f"sources[{idx}] must be an object")
                    continue
                missing = [f for f in expectations.required_source_fields if not str(src.get(f, "")).strip()]
                if missing:
                    fail_reasons.append(f"sources[{idx}] missing required fields: {missing}")

    if expectations.required_artifact_fields:
        artifacts = output.get("artifacts")
        if not isinstance(artifacts, dict):
            fail_reasons.append("artifacts must be an object when required_artifact_fields is set")
        else:
            missing_artifacts = [f for f in expectations.required_artifact_fields if not str(artifacts.get(f, "")).strip()]
            if missing_artifacts:
                fail_reasons.append(f"artifacts missing required fields: {missing_artifacts}")

    min_unique = expectations.min_unique_source_titles
    if min_unique is not None:
        sources = output.get("sources")
        titles: List[str] = []
        if isinstance(sources, list):
            for src in sources:
                if isinstance(src, dict):
                    titles.append(str(src.get("title", "")).strip())
        unique_titles = {t for t in titles if t}
        if len(unique_titles) < int(min_unique):
            fail_reasons.append(f"min_unique_source_titles not met: {len(unique_titles)} < {int(min_unique)}")

    # Compare-dimensions heuristic (MVP): count strong separators in answer.
    if expectations.compare_min_dimensions is not None:
        answer = str(output.get("answer", ""))
        parts = [p.strip() for p in re.split(r"[。；\n]+", answer) if p.strip()]
        dim_count = sum(1 for p in parts if len(p) >= 6)
        if dim_count < int(expectations.compare_min_dimensions):
            fail_reasons.append(
                f"compare.min_dimensions not met: {dim_count} < {int(expectations.compare_min_dimensions)}"
            )

    # Case-example heuristics (MVP): enforce "must contain" markers and forbid placeholder phrases.
    if expectations.case_must_contain_any or expectations.case_must_not_contain_any:
        answer = str(output.get("answer", ""))
        if expectations.case_must_contain_any and not any(k in answer for k in expectations.case_must_contain_any):
            fail_reasons.append(f"case_example.must_contain_any not satisfied: {expectations.case_must_contain_any}")
        hit_forbidden = [k for k in expectations.case_must_not_contain_any if k in answer]
        if hit_forbidden:
            fail_reasons.append(f"case_example.must_not_contain_any violated: {hit_forbidden}")

    if (
        expectations.report_min_references_count is not None
        or expectations.report_required_sections
        or expectations.report_required_flags
        or expectations.report_depth_required_flags
        or expectations.report_depth_min_analogous_cases_count is not None
        or expectations.report_depth_min_pattern_points_count is not None
        or expectations.report_depth_min_theory_frameworks_count is not None
    ):
        report_meta = _load_report_meta(output=output, project_root=project_root)
        if not isinstance(report_meta, dict):
            fail_reasons.append("report_meta must be an object when report expectations are set")
        else:
            if expectations.report_min_references_count is not None:
                refs = int(report_meta.get("references_count", 0) or 0)
                if refs < int(expectations.report_min_references_count):
                    fail_reasons.append(
                        f"report.min_references_count not met: {refs} < {int(expectations.report_min_references_count)}"
                    )

            if expectations.report_required_sections:
                sections = report_meta.get("sections")
                sections_list = [str(x) for x in sections] if isinstance(sections, list) else []
                missing_sections = [s for s in expectations.report_required_sections if s not in sections_list]
                if missing_sections:
                    fail_reasons.append(f"report.required_sections missing: {missing_sections}")

            for flag in expectations.report_required_flags:
                if not bool(report_meta.get(flag)):
                    fail_reasons.append(f"report.required_flags not satisfied: {flag}")

            for flag in expectations.report_depth_required_flags:
                if not bool(report_meta.get(flag)):
                    fail_reasons.append(f"report_depth.required_flags not satisfied: {flag}")

            if expectations.report_depth_min_analogous_cases_count is not None:
                count = int(report_meta.get("analogous_cases_count", 0) or 0)
                if count < int(expectations.report_depth_min_analogous_cases_count):
                    fail_reasons.append(
                        "report_depth.min_analogous_cases_count not met: "
                        f"{count} < {int(expectations.report_depth_min_analogous_cases_count)}"
                    )

            if expectations.report_depth_min_pattern_points_count is not None:
                count = int(report_meta.get("pattern_points_count", 0) or 0)
                if count < int(expectations.report_depth_min_pattern_points_count):
                    fail_reasons.append(
                        "report_depth.min_pattern_points_count not met: "
                        f"{count} < {int(expectations.report_depth_min_pattern_points_count)}"
                    )

            if expectations.report_depth_min_theory_frameworks_count is not None:
                frameworks = report_meta.get("theory_frameworks")
                count = len(frameworks) if isinstance(frameworks, list) else 0
                if count < int(expectations.report_depth_min_theory_frameworks_count):
                    fail_reasons.append(
                        "report_depth.min_theory_frameworks_count not met: "
                        f"{count} < {int(expectations.report_depth_min_theory_frameworks_count)}"
                    )

    if expectations.sentiment_min_parse_success_rate is not None or expectations.sentiment_min_llm_coverage is not None:
        raw_metrics = output.get("metrics")
        raw_metrics = raw_metrics if isinstance(raw_metrics, dict) else {}
        if expectations.sentiment_min_parse_success_rate is not None:
            parse_rate = float(raw_metrics.get("parse_success_rate", 0.0) or 0.0)
            if parse_rate < float(expectations.sentiment_min_parse_success_rate):
                fail_reasons.append(
                    "sentiment.min_parse_success_rate not met: "
                    f"{parse_rate:.3f} < {float(expectations.sentiment_min_parse_success_rate):.3f}"
                )
        if expectations.sentiment_min_llm_coverage is not None:
            llm_cov = float(raw_metrics.get("llm_coverage", 0.0) or 0.0)
            if llm_cov < float(expectations.sentiment_min_llm_coverage):
                fail_reasons.append(
                    "sentiment.min_llm_coverage not met: "
                    f"{llm_cov:.3f} < {float(expectations.sentiment_min_llm_coverage):.3f}"
                )

    return fail_reasons


def soft_warn_reasons(*, metrics: Dict[str, float], expectations: CaseExpectations) -> List[str]:
    warn_reasons: List[str] = []
    threshold = expectations.thresholds.get("relevance_score")
    if threshold is not None and metrics.get("relevance_score", 0.0) < float(threshold):
        warn_reasons.append(
            f"relevance_score below threshold: {metrics.get('relevance_score', 0.0):.3f} < {float(threshold):.3f}"
        )

    # Sentiment agreement is informative (drift/correction signal), not hard fail.
    if (
        expectations.sentiment_agreement_warning_min is not None
        or expectations.sentiment_agreement_warning_max is not None
    ):
        agreement_obj = metrics.get("_sentiment_agreement_obj")
        if isinstance(agreement_obj, dict):
            rate = agreement_obj.get("agreement_rate")
            if rate is not None:
                try:
                    r = float(rate)
                except Exception:
                    r = None
                if r is not None:
                    min_v = expectations.sentiment_agreement_warning_min
                    max_v = expectations.sentiment_agreement_warning_max
                    if min_v is not None and r < float(min_v):
                        warn_reasons.append(
                            f"sentiment agreement low (possible drift): {r:.3f} < {float(min_v):.3f}"
                        )
                    if max_v is not None and r > float(max_v):
                        warn_reasons.append(
                            f"sentiment agreement high (possible no-op): {r:.3f} > {float(max_v):.3f}"
                        )
    extra = metrics.get("_consistency_reasons")
    if isinstance(extra, list):
        for item in extra:
            s = str(item or "").strip()
            if s:
                warn_reasons.append(s)
    return warn_reasons


def evaluate_case(
    *,
    query: str,
    output: Dict[str, Any],
    latency_ms: int,
    expectations_raw: Dict[str, Any],
    project_root: Optional[Path] = None,
) -> Tuple[str, Dict[str, Any], List[str]]:
    """Return (status, metrics, reasons). Reasons list combines fail+warn."""
    expectations = parse_expectations(expectations_raw)
    metrics = compute_metrics(
        query=query,
        output=output,
        latency_ms=latency_ms,
        expectations=expectations,
        project_root=project_root,
    )
    public_metrics = {k: v for k, v in metrics.items() if not str(k).startswith("_")}
    public_metrics.update(_quality_grade(metrics=metrics, output=output, project_root=project_root))

    fails = hard_fail_reasons(output=output, metrics=metrics, expectations=expectations, project_root=project_root)
    if fails:
        return "fail", public_metrics, fails

    warns = soft_warn_reasons(metrics=metrics, expectations=expectations)
    budget = expectations.consistency_warning_budget
    if budget is not None:
        # Count only consistency-related warnings to avoid coupling with unrelated warning families.
        consistency_warns = [
            w
            for w in warns
            if str(w).startswith("placeholder_leakage")
            or str(w).startswith("lifecycle_consistency")
            or str(w).startswith("metric_source_consistency")
            or str(w).startswith("claim_consistency")
        ]
        public_metrics["consistency_warning_count"] = len(consistency_warns)
        if len(consistency_warns) > int(budget):
            warns.append(
                f"consistency.warning_budget exceeded: {len(consistency_warns)} > {int(budget)}"
            )
    if warns:
        return "warning", public_metrics, warns

    return "pass", public_metrics, []


def _quality_grade(*, metrics: Dict[str, Any], output: Dict[str, Any], project_root: Optional[Path]) -> Dict[str, Any]:
    """
    Compute coarse quality score for report-like outputs.

    This score is additive and non-blocking: pass/fail behavior still follows
    hard_fail_reasons / soft_warn_reasons. We expose the score to support
    "warning-pass" baselines and trend tracking.
    """
    score = 0.0

    # Base structure (max 35)
    score += 35.0 * float(metrics.get("structure_completeness", 0.0) or 0.0)

    # Stage metrics (max 15)
    score += 10.0 * float(metrics.get("section_coverage", 0.0) or 0.0)
    score += 5.0 * float(metrics.get("html_parse_success", 0.0) or 0.0)

    # Depth/references from report_meta (max 50)
    report_meta = _load_report_meta(output=output, project_root=project_root)
    if isinstance(report_meta, dict):
        refs = int(report_meta.get("references_count", 0) or 0)
        score += min(15.0, refs * 5.0)  # 3 refs reaches full points

        analogous = int(report_meta.get("analogous_cases_count", 0) or 0)
        score += min(10.0, analogous * 5.0)  # 2 cases reaches full points

        patterns = int(report_meta.get("pattern_points_count", 0) or 0)
        score += min(10.0, patterns * 5.0)  # 2 pattern points full

        if bool(report_meta.get("has_theory_analysis")):
            score += 5.0
        frameworks = report_meta.get("theory_frameworks")
        fw_count = len(frameworks) if isinstance(frameworks, list) else 0
        score += min(10.0, float(fw_count) * 5.0)  # 2 frameworks full
    # Consistency penalties (max -15): lifecycle/data口径/占位词泄漏
    consistency = _consistency_signals(output=output, project_root=project_root)
    if not bool(consistency.get("lifecycle_consistency_ok", True)):
        score -= 5.0
    if not bool(consistency.get("metric_source_consistency_ok", True)):
        score -= 6.0
    if not bool(consistency.get("claim_consistency_ok", True)):
        score -= 4.0
    placeholder_hits = int(consistency.get("placeholder_hits", 0) or 0)
    score -= min(4.0, float(placeholder_hits))

    score_100 = int(round(max(0.0, min(100.0, score))))
    if score_100 >= 85:
        tier = "pass"
    elif score_100 >= 60:
        tier = "warning"
    else:
        tier = "fail"
    return {"score_100": score_100, "quality_tier": tier}


def _load_report_meta(*, output: Dict[str, Any], project_root: Optional[Path]) -> Optional[Dict[str, Any]]:
    """Load report_meta from output object or from file path (preferred)."""
    embedded = output.get("report_meta")
    if isinstance(embedded, dict):
        return embedded

    file_path = output.get("report_meta_file_path")
    if not file_path:
        artifacts = output.get("artifacts") if isinstance(output.get("artifacts"), dict) else {}
        file_path = artifacts.get("report_json_file") if isinstance(artifacts, dict) else None
    if not file_path or not project_root:
        return None

    try:
        p = Path(str(file_path))
        if not p.is_absolute():
            p = project_root / p
        if not p.exists():
            return None
        obj = json.loads(p.read_text(encoding="utf-8"))
        return obj if isinstance(obj, dict) else None
    except Exception:
        return None


def _load_report_html(*, output: Dict[str, Any], project_root: Optional[Path]) -> str:
    artifacts = output.get("artifacts") if isinstance(output.get("artifacts"), dict) else {}
    fp = artifacts.get("report_html_file") if isinstance(artifacts, dict) else None
    if not fp or not project_root:
        return ""
    try:
        p = Path(str(fp))
        if not p.is_absolute():
            p = project_root / p
        if not p.exists():
            return ""
        return p.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""


def _consistency_signals(*, output: Dict[str, Any], project_root: Optional[Path]) -> Dict[str, Any]:
    """
    Report consistency checks used as warning-level harness guards:
    - lifecycle_consistency: KPI阶段 vs 正文阶段
    - placeholder_leakage: 占位词/证据不足泄漏
    - metric_source_consistency: 重复出现的百分比口径是否冲突
    """
    html_content = _load_report_html(output=output, project_root=project_root)
    if not html_content:
        return {
            "lifecycle_consistency_ok": True,
            "metric_source_consistency_ok": True,
            "placeholder_hits": 0,
            "reasons": [],
        }

    reasons: List[str] = []
    lower = html_content.lower()

    # 1) placeholder leakage
    placeholder_terms = [
        "证据不足",
        "请补充分析 json",
        "todo",
        "待补充",
        "placeholder",
    ]
    placeholder_hits = sum(lower.count(t.lower()) for t in placeholder_terms)
    if placeholder_hits > 0:
        reasons.append(f"placeholder_leakage: hit {placeholder_hits} placeholder markers")

    # 2) lifecycle consistency (KPI vs body stages)
    kpi_stage = None
    m = re.search(r"目前阶段</div>\s*<div[^>]*>([^<]+)</div>", html_content, flags=re.IGNORECASE)
    if m:
        kpi_stage = str(m.group(1) or "").strip()
    stage_terms = ["潜伏期", "扩散期", "爆发期", "衰退期", "成长期", "结束期"]
    found_terms = [t for t in stage_terms if t in html_content]
    lifecycle_ok = True
    if kpi_stage:
        norm_kpi = kpi_stage.replace("当前处于", "").strip()
        if norm_kpi in stage_terms:
            explicit_current_terms = re.findall(r"(?:当前处于|目前处于|现处于)\s*([潜伏扩散爆发衰退成长结束]{2,3}期)", html_content)
            explicit_current_terms = [x.strip() for x in explicit_current_terms if str(x).strip()]
            conflict_explicit = [x for x in explicit_current_terms if x != norm_kpi]
            conflict_terms = [x for x in found_terms if x != norm_kpi]
            # explicit current-stage conflicts are always warnings.
            if conflict_explicit:
                lifecycle_ok = False
                reasons.append(
                    f"lifecycle_consistency: KPI stage '{norm_kpi}' conflicts with explicit body stages {sorted(set(conflict_explicit))}"
                )
            # otherwise, keep backward-compatible loose check for strong conflicts.
            elif conflict_terms and "→" not in html_content:
                lifecycle_ok = False
                reasons.append(
                    f"lifecycle_consistency: KPI stage '{norm_kpi}' conflicts with body stages {sorted(set(conflict_terms))}"
                )

    # 3) metric source consistency (same metric phrase with different percentages)
    metric_ok = True
    pair_pattern = re.compile(
        r"(中性|中立|正面|负面)[^。；\n]{0,16}?(\d{1,3}(?:\.\d+)?)%",
        flags=re.IGNORECASE,
    )
    buckets: Dict[str, List[float]] = {}
    for label, num in pair_pattern.findall(html_content):
        try:
            v = float(num)
        except Exception:
            continue
        key = "中性" if label in {"中立", "中性"} else label
        buckets.setdefault(key, []).append(v)
    for key, vals in buckets.items():
        uniq = sorted(set(round(x, 1) for x in vals))
        if len(uniq) >= 2:
            spread = max(uniq) - min(uniq)
            if spread >= 1.0:
                metric_ok = False
                reasons.append(f"metric_source_consistency: '{key}' appears with conflicting percentages {uniq}")

    # 4) repeated claim consistency (same topic label should not map to conflicting claims)
    claim_ok = True

    # 4.1 risk-level consistency
    risk_terms = ["低风险", "中风险", "高风险", "重大风险"]
    risk_hits = [x for x in risk_terms if x in html_content]
    if len(set(risk_hits)) >= 2:
        claim_ok = False
        reasons.append(f"claim_consistency: conflicting risk labels {sorted(set(risk_hits))}")

    # 4.2 overall attitude consistency
    attitude_terms = ["整体态度</div><div class=\"kpi-value\">正面", "整体态度</div><div class=\"kpi-value\">中性", "整体态度</div><div class=\"kpi-value\">负面"]
    attitude_kpi = None
    for t in attitude_terms:
        if t in html_content:
            if "正面" in t:
                attitude_kpi = "正面"
            elif "中性" in t:
                attitude_kpi = "中性"
            elif "负面" in t:
                attitude_kpi = "负面"
            break
    explicit_attitude = re.findall(r"(?:整体态度|总体态度|结论态度)\s*(?:为|:|：)?\s*(正面|中性|中立|负面)", html_content)
    explicit_attitude = [("中性" if a in {"中立", "中性"} else a) for a in explicit_attitude]
    if attitude_kpi and explicit_attitude:
        conflict_att = [a for a in explicit_attitude if a != attitude_kpi]
        if conflict_att:
            claim_ok = False
            reasons.append(
                f"claim_consistency: KPI attitude '{attitude_kpi}' conflicts with body attitudes {sorted(set(conflict_att))}"
            )

    return {
        "lifecycle_consistency_ok": lifecycle_ok,
        "metric_source_consistency_ok": metric_ok,
        "claim_consistency_ok": claim_ok,
        "placeholder_hits": placeholder_hits,
        "reasons": reasons,
    }

