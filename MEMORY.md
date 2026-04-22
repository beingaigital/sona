# MEMORY.md - Sona 记忆系统

## 概述

记忆分为 **会话级（STM）**、**会话内可补丁的 harness 记忆**、**项目级配置** 与 **可追加的案例/经验文件**。  
长期图谱数据库等为可选扩展，**当前仓库主线不依赖 Neo4j 即可运行**。

> 实现提示：`PolicyLoader` 若在本文件全文匹配到特定旧版措辞（源码中的字面量），会把 `preference==平衡` 自动改成 **成本优先**。维护本文件时请避免复现该旧措辞；若需对齐行为请直接改 `USER.md` 的 `preference`。

<!-- HARNESS:BEGIN -->
## Harness injection（给模型看的记忆/复用策略摘要）

- STM：每个 `task_id` 对应 `memory/STM/<task_id>.json`，保存对话、token 累计、`harness_memory.session_prefs`（如 wiki 风格/topk/微博辅助开关等）。
- 项目记忆：`workflow/domain_routing.json` 等领域策略；样例/经验：`memory/examples.jsonl`、`memory/LTM/search_plan_experience.jsonl`（若存在）用于沉淀可复用片段，不是每次对话全量加载。
- 引用历史结论时优先指向**本会话或明确路径**；跨任务复用 sandbox 数据需与用户「是否复用」确认一致。
<!-- HARNESS:END -->

## STM（短期记忆）

### 位置

- 目录：`memory/STM/`（由 `utils/path.ensure_memory_dirs` 保证存在）
- 单会话文件：`memory/STM/<task_id>.json`

### 典型结构（与 `SessionManager.create_session` 对齐）

```json
{
  "task_id": "uuid",
  "created_at": "ISO-8601",
  "updated_at": "ISO-8601",
  "description": "会话描述",
  "initial_query": "用户或系统的首轮输入",
  "messages": [],
  "harness_memory": {
    "session_prefs": {},
    "notes": {}
  },
  "token_usage": {
    "total_tokens": 0,
    "prompt_tokens": 0,
    "completion_tokens": 0,
    "steps": []
  }
}
```

### 生命周期

- 随会话持续追加；消息压缩会重置部分 `token_usage` 累计字段（见 `SessionManager.replace_messages`）。
- 归档/清理策略由运维侧决定（仓库内**未强制**「7 天删除」逻辑，旧描述作废）。

## Harness 会话偏好（`harness_memory.session_prefs`）

由 `utils/harness_memory.normalize_session_pref_patch` 等归一化的键（示例）：

- `wiki_style`: `teach` | `concise`
- `wiki_topk`: 整数（裁剪到合理区间）
- `wiki_weibo_aux`: 是否附加微博智搜辅助

## 项目级与 LTM 式文件

| 类型 | 路径 | 用途 |
|------|------|------|
| 领域路由 | `workflow/domain_routing.json` | 按 domain 的 must_include / prefer / blocklist 等 |
| 案例记忆 | `memory/examples.jsonl` | `append_example_memory` 追加，JSONL 流式扩展 |
| 检索计划经验 | `memory/LTM/search_plan_experience.jsonl` | 事件流水线调试/经验（若启用） |

## 记忆读写策略（与当前代码一致）

- **读**：ReAct 历史经 `SessionChatMessageHistory` 从 STM 加载；策略片段经 `utils/policy_docs.format_harness_policy_for_prompt` 注入 system prompt（仅各文件 `HARNESS` 块，有长度上限）。
- **写**：每轮对话与工具结果写入会话 JSON；领域/样例文件由专门 API/工作流写入。

## 保留策略

- 默认：**不**在仓库内自动删除 STM；由磁盘管理或外部作业清理。
- JSONL 类文件建议按大小或时间做外部轮转。

## 与组件的对应关系

| 组件 | 路径 |
|------|------|
| 会话管理 | `utils/session_manager.py` |
| Harness 归一化 | `utils/harness_memory.py` |
| 策略注入 | `utils/policy_docs.py` |
| 任务产物 | `sandbox/<task_id>/` |
