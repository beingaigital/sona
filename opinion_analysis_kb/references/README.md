# 参考资料目录说明

该目录用于给舆情分析流程提供“可引用的本地资料”，会被以下模块自动检索：

- `utils/methodology_loader.py`
- `tools/oprag.py` 中的 `search_reference_insights` / `append_expert_judgement`

## 建议目录结构

```text
opinion_analysis_kb/references/
├── README.md
├── expert_notes/
│   ├── 舆情分析方法论.md
│   └── ...
├── raw/
│   ├── 04_单依纯迎来的一系列雷霆之锤.md
│   └── ...
└── wiki/
    ├── index.md
    ├── log.md
    ├── sources/
    ├── concepts/
    ├── entities/
    └── output/
```

## 文件内容建议

1. `expert_notes/*.md`
- 适合写“你的专家研判”
- 推荐格式：
  - 事件判断
  - 风险链条
  - 治理建议
  - 证据出处（链接/媒体名/时间）

2. `raw/*.md`
- 适合放事件评论、深度文章摘录、你手工整理的观点卡片
- 每段尽量短，方便检索命中

3. `wiki/concepts/*.md`
- 补充你常用的分析框架、指标口径、预警阈值

4. `wiki/entities/*.md`
- 补充你常用的分析框架、指标口径、预警阈值

5. `wiki/output/*.md`
- 适合放事件评论、深度文章摘录、你手工整理的观点卡片
- 每段尽量短，方便检索命中

## Harness 记忆体系（可审计 / 可回滚 / 可 A-B）

为了让智能体具备“可进化”的长期能力，本项目把记忆拆成三类，并在 harness 层做持久化与约束控制。

### 1) 项目记忆（Project memory）：领域路由与证据策略

- **配置文件**：`workflow/domain_routing.json`
- **用途**：
  - 将“领域编码/领域路由”从硬编码升级为可版本化配置
  - 定义“领域 → 必引资料清单 / 优先概念页 / 禁用来源 / 注入预算”
  - 支持审计、回滚与 A/B（通过 git diff + 分支/环境切换）
- **典型字段**（示意）：
  - `domains.<领域>.match.keywords`：领域命中关键词
  - `domains.<领域>.must_include.wiki_sources`：必引的专题/报告（更适合作为可复核证据）
  - `domains.<领域>.must_include.concept_pages`：必引概念总览页（帮助模型建立框架）
  - `domains.<领域>.prefer.concept_pages`：优先概念页（可选）
  - `domains.<领域>.blocklist.path_contains`：禁用来源路径片段（例如屏蔽 `wiki/output/_candidates`）
  - `domains.<领域>.injection_limits.max_seed_pages`：最多注入多少条领域“种子页”
  - `domains.<领域>.injection_limits.max_seed_chars_for_llm`：领域种子进入 LLM 上下文的字符预算（防止挤占事件本体证据）

### 2) 会话记忆（Session memory）：本次任务的临时偏好

- **落盘位置**：每个 `task_id` 的会话文件（由 `SessionManager` 管理）
  - 目录通常为 `memory/STM/<task_id>.json`
  - 字段为 `harness_memory.session_prefs`
- **用途**：保存本次任务偏好并在同一 task 的后续步骤复用，例如：
  - `wiki_style`：`teach` / `concise`
  - `wiki_topk`：召回条数
  - `wiki_weibo_aux`：是否启用微博智搜辅助（如启用，注意它是外部线索而非事实锚点）
- **写入方式**：调用方在执行 agent 时通过 `workflow_options` 传入偏好（harness 会自动写入会话记忆）。

### 3) 样例记忆（Example memory）：高质量样例用于回归评测

- **落盘文件**：`memory/examples.jsonl`（JSONL 便于追加与回归评测）
- **写入时机**：
  - 当 Wiki QA 产出被判定为“高价值”并写入 `wiki/output/_candidates/` 时，会自动把样例追加到 `examples.jsonl`
- **用途**：
  - 构建回归评测集（例如每晚跑固定样例：域命中率、域专题证据覆盖率、输出结构稳定性）
  - 驱动领域路由配置与召回策略的迭代（“改配置→跑样例→看指标”）

### 记忆约束（防止记忆污染/挤占证据）

- 领域路由注入遵循 `max_seed_pages` 上限
- 领域种子进入 LLM 的正文摘录遵循 `max_seed_chars_for_llm` 预算
- 推荐将“必引”优先放在 `wiki/sources/`（专题/季度/年度报告），并用概念页补框架；避免只靠概念页导致证据不足或不可复核
