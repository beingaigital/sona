# Harness 设计规范（harness_spec）

## 1. 目标与边界

本规范用于在不改变现有程序架构前提下，完成 Sona 的 Harness 注入升级。

- 保持现有架构：`CLI -> 路由 -> ReAct/Workflow -> tools -> sandbox`。
- 不新增执行引擎，不改路由核心逻辑，不改主工作流拓扑。
- 通过 `SOUL.md`、`AGENT.md`、`USER.md` 与 `prompt/*` 强化可执行约束。

## 2. Harness四项硬规则

### 2.1 事实可追溯
- 定义：所有关键结论必须能映射到输入证据。
- 最小要求：结论至少关联一个来源文件（如 `dataset_summary.json`、`timeline_result.json`、`sentiment_result.json`、`reference_insights.json`）。
- 禁止：无来源结论、来源与结论不一致。

### 2.2 证据不足显式化
- 定义：证据不满足阈值或存在冲突时，输出必须显式标注“证据不足”。
- 要求：不得用“可能”“大概率”替代证据缺失声明。

### 2.3 不可编造
- 定义：不得生成输入中不存在的数据、路径、时间、主体、来源。
- 重点：文件路径必须来自工具返回；不得手工猜测。

### 2.4 低样本中止
- 默认阈值：
  - `search_matrix.total_count < 30`
  - 或 `data_collect.valid_rows < 20`
- 动作：停止深度研判，进入 `confirm` 或 `降级输出`。
- 用户强制继续时：结果必须附“低样本，仅供参考”。

## 3. 四类任务执行策略

## 3.1 事件分析
- 触发：`/event` 或路由到 `full_report/event_analysis_workflow`。
- 执行链：提词 -> 采集 -> 统计 -> timeline/sentiment -> interpretation -> graph_rag -> report。
- 强制验证：
  - 提词合法性
  - search_matrix样本阈值
  - data_collect路径与行数
  - 分析JSON可解析
  - 报告结论可溯源

## 3.2 热点
- 触发：`/hot` 或路由 `hottopics_workflow`。
- 执行链：多源聚合 -> 洞察 -> 分类 -> 报告。
- 强制验证：
  - 数据窗口说明完整
  - 无数据时输出空报告说明
  - 结论需附覆盖范围

## 3.3 Wiki参考增强
- 触发：事件工作流中的参考检索步骤（非独立主流程）。
- 执行链：`search_reference_insights` -> `build_event_reference_links` -> 注入报告。
- 强制验证：
  - 参考信息仅用于补充解释
  - 理论观点需标注来源
  - 缺失时可跳过，不阻断主链

## 3.4 专题监测
- 触发：监测类意图（当前并入事件分析链路）。
- 执行链：专题词 -> 周期采集 -> 风险评估 -> 告警/报告。
- 强制验证：
  - 关键词、窗口、阈值变更确认
  - 连续低样本窗口触发中止
  - 仅在授权条件下推送预警

## 4. 与工作流验证节点映射

| Workflow节点 | 主要校验 | Harness动作 | 失败分支 |
|---|---|---|---|
| search_plan | searchWords非空、timeRange合法 | 记录提词依据 | retry/confirm |
| search_matrix | total_count阈值校验 | 低样本预警 | confirm/stop |
| data_collect | save_path真实、valid_rows达标 | 路径溯源校验 | retry/fallback/stop |
| analysis_timeline | JSON解析+节点可证据化 | 缺证据标注 | fallback/证据不足 |
| analysis_sentiment | JSON解析+样本代表性 | 低样本降级 | fallback/证据不足 |
| interpretation | 研判与前序结果一致 | 结论追溯检查 | 降级输出 |
| graph_rag | 增强内容可选 | 来源标注 | 跳过继续 |
| report_html | 报告生成成功+结论可追溯 | 证据索引检查 | 阻断发布 |

## 5. Prompt注入点映射

| 文件 | 注入内容 | 目的 |
|---|---|---|
| `SOUL.md` | 四项硬规则 + 四类任务策略 + 低样本门槛 | 系统级硬约束 |
| `AGENT.md` | Observe/Plan/Execute/Verify/Route 的 harness 检查 | 决策过程可执行化 |
| `USER.md` | harness偏好、低样本阈值、专题监测配置 | 用户可配置化 |
| `prompt/system_prompt.txt` | 四类任务策略与硬规则 | 主模型执行一致性 |
| `prompt/extract_search_terms.txt` | 证据不足与不可编造 | 提词阶段防幻觉 |
| `prompt/analysis_timeline.txt` | 节点可追溯与不足标注 | 时间线可靠性 |
| `prompt/analysis_sentiment.txt` | 低样本降级与不足标注 | 情感分析稳健性 |
| `prompt/interpretation.txt` | 研判证据约束 | 结论可信度 |
| `prompt/report_html_enhanced.txt` | 图后结论证据绑定 | 报告可审计性 |
| `prompt/report_html_template_fill.txt` | 无证据统一输出规则 | 模板填充一致性 |

## 6. 失败策略矩阵

| 失败类型 | 处理策略 | 是否中止 |
|---|---|---|
| 可恢复网络/限流 | 自动重试（上限内） | 否 |
| 提词不充分 | 换词/扩时段/确认 | 否 |
| 低样本 | confirm 或 降级 | 条件中止 |
| 路径无效/文件缺失 | fallback 或重采集 | 条件中止 |
| 证据链断裂 | 标注证据不足并降级 | 视严重度 |
| 报告生成失败 | 记录错误并阻断发布 | 是 |

## 7. 验收对照

- 事件分析、热点、wiki、专题监测四类任务均给出明确执行策略：**已覆盖**。
- prompt 文档从“原则”升级为“可执行约束 + 验证节点映射”：**已覆盖**。
- 不改变程序架构，仅变更 harness 文档与 prompt 语义层：**已满足**。
