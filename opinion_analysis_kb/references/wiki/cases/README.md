---
title: 案例库目录说明
wiki_section: cases
auto_generated: false
---

# 案例库目录说明

本目录用于保存事件分析流水线自动沉淀的标准案例页。每个完整事件报告生成后，`workflow.case_library_generator` 会读取对应 `sandbox/<task_id>/过程文件` 与 `结果文件`，生成包含 title、domain、actors、timeline、risk_patterns、response_tactics、evidence、report_path 的 `case_*.md`。

案例页会进入 `/case` 专用检索，也会被 `/wiki` 作为 Wiki 候选源召回，用于后续相似事件对照、处置经验复盘与 Graph RAG 证据补充。
