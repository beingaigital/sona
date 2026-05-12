"""微博智搜抓取工具：根据事件关键词抓取微博智搜可见片段。"""

from __future__ import annotations

import html
import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any
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


# 与微博智搜常见叙事结构对齐，供报告模板（INTRO / 时间线 / 争议 / 回应 / 复盘等）吸收线索。
_STRUCTURE_VERSION = 1
WEIBO_AISEARCH_SLOT_ORDER: tuple[str, ...] = (
    "event_facts",
    "timeline",
    "controversy",
    "brand_pr",
    "institutional",
    "accountability",
    "industry_reflection",
    "synthesis",
    "discussion_misc",
)
WEIBO_AISEARCH_SLOT_TITLES: dict[str, str] = {
    "event_facts": "事件事实与争议材料（→ 事件概述、核心事实）",
    "timeline": "时间线与节点（→ 传播时间线、阶段划分）",
    "controversy": "争议焦点与舆论质疑（→ 公众讨论焦点、争议核心）",
    "brand_pr": "品牌/主体回应与公关动作（→ 回应与处置观察）",
    "institutional": "机构、媒体与第三方表态（→ 官方与舆论场介入）",
    "accountability": "问责、处罚与组织处理（→ 升级与问责链）",
    "industry_reflection": "机制、行业与长期反思（→ 风险与建议依据）",
    "synthesis": "总结式判断与复盘口吻（→ 总结复盘、启示）",
    "discussion_misc": "其它讨论片段（→ 仅作氛围参考，慎引为事实）",
}
# 叙事模型占位符与 HTML 章节习惯命名对齐（见 tools/report_html_template.py）
_REPORT_BRIDGE: tuple[dict[str, Any], ...] = (
    {
        "template_hooks": ["INTRO_BACKGROUND", "事件概述"],
        "from_slots": ["event_facts", "timeline", "synthesis"],
        "writer_hint": "概述段优先吸收「事件事实」与含日期的「时间线」各 0–1 条；若与本地 CSV 时间窗不一致，以本地为准。",
    },
    {
        "template_hooks": ["时间线", "传播阶段", "timeline（JSON）"],
        "from_slots": ["timeline", "brand_pr", "accountability", "institutional"],
        "writer_hint": "时间线节点应对齐本地声量峰；机构表态、二次致歉与处罚宜按时间先后串联，避免合并为一句模糊因果。",
    },
    {
        "template_hooks": ["INTRO_TRIGGERS", "公众讨论焦点", "争议核心"],
        "from_slots": ["controversy", "discussion_misc", "brand_pr"],
        "writer_hint": "争议轴与「首次回应/道歉话术」对照写；单条情绪梗不得替代整体分布结论。",
    },
    {
        "template_hooks": ["RESPONSE_ANALYSIS_BULLETS", "处置建议", "启示/复盘"],
        "from_slots": ["institutional", "industry_reflection", "accountability", "synthesis"],
        "writer_hint": "机构与行业反思可作外脑线索；建议须落到主体可控动作，并与本地证据链交叉。",
    },
    {
        "template_hooks": ["SUMMARY_BULLETS", "RECAP_*", "总结复盘"],
        "from_slots": ["synthesis", "controversy", "accountability"],
        "writer_hint": "总结段只复述智搜中已出现的判断句式不够，须回扣本次数据与图表结论；禁止夸大「全网」「90%」等无本地支撑的占比。",
    },
)

_DATE_FULL = re.compile(r"\d{4}年\d{1,2}月\d{1,2}日")
_DATE_MD = re.compile(r"(?<!\d)\d{1,2}月\d{1,2}日(?!\d)")


def _each_slot_limit() -> int:
    raw = os.environ.get("SONA_WEIBO_AISEARCH_SLOT_EACH", "8").strip()
    try:
        n = int(raw)
    except Exception:
        n = 8
    return max(2, min(n, 20))


def _snippet_cap() -> int:
    raw = os.environ.get("SONA_WEIBO_AISEARCH_SNIPPET_IN_STRUCTURE", "240").strip()
    try:
        n = int(raw)
    except Exception:
        n = 240
    return max(80, min(n, 500))


def _has_timeline_signal(text: str) -> bool:
    if _DATE_FULL.search(text) or _DATE_MD.search(text):
        return True
    if "同日" in text or "次日" in text or "当晚" in text:
        return True
    return False


