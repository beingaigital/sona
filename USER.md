# USER.md - 用户画像与偏好

## 用户配置
- name: 用户名称（首次使用后更新）
- preference: 覆盖优先 | 成本优先 | 保守阈值
- report_length: 短篇(1000字) | 中篇(3000字) | 长篇(5000字+)
- auto_retry: 是否允许自动多轮重试

## 关注领域
- industries: 关注的行业列表（如：科技、金融、汽车、医疗）
- regions: 关注的地区列表（如：北京、上海、全国）
- topics: 关注的专题列表

## 预警配置
```yaml
risk_thresholds:
  low: 0.3
  medium: 0.6
  high: 0.8
  critical: 0.95

notification_channels:
  - cli  # 命令行输出
  # - email  # 邮件（可选）
  # - webhook  # Webhook（可选）
```

## 个性化
- report_tone: 专业 | 通俗 | 学术
- include_action_suggestions: true  # 是否包含行动建议
- default_time_range: "最近24小时" | "最近7天" | "最近30天"

## 使用习惯
- prefer_confirm: true  # 是否偏好确认后执行
- max_retry: 3  # 最大自动重试次数
- cost_limit_per_task: 10  # 单次任务最大成本（美元）

## 交互偏好
- show_intermediate_results: true  # 是否显示中间结果
- verbose_logging: true  # 是否详细日志
- user_modification_history: []  # 用户修改历史记录