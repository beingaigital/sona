---
name: workflow瘦身与WikiCLI和Harness方案
overview: 以 Harness Engineering 为总纲（模型决定上限、Harness 决定稳定性下限）：在 Prompt/Context/Tool/RAG/Memory/Eval/Obs/UI 八块分别落地版本化、契约、预算、回放与门禁；配合 workflow 瘦身、`/wiki` CLI 与垂直舆情 case 套件，让流程更合理、抓取更稳、报告更深、UI 可验收、token 与成本可计量、迭代可对比回归。
todos:
  - id: baseline-observability
    content: 给关键阶段加耗时/调用/上下文体量统计，先建立基线
    status: pending
  - id: report-budget-guard
    content: 为报告输入加 section 预算与优先级裁剪策略（可配置）
    status: pending
  - id: sentiment-cost-tuning
    content: 调低情感分析默认批量和文本截断，并保留回退开关
    status: pending
  - id: extract-workflow-modules
    content: 按职责拆分 workflow 子模块，主文件仅保留编排骨架
    status: pending
  - id: validate-and-regressions
    content: 建立小样本回放与输出一致性检查，分阶段上线
    status: pending
  - id: wiki-cli-mvp
    content: 设计并实现 `/wiki` 命令最小闭环（query、答案、来源引用）
    status: pending
  - id: wiki-retrieval-pipeline
    content: 定义知识库检索与问答链路（召回、重排、回答、失败回退）
    status: pending
  - id: wiki-eval-and-learning-loop
    content: 建立 `/wiki` 评测与学习闭环（命中率、可读性、学习日志）
    status: pending
  - id: harness-eval-runner
    content: 建立可模块化运行的评估 runner，支持单功能独立评测
    status: completed
  - id: deterministic-fixtures
    content: 建立可复现实验 fixture 与回放机制，降低外部依赖噪声
    status: pending
  - id: stage-level-scoring
    content: 为 workflow 各阶段定义质量评分与门禁指标
    status: pending
  - id: regression-dashboard
    content: 增加评测历史对比与趋势看板（质量/时延/回退率）
    status: pending
  - id: modular-architecture-boundaries
    content: 可插拔阶段边界与 WorkflowContext，对应图中 Execution Orchestration
    status: pending
  - id: contract-and-schema-tests
    content: 关键工具与产物 schema 契约测试，对应图中 Tool 与 Constraint/Verification
    status: pending
  - id: budget-and-governance
    content: 全链路 token/时延预算与降级顺序，对应 Context + Tool 调用经济性
    status: pending
  - id: ci-quality-gates
    content: CI 双门槛（质量不降、成本/性价比可接受）与结构化失败报告
    status: pending
isProject: false
---

# `event_analysis_workflow.py` 瘦身 + `/wiki` CLI + Harness 计划

## Harness Engineering：核心命题（与参考图一致）

- **定义**：Harness 是模型之外的工程体系（编排、状态、工具、上下文、评估观测、约束与韧性），把「能推理」变成「能**稳定、可测、可回归**地交付」。
- **演进**：Prompt（怎么说清）→ Context（给什么）→ **Harness（怎么可靠执行）**；本计划以第三阶段为纲，前两层持续版本化与预算化。
- **Industry takeaway**：天花板在模型，**稳定性下限在 Harness**；改造方向从「只调模型」转向「建系统让模型稳定工作」。

## 目标
- 把“几千行单文件编排”拆成可维护的模块边界。
- 降低报告与情感分析阶段的 token 开销与波动。
- 保持现有行为基本一致，优先低风险变更。
- **按八模块施工图推进**（见下文「施工图目录」），使垂直舆情能力、数据质量、报告深度、UI 可读、成本透明可分别度量与门禁。

## 现状判断（基于代码结构）
- 当前文件承担了过多职责：流程编排、协同分支、容错重试、知识增强、日志埋点、结果拼装。
- 长度主要不是“工具实现太多”，而是“编排逻辑 + 分支策略 + 兜底流程”都堆在同一层。
- token 热点更可能在下游工具的上下文拼装，尤其是报告生成阶段（一次性注入大量 JSON/摘要）。

## 施工图目录：Prompt / Context / Tool / RAG / Memory / Eval / Obs / UI

下面按图中模块命名组织：**职责 → 本仓库代码落点 → 改造动作 → 产物/trace → 关联 todos**。实施时以该目录导航，避免只堆 eval 或只改 prompt。

### 1) Prompt（提示词工程）

