# Sona Harness 与评估规范

更新日期：2026-05-11

## 1. 目标

本规范用于把 Sona 的报告质量要求落到可执行的评估与回归流程中。当前基线以本地 `feature-analysis` 分支为准，优先保护稳定事件分析链路，不通过大范围改造 CLI、模型配置或主工作流来接入评估。

Harness 的目标是：

- 结论可追溯到过程文件、图表数据或知识库来源。
- 低样本、证据缺口和模型弱输出能被识别并降级。
- 评估结果能写入 `eval_results/`，供下一轮报告生成和回归 dashboard 使用。
- 新增评估能力优先接入现有 `tests/evals/` 与 `scripts/eval_runner.py`，避免形成第二套孤立评测系统。

## 2. 四项硬规则

### 2.1 事实可追溯

关键结论必须能关联至少一个证据来源：

- 过程文件：如 `dataset_summary.json`、`volume_stats.json`、`keyword_stats.json`、`region_stats.json`、`timeline_analysis*.json`、`sentiment_analysis*.json`。
- 报告元数据：如 `report_meta.json` 中的 sections、references、analogous_cases、theory_frameworks。
- 知识库或 Graph RAG：如 `reference_insights`、相似案例、理论框架来源。

### 2.2 证据不足显式化但不污染报告

当确实缺少证据时，允许明确说明“证据不足”。但模板默认值、解析失败兜底、模型弱输出不应大面积泄漏“证据不足”占位文案。

优先策略：

- 有结构化统计数据时，用程序化叙事回填图表结论。
- 无数据时输出中性空状态，如“暂无时间线数据”“暂无关键词”。
- 只有在结论需要事实支撑且来源缺失时，才标注证据不足。

### 2.3 不可编造

禁止补写输入中不存在的具体时间、机构、人物、样本量、来源路径、法律条文和处置动作。文件路径必须来自工具返回或本地已存在文件。

### 2.4 低样本降级

默认低样本门槛：

- `search_matrix.total_count < 30`
- `data_collect.valid_rows < 20`

命中后不得输出确定性风险等级。可选动作是重试、扩词、请求确认或输出“低样本，仅供参考”的保守报告。

## 3. 任务策略

### 3.1 事件分析

主链路：提词 -> 采集 -> 统计 -> 时间线/情感 -> 研判 -> 知识增强 -> HTML 报告。

必验点：

- 提词合法，时间范围明确。
- 采集产物路径存在，样本量达标。
- 分析 JSON 可解析，字段完整。
- 报告 HTML 生成成功，关键章节和图表元数据完整。

### 3.2 热点分析

热点任务必须声明窗口期和数据覆盖。无数据时输出空结果说明，不做强结论。高风险热点进入事件分析前，应保留原始热榜项、风险因子和触发原因。

### 3.3 Wiki 与案例参考

Wiki/案例只作为解释增强，不替代原始舆情数据。理论观点和相似案例必须标明来源，不能把参考观点写成事实结论。

### 3.4 专题监测

专题关键词、排除词、频率和预警阈值变更需要确认。连续低样本窗口应停止自动研判，并提示调整关键词或时间窗。

## 4. 评估系统接入

当前本地评估入口是：

- `scripts/eval_runner.py`
- `tests/evals/runner.py`
- `tests/evals/cases/*.json`
- `tests/evals/scorers/core.py`
- `eval_results/<run_id>/summary.json`
- `workflow/regression_dashboard.py`

新增 golden case 应优先放入 `tests/evals/cases/`，并配套 replay fixture 或明确 live 执行边界。不要另建与现有 runner 不兼容的 `eval/runner.py` 体系，除非先完成迁移设计。

## 5. Golden Case 字段建议

推荐在 `tests/evals/cases/*.json` 中表达：

- `id`：样例 ID。
- `suite` / `suites`：如 `ci-gate`、`report-quality`、`domain-health`。
- `target`：`workflow`、`tool` 或 `wiki`。
- `stage`：如 `report`、`sentiment`、`data_collect`。
- `fixtures.mode`：优先 `replay`，需要真实联网或模型时才用 `live`。
- `expectations.required_fields`：输出必需字段。
- `expectations.thresholds`：结构、追溯、相关性、解析成功率等阈值。
- `expectations.report`：报告章节、引用数量、关键 flags。
- `expectations.report_depth`：相似案例、风险模式、理论框架等深度指标。
- `expectations.consistency`：占位词泄漏、生命周期口径、数值一致性的 warning 预算。

## 6. 回归门槛

合并前至少运行：

```bash
python3 -m py_compile tools/report_html_template.py
pytest -q tests/contracts/test_report_eval_feedback.py tests/contracts/test_eval_runner_strict_warnings.py tests/contracts/test_regression_dashboard.py
```

涉及 `scripts/eval_runner.py`、`tests/evals/`、`workflow/runtime_harness.py` 的改动，还应运行对应 `tests/contracts/test_*eval*` 与 `workflow-smoke` replay 套件。

## 7. 合并纪律

不应合并以下内容：

- `__pycache__/`、`.pyc`、本地内存会话、个人运行结果、临时评分 JSON。
- 未经确认的模型供应商切换或 API key 环境变量切换。
- 默认启用的交互式评分/反馈收集，除非明确不会阻塞 CLI 主流程。
- 与现有 `tests/evals` 并行但不互通的第二套评估入口。

