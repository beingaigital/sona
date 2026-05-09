# Sona 垂类升级执行计划

更新日期：2026-04-28  
当前基线：`feature-analysis` 分支，稳定快照提交 `cc6953c`  
目标周期：6 周完成可用升级，8 周完成稳定化与验收  
协作规模：16 位同学，按模块小组并行推进

---

## 1. 总判断

Sona 目前已经从“能调用工具的 ReAct Agent”进入“可稳定跑完舆情事件分析流水线”的阶段。现有架构里，`cli/router.py` 负责意图路由，`workflow/event_analysis_pipeline.py` 承担固定事件分析链路，`workflow/wiki_rag.py` 与 `tools/graph_rag_query.py` 已经为本地 Wiki 和 Neo4j Graph RAG 留出了入口，`SOUL.md`、`USER.md`、`AGENT.md` 也开始形成 harness 文档体系。

下一阶段不应该急着堆功能，而要把 Sona 做成“舆情分析垂类专家系统”：

- 让报告质量可评测、可复盘、可迭代。
- 让舆情知识库从资料堆变成领域方法论和案例网络。
- 让事件分析从一次性报告升级为持续监测、专题分析和案例沉淀。
- 让 CLI 保持稳定，同时为未来接入 Opinion System 的对话式入口预留 API/工作流边界。

外部舆情产品的共性能力主要集中在全网/多源监测、关键词规则、情感识别、负面预警、趋势可视化、报告导出、传播路径和知识图谱等模块。参考调研来源：