def _matching_slots(snippet: str) -> set[str]:
    """基于关键词/日期信号将单条智搜片段归入一个或多个叙事槽（启发式，非平台官方标签）。"""
    s = snippet
    m: set[str] = set()
    if _has_timeline_signal(s):
        m.add("timeline")
    if any(k in s for k in ("总结", "复盘", "启示", "教训", "归根结底", "核心在于", "典型案例", "始于", "终于")):
        m.add("synthesis")
    if any(
        k in s
        for k in (
            "争议",
            "质疑",
            "价值观",
            "公序良俗",
            "伦理",
            "冒犯",
            "低俗",
            "饭圈",
            "亚文化",
            "出轨",
            "矮化",
            "贬低",
            "玩梗",
            "扭曲",
            "幽默",
            "精神出轨",
            "刻板印象",
        )
    ):
        m.add("controversy")
    if any(
        k in s
        for k in (
            "道歉",
            "致歉",
            "声明",
            "回应",
            "下架",
            "整改",
            "承诺",
            "初衷",
            "打破刻板",
            "精选",
            "敷衍",
            "认错",
        )
    ):
        m.add("brand_pr")
    if any(
        k in s
        for k in (
            "问责",
            "处罚",
            "降级",
            "职级",
            "罚薪",
            "冻结",
            "高管",
            "内部通告",
            "内部发布",
            "定级",
            "重大品牌事故",
            "直降",
        )
    ):
        m.add("accountability")
    if any(
        k in s
        for k in (
            "中国妇女报",
            "浙江宣传",
            "广告协会",
            "新京报",
            "澎湃新闻",
            "红星新闻",
            "界面新闻",
            "快评",
            "校方",
            "大学",
            "协会",
            "官媒",
            "下场",
        )
    ):
        m.add("institutional")
    if any(k in s for k in ("审核", "流量", "营销", "失灵", "长期主义", "本分", "段永平", "行业警示", "底线", "机制")):
        m.add("industry_reflection")
    if any(k in s for k in ("文案", "海报", "原文", "宣发", "物料", "配图", "母亲节", "表述为", "内容为", "称：", "称，")):
        m.add("event_facts")
    if not m:
        m.add("discussion_misc")
    return m


def decompose_weibo_aisearch_results(results: Any) -> dict[str, Any]:
    """
    将智搜 ``results`` 启发式分槽，便于与报告模板占位符对齐。

    Returns:
        含 ``version``、``slots``、``report_bridge``、``disclaimer`` 的字典；与工具 JSON 中
        ``structured`` 字段形态一致。旧版仅含 ``results`` 的 JSON 可在消费端调用本函数补算。
    """
    slots: dict[str, list[str]] = {k: [] for k in WEIBO_AISEARCH_SLOT_ORDER}
    seen: dict[str, set[str]] = {k: set() for k in WEIBO_AISEARCH_SLOT_ORDER}
    cap = _snippet_cap()
    each_lim = _each_slot_limit()

    if not isinstance(results, list):
        results = []

    for row in results:
        if not isinstance(row, dict):
            continue
        raw = str(row.get("snippet") or "").strip()
        if len(raw) < 12:
            continue
        flat = re.sub(r"\s+", " ", raw)
        display = flat[:cap] + ("..." if len(flat) > cap else "")
        key = flat[:96]
        for slot in _matching_slots(flat):
            if slot not in slots:
                continue
            if len(slots[slot]) >= each_lim:
                continue
            if key in seen[slot]:
                continue
            seen[slot].add(key)
            slots[slot].append(display)

    slots = {k: v for k, v in slots.items() if v}

    return {
        "version": _STRUCTURE_VERSION,
        "slots": slots,
        "report_bridge": [dict(x) for x in _REPORT_BRIDGE],
        "disclaimer": (
            "以下为基于可见片段的关键词分槽，非微博官方结构；须与本地采集数据、"
            "图表与可核验报道交叉验证后再写入正文。"
        ),
    }


def _structure_enabled() -> bool:
    v = str(os.environ.get("SONA_WEIBO_AISEARCH_STRUCTURE", "true") or "true").strip().lower()
    return v in ("1", "true", "yes", "y", "on")


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
    输出：JSON字符串，含 topic/url/count/results/error/fetched_at；默认另含
    ``structured``（分槽线索与 ``report_bridge``，便于与报告模板占位符对齐）。
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

    structured: dict[str, Any] | None = None
    if _structure_enabled():
        structured = decompose_weibo_aisearch_results(results)

    payload: dict[str, Any] = {
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
    }
    if structured is not None:
        payload["structured"] = structured

    return json.dumps(payload, ensure_ascii=False)

