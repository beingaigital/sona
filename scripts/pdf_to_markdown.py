"""
PDF 转 Markdown（单文件输出，无二次 repair）。

用法：
  python pdf_to_markdown.py <PDF路径> [输出目录]

说明：
- 优先使用 PyMuPDF 的阅读顺序抽取 page.get_text(sort=True)；必要时回退到按块排序或默认抽取。
- 去除 PDF 私用区字符（U+E000–U+F8FF），并做常见「内嵌字体映射」导致的别字替换（以本刊已知样本为主）。
- 学术双栏 PDF 无法保证 100% 与纸版一致；若与 PDF 不一致，以 PDF 为准。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

_PUA = re.compile(r"[\ue000-\uf8ff\ufeff]")


def strip_private_use(text: str) -> str:
    return _PUA.sub("", text)


# 本刊该 PDF 抽取中反复出现的字形/编码问题（保守替换）
_GLYPH_FIXES: tuple[tuple[str, str], ...] = (
    ("哭发么共卫生事件", "突发公共卫生事件"),
    ("徵博", "微博"),
    ("漬化", "演化"),
    ("时厚趋势", "时序趋势"),
    ("徽博", "微博"),
    ("ＣＬＤＡ", "LDA"),
    ("Ｓ０Ｍ", "SOM"),
)


def normalize_extracted_text(text: str) -> str:
    text = strip_private_use(text)
    for a, b in _GLYPH_FIXES:
        text = text.replace(a, b)
    text = re.sub(r"理\s*：\s*\n\s*论\s*：\s*\n(?:\s*：\s*)?\n?\s*探\s*\n\s*杀\s*：", "理论探讨", text)
    text = re.sub(r"理\s*\n\s*论\s*\n\s*探\s*\n\s*杀", "理论探讨", text)
    # 常见括号与全角英文标点
    text = re.sub(r"分配\s*LDA\s*）", "分配（LDA）", text)
    text = re.sub(r"（\s*SOM\s*）", "（SOM）", text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r" {2,}", " ", text)
    return text.strip()


def extract_page_text_blocks(page) -> str:
    blocks = page.get_text("blocks") or []
    rows: list[tuple[float, float, str]] = []
    for b in blocks:
        if len(b) < 7 or b[6] != 0:
            continue
        x0, y0, _x1, _y1, txt = b[0], b[1], b[2], b[3], b[4]
        t = (txt or "").strip()
        if not t:
            continue
        rows.append((y0, x0, t))
    rows.sort(key=lambda r: (r[0], r[1]))
    return "\n\n".join(r[2] for r in rows)


def extract_page_text(page) -> str:
    """阅读顺序优先，其次块排序，最后默认抽取。"""
    t = normalize_extracted_text(page.get_text(sort=True) or "")
    if len(t) < 80:
        t2 = normalize_extracted_text(extract_page_text_blocks(page))
        if len(t2) > len(t):
            t = t2
    if len(t) < 40:
        t3 = normalize_extracted_text(page.get_text() or "")
        if len(t3) > len(t):
            t = t3
    return t


def infer_title(pdf_file: Path, doc) -> str:
    """元数据标题优先；否则用已知题名（与《情报资料工作》该文一致）。"""
    meta = (doc.metadata or {}).get("title") or ""
    meta = strip_private_use(meta.strip())
    if 8 <= len(meta) <= 120 and "卫生" in meta:
        return meta
    # 与文件名/论文一致的标准中文题名（文件名中含 _省略_er 多为拷贝时损坏）
    if "埃博拉" in pdf_file.stem and "安璐" in pdf_file.stem:
        return "突发公共卫生事件的微博主题演化模式和时序趋势——以 Twitter 和 Weibo 的埃博拉微博为例"
    return pdf_file.stem.replace("_省略_er", "Twitter").replace("_", " ")


def pdf_to_markdown(pdf_path: str, output_dir: str | None = None) -> str:
    try:
        import fitz
    except ImportError:
        print("需要安装 pymupdf: pip install pymupdf", file=sys.stderr)
        sys.exit(1)

    pdf_file = Path(pdf_path)
    if not pdf_file.exists():
        print(f"文件不存在: {pdf_path}", file=sys.stderr)
        sys.exit(1)

    output_dir = Path(output_dir) if output_dir else pdf_file.parent
    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"正在转换: {pdf_file.name}")

    doc = fitz.open(str(pdf_file))
    title = infer_title(pdf_file, doc)
    n_pages = len(doc)

    parts: list[str] = [
        f"# {title}\n",
        f"> 由 `scripts/pdf_to_markdown.py` 从 `{pdf_file.name}` 自动抽取；个别字形若与 PDF 不一致，以 PDF 为准。\n",
        f"*页数: {n_pages}*\n",
    ]

    for page_num in range(n_pages):
        text = extract_page_text(doc[page_num])
        if not text:
            continue
        parts.append("\n---\n")
        parts.append(f"\n<!-- 原书第 {page_num + 1} 页 -->\n\n")
        parts.append(text)

    doc.close()

    body = "".join(parts)
    body = normalize_extracted_text(body)
    # 去掉 PDF 行首多余空格（非代码块场景）
    body = "\n".join(line.lstrip() for line in body.splitlines())
    body = re.sub(r"\n{3,}", "\n\n", body).strip()
    output_file = output_dir / f"{pdf_file.stem}.md"
    output_file.write_text(body + "\n", encoding="utf-8")
    print(f"转换完成: {output_file}")
    return str(output_file)


def batch_convert(input_dir: str, output_dir: str | None = None) -> list[str]:
    import fitz  # noqa: F401

    input_path = Path(input_dir)
    if not input_path.exists():
        print(f"目录不存在: {input_dir}", file=sys.stderr)
        return []

    pdf_files = sorted(input_path.glob("*.pdf"))
    if not pdf_files:
        print(f"目录下没有 PDF: {input_dir}", file=sys.stderr)
        return []

    out: list[str] = []
    for pdf_file in pdf_files:
        try:
            out.append(pdf_to_markdown(str(pdf_file), output_dir))
        except Exception as e:
            print(f"转换失败 {pdf_file.name}: {e}", file=sys.stderr)
    return out


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法:", file=sys.stderr)
        print("  单文件: python pdf_to_markdown.py <PDF路径> [输出目录]", file=sys.stderr)
        print("  批量:   python pdf_to_markdown.py --batch <目录> [输出目录]", file=sys.stderr)
        sys.exit(1)

    if sys.argv[1] == "--batch":
        if len(sys.argv) < 3:
            print("请提供输入目录", file=sys.stderr)
            sys.exit(1)
        batch_convert(sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else None)
    else:
        pdf_to_markdown(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None)
