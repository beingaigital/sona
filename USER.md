# USER.md - 用户画像与偏好

## Active config（会被代码读取）

下面 fenced 代码块中的 **逐行 `key: value`** 会被 `cli/router.py` 的 `PolicyLoader` 扫描，用于构造 `route_policy`（影响「有历史数据时是否优先复用」等路由偏好）。

**请勿在生效行里写带 `|` 的枚举模板**（含 `|` 的行会被忽略，与解析器约定一致）。

当前 **PolicyLoader 会读取并生效** 的键：

| 键 | 作用 |
|----|------|
| `preference` | 与 `IntentRouter` 组合使用：`覆盖优先` / `深挖优先` 在有历史数据时倾向**完整重跑**；其它值在「有现成数据」时更常路由到**复用优先**路径。另：交互里「是否使用已有数据」的 **默认选项** 在 `preference == 覆盖优先` 时为「默认否（倾向重采）」，否则默认为「是」。 |
| `prefer_confirm` | 为 `true` 时，检测到 sandbox 有候选历史数据会 **交互确认** 是否复用；为 `false` 时按上面规则自动选。 |
| `auto_retry` / `max_retry` | 读入 `route_policy` 供展示与后续扩展；**低样本扩窗重采轮数**等仍以流水线内环境变量为准。 |
| `report_length` | 读入 `route_policy` 后经 `workflow_options` 传入完整事件分析流水线，在 `report_html` 与模板叙事模型提示词中生效（短篇 / 中篇 / 长篇）。 |

```yaml
name: ""
preference: 平衡
report_length: 中篇
auto_retry: true
prefer_confirm: true
max_retry: 2
```

`name` 目前未被 `PolicyLoader` 使用，仅作文档占位。

<!-- HARNESS:BEGIN -->
## Harness injection（给模型看的用户偏好摘要）

- preference: 平衡（与「覆盖优先 / 成本优先 / 深挖优先」等对比时，表示不极端偏向重采或省成本）
- report_length: 中篇
- prefer_confirm: true（有历史数据时询问是否复用）
- auto_retry: true；max_retry: 2（与路由策略一并注入；流水线级重试另受环境变量约束）
<!-- HARNESS:END -->

## 用户配置（模板说明，不直接参与解析）

- `preference` 可选语义示例：`覆盖优先` | `成本优先` | `保守阈值` | `深挖优先` | `平衡`
- `report_length`：短篇 | 中篇 | 长篇（具体字数由 prompt 与模型共同决定）

## 关注领域（文档化偏好，按需维护）

- industries / regions / topics：便于你或 Agent 在写检索方案时对齐范围。

## 预警与通知（若将来接入推送）

```yaml
risk_thresholds:
  low: 0.3
  medium: 0.6
  high: 0.8
  critical: 0.95

notification_channels:
  - cli
```

## 个性化（建议与 prompt 对齐）

- report_tone: 专业 | 通俗 | 学术
- include_action_suggestions: 是否在报告含行动建议段落
- default_time_range: 如「最近7天」——检索默认值可与 `extract_search_terms` 提示词协同

## 预留 / 未接入 PolicyLoader 的字段

以下字段**不会**被 `PolicyLoader` 解析，仅作个人备忘或与未来功能对齐：

- `cost_limit_per_task`（美元）：成本控制若要做，建议与 `utils/token_tracker` 或环境变量方案统一）
- `show_intermediate_results` / `verbose_logging`：视 CLI 启动参数与环境而定

## 交互偏好（行为说明）

- `prefer_confirm: false` 适合批跑或完全信任复用策略；默认 `true` 更安全。
- 用户若修改了本文件，**重启 CLI** 或重新加载会话策略后即可反映到下一次 `route_query`。
