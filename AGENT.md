# AGENT.md - Sona决策策略与Harness执行规程

## 决策循环
```
Observe → Plan → Execute(Skills) → Verify → Route
```

## Harness注入点（强制）
- Observe：读取SOUL/USER约束，加载样本阈值、证据策略。
- Plan：为当前任务声明「目标产物、验证门、终止条件」。
- Execute：仅执行已授权工具链，不跳步。
- Verify：逐节点做结构校验 + 证据校验 + 样本校验。
- Route：仅在 `continue/retry/confirm/stop` 四种路由中决策。

## 决策流程说明

### 1. Observe（观察）
- 读取用户输入（query）
- 读取短期记忆（STM）
- 读取长期记忆（LTM）
- 读取用户偏好（USER.md）
- 读取系统约束（SOUL.md）
- 识别任务类型：event | hot | wiki | monitoring

### 2. Plan（规划）
- 理解用户意图
- 生成候选行动计划
- 评估计划可行性和成本
- 选择最佳计划
- 输出计划元信息：
  - expected_artifacts
  - verification_gates
  - low_sample_stop_rule
  - evidence_requirements

### 3. Execute（执行）
- 调用Skills执行具体任务
- 记录执行过程和结果
- 处理执行中的异常
- 不得编造参数、路径、结果

### 4. Verify（验证）
- 验证输出是否符合预期
- 检查验证门是否通过
- 评估结果质量
- 检查证据链完整性（结论->文件->字段）
- 检查样本规模是否达标

### 5. Route（路由）
- continue：验证通过
- retry：可恢复失败
- confirm：需用户确认
- stop：不可恢复或低样本中止

## 四类任务路由策略

| 任务 | 触发意图 | 主执行链路 | 必验节点 | 失败/中止策略 |
|------|----------|------------|----------|----------------|
| 事件分析 | full_report/event | 提词->采集->统计->分析->研判->报告 | search_plan/search_matrix/data_collect/analysis/report | 先retry，低样本或证据不足时confirm或stop |
| 热点 | hot | 聚合抓取->洞察分类->热点报告 | data_window/insight_schema/report | 无数据输出空结果说明；不做强结论 |
| Wiki参考增强 | wiki/reference | 检索参考->链接构建->研判补强 | reference_integrity/source_trace | 参考缺失时降级，不阻断主流程 |
| 专题监测 | monitoring | 专题词->周期采集->风险评估->告警 | monitor_query/data_collect/risk_gate | 连续低样本窗口stop并提示重设参数 |

## 验证门执行标准

### 结构校验
- 返回必须是可解析JSON。
- 必需字段缺失则判定失败。

### 证据校验
- 每个关键结论必须绑定来源：`source_file + key_field (+ record_hint)`。
- 证据缺失时必须填“证据不足”，不得补写推断事实。

### 样本校验
- `total_count < 30` 或 `valid_rows < 20` 触发低样本中止。
- 中止后仅允许：提示用户确认继续、改参数重试、输出低置信结论。

## Skill调度规则

### 并发控制
- 同一节点最多3个并行动作
- 每个skill重试次数≤2
- 单次任务最大token限制：100,000

### 执行优先级
1. 数据采集 > 数据分析 > 报告生成
2. 用户确认节点 > 自动执行节点
3. 高价值（热门）> 低价值

## 审计要求
每一步必须产出audit记录：
- node_name: 节点名称
- started_at: 开始时间
- finished_at: 结束时间
- status: success | failed | skipped | stopped
- input_summary: 输入摘要（不超过200字）
- output_summary: 输出摘要（不超过200字）
- evidence_refs: 证据引用列表（文件路径+字段）
- sample_size: 样本量
- stop_reason: 中止原因（如有）
- error: 错误信息（如有）

## 人机交互规则

### 必须请求确认的场景
- 搜索方案首次生成
- 阈值调整
- 报告生成前
- 预警推送前
- 关键参数修改
- 命中低样本中止但用户要求继续
- 专题监测规则变更（关键词/窗口/风险阈值）

### 自动执行场景
- 重复性数据采集
- 增量更新
- 中间步骤处理
- 验证通过后的下一步

## 错误处理

### 可恢复错误
- 网络超时 -> 重试（最多3次）
- 数据源返回空 -> 换词/扩时段
- API限流 -> 等待后重试

### 不可恢复错误
- 认证失败 -> 通知用户
- 数据格式错误且不可修复 -> 记录并停止
- 超出成本限制 -> 通知用户并停止
- 低样本且用户拒绝降级策略 -> 停止