- **职责**：角色与任务边界、风格与输出结构、**版本可追溯**；与垂直舆情分析话术对齐（阶段目标写进系统/人类消息分层）。
- **代码落点**：
  - 业务侧长提示与流程约束：`[cli/event_analysis_workflow.py](cli/event_analysis_workflow.py)`（编排中拼装给各工具/模型的约束文案）
  - 报告生成：`[tools/report_html.py](tools/report_html.py)`、`[tools/report_html_template.py](tools/report_html_template.py)`
  - 抽取/解释/情感等工具内嵌 prompt：`[tools/extract_search_terms.py](tools/extract_search_terms.py)`、`[tools/generate_interpretation.py](tools/generate_interpretation.py)`、`[tools/analysis_sentiment.py](tools/analysis_sentiment.py)` 等
  - 可集中管理的静态模板目录：`[prompt/](prompt/)`（与现有 prompt 资产对齐，逐步迁入按场景分子目录）
- **改造动作**：prompt **版本号**（如 `report v3`）写入 trace；大文本改为文件引用 + hash；报告类提示词与「蒸馏输入」契约绑定（见 Context / Phase3）。
- **产物**：`prompt_version`、`prompt_ref` 字段；必要时 `prompts/<domain>/<name>_<version>.md`。
- **关联 todos**：`report-budget-guard`、`baseline-observability`、`stage-level-scoring`（报告深度类指标）。

### 2) Context（上下文工程）

- **职责**：**给什么、给多少、谁先谁后**；裁剪、去重、分层（事实层 vs 解读层）；避免把全流程 JSON 无差别灌进最终报告调用。
- **代码落点**：
  - 全流程上下文组装与可选增强开关：`[cli/event_analysis_workflow.py](cli/event_analysis_workflow.py)`（引用 `search_reference_insights`、`load_sentiment_knowledge`、`graph_rag_query`、`weibo_aisearch` 等）
  - 报告侧巨型上下文拼接：`[tools/report_html.py](tools/report_html.py)`
  - 模型参数与 usage：`[model/factory.py](model/factory.py)`
- **改造动作**：section 级 **预算与优先级**；输出 `prompt_budget_breakdown.json`（各块字符数/估算 token）；两段式「canonical 蒸馏 JSON → 报告」减少堆料（与 Phase3 一致）。
- **产物**：`prompt_budget_breakdown.json`、蒸馏中间 JSON schema。
- **关联 todos**：`report-budget-guard`、`budget-and-governance`、`extract-workflow-modules`。

### 3) Tool（工具工程）

- **职责**：**稳定接口、可失败分类、可重试、可录制回放**；控制调用的**时机与次数**（能力 vs 复杂度），直接决定抓取稳定性与经济性。
- **代码落点**：
  - 工具聚合与导出：`[tools/__init__.py](tools/__init__.py)`
  - 采集/分析类：`[tools/data_collect.py](tools/data_collect.py)`、`[tools/dataset_summary.py](tools/dataset_summary.py)`、`[tools/analysis_timeline.py](tools/analysis_timeline.py)`、`[tools/analysis_sentiment.py](tools/analysis_sentiment.py)` 等
  - 工作流内统一调用：`[cli/event_analysis_workflow.py](cli/event_analysis_workflow.py)` 中 `_invoke_tool_*`、超时与重试逻辑
- **改造动作**：工具输出 **JSON schema 契约** + pytest；采集产物附带 **质量元数据**（行数、去重率、时间窗覆盖）；关键路径实现 **Tool Gateway**（计时、错误码、重试决策、trace 事件）。
- **产物**：契约测试；tool 级 trace 事件；fixture 录制格式（与 `deterministic-fixtures` 一致）。
- **关联 todos**：`contract-and-schema-tests`、`deterministic-fixtures`、`sentiment-cost-tuning`、`stage-level-scoring`（抓取质量指标）。

### 4) RAG（检索增强）

- **职责**：图谱/知识库/外部参考的召回与 **Top-K、去重、可解释引用**；与舆情垂直领域的实体/时间线对齐。
- **代码落点**：
  - Graph RAG：`[tools/graph_rag_query.py](tools/graph_rag_query.py)`；编排侧采纳逻辑在 `event_analysis_workflow`
  - 参考观点/链接等：`[tools/yqzk.py](tools/yqzk.py)`（含 `search_reference_insights`、`build_event_reference_links`、`load_sentiment_knowledge` 等）、`[tools/weibo_aisearch.py](tools/weibo_aisearch.py)`
  - 未来 `/wiki` 检索面：知识库目录 `舆情深度分析/references/wiki/`（`concepts/`、`entities/`、`sources/`）
- **改造动作**：检索结果结构统一（`title/path/snippet/score`）；**hit@k、引用可追溯** 进 harness；与 Context 预算联动（检索条数上限）。
- **关联 todos**：`wiki-retrieval-pipeline`、`wiki-cli-mvp`、`stage-level-scoring`。

### 5) Memory（记忆与状态）

