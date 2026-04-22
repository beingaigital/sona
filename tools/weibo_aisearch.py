"""微博智搜抓取工具：根据事件关键词抓取微博智搜可见片段。"""

from __future__ import annotations

import html
import json
import os
import re
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

import requests
from langchain_core.tools import tool
from utils.env_loader import get_env_config


def _parse_cookie_header(cookie_header: str) -> dict[str, str]:
    cookies: dict[str, str] = {}
    for part in str(cookie_header or "").split(";"):
        seg = part.strip()
        if not seg or "=" not in seg:
            continue
        key, value = seg.split("=", 1)
        key = key.strip()
        value = value.strip()
        if key:
            cookies[key] = value
    return cookies


def _load_cookies_from_path(path_like: str) -> dict[str, str]:
    """
    从文件加载 Cookie，兼容：
    1) Playwright storage_state.json: {"cookies":[{"name":"SUB","value":"..."}]}
    2) Cookie 字典: {"SUB":"...","SUBP":"..."}
    3) Cookie 列表: [{"name":"SUB","value":"..."}, ...]
    """
    path = Path(str(path_like or "").strip()).expanduser()
    if not path.exists() or not path.is_file():
        return {}
    try:
        content = path.read_text(encoding="utf-8", errors="replace")
        obj = json.loads(content)
    except Exception:
        return {}

    cookies: dict[str, str] = {}
    if isinstance(obj, dict) and isinstance(obj.get("cookies"), list):
        for item in obj.get("cookies", []):
            if not isinstance(item, dict):
                continue
            name = str(item.get("name", "")).strip()
            value = str(item.get("value", "")).strip()
            if name and value:
                cookies[name] = value
        return cookies

    if isinstance(obj, dict):
        for k, v in obj.items():
            name = str(k or "").strip()
            value = str(v or "").strip()
            if name and value:
                cookies[name] = value
        return cookies

    if isinstance(obj, list):
        for item in obj:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name", "")).strip()
            value = str(item.get("value", "")).strip()
            if name and value:
                cookies[name] = value
    return cookies


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


def _fetch_with_playwright(
    url: str,
    timeout_sec: int,
    cookies: dict[str, str] | None = None,
    storage_state_path: str = "",
) -> str:
    """
    Playwright 回退抓取：用于 requests 触发 Visitor System 或正文为空场景。
    """
    from playwright.sync_api import sync_playwright

    timeout_ms = max(8000, min(timeout_sec * 1000, 120000))
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            storage_path = Path(str(storage_state_path or "").strip()).expanduser() if storage_state_path else None
            if storage_path and storage_path.exists() and storage_path.is_file():
                context = browser.new_context(storage_state=str(storage_path))
            else:
                context = browser.new_context()
            cookie_items = cookies or {}
            if cookie_items:
                merged = "; ".join([f"{k}={v}" for k, v in cookie_items.items()])
                context.set_extra_http_headers({"Cookie": merged})
                cookie_list = []
                for k, v in cookie_items.items():
                    cookie_list.append({"name": k, "value": v, "domain": ".weibo.com", "path": "/"})
                    cookie_list.append({"name": k, "value": v, "domain": "s.weibo.com", "path": "/"})
                context.add_cookies(cookie_list)
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
    # 确保 .env 已加载到进程环境变量（与项目其他工具行为一致）
    get_env_config()
    topic = str(query or "").strip() or "舆情事件"
    k = max(1, min(int(limit or 12), 30))
    refer = str(os.environ.get("SONA_WEIBO_AISEARCH_REFER", "weibo_aisearch")).strip() or "weibo_aisearch"
    url = f"https://s.weibo.com/aisearch?q={quote(topic)}&Refer={quote(refer)}"

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
    cookie_header = str(os.environ.get("SONA_WEIBO_COOKIE", "") or "").strip()
    cookie_path = str(os.environ.get("SONA_WEIBO_COOKIE_PATH", "") or "").strip()
    cookies = _parse_cookie_header(cookie_header)
    if not cookies and cookie_path:
        cookies = _load_cookies_from_path(cookie_path)
    if cookie_header and cookies:
        headers["Cookie"] = cookie_header
    elif cookies:
        headers["Cookie"] = "; ".join([f"{k}={v}" for k, v in cookies.items()])

    try:
        resp = requests.get(url, headers=headers, cookies=(cookies or None), timeout=timeout_sec)
        text = resp.text or ""
    except Exception as e:
        return json.dumps(
            {
                "topic": topic,
                "url": url,
                "count": 0,
                "results": [],
                "error": f"抓取失败: {str(e)}",
                "authenticated": bool(cookies),
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
            pw_html = _fetch_with_playwright(
                url,
                timeout_sec=timeout_sec,
                cookies=cookies,
                storage_state_path=cookie_path,
            )
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
            "authenticated": bool(cookies),
            "cookie_path_used": bool(cookie_path and Path(cookie_path).expanduser().exists()),
            "fetched_at": datetime.now().isoformat(sep=" "),
        },
        ensure_ascii=False,
    )

