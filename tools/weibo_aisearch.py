"""微博智搜抓取工具：根据事件关键词抓取微博智搜可见片段。"""

from __future__ import annotations

import html
import json
import os
import re
from datetime import datetime
from urllib.parse import quote

import requests
from langchain_core.tools import tool


def _extract_snippets_from_html(text: str, limit: int) -> list[dict[str, str]]:
    blocks = re.findall(r"<p[^>]*class=\"txt\"[^>]*>([\s\S]*?)</p>", text, flags=re.IGNORECASE)
    if not blocks:
        blocks = re.findall(r"<a[^>]*href=\"//weibo\\.com/[^\"#]+\"[^>]*>([\s\S]*?)</a>", text, flags=re.IGNORECASE)

    results: list[dict[str, str]] = []
    seen: set[str] = set()
    for b in blocks:
        s = re.sub(r"<[^>]+>", " ", b)
        s = html.unescape(re.sub(r"\s+", " ", s)).strip()
        if len(s) < 12:
            continue
        key = s[:80]
        if key in seen:
            continue
        seen.add(key)
        results.append({"snippet": s[:220] + ("..." if len(s) > 220 else "")})
        if len(results) >= limit:
            break
    return results


def _is_visitor_page(text: str) -> bool:
    low = (text or "").lower()
    return ("visitor system" in low) or ("sina visitor system" in low)


def _fetch_with_playwright(url: str, timeout_sec: int) -> str:
    """
    Playwright 回退抓取：用于 requests 触发 Visitor System 或正文为空场景。
    """
    from playwright.sync_api import sync_playwright

    timeout_ms = max(8000, min(timeout_sec * 1000, 120000))
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            context = browser.new_context()
            page = context.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            page.wait_for_timeout(2500)
            try:
                page.wait_for_load_state("networkidle", timeout=min(10000, timeout_ms))
            except Exception:
                pass
            return page.content() or ""
        finally:
            browser.close()


@tool
def weibo_aisearch(query: str, limit: int = 12) -> str:
    """
    描述：抓取微博智搜页面的可见文本片段，作为舆情分析外部参考线索。
    使用时机：在事件分析阶段需要引入微博智搜线索时调用。
    输入：
      - query: 事件关键词或主题
      - limit: 返回片段数量上限（1~30，默认12）
    输出：JSON字符串，含 topic/url/count/results/error/fetched_at。
    """
    topic = str(query or "").strip() or "舆情事件"
    k = max(1, min(int(limit or 12), 30))
    url = f"https://s.weibo.com/aisearch?q={quote(topic)}&Refer=aisearch_aisearch"

    timeout_sec = 12
    try:
        timeout_sec = max(5, min(int(os.environ.get("SONA_REFERENCE_FETCH_TIMEOUT_SEC", "12")), 60))
    except Exception:
        pass

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }

    try:
        resp = requests.get(url, headers=headers, timeout=timeout_sec)
        text = resp.text or ""
    except Exception as e:
        return json.dumps(
            {
                "topic": topic,
                "url": url,
                "count": 0,
                "results": [],
                "error": f"抓取失败: {str(e)}",
                "fetched_at": datetime.now().isoformat(sep=" "),
            },
            ensure_ascii=False,
        )

    results = _extract_snippets_from_html(text, k)
    fallback_used = False
    fallback_error = ""
    need_fallback = _is_visitor_page(text) or not results
    enable_fallback = str(os.environ.get("SONA_WEIBO_AISEARCH_PLAYWRIGHT_FALLBACK", "true")).strip().lower() in (
        "1",
        "true",
        "yes",
        "y",
        "on",
    )

    if need_fallback and enable_fallback:
        try:
            pw_html = _fetch_with_playwright(url, timeout_sec=timeout_sec)
            pw_results = _extract_snippets_from_html(pw_html, k)
            if pw_results:
                results = pw_results
            fallback_used = True
        except Exception as e:
            fallback_error = f"Playwright 回退失败: {str(e)}"

    error_text = ""
    if not results:
        if _is_visitor_page(text):
            error_text = "微博智搜返回访客验证页（Visitor System），未获取到正文片段"
        elif fallback_error:
            error_text = fallback_error
        else:
            error_text = "未提取到可用微博智搜片段"

    return json.dumps(
        {
            "topic": topic,
            "url": url,
            "count": len(results),
            "results": results,
            "error": error_text,
            "fallback_used": fallback_used,
            "source": "playwright" if fallback_used and results else "requests",
            "fetched_at": datetime.now().isoformat(sep=" "),
        },
        ensure_ascii=False,
    )

