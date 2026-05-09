# 领域资料放进 raw —— 与老师说明对齐

## 1. 放哪里

- **基础知识库**（全班共用那份 zip）：解压到项目里约定好的「智库根」（见仓库根目录 `opinion_analysis_kb/README.md` 或组长说明），一般对应 `opinion_analysis_kb/references/` 下的 wiki / raw / expert_notes 等。
- **三个垂类（健康 / 交通 / 熊猫等）**：新增素材请放进 **本目录**  
  `opinion_analysis_kb/references/raw/`  
  建议子目录习惯：
  - `健康舆情/` …
  - `交通舆情/` …
  - `熊猫知识/` …（熊猫已是 md，可直接放）

## 2. 格式（老师要求）

| 类型 | 建议 |
|------|------|
| **熊猫** | 已是 Markdown、篇幅合适 → **可直接放入**对应子文件夹。 |
| **健康 / 交通** | 若有「整本书」或超长 Word/PDF → **按章节拆成多个文件**；**优先 Markdown**，其次 PDF；**尽量少留 Word**（不利于检索与自动编译）。 |

**拆分已有长 Markdown：**

```bash
cd /path/to/sona-main
.venv/bin/python scripts/split_markdown.py "路径/某长文.md" "opinion_analysis_kb/references/raw/健康舆情/某书拆分输出/"
```

**PDF 转 Markdown：**

```bash
.venv/bin/python scripts/pdf_to_markdown.py <PDF路径> [输出目录]
```

**旧版 `.doc`（非 `.docx`）在 macOS 上可先转纯文本，再整理为 `.md`（本仓库 `健康舆情/公卫参考文献` 已按此处理一例）：**

```bash
textutil -convert txt "某.doc" -output "某.txt"
# 检查正文后合并标题写入 `某.md`，或直接将内容贴入带一级标题的 Markdown 文件
```

## 3. 放进 raw 之后如何「自动编译」

编译指：把 raw 里的文本交给 **`build_reference_wiki`**，生成规范 wiki 页并更新 `wiki/sources` 与索引。

**终端（推荐）：**

```bash
cd /path/to/sona-main
.venv/bin/python scripts/compile_kb_from_raw.py --limit 80
# 若需强制重跑已有页：
.venv/bin/python scripts/compile_kb_from_raw.py --force --limit 80
```

**Cursor 对话框里可以说：**

> 我在 `opinion_analysis_kb/references/raw` 里新增了文件，请帮我运行  
> `python scripts/compile_kb_from_raw.py`（或 `--list-raw` 先看列表）。

## 4. 前置条件（必须）

- **`.env` / `config` 里 tools 模型已配置且可调用**（编译会用 LLM 把 raw 编译成 wiki 页）。  
  若 API 未配置，脚本会报错或 `errors` 非空，需要先修好密钥与模型路由。

## 5. 仅查看 raw 里有哪些文本（不调模型）

```bash
.venv/bin/python scripts/compile_kb_from_raw.py --list-raw
```