- **职责**：当前 run 的 **状态机**（阶段、中间产物路径、失败原因）；跨任务的 **LTM/经验**（搜索方案复用、相似 query）；避免「长篇对话失忆」与重复采数。
- **代码落点**：
  - 会话：`[utils/session_manager.py](utils/session_manager.py)`、`[cli/router.py](cli/router.py)`（意图与 sandbox 数据检测）
  - 经验 JSONL：`memory/LTM/search_plan_experience.jsonl`（与 workflow 中 `EXPERIENCE_PATH` 一致）
  - Agent 记忆：`MEMORY.md` 策略由 `[cli/router.py](cli/router.py)` 中 `PolicyLoader` 读取
  - 工作流内协作与状态：`[cli/event_analysis_workflow.py](cli/event_analysis_workflow.py)`
- **改造动作**：抽离 **`WorkflowContext` dataclass**（阶段输出、artifact 指针、预算字段）；经验复用 **单独 case**（质量/成本是否真提升）；LTM 写入规则版本化。
- **关联 todos**：`extract-workflow-modules`、`modular-architecture-boundaries`、`validate-and-regressions`。

### 6) Eval（评估与回归）

- **职责**：**单测阶段/单工具/单命令**；golden/replay；suite 级 pass rate；与 CI 门禁挂钩；垂直舆情 **套件**（突发/反转/辟谣等）。
- **代码落点**：
  - Runner：`[tests/evals/runner.py](tests/evals/runner.py)`、`[scripts/eval_runner.py](scripts/eval_runner.py)`
  - 用例与 fixture：`tests/evals/cases/`、`tests/fixtures/`、`eval_results/`
  - Spec：`[docs/specs/harness_workflow_wiki_v1.md](docs/specs/harness_workflow_wiki_v1.md)`
- **改造动作**：补全 `suite=`、`workflow_sentiment/report` case；scorer 目录化；**双门槛**（质量 + 经济性）；复用图中 Planner-Generator-Evaluator 思想时可做单阶段「自检再提交」。
- **关联 todos**：`harness-eval-runner`、`stage-level-scoring`、`deterministic-fixtures`、`ci-quality-gates`。

### 7) Obs（可观测性：日志、Tracing、计量）

- **职责**：**阶段账单**（耗时、token、重试、降级）；请求级 trace；与 debug 日志区分——要能 **聚合看趋势**。
- **代码落点**：
  - Agent 事件流：`[agent/reactagent.py](agent/reactagent.py)`（`astream_events` 等）
  - 工作流 NDJSON：`[cli/event_analysis_workflow.py](cli/event_analysis_workflow.py)`（`_append_ndjson_log`、`LOG_PATH`）
  - 模型 usage：`[model/factory.py](model/factory.py)`
- **改造动作**：统一 **Obs 事件 schema**（`stage`、`tool`、`usage`、`budget_action`）；输出 `cost_breakdown.json`；敏感路径可开关采样。
- **关联 todos**：`baseline-observability`、`budget-and-governance`、`regression-dashboard`。

### 8) UI（呈现与交付）

- **职责**：交互 CLI **清晰**、HTML 报告 **结构可读、版式稳定**；用 **可解析 + DOM/章节契约** 做自动化验收（先做结构门禁，再上视觉回归可选）。
- **代码落点**：
  - 交互与命令：`[cli/main.py](cli/main.py)`、`[cli/display.py](cli/display.py)`、`[cli/interactive.py](cli/interactive.py)`；后续 `/wiki` 挂载点同文件
  - HTML 报告：`[tools/report_html.py](tools/report_html.py)`、模板与资源 `report_html_template` 与同目录静态资源（若有）
- **改造动作**：HTML **最小 DOM/章节契约**（目录、锚点、关键块）；`layout_sanity_checks` 纳入 scorer；CLI 输出统一「阶段结论卡片」可选。
- **关联 todos**：`wiki-cli-mvp`、`contract-and-schema-tests`（HTML）、`stage-level-scoring`。

## 分阶段实施（与原 Phase 对齐 harness 模块）

### Phase 0：先量化，不改行为（1 天）
- **侧重模块**：Obs（基线）+ Context/Tool（体量和耗时体量）。
- 在工作流主流程添加统一统计：每阶段耗时、调用次数、是否重试、输入上下文长度。
- 对报告与情感分析调用增加“prompt 组成明细”指标（各 section 字符数/条目数）。
- 产出一份基线：Top3 最耗时与 Top3 最耗 token 环节。

### Phase 1：低风险降本（1-2 天）
- **侧重模块**：Context + Prompt + Tool（节流）。
- 报告生成前做上下文预算：设置硬上限（按 section 优先级裁剪）。
- 情感分析默认截断长度与批大小下调，并暴露 env 开关以回退。
- 对可选增强（参考资料/额外检索）增加条件触发，避免每次都叠加上下文。

