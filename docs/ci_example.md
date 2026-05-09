# CI Gate Example (MVP)

下面给一个最小 CI 流程示例：先跑 eval，再执行门禁。

## 本地命令（等价于 CI 步骤）

```bash
# 1) 运行最小评测（示例）
python scripts/eval_runner.py --suite workflow --mode replay --run-id ci-smoke

# 2) 门禁（默认阈值：pass_rate>=0.85, fallback_rate<=0.20）
python scripts/eval_gate.py --run-id ci-smoke
```

## GitHub Actions 示例（可直接改成你们仓库配置）

```yaml
name: eval-gate

on:
  pull_request:
  push:
    branches: [ main ]

jobs:
  eval-gate:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - name: Install deps
        run: pip install -r requirements.txt
      - name: Run replay eval
        run: python scripts/eval_runner.py --suite workflow --mode replay --run-id ci-smoke
      - name: Gate
        run: python scripts/eval_gate.py --run-id ci-smoke
```

## 可选参数

- `--latest`：对 `eval_results/` 最新一次运行做门禁  
- `--suite <prefix>`：仅对指定前缀 case 评估（按 `case_id` 前缀匹配）  
- `--min-pass-rate`、`--max-fallback-rate`：覆盖默认阈值
