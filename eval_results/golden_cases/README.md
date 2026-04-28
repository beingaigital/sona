## Golden cases

本目录用于沉淀**可复现/可回归**的“高质量范本运行”产物，供后续：

- 回归测试：确保改动不会显著破坏报告质量与关键 guard 指标
- 报告模板参考：沉淀优秀结构/图表配置/叙事风格
- Harness 评分对照：以 `runtime_harness_scorecard.json` 为基线

约定（v1）：

- 每个案例一个子目录：`eval_results/golden_cases/<case_id>/`
- 必选文件：
  - `manifest.json`
  - `runtime_harness_scorecard.json`
  - `runtime_harness_trace.json`（若存在）
- 推荐文件：
  - `report.html`
  - `report_meta.json`
  - `process_*.json`（挑选轻量过程文件，避免提交大 CSV）

