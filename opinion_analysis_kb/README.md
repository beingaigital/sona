# opinion_analysis_kb（垂类知识库）

承接《SONA_VERTICAL_UPGRADE_PLAN》中的知识库布局。

## 顶层结构

- **`references/`** — 自 **`opinion_analysis_kb.zip`** 解压的通用智库资产（`raw/`、`wiki/`、`expert_notes/` 等），可与既有 `舆情深度分析/references/` 互补；后续编译脚本可择一并入库。
- **`domains/health/`** — 任务 07：健康舆情包（Wiki 索引 15 + 你方「健康舆情」控烟报告与公卫参考文献 + `playbook_health.md` + 案例 `H01–H10`）。
- **`domains/panda/`** — 任务 09：大熊猫包（你方「熊猫知识」20 篇 `docs/` + `playbook_panda.md` + 案例 `P01–P08`）。
- **`domains/transport/`** — 交通舆情包（课题摘录 **30** 篇 + `playbook_transport.md` + 案例 `T01–T10`，详见 `domains/transport/MANIFEST.md`）。

## 导入记录（2026-05-07）

已从微信 `temp/drag` 解压并归位：

| 压缩包 | 归位说明 |
|--------|-----------|
| `opinion_analysis_kb.zip` | → 本目录下 `references/` 等 |
| `健康舆情.zip` | → `domains/health/materials/` |
| `熊猫知识.zip` | → `domains/panda/docs/` |

## 与老师口径对齐：领域新进 raw → 编译 wiki

- **垂类增量**（健康 / 交通 / 熊猫等）请优先放进 **`references/raw/`**（可分子目录），详见 **`references/raw/README_领域资料上传说明.md`**。
- 放好后在本机执行：**`python scripts/compile_kb_from_raw.py`**（需 `.env` 里 tools 模型可用）；或仅在 Cursor 里说「raw 里新增了文件，帮忙编译」并让助手运行该命令。
- 长文档请先 **`scripts/split_markdown.py`** 按章拆分；PDF 可选用 **`scripts/pdf_to_markdown.py`** 再入库。
