#!/usr/bin/env python3
"""
修复 PDF→Markdown 抽取中的私有区乱码、分页标记、文末广告等。

刻意不做「全文中文断行合并」：双栏排版下合并会把左右栏串行，越修越乱。
若需可读正文，请用期刊 PDF 在支持版面分析的工具中重抽，或手工校对。

用法：
  .venv/bin/python scripts/repair_chinese_pdf_md.py <输入.md> [--in-place] [-o 输出.md]
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path


_PUA = re.compile(r"[\ue000-\uf8ff\ufeff]")


def strip_pua(s: str) -> str:
    return _PUA.sub("", s)


def merge_latin_fragments(text: str) -> str:
    """仅合并 ASCII / 全角拉丁词内误断行，不碰汉字换行。"""
    fw = "Ａ-Ｚａ-ｚ０-９．，；：（）／－＋＝％"
    text = re.sub(r"(?<=[a-zA-Z.,;:/])\n(?=[a-zA-Z])", "", text)
    text = re.sub(rf"(?<=[{fw}])\n(?=[{fw}])", "", text)
    return text


def trim_trailing_ads(text: str) -> str:
    markers = ("欢迎订阅", "《社会科学总论》", "中国人民大学书报资料中心")
    cut = len(text)
    for m in markers:
        i = text.find(m)
        if i != -1 and i < cut:
            cut = i
    if cut < len(text):
        text = text[:cut].rstrip()
    return text


def ocr_title_line_fixes(s: str) -> str:
    """首屏竖排/错字常见替换（保守，仅替换明确错形）。"""
    reps = (
        ("哭发么共卫生事件", "突发公共卫生事件"),
        ("徵博", "微博"),
        ("漬化", "演化"),
        ("时厚趋势", "时序趋势"),
        ("徽博", "微博"),
        ("理论探杀", "理论探讨"),
    )
    for a, b in reps:
        s = s.replace(a, b)
    # 竖排标题被拆成「理 / 论 / 探 / 杀」四行（「杀」为「讨」类 OCR 误识）
    s = re.sub(r"理\s*\n\s*论\s*\n\s*探\s*\n\s*杀", "理论探讨", s)
    return s


def repair_body(raw: str) -> str:
    lines = raw.splitlines()
    out: list[str] = []
    for line in lines:
        s = strip_pua(line)
        if re.match(r"^##\s*第\s*\d+\s*页\s*$", s.strip()):
            continue
        if not s.strip():
            continue
        if re.fullmatch(r"[：:；，、．。\s|｜]+", s.strip()):
            continue
        out.append(s)

    text = "\n".join(out)
    text = merge_latin_fragments(text)
    text = trim_trailing_ads(text)
    text = ocr_title_line_fixes(text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


FRONT = """---
title: 突发公共卫生事件的微博主题演化模式和时序趋势——以 Twitter 和 Weibo 的埃博拉微博为例
authors: 安璐, 杜廷尧, 余传明, 周利琴, 李纲
journal: 情报资料工作
year: 2016
issue: 第5期
source_note: 由同目录 PDF 经 scripts/pdf_to_markdown.py（按文本块 bbox 排序）抽取，再经 repair_chinese_pdf_md.py 去乱码、分页与文末广告；作者/脚注等仍可能有错序，引用请以 PDF 为准。
---

# 突发公共卫生事件的微博主题演化模式和时序趋势——以 Twitter 和 Weibo 的埃博拉微博为例

**作者**：安璐，杜廷尧，余传明，周利琴，李纲  
**单位**：武汉大学信息管理学院；中南财经政法大学信息与安全工程学院

## 摘要

文章利用潜在狄利克雷分配（LDA）模型和自组织映射（SOM）方法，比较分析了 Twitter 与 Weibo 平台上关于西非埃博拉（Ebola）病毒爆发的微博热点主题类别，揭示其演化模式和时序趋势的异同点，并根据这些特点为突发公共卫生事件管理部门的应急决策提供了实际建议。

## 关键词

时序分析；主题演化模式；埃博拉爆发；微博；突发公共卫生事件

---

## 正文（PDF 抽取稿，已去乱码与分页）

> 以下仍为按页按行抽取的正文，**双栏期刊在自动抽取时可能出现左右栏交错**；引用结论请以期刊 PDF 为准。若你有原版 PDF，可在项目根执行：  
> `python scripts/pdf_to_markdown.py <你的.pdf> opinion_analysis_kb/domains/health/materials/公卫参考文献/`  
> 再人工对照排版校对。

"""


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("path", type=Path)
    ap.add_argument("--in-place", action="store_true")
    ap.add_argument("-o", "--output", type=Path, default=None)
    args = ap.parse_args()
    p: Path = args.path
    if not p.is_file():
        print(f"文件不存在: {p}", file=sys.stderr)
        sys.exit(1)

    raw = p.read_text(encoding="utf-8", errors="replace")
    if args.in_place:
        legacy = p.with_suffix(p.suffix + ".legacy")
        if not legacy.exists():
            legacy.write_text(raw, encoding="utf-8")
            print(f"已备份原稿: {legacy}")

    body = repair_body(raw)
    body = re.sub(
        r"^#\s*[^\n]+\n+(\*来源:.*\n+)?(\*页数:.*\n+)?",
        "",
        body,
        count=1,
        flags=re.MULTILINE,
    )
    final = FRONT + body.lstrip()

    if args.in_place:
        p.write_text(final, encoding="utf-8")
        print(f"已写入: {p}")
    elif args.output:
        args.output.write_text(final, encoding="utf-8")
        print(f"已写入: {args.output}")
    else:
        out = p.with_suffix(".repaired.md")
        out.write_text(final, encoding="utf-8")
        print(f"已写入: {out}")


if __name__ == "__main__":
    main()