### Phase 2：文件瘦身（行为保持为主，2-4 天）
- **侧重模块**：Orchestration（执行编排）+ Memory（状态抽离）+ Obs（telemetry 集中）。
- 从 `cli/event_analysis_workflow.py` 提取模块（先搬运、后优化）：
  - `workflow/runner.py`：主流程与阶段调度
  - `workflow/collab.py`：人机协同分支
  - `workflow/collect_resilience.py`：采集重试与兜底策略
  - `workflow/graph_enrichment.py`：图谱增强与采纳逻辑
  - `workflow/reuse.py`：历史复用与经验匹配
  - `workflow/telemetry.py`：统一日志/埋点入口
- 主文件仅保留：入口参数解析 + 调度调用 + 高层错误处理。

### Phase 3：中风险优化（可选）
- **侧重模块**：Context（两段式）+ Prompt（版本绑定蒸馏物）+ Tool Gateway（预算）。
- 报告生成改为“两段式”：先蒸馏标准输入 JSON，再交给生成器。
- 为所有 LLM 调用加统一预算网关（超限裁剪 + 降级策略）。

## 验收标准
- 主流程文件行数下降到当前的 30%-40%（只保留 orchestration skeleton）。
- 单次运行 token 总量明显下降（目标先拿到 20%-40%）。
- 相同输入下，核心输出字段完整性不下降；失败率与重试率不升高。

## 回滚与安全策略
- 每个 phase 独立提交，保证可单独回退。
- 所有“预算裁剪”默认可通过环境变量关闭。
- 先在小样本事件集回放，再全量启用。

## `/wiki` CLI 可行性判断
- 这个想法非常契合 Karpathy LLM Wiki 思路：把“知识库能力”产品化成一个高频命令入口。
- 对你当前项目价值很直接：既能压测和观察 wiki 检索问答质量，也能作为“舆情知识学习助手”日常使用。
- 与现有 `sona` CLI 体系一致，能复用现有模型调用、日志、配置与缓存机制。

## `/wiki` 命令方案（新增）

### 目标
- 新增命令：`sona /wiki "<问题>"`（或等价子命令形式）。
- 输出结构：`简明回答 + 关键依据 + 来源条目`，保证可追溯。
- 支持两类模式：`Q&A`（问题回答）和 `Learn`（主题学习导读）。

### 最小可用 MVP（第一阶段）
- 命令接口：
  - `sona /wiki "什么是舆情反转？"`
  - 可选参数：`--topk`、`--max-context`、`--style concise|teach`、`--show-sources`
- 执行链路（先简单可用）：
  1) 问题规范化（提取关键词/实体）
  2) 在 wiki（`concepts/entities/sources`）做召回
  3) 选取 TopK 片段构造上下文
  4) 生成回答并附来源
- 失败回退：
  - 召回不足时明确提示“证据不足”，返回可继续追问建议，而不是硬编答案。

### 进阶能力（第二阶段）
- 检索增强：
  - 先 BM25/关键词召回，再做轻量重排（可用 embedding 或规则打分）。
  - 引入“去重与多样性”策略，避免返回同质来源。
- 学习模式：
  - `--style teach` 输出“定义 -> 典型案例 -> 实务提醒 -> 延伸阅读”。
  - 支持“连续追问”复用上轮来源，形成学习链路。

### 评测与学习闭环（第三阶段）
- 效果指标：
  - 检索命中率（top-k 是否含标准答案来源）
  - 回答可追溯率（答案句子是否能映射到引用片段）
  - 用户主观质量（有用性/准确性/可读性）
- 运行日志：
  - 记录 query、召回来源、最终引用、是否触发回退、响应时间。
- 数据闭环：
  - 将高质量问答沉淀为“常见舆情知识卡片”，反哺专家库。

## `/wiki` 与 workflow 的协同收益
- 共享同一套“预算控制 + 日志观测”基础设施，避免重复建设。
- `event_analysis_workflow.py` 可以把 `/wiki` 作为可选知识增强节点（按条件触发，不强耦合）。
- 先把 `/wiki` 做成独立命令，有利于单点迭代和快速验证，再决定是否深度并入主流程。

## Harness 工程化评估（新增）

## 现状短板（你判断是对的）
- 当前以“端到端跑通”为主，缺少“单模块可独立评估”的标准入口。
- 评估成本高：很多场景需要全流程重跑，无法只测某一步（如情感分析、报告生成、检索问答）。
- 有日志但缺评分：已有 debug/阶段日志，但没有统一质量分数与回归门禁。
- 可复现性不足：外部检索、LLM 非确定性、时间/随机因素导致结果漂移，不利于稳态对比。

## Harness 目标能力
- 单功能评估：可以只跑某个 stage 或某个工具，不必全量重跑。
- 可复现回放：同一输入可重放并稳定对比（fixture/replay）。
- 可量化打分：每个阶段都有质量指标与通过门槛。
- 可回归对比：按版本跟踪质量、时延、失败/回退率趋势。

