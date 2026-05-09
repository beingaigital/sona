#!/usr/bin/env python3
"""Fetch full article HTML from linked URLs and rewrite PH_SUPP files under 公卫舆情报告补充/ (## 内容)."""

from __future__ import annotations

import html as html_lib
import re
import sys
import urllib.error
import urllib.request
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SUPP_DIR = ROOT / "opinion_analysis_kb/domains/health/materials/公卫舆情报告补充"

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


def fetch(url: str, timeout: int = 45) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
    for enc in ("utf-8", "gbk", "gb2312"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="ignore")


def extract_wechat_js_content(html: str) -> str:
    """Extract inner HTML of #js_content (handles nested divs by depth counting)."""
    start_m = re.search(
        r'<div\s[^>]*\bid="js_content"[^>]*>',
        html,
        re.I,
    )
    if not start_m:
        return ""
    i = start_m.end()
    depth = 1
    while i < len(html) and depth:
        # next div open or close
        next_open = html.find("<div", i)
        next_close = html.find("</div>", i)
        if next_close == -1:
            break
        if next_open != -1 and next_open < next_close:
            depth += 1
            i = next_open + 4
        else:
            depth -= 1
            if depth == 0:
                inner = html[start_m.end() : next_close]
                return _wechat_inner_to_text(inner)
            i = next_close + len("</div>")
    return ""


def _wechat_inner_to_text(inner: str) -> str:
    """Convert WeChat rich HTML chunk to plain text with line breaks."""
    inner = re.sub(r"(?is)<script[^>]*>.*?</script>", "", inner)
    inner = re.sub(r"(?is)<style[^>]*>.*?</style>", "", inner)

    class P(HTMLParser):
        def __init__(self) -> None:
            super().__init__()
            self.parts: list[str] = []
            self._list_depth = 0

        def handle_starttag(self, tag: str, attrs) -> None:
            if tag in ("p", "section") and self.parts and not str(self.parts[-1]).endswith(
                "\n"
            ):
                self.parts.append("\n")
            elif tag == "br":
                self.parts.append("\n")
            elif tag == "img":
                src = dict(attrs).get("src") or ""
                if src:
                    self.parts.append(f"\n[图片]({src})\n")

        def handle_endtag(self, tag: str) -> None:
            if tag in ("p", "section"):
                self.parts.append("\n")

        def handle_data(self, data: str) -> None:
            if data.strip():
                self.parts.append(data)

    p = P()
    try:
        p.feed(inner)
        p.close()
    except Exception:
        return re.sub(r"<[^>]+>", "", inner)
    text = "".join(p.parts)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def html_to_plain_paragraphs(fragment: str) -> str:
    """Strip tags from a small HTML fragment; keep img as markdown links."""

    class P(HTMLParser):
        def __init__(self) -> None:
            super().__init__()
            self.out: list[str] = []
            self._href: str | None = None

        def handle_starttag(self, tag: str, attrs) -> None:
            ad = dict(attrs)
            if tag == "br":
                self.out.append("\n")
            elif tag == "p":
                if self.out and not str(self.out[-1]).endswith("\n"):
                    self.out.append("\n")
            elif tag == "img":
                src = ad.get("src") or ""
                alt = ad.get("alt") or "图片"
                self.out.append(f"\n![{alt}]({src})\n")
            elif tag == "a":
                self._href = ad.get("href")

        def handle_endtag(self, tag: str) -> None:
            if tag == "p":
                self.out.append("\n\n")
            elif tag == "a":
                self._href = None

        def handle_data(self, data: str) -> None:
            self.out.append(data)

    p = P()
    p.feed(fragment)
    p.close()
    text = "".join(p.out)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def extract_eefung_document_content(html: str, base: str) -> str:
    m = re.search(
        r'<div class="document-content">([\s\S]*?)</div>\s*<div class="document-footer">',
        html,
    )
    if not m:
        m = re.search(r'<div class="document-content">([\s\S]*?)</div>', html)
    if not m:
        return ""
    inner = m.group(1)
    # normalize relative image URLs
    inner = re.sub(
        r'src="/image/',
        f'src="{base.rstrip("/")}/image/',
        inner,
    )
    inner = re.sub(
        r"src='/image/",
        f"src='{base.rstrip('/')}/image/",
        inner,
    )
    return html_to_plain_paragraphs(inner)