- [乐思舆情监测系统](https://knowlesys.cn/index.html)
- [乐思品牌声誉管理解决方案](https://www.knowlesys.cn/usecase-brand.html)
- [FineReport：舆情报表实时监测能力](https://www.finereport.com/blog/article/6888a9e5d2527e0eb7c4b9b1)
- [FineReport：舆情风险预警报表能力](https://www.finereport.com/blog/article/6888aa03d2527e0eb7c4bb47)

这些产品给 Sona 的启发是：Sona 不必复制传统大屏系统，而应发挥 Agent 优势，做“能解释、能积累、能改进”的分析引擎。

---

## 2. 北极星目标

6 周后，Sona 应达到以下状态：

1. 事件分析报告：同一事件多次运行时，结构稳定、口径一致、图表可读、结论能追溯到数据或知识库。
2. 垂类知识库：健康舆情、交通舆情、大熊猫舆情形成独立领域包，能在报告生成时被自动召回。
3. Graph RAG：Neo4j 已接入主工作流，至少能召回相似案例、主体关系、治理经验和理论框架。
4. 案例库 Wiki：每次分析能沉淀一份标准化案例文件，并能被后续检索、归类和复用。
5. 专题监测：支持以“高铁舆情”“控烟舆情”等关键词创建专题，周期性采集、入库、分析和预警。
6. Harness 与评估：有 golden cases、自动评分、人工反馈入口和回归 dashboard。
7. 接入准备：CLI 继续可用，同时提供清晰的工作流 API，方便未来挂到 Opinion System 的对话入口。

---

## 3. 当前系统体检

### 3.1 已经具备的基础

- `SOUL.md`：定义了系统目标、约束、质量门和输出契约。
- `USER.md`：已经支持 preference、report_length、prefer_confirm 等偏好配置。
- `AGENT.md`：已经描述了 Observe -> Plan -> Act -> Verify -> Route 的策略。
- `workflow/event_analysis_pipeline.py`：完整事件分析主链路已经形成。
- `tools/analysis_timeline.py`、`tools/analysis_sentiment.py`、`tools/keyword_stats.py`、`tools/region_stats.py`、`tools/volume_stats.py`：基础统计与分析工具齐全。
- `tools/report_html.py` 与报告模板：可生成 HTML 报告。
- `workflow/wiki_rag.py`、`workflow/wiki_cli.py`：本地知识库问答已可用。
- `tools/graph_rag_query.py`：Neo4j Graph RAG 工具已存在，但仍偏“可调工具”，尚未深度接入工作流。
- `opinion_analysis_kb/`：已有 raw、expert_notes、wiki output、entities 等知识库资产。
- `tests/fixtures/` 与 `scripts/eval_runner.py`：已有评估与回归测试雏形。

### 3.2 主要短板

- Harness 还偏“原则文档”，缺少可执行评估标准、评分器和报告质量闭环。
- Wiki/KB 仍以文件沉淀为主，缺少领域 taxonomy、案例 schema、关系抽取和图谱编译流程。
- Graph RAG 工具存在，但主报告链路里的调用时机、证据融合和失败降级还不够稳定。
- 报告中的图表、情感、时间线、关键事件节点、处置建议需要进一步“舆情专家化”。
- Hot 热点分析还偏信息聚合，缺少热点分层、风险判定、事件簇归并和后续深挖入口。
- 缺少 Postgres 持久层支撑专题监测、历史趋势、案例库和定时任务。
- GUI 是否建设还没到“单独做产品”的时机，应先设计 API 和对话式接入边界。

---

## 4. 产品路线

### Phase 0：基线冻结与质量仪表盘（第 0-1 周）

目标：把当前稳定版本固定下来，让之后每个改造都能被评估。

交付物：

- `docs/SONA_VERTICAL_UPGRADE_PLAN_20260428.md`
- `eval/golden_cases/`：至少 15 个事件样例，覆盖健康、交通、大熊猫、消费、文旅、教育、政务。
- `scripts/eval_runner.py` 增强：支持报告结构评分、事实可追溯评分、建议可执行性评分。
- `workflow/regression_dashboard.py` 增强：输出简洁 HTML dashboard。
- 基线报告：记录当前版本在 15 个样例上的表现。

### Phase 1：Harness 与报告质量闭环（第 1-2 周）

目标：让 Sona 的“好报告”有标准、有评分、有反馈回路。

重点：

- 重写 `SOUL.md`、`AGENT.md`、`USER.md` 的 harness injection，使其更像可执行规范。
- 建立报告 rubric：
  - 事实准确性
  - 数据完整性
  - 图表一致性
  - 时间线相关性
  - 情感分析可信度
  - 理论研判质量
  - 舆情应对建议可执行性
  - 证据引用完整性
- 增加人工反馈表单/JSON schema，把“老师修改意见”变成训练和评估资产。

### Phase 2：知识库与 Graph RAG 垂类化（第 2-4 周）

目标：把 opinion_analysis_kb 做成“舆情分析方法论 + 领域知识 + 案例图谱”。

重点：

- 整理并编译更多舆情分析内容，如“舆论课笔记”、清博/人民网/识微/政务公开报告、典型时评。
- 建立统一知识类型：
  - `Methodology`：理论、模型、分析框架。
  - `Case`：舆情事件案例。
  - `DomainPlaybook`：健康、交通、大熊猫等领域应对指南。
  - `Actor`：主体、机构、媒体、KOL。
  - `RiskPattern`：风险模式，如迟回应、口径冲突、次生舆情、标签化攻击。
  - `ResponseTactic`：回应策略，如先事实后态度、分层回应、第三方背书。
- Neo4j 编译：
  - 从 wiki markdown 抽取实体和关系。
  - 建立 Case -> Actor -> RiskPattern -> ResponseTactic -> Theory 的边。
  - Graph RAG 在报告中输出“相似案例”和“可迁移经验”。

### Phase 3：报告生成升级（第 3-5 周）

目标：让最终 HTML 报告接近可交付专业舆情分析稿。

重点：

- 事件分析：
  - 时间线趋势图增加关键事件节点。
  - 情感趋势与声量趋势联动解释。
  - 渠道分布、地域分布、用户画像口径统一。
  - 词云优化为更清爽的主题词图。
  - 处置建议从“泛泛建议”升级为“分阶段行动清单”。
- Hot 热点分析：
  - 热点分级：一般关注、潜在风险、高风险、需深挖。
  - 热点聚类：相似事件合并，避免榜单式重复。
  - 输出热点卡片：事件摘要、热度、风险因子、建议动作、是否进入事件分析。
- 报告验证：
  - HTML 非空、图表渲染、JSON 字段完整、数值一致性检查。

### Phase 4：案例库与专题监测（第 4-6 周）

目标：让 Sona 从“单次事件分析”升级为“持续积累的舆情知识系统”。

重点：

- 案例库：
  - 每次综合 sandbox 输出后，自动生成 `case.md`。
  - 进入 `opinion_analysis_kb/references/wiki/cases/`。
  - 自动提取标签、领域、主体、风险模式、处置建议、复盘结论。
  - 可按领域、风险类型、主体、平台、时间检索。
- 专题监测：
  - 建立 Postgres 数据库。
  - 支持专题配置：关键词、排除词、平台、频率、预警阈值。
  - 周期性抓取数据入库。
  - 支持专题日报/周报/月报。
  - 支持“专题 -> 事件簇 -> 单事件深挖”的工作流。

### Phase 5：接入 Opinion System 与轻量 GUI 决策（第 6-8 周）

目标：不急着做重 GUI，先把 Sona 做成可被对话系统稳定调用的分析服务。

建议：

- 暂不建设完整 GUI 后台，避免分散精力。
- 优先提供：
  - `sona analyze-event`
  - `sona monitor-topic`
  - `sona hot`
  - `sona wiki`
  - `sona case`
  - `sona serve` 或 FastAPI 服务层
- Opinion System 通过对话框触发 Sona 工作流，并接收报告、案例和中间状态。
- 轻量 GUI 可以只做“报告查看 + 案例库检索 + 专题配置”，不做完整分析编辑器。

---

## 5. 20 人任务分配

### A 组：Harness 与评估（赵梓臣、邝星恺、孙明禅、王焰琼）

####  01：Harness 主设计

负责范围：

- 重构 `SOUL.md`、`AGENT.md`、`USER.md` 的 harness injection。
- 把“事实可追溯、证据不足、不可编造、低样本中止”等规则写成明确可执行条款。
- 输出 `docs/harness_spec.md`。

验收：

- 事件分析、热点、wiki、专题监测四类任务都有明确执行策略。
- prompt 文档不再只有原则，还能对应到 workflow 的验证节点。

#### 02：Golden Cases 与评测集

负责范围：

- 建立 `eval/golden_cases/`。
- 收集 15-30 个样例，包括健康、交通、大熊猫、消费、文旅、教育、政务。
- 每个样例包括 query、期望报告要点、错误红线、参考材料路径。

验收：

- 至少 15 个样例能被 `scripts/eval_runner.py` 批量读取。
- 每个样例都能对应到一个明确评分 rubric。

#### 03：自动评分器

负责范围：

- 扩展 `scripts/eval_runner.py`。
- 增加结构评分、字段评分、证据评分、数值一致性评分。
- 生成 `eval/results/*.json`。

验收：

- 任意报告可得到 0-100 的质量分。
- 评分输出能指出具体失败项，而不是只有总分。

#### 04：人工反馈闭环

负责范围：

- 设计 `feedback_schema.json`。
- 支持把老师/用户对报告的修改意见沉淀为 JSONL。
- 在下一次报告生成时可作为“风格和错误提醒”召回。

验收：

- 至少 5 条人工反馈能进入评估和生成链路。
- 支持按领域、报告章节、错误类型检索反馈。

### B 组：知识库与 Graph RAG（李思颐、刘可、央金旺姆、奚丹婷）

#### 05：知识库 Schema 与编译器

负责范围：

- 设计 `opinion_analysis_kb/schema/`。
- 定义 Methodology、Case、DomainPlaybook、Actor、RiskPattern、ResponseTactic。
- 编写 wiki markdown -> structured JSON 的编译脚本。

验收：

- 至少 100 篇现有 wiki/output 文档可编译为结构化 JSON。
- 编译失败有错误报告，不静默跳过。

#### 06：Neo4j 接入主流程

负责范围：

- 完善 `tools/graph_rag_query.py`。
- 新增 Neo4j connection health check。
- 在 `workflow/event_analysis_pipeline.py` 中稳定调用 Graph RAG。

neo4j的用户名和密码：
url: neo4j+s://3cb72f36.databases.neo4j.io   
user: 3cb72f36   
password: tjji4vlwgaz8qyG5sENRVMF8fqvHNGKtnwlQPocMjbE   
database: 3cb72f36 

验收：

- 无 Neo4j 时自动降级，不影响报告生成。
- 有 Neo4j 时报告能出现相似案例、关系网络和处置经验。

#### 07：健康舆情领域包

负责范围：

- 建立 `opinion_analysis_kb/domains/health/`。
- 收集医疗纠纷、公共卫生、控烟、医患关系、医院管理相关材料。
- 总结健康舆情的风险模式和应对策略。
（跟三虎要资料）

验收：

- 至少 30 篇领域文档、10 个案例、1 份 health playbook。
- 健康事件报告能自动召回该领域知识。

####  08：交通舆情领域包

负责范围：

- 建立 `opinion_analysis_kb/domains/transport/`。
- 覆盖铁路、高铁、航空、网约车、公交、道路安全。
- 特别沉淀 12306、高铁服务、票务、公平感、公共安全相关案例。
（跟三虎要资料）

验收：

- 至少 30 篇领域文档、10 个案例、1 份 transport playbook。
- 交通事件报告能给出更具体的处置建议和监管口径。

#### 09：大熊猫舆情领域包

负责范围：

- 建立 `opinion_analysis_kb/domains/panda/`。
- 覆盖动物保护、文旅、园区管理、直播传播、粉丝社群、跨地域情绪。
- 总结大熊猫舆情的独特传播机制。
（跟三虎要资料）

验收：

- 至少 20 篇领域文档、8 个案例、1 份 panda playbook。
- 大熊猫相关事件能识别粉丝群体、保护伦理、文旅品牌风险。

### C 组：报告质量与可视化（阿力马斯、林俞锦、杨欣怡）

####  10：时间线与关键节点

负责范围：

- 改造 `tools/analysis_timeline.py`。
- 趋势线增加关键事件节点。
- 节点必须包含时间、事件、来源证据、影响判断。

验收：

- Top 5 时间线节点中无关节点比例低于 20%。
- HTML 报告趋势图能展示关键节点。

#### 11：情感分析与情绪机制

负责范围：

- 改造 `tools/analysis_sentiment.py`。
- 区分正/负/中性之外的情绪：愤怒、焦虑、质疑、同情、嘲讽、支持。
- 建立抽样校验机制。

验收：

- 情感分析输出包含情绪结构和典型表达。
- 报告能解释“为什么负面/质疑上升”。

#### 12：图表与词云美化

负责范围：

- 改造 `tools/report_html_template.py` 与相关前端模板。
- 优化词云、渠道图、地域图、趋势图。
- 统一配色、字体、图例和移动端可读性。

验收：

- 图表不重叠、不空白、数值口径清晰。
- 词云从装饰图升级为主题识别图。

#### 13：舆情应对建议生成器

负责范围：

- 重写报告中的“处置建议”逻辑。
- 输出按阶段、主体、动作、话术、风险、验证指标组织。
- 引入 DomainPlaybook 和相似案例经验。

验收：

- 建议不再是泛泛表述。
- 每份报告至少包含“24 小时内、3 天内、7 天内、复盘期”的行动清单。

#### 14：Hot 热点分析升级

负责范围：

- 改造 `tools/hottopics.py` 与 `cli/hot_ui.py`。
- 增加热点分级、事件聚类、风险因子、深挖入口。
- 将热点结果可选沉淀为 case candidate。

验收：

- `/hot` 输出不只是榜单，而是态势摘要。
- 高风险热点可一键进入事件分析。

### D 组：案例库与专题监测（王照翔、罗子淳、桂溢蔓）

#### 15：案例库生成

负责范围：

- 从 sandbox 输出自动生成 `case.md`。
- 设计 case front matter：title、domain、actors、timeline、risk_patterns、response_tactics、evidence、report_path。
- 写入 `opinion_analysis_kb/references/wiki/cases/`。

验收：

- 每次完整事件分析都能生成一份标准案例文件。
- 案例能被 wiki 检索命中。

#### 16：案例库 Wiki 检索与索引

负责范围：

- 扩展 `workflow/wiki_cli.py`。
- 支持案例专用检索：按领域、主体、风险类型、平台、时间筛选。
- 输出“相似案例对比”。

验收：

- 输入“找几个高铁服务争议案例”能返回结构化列表。
- 相似案例能被 Graph RAG 和报告生成复用。

#### 17：Postgres 专题监测数据层

负责范围：

- 设计 Postgres schema：
  - `monitor_topics`
  - `topic_keywords`
  - `collected_posts`
  - `topic_snapshots`
  - `alerts`
  - `case_links`
- 编写 migration 和数据访问层。

supabase的数据库地址（已添加密码）
postgresql://postgres.fhatjtbrgtynrfqtvpsn:[aX1cpLqUVmrBXX9s]@aws-1-us-west-2.pooler.supabase.com:5432/postgres

密码是 aX1cpLqUVmrBXX9s

验收：

- 可创建专题、写入采集数据、读取历史趋势。
- 本地无 Postgres 时给出清晰配置错误，不影响事件分析主链路。

#### 18：专题监测工作流

负责范围：

- 新增 `workflow/topic_monitoring_pipeline.py`。
- 支持创建专题、周期采集、快照分析、风险预警、专题报告。
- 与 Opinion System 设计中的数据库方案对齐。

验收：

- 可运行示例：“高铁舆情”专题连续监测。
- 支持日报/周报输出。

### E 组：架构接入与产品化（胡可婷、雍珍）

#### 19：CLI/API 

负责范围：

- 梳理 CLI 命令和工作流 API。
- 设计 `sona serve` 或 FastAPI 层。
- 输出 OpenAPI 风格接口草案。

验收：

- 可以通过 API 触发事件分析、专题分析、wiki 查询。
- API 返回 task_id、状态、中间文件、最终报告路径。

#### 20：轻量 GUI 原型与取舍

负责范围：


- 设计基础的GUI界面：包括新建任务、报告查看、案例检索、专题配置、任务状态。
- 不做复杂大屏，建议做成聊天机器人的形式。

验收：

- 输出 `docs/gui_decision.md`。
- 若做原型，仅做轻量 viewer/config，不影响主工作流。

---

## 6. 关键技术改造清单

### 6.1 Harness 文件

需要改：

- `SOUL.md`
- `USER.md`
- `AGENT.md`
- `MEMORY.md`
- `utils/harness_memory.py`
- `utils/policy_docs.py`
- `workflow/runtime_harness.py`

目标：

- 从“文档提示”升级为“运行时策略”。
- 让工作流能读取领域、报告长度、风险偏好、是否复用数据、评估模式。

### 6.2 评估系统

需要改/新增：

- `scripts/eval_runner.py`
- `scripts/eval_dashboard.py`
- `workflow/regression_dashboard.py`
- `tests/fixtures/`
- `eval/golden_cases/`

评分维度：

- `structure_score`
- `evidence_score`
- `data_consistency_score`
- `timeline_relevance_score`
- `sentiment_quality_score`
- `actionability_score`
- `domain_knowledge_score`
- `overall_score`

### 6.3 知识库编译

需要改/新增：

- `opinion_analysis_kb/references/wiki/WIKI_SCHEMA.md`
- `opinion_analysis_kb/schema/`
- `opinion_analysis_kb/domains/`
- `scripts/compile_kb.py`
- `scripts/sync_neo4j.py`

核心原则：

- 原始材料不直接等于知识库。
- 需要经过清洗、摘要、实体关系抽取、质量标注和索引。

### 6.4 Graph RAG

需要改：

- `tools/graph_rag_query.py`
- `workflow/event_analysis_pipeline.py`
- `tools/generate_interpretation.py`
- `tools/report_html.py`

输出应包含：

- 相似案例
- 关联主体
- 风险模式
- 可借鉴处置动作
- 理论框架
- 证据来源

### 6.5 报告生成

需要改：

- `tools/report_html_template.py`
- `tools/report_html.py`
- `prompt/report_html_template_fill.txt`
- `prompt/report_html_enhanced.txt`
- `tools/analysis_timeline.py`
- `tools/analysis_sentiment.py`

重点：

- 图表不只是展示，要服务解释。
- 建议不只是“加强沟通”，要给出可执行动作。
- 口径不一致时自动阻断或降级。

### 6.6 专题监测

需要新增：

- `workflow/topic_monitoring_pipeline.py`
- `tools/topic_store.py`
- `tools/topic_collect.py`
- `tools/topic_snapshot.py`
- `tools/topic_alert.py`
- `scripts/run_topic_monitor.py`

数据库建议：

- Postgres 存结构化长期数据。
- 本地文件继续存报告和中间产物。
- Neo4j 存关系和案例知识。

---

## 7. 验收标准

### 第 2 周验收

- 当前 22 个测试全部通过。
- 至少 15 个 golden cases 可运行。
- 每份报告有自动评分。
- Harness 文档与运行时策略一致。

### 第 4 周验收

- 健康、交通、大熊猫领域包初版完成。
- Neo4j Graph RAG 能进入事件报告。
- 报告处置建议明显更具体。
- Hot 分析支持热点分级和深挖入口。

### 第 6 周验收

- 案例库自动生成可用。
- “高铁舆情”专题监测 demo 可跑。
- 专题数据能入 Postgres。
- 事件报告、专题报告、案例文件三者能互相链接。

### 第 8 周验收

- 30 个 golden cases 总分平均提升 20% 以上。
- 核心链路失败能清晰降级。
- Opinion System 接入 API 草案完成。
- GUI 决策文档完成。

---

## 8. 风险与取舍

### 风险 1：知识库越堆越乱

对策：

- 严格区分 raw、candidate、approved、compiled。
- 每条知识必须有 source、domain、confidence、last_updated。

### 风险 2：Graph RAG 接入后报告变得不稳定

对策：

- Graph RAG 只做增强，不作为硬依赖。
- 设置证据阈值，低置信时只输出“可参考”，不进入核心结论。

### 风险 3：专题监测牵涉数据库和调度，容易拖慢主线

对策：

- 先做最小 Postgres schema 和手动触发脚本。
- 定时任务、权限、多用户后置。

### 风险 4：GUI 分散研发资源

对策：

- 先做 API 和报告 viewer。
- 未来主要交互入口放到 Opinion System 对话框。

### 风险 5：报告质量难以客观评估

对策：

- 自动评分只负责结构和一致性。
- 专家反馈负责判断深度和建议质量。
- 两者合并为综合评分。

---

## 9. 推荐开工顺序

第一优先级：

1. 同学 01-04 启动 harness 与 eval。
2. 同学 05-06 启动 KB schema 和 Neo4j 接入。
3. 同学 10、13 启动时间线和处置建议改造。
4. 同学 17-18 启动专题监测最小数据层。

第二优先级：

1. 同学 07-09 完成三大领域包。
2. 同学 11-12 完成情感和图表升级。
3. 同学 15-16 完成案例库。

第三优先级：

1. 同学 14 升级 hot。
2. 同学 19 做 API 接入。
3. 同学 20 做 GUI 取舍和轻量原型。

---

## 10. 最终产品形态

Sona 最理想的下一阶段形态不是“一个有很多按钮的软件”，而是一个舆情分析工作台内核：

- 用户在 Opinion System 对话框里提出任务。
- Sona 自动判断是事件分析、热点态势、专题监测、案例查询还是知识问答。
- 运行过程中保留 CLI/debug 能力，方便开发和复盘。
- 产出报告、案例、图谱证据、专题快照。
- 每一次分析都反哺知识库和评估集。

这样，Sona 会越用越像一个懂舆情的研究团队，而不是一次性报告生成器。