## 分阶段实施（Harness）

### H0：最小评估骨架（1-2 天）
- 新增评估入口 runner（建议放在 `tests/evals` + `scripts/eval_runner.py`）：
  - 支持 `--target workflow|tool|wiki`
  - 支持 `--stage` 只跑单阶段
  - 支持 `--case` 指定样本集
- 定义统一结果协议：`metrics.json`、`artifacts/`、`trace.jsonl`。

### H1：可复现 fixture（2-3 天）
- 增加 `EVAL_MODE`（或同等配置）：
  - 固定随机种子、固定时间源、可切换离线工具回放。
- 建立 fixture 数据结构：
  - `input`（query/参数）
  - `recorded_tool_outputs`（可重放）
  - `expected`（关键指标阈值/结构断言）
- 对高波动环节先做“结构断言 + 区间阈值”，不做逐字比对。

### H2：阶段级评分与门禁（2 天）
- 为 workflow 关键阶段定义评分器（scorer）：
  - 召回质量（命中率/覆盖）
  - 分析结构完整率（关键字段齐全）
  - 报告可用性（章节覆盖/HTML 可解析）
  - 回退/重试率（越低越好）
- 在 runner 输出 `pass/fail` 与失败原因，支持回归门禁。

### H3：回归看板（1-2 天）
- 聚合多次运行结果，输出静态 `dashboard`：
  - pass rate、P50/P95 时延、fallback rate、阶段错误分布
  - 模型版本/参数切换前后对比
- 先本地静态看板，后续再接 CI。

## 与 `/wiki` 的联动评估
- `/wiki` 天然适合 harness：问答任务短、反馈快、可做每日回归。
- 首批指标建议：
  - 检索命中率（TopK 是否覆盖标准来源）
  - 回答可追溯率（答案句子能否映射到引用）
  - 学习可用性评分（简洁、可读、可操作）
- 这能反向提升主 workflow 的知识增强质量。

## 你这个 agent 的关键提升点（按优先级）
- 先补“评估入口”而不是先大改算法：没有 harness 的优化很难证明收益。
- 把“端到端成功”拆成“阶段可证实成功”：每阶段都能独立跑、独立判分。
- 把“日志”升级为“可比较指标”：从可读日志走向可决策指标。
- 用 replay 减少全流程重跑：特别适合你现在迭代频繁、功能复杂的状态。

## 你期望的“最终效果”如何通过 Harness 达成（目标 → 杠杆 → 落地）

下面把你的目标翻译成 harness 工程改造可直接推动的机制：**先量化指标，再把它变成门禁**，用 replay/回归把提升固化下来。

## 1) 流程调用更合理、更契合舆情分析垂直领域
- **Harness 杠杆**：
  - 建立“阶段级合理性指标”与失败原因（不是只看是否跑通）。
  - 以 case 套件覆盖典型舆情场景（突发、反转、争议、谣言澄清、公共安全等），避免优化只对单一事件有效。
- **落地到计划**：
  - 在 `stage-level-scoring` 中增加：`stage_path_validity`（是否走对路径）、`domain_coverage_score`（关键维度覆盖）。
  - 在 `tests/evals/cases/` 增加垂直领域套件 `suite=public_opinion_basic`（至少 10 个种子事件）。

## 2) 数据抓取更稳定、更科学
- **Harness 杠杆**：
  - 为抓取阶段定义可验证的“数据质量契约”：去重率、缺失率、时间窗口覆盖、采样偏差提示、失败重试的有效性。
  - 把外部依赖波动隔离：live 观察真实稳定性，replay 固化回归对比。
- **落地到计划**：
  - 在 `contract-and-schema-tests` 增加抓取产物 schema 与质量断言（CSV/JSON 必需字段、行数下限、去重字段）。
  - 在 `deterministic-fixtures` 增加“抓取录制/回放”协议（同一抓取输入可重放工具输出）。
  - 在 `regression-dashboard` 增加：`collect_success_rate`、`unique_ratio`、`missing_rate`、`retry_effectiveness`。

## 3) 生成报告更有深度（而非堆料）
- **Harness 杠杆**：
  - 把“深度”拆成可测维度：结构覆盖、证据密度、论证链完整性、反事实/争议点处理、结论可操作性。
  - 对报告做“结构化蒸馏输入”并控制上下文膨胀（预算治理 + 去重）。
- **落地到计划**：
  - 在 `stage-level-scoring` 增加：`report_section_coverage`、`evidence_density`、`argument_chain_score`、`actionability_score`（先规则化，后再引入 LLM judge）。
  - 在 `budget-and-governance` 明确报告输入 section 的裁剪优先级，并记录“裁剪原因/裁剪量”进 trace。
  - 将 `report-budget-guard` 升级为“可解释裁剪”（输出 `prompt_budget_breakdown.json`）。