def extract_generic_article(html: str) -> str:
    """Fallback: strip scripts/styles and take visible text."""
    html = re.sub(r"(?is)<script[^>]*>.*?</script>", "", html)
    html = re.sub(r"(?is)<style[^>]*>.*?</style>", "", html)

    class P(HTMLParser):
        def __init__(self) -> None:
            super().__init__()
            self.parts: list[str] = []
            self._skip = 0

        def handle_starttag(self, tag: str, attrs) -> None:
            if tag in ("script", "style", "noscript"):
                self._skip += 1
            elif tag == "br":
                self.parts.append("\n")

        def handle_endtag(self, tag: str) -> None:
            if tag in ("script", "style", "noscript") and self._skip:
                self._skip -= 1

        def handle_data(self, data: str) -> None:
            if not self._skip and data.strip():
                self.parts.append(data.strip())

    p = P()
    p.feed(html)
    p.close()
    blob = "\n\n".join(p.parts)
    blob = re.sub(r"\n{3,}", "\n\n", blob)
    return blob


def parse_url_from_md(content: str) -> str | None:
    for pat in (
        r"\*\*链接\*\*:\s*(https?://\S+)",
        r"\*\*原文链接\*\*:\s*(https?://\S+)",
    ):
        m = re.search(pat, content)
        if m:
            return m.group(1).rstrip(")")
    return None


def rewrite_md(path: Path, full_body: str, note: str | None = None) -> None:
    text = path.read_text(encoding="utf-8")
    body_parts: list[str] = []
    if note:
        body_parts.append(f"> {note}")
        body_parts.append("")
    body_parts.append(full_body)
    body_out = "\n".join(body_parts)
    body_lines = []
    for line in body_out.splitlines():
        body_lines.append("\\" + line if line.startswith("#") else line)
    body_out = "\n".join(body_lines) + "\n"
    if re.search(r"\n## (?:内容|全文)\n", text):
        text = re.sub(
            r"\n## (?:内容|全文)\n[\s\S]*",
            f"\n## 内容\n\n{body_out}",
            text,
            count=1,
        )
    else:
        text = text.rstrip() + f"\n\n## 内容\n\n{body_out}"
    path.write_text(text, encoding="utf-8")


def main() -> int:
    md_files = sorted(
        p for p in SUPP_DIR.glob("*.md") if p.name != "README.md"
    )
    errors: list[str] = []
    for path in md_files:
        content = path.read_text(encoding="utf-8")
        url = parse_url_from_md(content)
        if not url:
            errors.append(f"{path.name}: no URL")
            continue
        try:
            html = fetch(url)
        except urllib.error.HTTPError as e:
            errors.append(f"{path.name}: HTTP {e.code} {url}")
            continue
        except Exception as e:  # noqa: BLE001
            errors.append(f"{path.name}: {e!s} {url}")
            continue

        note: str | None = None
        full_body = ""
        if "mp.weixin.qq.com" in url:
            full_body = extract_wechat_js_content(html)
            if not full_body:
                full_body = extract_generic_article(html)
                note = "（未能定位 `js_content`，以下为页面可见文本摘录。）"
        elif "eefung.com" in url:
            from urllib.parse import urlparse

            base = f"{urlparse(url).scheme}://{urlparse(url).netloc}"
            full_body = extract_eefung_document_content(html, base)
            if not full_body:
                full_body = extract_generic_article(html)
                note = "（未能定位 `document-content`，以下为页面可见文本。）"
            else:
                note = (
                    "（蚁坊报告正文大量以图片呈现，下文已保留段落文字并嵌入图片链接；"
                    "图表细节请以原网页为准。）"
                )
        else:
            full_body = extract_generic_article(html)

        full_body = html_lib.unescape(full_body)
        if len(full_body) < 80:
            errors.append(f"{path.name}: extracted too short ({len(full_body)} chars)")
            continue
        rewrite_md(path, full_body, note)
        print("OK", path.name, len(full_body))

    if errors:
        print("\n--- issues ---", file=sys.stderr)
        for e in errors:
            print(e, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
