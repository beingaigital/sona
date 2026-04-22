# Harness + Workflow + Wiki：V1 验收标准（冻结）与下一迭代 Backlog

本文 **冻结** V1 阶段「做到什么算交付」的最低标准，并列出后续迭代 backlog，便于排期与拒绝范围蔓延。详细契约仍以 [`harness_workflow_wiki_spec_v1.md`](harness_workflow_wiki_spec_v1.md) 为准。

## 1. V1 冻结范围（In Scope，已具备或可验证）

以下条目在 V1 里程碑中视为 **已完成或可验收**（以仓库当前实现与 CI 为准）：

1. **评测入口**：`scripts/eval_runner.py` 可筛选 `--target` / `--stage` / `--case` / `--suite` / `--mode`。
2. **标准产物**：每次运行生成 `eval_results/<run_id>/summary.json`、分 case 的 `metrics.json`、`artifacts/output.json`、`trace.jsonl`；并写入 `ci_report.json`。
3. **回放模式**：`fixtures.mode=replay` + 录制 JSON，可在无外部依赖下稳定执行；支持 `EVAL_DETERMINISTIC=1` 稳定时间/随机相关表现。
4. **评分器 MVP**：`traceability_score`、`relevance_score`、`structure_completeness`、时延与结构类断言；区分 **fail** 与 **warning**（见 scorers）。
5. **用例与 fixture**：`tests/evals/cases` 与 `tests/fixtures` 目录规范；首批多 target（wiki / tool / workflow）case 可跑通。
6. **CI 最小门禁**：`.github/workflows/ci.yml` 执行 `pytest` + `ci-gate` 套件（replay + deterministic）；失败时上传 `ci_report.json`。
7. **多套件标签**：case 支持 `suite` + `suites`，用于 `ci-gate` 与原有 smoke 套件并存。
8. **回归看板（初版）**：可聚合历史 summary 并生成静态 `dashboard.md`（见 `scripts/eval_dashboard.py`）。
9. **预算治理（局部）**：`workflow/budget.py` 等与情感等阶段的可观测/降级钩子已落地（全链路预算为后续项）。
10. **文档**：[`harness_eval_playbook.md`](../guides/harness_eval_playbook.md) 描述写 case、回放、读评分与退出码。

## 2. V1 明确不做（Out of Scope）

- 大规模模型策略或算法重写。
- 全量历史数据迁移、复杂在线实验平台。
- 重视觉回归（以 HTML/CLI **结构** 门禁为主）。
- **双门槛 CI**（质量 + 成本/性价比联合门禁）仅作为方向，未在 V1 强制。

## 3. V1 验收检查清单（发布前自检）

- [ ] `pytest -q` 通过。
- [ ] `EVAL_MODE=replay EVAL_DETERMINISTIC=1 python scripts/eval_runner.py --suite ci-gate --mode replay` 退出码 0。
- [ ] 新增/修改 case 后已同步更新 fixture 与 `expectations`，并本地跑过对应 `--case`。
- [ ] 若启用 `--strict-warnings`，确认当前套件无意外 warning 或已接受为阻断。

## 4. 下一迭代 Backlog（建议优先级从高到低）

与改造计划中的 pending 项对齐，供 V2 排期（可随时调整顺序）：

1. **baseline-observability**：关键阶段耗时、调用次数、上下文体量基线与聚合。
2. **report-budget-guard**：报告输入 section 预算与优先级裁剪，产出 `prompt_budget_breakdown` 类产物。
3. **sentiment-cost-tuning**：情感分析默认批量/截断降本 + 可回退开关。
4. **extract-workflow-modules**：`event_analysis_workflow.py` 持续瘦身，编排与子模块边界清晰。
5. **validate-and-regressions**：小样本回放与输出一致性检查、分阶段上线策略。
6. **wiki-cli-mvp / wiki-retrieval-pipeline**：`/wiki` 命令与检索链路产品化（若与当前 CLI 部分重叠，则合并为「统一入口 + 文档」）。
7. **wiki-eval-and-learning-loop**：命中率、可读性、学习日志闭环。
8. **stage-level-scoring**：阶段级 domain 指标（抓取质量、报告深度等）与门禁。
9. **contract-and-schema-tests**：扩展关键工具 I/O schema 与契约测试覆盖。
10. **budget-and-governance**：全链路 token/时延/重试预算与降级顺序统一。
11. **ci-quality-gates**：**双门槛**（质量不降 + 成本可接受）与结构化失败报告增强。
12. **modular-architecture-boundaries**：可插拔阶段与 `WorkflowContext` 治理。

## 5. 变更记录

| 日期 | 说明 |
|------|------|
| 2026-04-19 | Day10：首次冻结 V1 验收与 backlog |
