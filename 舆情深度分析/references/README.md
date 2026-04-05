# 参考资料目录说明

该目录用于给舆情分析流程提供“可引用的本地资料”，会被以下模块自动检索：

- `utils/methodology_loader.py`
- `tools/舆情智库.py` 中的 `search_reference_insights` / `append_expert_judgement`

## 建议目录结构

```text
舆情深度分析/references/
├── README.md
├── expert_notes/
│   ├── 20260326_张雪峰事件.md
│   └── ...
├── event_articles/
│   ├── 张雪峰_媒体评论汇编.md
│   └── ...
└── methods/
    └── 你自己的方法论补充.md
```

## 文件内容建议

1. `expert_notes/*.md`
- 适合写“你的专家研判”
- 推荐格式：
  - 事件判断
  - 风险链条
  - 治理建议
  - 证据出处（链接/媒体名/时间）

2. `event_articles/*.md`
- 适合放事件评论、深度文章摘录、你手工整理的观点卡片
- 每段尽量短，方便检索命中

3. `methods/*.md`
- 补充你常用的分析框架、指标口径、预警阈值

## 自动写入专家研判

你可以在 Agent 内调用：

- `append_expert_judgement(topic, judgement, tags, source)`

它会把内容写入 `expert_notes/`，后续报告会自动检索并引用。