## 4) 可视化界面更美观清晰（CLI/报告 HTML）
- **Harness 杠杆**：
  - UI 也能 harness：用“视觉/可读性”验收替代纯主观讨论。
  - 对 HTML 报告做自动化校验：可解析、关键模块存在、CSS/资源路径有效、无明显溢出（先做结构与可解析门禁）。
- **落地到计划**：
  - 在 `stage-level-scoring` 增加 `html_parse_success`、`layout_sanity_checks`（标题层级、目录、图表占位、表格列数）。
  - 在 `contract-and-schema-tests` 增加“报告 HTML 最小 DOM 契约”（章节锚点/目录/关键卡片块存在）。
  - 在 `regression-dashboard` 增加 “UI 通过率” 与“结构缺失热力图”。

## 5) 流程化与 token 消耗更透明
- **Harness 杠杆**：
  - 把 token/耗时做成“按阶段账单”，并支持 replay 快速复现某次成本异常。
  - 把所有 LLM/tool 调用统一走治理网关，天然可计量、可对比、可降级。
- **落地到计划**：
  - `baseline-observability` 增加：`prompt_tokens/completion_tokens`、stage 级 `cost_estimate`、`budget_trigger_count`。
  - 在 `harness-eval-runner` 输出 `cost_breakdown.json`（每阶段 token/耗时/重试次数）。
  - 在 `regression-dashboard` 增加成本趋势（P50/P95 token 与 cost）。

## 6) 整个任务完成更经济（更少重跑、更低成本、更少无效调用）
- **Harness 杠杆**：
  - 通过门禁把“无效提升”挡住：必须在固定 suite 上同时提升质量/成本比。
  - 通过 replay 把调参/重构成本降到最小：大部分变更先在 replay 套件回归，再选择性 live 复检。
  - 通过预算治理与降级，把极端 case 的成本上限锁住。
- **落地到计划**：
  - 在 `ci-quality-gates` 加“双门槛”：质量不降 + 成本不升（或允许小幅上升但必须显著增益）。
  - 引入“经济性 KPI”：`cost_per_quality_point`、`tokens_per_report_section`、`rerun_rate`。

## 目标导向的验收方式（建议）
- 每次改造都要回答两件事（由 harness 自动生成）：
  - **质量**：在固定 suite 上哪些指标变好了？哪些变差了？
  - **经济性**：成本账单变化是多少？超预算与降级触发了几次？重试是否有效？

## 最小 Harness 目录草图（可直接落地）
- `tests/evals/runner.py`：统一评估入口（workflow/tool/wiki 三类 target）。
- `tests/evals/cases/`：评测样本集（按主题或功能分组）。
- `tests/evals/scorers/`：评分器（结构完整性、命中率、可追溯率、时延）。
- `tests/evals/replay.py`：离线回放执行器（读取录制的工具输出）。
- `tests/evals/schema/`：评测输入输出 schema（case/result/trace）。
- `tests/fixtures/`：固定输入与录制输出（可复现）。
- `scripts/eval_runner.py`：CLI 入口（便于本地和 CI 执行）。
- `eval_results/`：每次评测结果归档与历史对比数据。

## 最小 Case Schema（建议）
```yaml
id: wiki_concept_001
target: wiki            # wiki | tool | workflow
stage: null             # 可选：只评测某阶段，如 sentiment/report
input:
  query: "什么是舆情反转？"
  options:
    topk: 6
    style: teach
fixtures:
  mode: replay          # replay | live
  recorded_tools: "tests/fixtures/wiki_concept_001/tools.json"
expectations:
  required_fields: ["answer", "sources"]
  min_sources: 2
  max_latency_ms: 15000
  thresholds:
    traceability_score: 0.7
    relevance_score: 0.75
```

## 结果 Schema（最小）
```json
{
  "run_id": "2026-04-16T01:30:00Z",
  "case_id": "wiki_concept_001",
  "target": "wiki",
  "status": "pass",
  "metrics": {
    "latency_ms": 8421,
    "traceability_score": 0.81,
    "relevance_score": 0.78,
    "fallback_rate": 0.0
  },
  "artifacts": {
    "trace": "eval_results/<run_id>/wiki_concept_001.trace.jsonl",
    "output": "eval_results/<run_id>/wiki_concept_001.output.json"
  },
  "fail_reasons": []
}
```

## 执行命令草案（MVP）
- 全量样本：`python scripts/eval_runner.py --target wiki --suite basic`
- 单样本：`python scripts/eval_runner.py --case wiki_concept_001`
- 单阶段：`python scripts/eval_runner.py --target workflow --stage sentiment`
- 回放模式：`python scripts/eval_runner.py --case wiki_concept_001 --mode replay`

