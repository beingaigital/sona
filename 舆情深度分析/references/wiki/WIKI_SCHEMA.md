# 舆情智库 Wiki Schema（v2.1）

本文件定义 `references/wiki` 的结构规范、页面约定与维护流程。
目标：让 LLM 在 ingest/query/lint 时行为稳定、可复用、可演进。

## 1) 目录结构

- `index.md`：内容索引（目录型）
- `log.md`：变更日志（时间序列，append-only）
- `sources/`：来源编译页（raw / expert notes 的单源页面）
- `concepts/`：概念与方法论页（议程设置、回应窗口、次生舆情等）
- `entities/`：实体页（人物、机构、平台、媒体、地域、群体）
- `output/`：问答产物与专题输出（可回流入 wiki）

## 2) 页面命名与 frontmatter

每个 wiki 页面必须包含 YAML frontmatter，至少包含：

- `title`: 页面标题
- `source_file`: 原始来源文件绝对路径
- `updated_at`: 更新时间（`YYYY-MM-DD HH:mm:ss`）
- `tags`: 标签数组（建议 3-8 个）
- `source_type`: `raw | expert_note | method | mixed`
- `confidence`: `high | medium | low`
- `wiki_section`: `sources | concepts | entities | output`

建议增加：

- `entities`: 实体数组（人名、机构、平台、地点）
- `topics`: 主题数组（如：公共秩序、未成年人、平台治理）
- `related_concepts`: 关联概念数组
- `related_entities`: 关联实体数组

## 3) 正文结构（固定顺序）

每页按以下章节顺序输出，缺证据时必须写“证据不足”：

1. `## 事件概述`
2. `## 关键事实`
3. `## 传播机制`
4. `## 情绪与立场`
5. `## 风险点`
6. `## 可复用方法论`
7. `## 相似议题线索`
8. `## 引用片段`

## 4) 写作与证据规则

- 禁止编造；无法确认时写“证据不足”。
- 句子尽量短，优先可检索性与可引用性。
- 结论优先采用链路：证据 -> 机制 -> 风险/影响 -> 建议。
- 关键观点尽量附引用片段（原文短句）。
- 若来源包含 `expert_notes`，优先抽取“概念、框架、方法、判断标准”。
- 重点识别舆情分析实体与概念：
  - 实体：事件主体、机构、媒体、平台、受众群体、地域
  - 概念：议程设置、沉默螺旋、情绪传染、次生舆情、回应窗口、叙事框架等

## 5) Ingest 工作流

每次 ingest 单个 source 时，执行：

1. 读取 source（默认来自 `raw/` 与 `wiki/output/`），抽取实体与核心论点
2. 生成/更新 `sources/<slug>.md`
3. 抽取候选实体与概念，增量更新 `entities/` 与 `concepts/`（允许后续异步完善）
4. 更新 `index.md`（标题 + 一行摘要）
5. 追加 `log.md`（`## [YYYY-MM-DD HH:mm:ss] ingest | <source_name>`）

## 6) Query 工作流

回答问题时优先读取：

1. `index.md`（定位页）
2. 命中 `concepts/`、`entities/`、`sources/`（按相关度）
3. raw/expert_notes（补证据）
4. 高价值回答沉淀为 `output/` 页面，并链接相关概念/实体

说明：`output/` 中沉淀的高价值内容会在后续 ingest 中回流，持续强化概念页与实体页。

回答需尽可能附上来源页或引用片段。

## 7) Lint 工作流（周期巡检）

定期检查以下问题：

- 章节缺失、frontmatter 缺字段
- 证据与结论不一致
- 旧结论被新材料推翻但未更新
- 孤立页面（无主题/实体关联）
- 高频概念未沉淀为稳定标签
- `output/` 中高价值结论是否已回流 `concepts/` 或 `entities/`
- 概念页与实体页是否具备双向链接（关联概念/关联实体）

## 8) 变更策略

- 本 schema 可逐步演进；修改后新 ingest 立即生效。
- 大改建议提升版本号（如 v2）并在 `log.md` 记一条 schema 变更记录。
