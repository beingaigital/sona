# AGENT.md - Sona 决策策略

## 决策循环（概念）

```
Observe → Plan → Act(Tools) → Verify → Route
```

与实现对齐时：**Observe** 含用户 query、会话 JSON、`route_policy`；**Act** 在 CLI 中表现为「意图路由 → 工作流或 ReAct」；**Verify** 含工具 JSON 可解析性、样本量门槛、文件落盘检查。

<!-- HARNESS:BEGIN -->
## Harness injection（给模型看的执行策略摘要）

- 先判断任务类型再动手：完整舆情报告、轻量事件概述、热点、开放问答（ReAct）路径不同，避免用重型采集解决简单问句。
- 开放问答（QA 工具模式）：优先直接回答；避免重复调用同一工具且输入语义几乎相同；外部检索连续失败则说明证据不足并停止试探。
- 用户偏好 `prefer_confirm: true` 且检测到 sandbox 有候选数据时，**是否在交互里复用历史 CSV** 需要用户一次确认（除非策略关闭确认）。
- 任何结论应能指向工具输出或摘录；验证失败说明失败原因，再决定重试、改参或请求人类。
<!-- HARNESS:END -->

## 与 `cli/router.py` 对齐的路由结果

`IntentRouter.route` 返回的决策字符串（模型在解释「系统正在做什么」时应引用这些名称）：

| 路由值 | 典型触发 | 执行形态（摘要） |
|--------|-----------|------------------|
| `event_analysis_workflow` | 强事件/报告意图，或「有历史数据但策略要求覆盖/深挖」 | 完整流水线，含采集（除非后续参数指定跳过） |
| `event_analysis_with_existing_data` | 事件分析意图 + sandbox 命中数据 + 策略偏「复用/成本」 | 仍走事件工具链，但**默认倾向跳过 data_collect**（最终是否跳过以交互确认为准） |
| `event_brief_workflow` | 「事件经过 / 概述 / 发生了什么」等轻量模式 | 仅 `extract_search_terms` + 文本概述 |
| `hottopics_workflow` | 热点/态势类 query | 热点聚合 UI / 命令流 |
| `reactagent` | 未命中上述强意图 | LangChain `create_agent`，全量或 QA 工具集由上层 `task_mode` 决定 |

## 路由与 `USER.md` 的耦合（精要）

- `preference` 为 **`覆盖优先` 或 `深挖优先`** 且已有数据：路由更倾向 **`event_analysis_workflow`**（强调重新跑全链路的意愿）。
- 其它 `preference` 且已有数据：更常得到 **`event_analysis_with_existing_data`**（强调在旧数据上继续分析）。
- `prefer_confirm`：控制检测到历史数据时，是 **Confirm 问答** 还是 **按默认直接选**。

## Skill / 工具调度原则

- **并发**：同一决策点避免无必要并行轰炸外部 API；流水线内部已有自己的阶段划分。
- **重试**：工具级错误由封装处重试；事件流水线对「低样本」有**有限轮次扩窗重采**（受环境变量约束，与 `USER.md` 的 `max_retry` 不是同一套计数器）。
- **上下文**：长会话注意 `compress_messages` 与 token 统计；不要在压缩后假设仍能看到最早的用户原话全文。

## 审计与日志（与仓库实现一致）

- **会话级**：`memory/STM/<task_id>.json` 保存消息、`token_usage`、`harness_memory`（会话偏好补丁等）。
- **调试 NDJSON**：交互 CLI 使用项目下 `.cursor/debug.log`（路径以 `cli/interactive.py` 中常量为准）追加结构化行，便于用 grep/ jq 对照一次完整运行。

下列「理想化 audit 字段」若尚未全部自动化，**以会话文件与 NDJSON 为准**：

- node_name / started_at / finished_at / status / input_summary / output_summary / error

## 人机交互规则（与当前 CLI 对齐）

### 通常会打断你问一下的场景

- 已有 sandbox 数据且 `prefer_confirm: true`：**是否复用旧数据**（默认受 `preference` 影响）。
- 工作流中 Rich / `Prompt.ask` 询问检索方案、阈值、是否继续等（以终端实际提示为准）。

### 通常自动推进的场景

- 工具链内部已确认的步骤（如 interpretation fallback 后继续）。
- 样本量充足且验证通过后的统计与报告阶段。

## 错误处理取向

- **可恢复**：超时、限流、偶发空结果 → 有限重试或扩参；仍失败则向用户暴露部分成功产物路径。
- **不可恢复**：鉴权失败、路径不可写、硬阈值中止 → 明确错误信息，不伪造成功。

## PolicyLoader 约定（勿随意删改关键词）

`cli/router.py` 会扫描本文全文：若包含短语 **必须请求确认的场景**，则强制 `prefer_confirm=true`（与 `USER.md` 解耦时的安全兜底）。下列条目保留该字面措辞以便探测：

### 必须请求确认的场景

- 覆盖或放弃已确认的 sandbox 复用决策
- 可能显著增加费用/外部调用次数的参数变更（以交互提示为准）