## 首批 5 个评测 case 建议
- `wiki_concept_001`：概念问答（定义与边界）。
- `wiki_case_002`：案例解析（事件 -> 机制 -> 启示）。
- `wiki_compare_003`：概念对比（如舆情反转 vs 舆情发酵）。
- `workflow_sentiment_004`：情感分析阶段独立评测。
- `workflow_report_005`：报告生成阶段结构完整性评测。

## 评分口径（防漂移版本）

## 1) traceability_score（可追溯率）
- **目的**：答案中的关键陈述是否能被引用来源支撑。
- **输入**：`answer`、`sources`、（可选）`source_snippets`。
- **计算**：
  - 先抽取答案关键陈述句 `N` 条（按句号/分号切分，过滤过短句）。
  - 对每条陈述，若能在任一 `source_snippet` 找到语义匹配证据记为 `1`，否则 `0`。
  - `traceability_score = 支撑陈述数 / N`。
- **默认阈值**：`>= 0.70`。
- **失败条件**：
  - `sources` 为空；
  - 或 `traceability_score < 阈值`。

## 2) relevance_score（相关性）
- **目的**：回答是否围绕用户问题核心意图，不跑题。
- **输入**：`query`、`answer`。
- **计算**（MVP 建议规则化，避免额外模型开销）：
  - 从 `query` 提取关键词/实体集合 `Q`。
  - 从 `answer` 提取关键词集合 `A`。
  - `keyword_overlap = |Q ∩ A| / max(1, |Q|)`。
  - 加上结构惩罚：若答案超过最大长度且无分段，扣 `0.05 ~ 0.10`。
  - `relevance_score = clamp(keyword_overlap - penalty, 0, 1)`。
- **默认阈值**：`>= 0.75`。
- **失败条件**：`relevance_score < 阈值`。

## 3) structure_completeness（结构完整率）
- **目的**：输出结构字段是否齐全可用（尤其 workflow/report）。
- **输入**：输出 JSON/HTML 元数据。
- **计算**：
  - 设必需字段集合 `R`（按 target 定义）。
  - `structure_completeness = 命中字段数 / |R|`。
- **默认阈值**：`wiki >= 0.90`，`workflow/report = 1.0`（关键字段必须全有）。
- **失败条件**：低于阈值或关键字段缺失（如 `answer`、`sources`、`result_file_path`）。

## 4) latency_ms（时延）
- **目的**：控制可用性与成本。
- **输入**：runner 统一打点开始/结束时间。
- **计算**：`latency_ms = end_ts - start_ts`。
- **默认阈值**：
  - `/wiki` 单问答：`<= 15s`（replay）/`<= 30s`（live）
  - 单阶段工具评测：按阶段单独配置。
- **失败条件**：超阈值。

## 5) fallback_rate（回退率）
- **目的**：衡量系统稳态质量，避免“表面可用但经常兜底”。
- **输入**：trace 中 fallback/retry 事件计数。
- **计算**：
  - `fallback_rate = fallback_case_count / total_case_count`（suite 级）
  - 单 case 可记 `fallback_count`。
- **默认阈值**：`suite <= 0.20`，关键路径 case 期望 `0`。
- **失败条件**：suite 级超过阈值或关键 case 触发禁用型 fallback。

## 6) pass/fail 聚合规则（统一）
- 单 case 通过条件：所有“硬性指标”达标（结构完整率、traceability、latency）。
- 若仅软性指标不达标（如 relevance 略低），记为 `warning`，不立即 fail（MVP 阶段）。
- suite 通过条件：
  - `pass_rate >= 0.85`
  - 且无 blocker 级失败（关键字段缺失/不可追溯/严重超时）。

## 7) 阶段专用指标补充（workflow）
- `collect_success_rate`：采集成功条数 / 目标条数。
- `sentiment_parse_success_rate`：情感结果可解析条数 / 总条数。
- `report_section_coverage`：报告必需章节覆盖率。
- `error_recovery_efficiency`：错误后恢复成功次数 / 错误触发次数。

## Harness 全景能力检查清单（用于后续自检）
- 模块化编排：阶段是否可独立替换与单测？
- 契约稳定性：关键 I/O 是否有 schema 与兼容策略？
- 复现性：是否可固定种子/时间并回放外部调用？
- 评估性：是否支持 stage 级 runner + scorer？
- 治理性：是否有预算上限与明确降级规则？
- 交付性：是否接入 CI 门禁并产出结构化报告？

## 两周执行清单（每日可打勾）

### Day 1：评估入口骨架
- [x] `eval runner` 可执行（支持 `--target --stage --case`）。
- [x] 运行后生成统一目录：`metrics`、`artifacts`、`trace`。
- [x] 至少 1 个 demo case 可跑通并产出结果文件。

