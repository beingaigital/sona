# Eval harness (`tests/evals`)

- **`runner.py`**：加载 `cases/*.json`，执行 wiki / replay tool / replay workflow 路径，写出 `eval_results/<run_id>/` 下产物。
- **`cases/`**：评测定义（`target`、`suite`/`suites`、`fixtures`、`expectations`）。
- **`scorers/`**：`evaluate_case` — 计算指标并输出 `pass` / `warning` / `fail` 与 `fail_reasons`。

实操步骤、命令与退出码说明见 [`docs/guides/harness_eval_playbook.md`](../../docs/guides/harness_eval_playbook.md)。
