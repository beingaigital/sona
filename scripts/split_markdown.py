"""
按章节拆分过长的 Markdown 文件
使用方法：python split_markdown.py <文件路径> [输出目录]
"""

import re
import sys
from pathlib import Path


def split_markdown_by_headers(input_path: str, output_dir: str = None) -> list[str]:
    """按一级标题(#)拆分 Markdown 文件"""

    input_file = Path(input_path)
    if not input_file.exists():
        print(f"文件不存在: {input_path}")
        return []

    content = input_file.read_text(encoding='utf-8')

    if output_dir is None:
        output_dir = input_file.parent
    else:
        output_dir = Path(output_dir)

    output_dir.mkdir(parents=True, exist_ok=True)

    base_name = input_file.stem

    pattern = r'^(#{1}\s+.+)$\n'
    parts = re.split(pattern, content, flags=re.MULTILINE)

    chunks = []
    current_title = base_name
    current_content = []

    for i, part in enumerate(parts):
        if re.match(r'^#{1}\s+', part):
            if current_content:
                chunks.append((current_title, ''.join(current_content)))

            header_match = re.match(r'^#{1}\s+(.+)$', part)
            if header_match:
                current_title = header_match.group(1).strip()
            current_content = [part]
        else:
            current_content.append(part)

    if current_content:
        chunks.append((current_title, ''.join(current_content)))

    output_files = []
    for idx, (title, content) in enumerate(chunks):
        safe_title = re.sub(r'[<>:"/\\|?*]', '_', title)
        safe_title = safe_title[:80].strip()

        if idx == 0:
            output_file = output_dir / f"{base_name}_目录.md"
        else:
            output_file = output_dir / f"{base_name}_{idx:02d}_{safe_title}.md"

        frontmatter = f"""---
title: {title}
source_file: {input_file.name}
split_index: {idx}
---

"""

        output_file.write_text(frontmatter + content, encoding='utf-8')
        output_files.append(str(output_file))
        print(f"创建: {output_file.name}")

    return output_files


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法: python split_markdown.py <文件路径> [输出目录]")
        sys.exit(1)

    input_path = sys.argv[1]
    output_dir = sys.argv[2] if len(sys.argv) > 2 else None

    files = split_markdown_by_headers(input_path, output_dir)
    print(f"\n共拆分成 {len(files)} 个文件")