### Day 2：样本与 fixture
- [ ] 建立 `tests/evals/cases` 与 `tests/fixtures` 目录规范。
- [ ] 首批 5 个 case 文件建好（可先占位）。
- [ ] 每个 case 至少包含 `input` 与 `expectations`。

### Day 3：评分器 MVP
- [ ] 实现 `traceability_score` 计算与阈值判断。
- [ ] 实现 `relevance_score`、`structure_completeness`、`latency_ms`。
- [ ] 输出统一 `pass/fail + fail_reasons`。

### Day 4：复现模式
- [ ] 增加 `EVAL_MODE`（固定随机/时间源）。
- [ ] 支持 replay 模式读取录制工具输出。
- [ ] 至少 2 个 case 在 replay 模式下稳定复现。

### Day 5：workflow 首次瘦身
- [ ] 从主 workflow 抽离 `runner` 与 `telemetry` 最小模块。
- [ ] 主文件减少到“入口 + 调度 + 总控”的骨架化形态。
- [ ] 现有端到端流程不回归（至少 1 次完整跑通）。

### Day 6：契约与 schema
- [ ] 关键工具定义输入/输出 schema（至少 2-3 个热点工具）。
- [ ] 增加契约测试（字段必填/类型/错误结构）。
- [ ] 字段变更触发明确失败而非静默兼容。

### Day 7：预算治理
- [ ] 定义阶段预算（token/时延/重试上限）。
- [ ] 超预算触发降级（裁剪上下文/跳过可选增强）。
- [ ] 输出预算摘要（触发次数、原因、动作）。

### Day 8：回归看板
- [ ] 聚合多次 eval 结果到历史文件。
- [ ] 输出静态 dashboard（pass rate、p95、fallback rate）。
- [ ] 支持“本次 vs 上次”差异查看。

### Day 9：CI 门禁
- [ ] 把最小 eval 套件接入 CI（smoke + 关键 case）。
- [ ] PR/提交显示结构化失败原因。
- [ ] 关键 blocker 指标不达标时自动 fail。

### Day 10：稳定化与文档
- [ ] 复盘失败 case，调整阈值与降级策略。
- [ ] 补齐“如何写 case/如何回放/如何看评分”文档。
- [ ] 冻结 V1 验收标准，形成下一迭代 backlog。

## 建议新增 Spec（1页版）

## 为什么现在就写
- 当前 plan 已覆盖“做什么”，但还缺“做到什么算正确”的统一契约。
- 你的改造范围跨 workflow、`/wiki`、harness、CI，多模块协同时 spec 能显著减少返工。
- spec 可以作为 Day1-Day10 的验收基准，不替代 plan，而是约束实现细节。

## 推荐落位
- 主文档：`docs/specs/harness_workflow_wiki_v1.md`
- 关联索引：在计划中保留链接到该 spec。

## Spec 模板（可直接填充）

### 1. 背景与目标
- 背景：当前系统“可运行但弱可评估/弱复现/弱门禁”。
- 目标：在不破坏现有可用性的前提下，完成 workflow 瘦身、`/wiki` CLI 化、harness 工程化。

### 2. 范围与非范围
- In Scope：
  - `event_analysis_workflow` 模块化拆分（保留行为一致性）。
  - `/wiki` 命令 MVP（问答 + 来源引用 + 基础学习模式）。
  - harness 最小闭环（runner、case、scorer、replay、dashboard、CI gate）。
- Out of Scope（V1 不做）：
  - 大规模算法重写；
  - 全量历史数据迁移；
  - 复杂在线实验平台。

### 3. 架构与接口契约
- workflow 阶段接口：`run(context) -> context`。
- 统一状态：`WorkflowContext` 关键字段定义。
- 工具 I/O schema：关键工具输入输出、错误结构、版本兼容策略。

### 4. 运行模式
- `live`：真实外部依赖，关注真实质量。
- `replay`：录制输出回放，关注可复现与回归比较。
- 模式差异：允许波动项与不允许波动项清单。

### 5. 评估协议
- case schema、result schema（引用 plan 中定义）。
- 指标口径：traceability、relevance、structure、latency、fallback。
- 通过规则：单 case 与 suite 的 pass/fail 聚合。

### 6. 预算治理与降级
- 预算维度：token、时延、重试次数。
- 超预算动作顺序：裁剪上下文 -> 跳过可选增强 -> 模型降级（如启用）。
- 治理日志：记录触发原因、动作、影响结果。

### 7. CI 门禁与交付
- 最小门禁集：smoke + 关键 case。
- 阻断条件：blocker 指标不达标即 fail。
- 报告格式：结构化失败原因 + 建议修复方向。

### 8. 里程碑与验收
- 直接映射 Day1-Day10 checklist。
- 每个里程碑对应可验证产物（文件、报告、指标）。