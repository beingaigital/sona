# Harness 评测实操手册（Case / 回放 / 评分）

本文说明如何编写评测用例、使用回放 fixture、解读评分结果与产物路径。实现细节以 `tests/evals/runner.py`、`tests/evals/scorers/core.py` 为准。

## 1. 目录与产物

| 路径 | 含义 |
|------|------|
| `tests/evals/cases/*.json` | 单条评测定义（输入、fixtures、expectations） |
| `tests/fixtures/<case_id>/` | 回放用录制数据（常见为 `tools.json` 或 `output.json`） |
| `eval_results/<run_id>/` | 一次 runner 运行的汇总与分 case 产物 |
| `eval_results/<run_id>/summary.json` | 本次运行总览（pass/fail/warning 计数、各 case 摘要） |
| `eval_results/<run_id>/ci_report.json` | CI 门禁视图（`status`、`blockers`） |
| `eval_results/<run_id>/<case_id>/metrics.json` | 单 case：`status`、`metrics`、`fail_reasons` |
| `eval_results/<run_id>/<case_id>/artifacts/output.json` | 被执行体的输出快照 |
| `eval_results/<run_id>/<case_id>/trace.jsonl` | 极简 trace 事件 |

## 2. Case JSON 怎么写

最小字段：

- `id`：case 唯一 id，建议与 fixture 目录名一致。
- `target`：`wiki` | `tool` | `workflow`。
- `stage`：`tool` / `workflow` 时与工具或阶段对应（如 `data_collect`、`sentiment`、`report`）；`wiki` 可为 `null`。
- `suite`：主套件标签（如 `basic`、`workflow-smoke`）。
- `suites`（可选）：额外标签列表；任一命中即纳入 `--suite X`（用于 `ci-gate` 等多归属）。
- `input`：传给执行逻辑的参数（如 wiki 的 `query`、`options`）。
- `fixtures`：
  - `mode`：`replay` 表示从文件读入输出，不触发 live 网络/模型。
  - `recorded_tools`：相对于仓库根的路径，指向录制的 JSON。
- `expectations`：断言与阈值（见下文「评分与阈值」）。

`target=wiki` 且 `replay` 时，runner 直接将 fixture JSON 当作 `answer_wiki_query` 的结构化输出用于打分。

`target=tool` 且 `replay` 时，fixture 会走 `workflow/tool_schemas` 中对应 stage 的 schema 校验（若配置）。

## 3. 回放（Replay）怎么做

1. 在 `tests/fixtures/<case_id>/` 下放置录制文件（命名需与 case 里 `recorded_tools` 一致）。
2. case 中设置 `"fixtures": { "mode": "replay", "recorded_tools": "tests/fixtures/..." }`。
3. 录制内容应 **小**、**结构完整**、**字段与 expectations 对齐**；避免无关字段漂移。

更详细的 fixture 约定见 [`tests/fixtures/README.md`](../../tests/fixtures/README.md)。

推荐本地/CI 使用：

```bash
export EVAL_MODE=replay
export EVAL_DETERMINISTIC=1   # 固定时间戳与随机种子，便于 diff
```

## 4. 如何运行

```bash
# 全量 cases（受 filter 影响）
python scripts/eval_runner.py --mode replay

# 指定套件（如 CI 的 ci-gate）
python scripts/eval_runner.py --suite ci-gate --mode replay

# 单条 case
python scripts/eval_runner.py --case wiki_concept_001 --mode replay
```

常用参数：

- `--exit-zero`：即使有 `fail` 也进程退出 0（仅生成报告）。
- `--strict-warnings` 或 `EVAL_STRICT_WARNINGS=1`：出现 `warning` 状态 case 时退出 **4**（加严门禁，默认关闭）。

退出码约定：

- `0`：成功（且无 fail；若启用 strict-warnings，还需无 warning）。
- `1`：存在 `fail` case。
- `2`：runner 导入等致命错误。
- `3`：指定了 `--suite` 但没有匹配任何 case。
- `4`：`--strict-warnings` 下存在 `warning` case。

## 5. 评分与阈值：fail vs warning

`tests/evals/scorers/core.py` 中逻辑概要：

- **硬失败（`fail`）**：`hard_fail_reasons` — 如必填字段缺失、`traceability_score` / `structure_completeness` 低于阈值、超时、`min_sources` 不满足、报告 meta 约束、情感阶段解析率/覆盖率不足等。
- **软告警（`warning`）**：`soft_warn_reasons` — 如 `relevance_score` 低于阈值、情感与已有列 agreement 落在「可疑」区间等。默认 **不** 导致进程失败（CI 仍以 fail 为准）。

若希望 warning 也阻断合并，使用 `--strict-warnings`。

与 **预算降级** 相关的环境变量见 `workflow/budget.py`（如 `SONA_BUDGET_*`），与评测独立，但可一起在回归中观察。

## 6. 如何读结果、复盘失败 case

1. 看 `summary.json`：`fail_cases`、`warning_cases`、`results[]` 里每条 `status` 与 `fail_reasons`。
2. 看 `ci_report.json`：`blockers` 聚合了所有 `fail` 的 case 与原因（空 suite 匹配时也会有说明）。
3. 深入单 case：打开 `metrics.json` 与 `artifacts/output.json`，对照 `expectations` 与 `metrics` 中的分数。

修复步骤建议：先区分是 **fixture 过时**、**阈值过紧**，还是 **schema/业务回归**；调整时同步更新 fixture 与 case，避免「只改一边」。

## 7. 回归看板（可选）

在多次运行 `eval_runner` 之后：

```bash
python scripts/eval_dashboard.py
```

可生成 `eval_results/history.json` 与 `eval_results/dashboard.md`，用于对比最近 run 的 pass rate、时延与 fallback（详见 Day8 清单与 `workflow/regression_dashboard.py`）。

## 8. 相关规范

- 契约与里程碑：[`docs/specs/harness_workflow_wiki_spec_v1.md`](../specs/harness_workflow_wiki_spec_v1.md)
- V1 验收与 backlog：[`docs/specs/harness_v1_acceptance.md`](../specs/harness_v1_acceptance.md)
