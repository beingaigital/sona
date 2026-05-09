# Fixtures 目录说明

本目录用于存放评测 replay 所需的最小回放样本。

## 目录规范

- `tests/fixtures/<case_id>/tools.json`
- 每个 case 对应一个独立目录
- `tools.json` 保存该 case 在 replay 模式下的最小可用输出样本

当前示例：

- `wiki_concept_001/tools.json`
- `wiki_case_002/tools.json`
- `wiki_compare_003/tools.json`
- `workflow_sentiment_004/tools.json`
- `workflow_report_005/tools.json`

## 编写原则

1. 先保证结构正确，再逐步提高内容质量。
2. Day2 阶段允许使用“最小可跑样本”。
3. replay fixture 的目标是保证回归稳定，不等价于 live 真实效果。
4. 不同 target 的 fixture 结构可以不同，但必须满足对应 case 的 `expectations.required_fields`。

## 最小字段建议

### wiki

通常至少包含：

- `answer`
- `sources`

示例：

```json
{
  "answer": "舆情反转是指公众对同一事件的主导判断在新证据出现后发生方向性改变。",
  "sources": [
    {
      "title": "舆情反转",
      "snippet": "舆情反转通常由新增事实或权威通报触发。"
    }
  ]
}
```

### workflow

Day2 可先使用 warning/pass-through 结构，通常至少包含：

- `status`
- `message`

示例：

```json
{
  "status": "warning",
  "message": "workflow replay stub loaded"
}
```

## 维护建议

- 新增 case 时，同时新增对应 fixture。
- 如果 case 的 `required_fields` 变更，需同步更新 fixture。
- Day3/Day4 可以继续增强 fixture 的真实性，并逐步收紧 `thresholds`。
