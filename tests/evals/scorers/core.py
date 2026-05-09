"""Core metric computations for evaluation cases."""

from __future__ import annotations

import re
from typing import Any, Dict, List


def safe_div(numerator: float, denominator: float) -> float:
    """Safely divide two numbers."""
    if denominator <= 0:
        return 0.0
    return numerator / denominator


def keywords(text: str) -> List[str]:
    """Extract normalized keywords from mixed Chinese/English text."""
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
            # Expand Chinese phrases into small n-grams for better overlap matching.
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


def contains_any_evidence(answer: str, snippets: List[str]) -> float:
    """Estimate how many answer statements are supported by evidence snippets."""
    if not answer.strip():
        return 0.0
    statements = [s.strip() for s in answer.replace("；", "。").split("。") if len(s.strip()) >= 8]
    if not statements:
        return 0.0
    supported = 0
    for statement in statements:
        tokens = keywords(statement)
        hit = any(token and len(token) >= 2 and token in snippet for token in tokens for snippet in snippets)
        if hit:
            supported += 1
    return safe_div(float(supported), float(len(statements)))


def keyword_overlap(query: str, answer: str) -> float:
    """Measure lexical overlap between query and answer."""
    q = set(keywords(query))
    if not q:
        return 0.0
    a = set(keywords(answer))
    return safe_div(float(len(q & a)), float(len(q)))


def structure_completeness(output: Dict[str, Any], required_fields: List[str]) -> float:
    """Compute how many required fields are present and non-empty."""
    if not required_fields:
        return 1.0
    hit = sum(1 for field in required_fields if field in output and output[field] not in (None, "", []))
    return safe_div(float(hit), float(len(required_fields)))


def compute_metrics(case: Any, output: Dict[str, Any], latency_ms: int) -> Dict[str, float]:
    """Compute MVP metrics for both wiki and workflow replay outputs."""
    expectations = case.expectations if isinstance(case.expectations, dict) else {}
    required_fields = expectations.get("required_fields")
    required_fields = required_fields if isinstance(required_fields, list) else []

    answer = str(output.get("answer", ""))
    query = str(case.input_payload.get("query", ""))
    sources = output.get("sources")
    source_snippets: List[str] = []
    if isinstance(sources, list):
        for item in sources:
            if isinstance(item, dict):
                source_snippets.append(str(item.get("snippet", "")))
                source_snippets.append(str(item.get("title", "")))
            elif isinstance(item, str):
                source_snippets.append(item)

    return {
        "latency_ms": float(latency_ms),
        "traceability_score": contains_any_evidence(answer, source_snippets),
        "relevance_score": keyword_overlap(query, answer),
        "structure_completeness": structure_completeness(output, required_fields),
        "fallback_rate": 0.0,
    }
