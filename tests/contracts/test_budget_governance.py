from __future__ import annotations

from workflow.budget import estimate_tokens, sentiment_budget_from_env


def test_estimate_tokens_monotonic() -> None:
    short = estimate_tokens("高铁熊孩子")
    long_text = estimate_tokens("高铁熊孩子" * 20)
    assert short > 0
    assert long_text > short


def test_sentiment_budget_defaults() -> None:
    b = sentiment_budget_from_env(default_timeout_sec=30)
    assert b.stage == "sentiment"
    assert b.token_budget >= 2000
    assert b.latency_budget_ms >= 1000
    assert b.retry_budget >= 0

