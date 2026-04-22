# `tools/` 说明

本目录为 **LangChain Tool** 形态的原子能力（`@tool`），供 ReAct Agent 与 `workflow/event_analysis_pipeline` 调用。

## 内部模块（以下划线开头，不导出为 Agent 工具）

| 文件 | 用途 |
|------|------|
| `_csv_io.py` | CSV 多编码读取、流式采样与总行数统计 |
| `_contracts.py` | JSON 序列化与标准错误 dict 辅助 |
| `_observe.py` | `tool_span`：基于标准库 `logging` 的耗时记录（logger：`sona.tools`） |

对外仍通过 `tools/__init__.py` 暴露业务工具；内部模块仅供 `tools` 包内引用。
