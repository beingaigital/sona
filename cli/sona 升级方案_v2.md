# Sona 升级方案 v2 - 自主决策舆情分析系统

**版本**: v2  
**更新日期**: 2026-03-21  
**目标**: 将Sona升级为具备自主决策能力的舆情分析Agent系统

---

## 一、现有架构分析

### 1.1 当前组件

| 组件 | 位置 | 功能 |
|------|------|------|
| Agent | `agent/reactagent.py` | ReAct Agent主逻辑 |
| Tools | `tools/*.py` | extract_search_terms, data_num, data_collect, analysis_timeline, analysis_sentiment, graph_rag_query, report_html, hottopics |
| Prompt | `prompt/*.txt` | 系统提示词、分析提示词 |
| Memory | `memory/STM/` | 短期记忆（会话级） |
| Utils | `utils/*.py` | session_manager, env_loader, prompt_loader |

### 1.2 现有工作流

1. **态势感知工作流 (/hot)** - hottopics.py (LangGraph)
2. **事件分析工作流** - 通过ReAct Agent调用tools

---

## 二、目标架构：四工作流 + 自主决策

### 2.1 四大工作流

```
┌─────────────────────────────────────────────────────────────────────────┐
│                           Sona Agent System                              │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  ┌──────────────────┐    ┌──────────────────┐    ┌──────────────────┐  │
│  │  舆情事件分析     │    │  专题监测预警     │    │  态势感知         │  │
│  │  工作流           │    │  工作流           │    │  工作流           │  │
│  │                  │    │                  │    │                  │  │
│  │  1.用户创建会话  │    │  1.创建专题      │    │  1./hot触发      │  │
│  │  2.Query理解    │    │  2.设置关键词    │    │  2.抓取热搜榜    │  │
│  │  3.搜索方案生成 │    │  3.自动抓取      │    │  3.趋势对比      │  │
│  │  4.用户确认     │    │  4.流体力学模型  │    │  4.热点判断      │  │
│  │  5.数据采集     │    │  5.风险预警      │    │  5.深度解析      │  │
│  │  6.清洗去重     │    │  6.推送建议      │    │  6.热点入库      │  │
│  │  7.舆情分析     │    │                  │    │                  │  │
│  │  8.Graph RAG   │    │                  │    │                  │  │
│  │  9.报告生成     │    │                  │    │                  │  │
│  └────────┬─────────┘    └────────┬─────────┘    └────────┬─────────┘  │
│           │                       │                       │           │
│           └───────────────────────┴───────────────────────┘           │
│                                   │                                    │
│                    ┌──────────────┴──────────────┐                    │
│                    │     自主决策核心 (Agent)      │                    │
│                    │                              │                    │
│                    │  Observe → Plan → Execute    │                    │
│                    │         → Verify → Route    │                    │
│                    │                              │                    │
│                    └──────────────┬──────────────┘                    │
│                                   │                                    │
│                    ┌──────────────┴──────────────┐                    │
│                    │     记忆系统 (Memory)        │                    │
│                    │                              │                    │
│                    │  STM + LTM + User Preference │                    │
│                    └─────────────────────────────┘                    │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## 三、OpenClaw风格架构设计

### 3.1 文档体系（新增）

```
sona-master/
├── SOUL.md           # 系统目标与约束
├── USER.md           # 用户画像与偏好
├── AGENT.md          # 决策策略与路由规则
├── MEMORY.md         # 记忆系统设计
├── TOOLS.md          # 工具映射总表
├── docs/
│   └── skills/       # 技能定义文档
│       ├── extract_search_plan_skill.md
│       ├── build_search_matrix_skill.md
│       ├── collect_dataset_skill.md
│       ├── analyze_timeline_skill.md
│       ├── analyze_sentiment_skill.md
│       ├── enrich_with_graph_rag_skill.md
│       ├── generate_interpretation_skill.md
│       ├── generate_report_skill.md
│       └── monitor_topic_skill.md
└── schemas/
    └── event_analysis_state.py  # LangGraph State定义
```

### 3.2 SOUL.md - 系统目标与约束

```markdown
# SOUL.md - Sona系统目标与约束

## 核心目标
- 完成舆情任务并产出可验证artifacts（报告/预警/图谱证据）
- 提供四种工作流：事件分析、专题监测、态势感知、深度研究

## 输出契约
每个工作流的最终输出必须包含：
- 事件分析：report.html路径 + interpretation.json + graph_rag_enrichment.json
- 专题监测：alert.json + metrics.json + risk_level
- 态势感知：trend_summary.json + hot_event数据库

## 验证门（必须通过的节点）
- search_plan验证：searchWords非空、timeRange合法
- data_collect验证：save_path存在、count≥最小阈值
- analysis_*验证：返回JSON可解析且字段齐全

## 失败策略
- 失败时默认进入Recovery（换词/扩时段/降阈值/提示用户）
- 关键节点必须人确认：搜索方案、阈值调整、报告确认

## 约束
- 不允许自行修改已确认的搜索方案
- 不允许跳过数据验证门
- 不允许在未授权情况下推送预警
```

### 3.3 USER.md - 用户画像

```markdown
# USER.md - 用户画像

## 用户配置
- name: 用户名称
- preference: 覆盖优先 | 成本优先 | 保守阈值
- report_length: 短篇 | 中篇 | 长篇
- auto_retry: 是否允许自动多轮重试

## 关注领域
- industries: [行业列表]
- regions: [地区列表]
- topics: [专题列表]

## 预警配置
- risk_thresholds: {low: 0.3, medium: 0.6, high: 0.8}
- notification_channels: [cli, email, webhook]

## 个性化
- report_tone: 专业 | 通俗 | 学术
- include_action_suggestions: true/false
```

### 3.4 AGENT.md - 决策策略

```markdown
# AGENT.md - Sona决策策略

## 决策循环
```
Observe → Plan → Execute(Skills) → Verify → Route
```

## 路由策略

| 场景 | 路由 |
|------|------|
| 用户首次创建会话 | human_confirm (等待确认搜索方案) |
| 搜索结果不理想 | auto_retry (换词/扩时) |
| 数据采集失败 | recovery (降阈值/换数据源) |
| 阈值触发预警 | auto_push (按配置推送) |
| 热点事件涌现 | deep_dive (调用事件分析工作流) |

## Skill调度规则
- 同一节点最多3个动作
- 每个skill重试次数≤2
- 成本控制：单次任务最大token限制

## 审计要求
- 每一步必须产出audit（输入/输出摘要、校验结果、耗时）
```

### 3.5 MEMORY.md - 记忆系统

```markdown
# MEMORY.md - Sona记忆系统

## STM (短期记忆)
- 位置: memory/STM/
- 存储: 当前会话对话、artifacts摘要、用户修改点
- 生命周期: 会话结束清除

## LTM (长期记忆)
- 存储: Neo4j图数据库 + 本地索引
- 实体类型:
  - EventCase: 事件分析结论、关键证据、时间范围
  - HotTopic: 热点分类、专题标签、演化轨迹
  - UserPreference: 用户偏好与反馈
  - MonitorTopic: 监测专题配置与状态

## LTM读取策略
- 工作流开始时读取：用户偏好 + 相似历史案例
- 监测预警时读取：历史阈值参照

## 保留策略
- 事件案例: 保留2年
- 热点话题: 保留1年
- 用户偏好: 实时更新
```

---

## 四、四大工作流详细设计

### 4.1 舆情事件分析工作流

```
用户创建会话
    ↓
[Query理解] → 提取event_goal, event_domain
    ↓
[搜索方案生成] → extract_search_terms → search_plan{introduction, words, timeRange}
    ↓ (人机交互)
[用户确认] → 可修改search_plan
    ↓
[数量分配] → data_num → search_matrix{total_count, threshold}
    ↓
[数据采集] → data_collect → save_path
    ↓
[清洗/去重/标准化] → 生成dataset_summary.json
    ↓
[舆情分析]
  ├── analysis_timeline → timeline.json
  └── analysis_sentiment → sentiment.json
    ↓
[初步解读] → LLM生成interpretation.json
    ↓
[Graph RAG增强] → graph_rag_query → enrichment.json
    ↓
[报告生成] → report_html → final_report.html
    ↓
[落库LTM] → 写入EventCase实体
```

### 4.2 舆情专题监测工作流

```
用户创建专题
    ↓
[配置监测] → keywords, time_interval, risk_thresholds
    ↓
[增量抓取] → 按last_fetch_time增量采集
    ↓
[指标计算] → 舆论流体力学模型
    ├── 提及量 (count)
    ├── 情感压力 (sentiment_pressure)
    ├── 来源多样性 (source_diversity)
    ├── 传播速度 (velocity)
    └── 风险评分 (risk_score)
    ↓
[阈值判定] → risk_level {low/medium/high/critical}
    ↓
[风险预警] → alert.json + 处理建议
    ↓
[推送] → 按配置渠道推送
    ↓
[反馈闭环] → 用户采纳/调整 → 更新阈值
```

### 4.3 态势感知工作流 (/hot)

```
/hot命令触发
    ↓
[抓取热搜] → hottopics.py (多平台)
    ↓
[清洗去重] → 标准化 + 去重
    ↓
[趋势判断] → 生成trend_summary.json
    ├── 上升榜单
    ├── 下降榜单
    └── 涌现事件
    ↓
[热点筛选] → 按增长速率+负面占比排序
    ↓
[重点事件深度解析] → 调用事件分析工作流
    ↓
[热点入库] → 写入HotTopic实体
    ↓
[报告] → hot_report.html
```

### 4.4 深度研究工作流（可选扩展）

```
用户发起深度研究请求
    ↓
[多维度规划] → 生成研究大纲
    ↓
[并行信息采集] → 多源数据抓取
    ↓
[交叉验证] → 多源对比
    ↓
[综合分析] → 生成深度分析报告
    ↓
[归档] → 研究案例库
```

---

## 五、LangGraph State设计

```python
# schemas/event_analysis_state.py

from typing import TypedDict, List, Dict, Optional, Literal
from datetime import datetime

class AuditStep(TypedDict):
    node_name: str
    started_at: str
    finished_at: str
    status: Literal["success", "failed", "skipped"]
    input_summary: str
    output_summary: str
    error: Optional[str]

class SearchPlan(TypedDict):
    event_introduction: str
    search_words: List[str]
    time_range: str  # "YYYY-MM-DD;YYYY-MM-DD"

class SearchMatrix(TypedDict):
    search_matrix: Dict[str, int]
    total_count: int
    threshold: int

class CollectedDataset(TypedDict):
    save_path: str
    meta: Dict[str, Any]
    row_count: int

class Interpretation(TypedDict):
    narrative_summary: str
    key_risks: List[str]
    key_events: List[str]
    event_type: Optional[str]
    domain: Optional[str]

class GraphRagEnrichment(TypedDict):
    similar_cases: List[Dict]
    theories: List[Dict]
    indicators: List[Dict]

class ReportOutput(TypedDict):
    html_path: str
    file_url: str

class EventAnalysisState(TypedDict):
    # 基础
    task_id: str
    created_at: str
    user_query: str
    
    # 记忆
    user_preferences: Dict[str, Any]
    recent_stm_notes: List[str]
    
    # 路由
    route_mode: Literal["auto", "semi_auto", "human_first"]
    needs_human_confirm: bool
    
    # 工作流节点
    search_plan: Optional[SearchPlan]
    search_plan_valid: bool
    search_matrix: Optional[SearchMatrix]
    collected_dataset: Optional[CollectedDataset]
    timeline_analysis: Optional[Dict]
    sentiment_analysis: Optional[Dict]
    interpretation: Optional[Interpretation]
    graph_rag_enrichment: Optional[GraphRagEnrichment]
    report_output: Optional[ReportOutput]
    
    # 审计
    audit: List[AuditStep]
    error: Optional[str]
```

---

## 六、实施路线图

### 阶段A：架构基础搭建（1-2周）
- [ ] 创建SOUL.md, USER.md, AGENT.md, MEMORY.md
- [ ] 建立skills/目录结构
- [ ] 完善LangGraph State定义

### 阶段B：工作流编排升级（2-3周）
- [ ] 事件分析工作流：人机交互节点
- [ ] 数据标准化artifact
- [ ] LTM写入接口（Neo4j）

### 阶段C：自主决策循环（2-3周）
- [ ] Plan-Execute-Verify-Route决策图
- [ ] 硬验证门 + Recovery分支
- [ ] 审计日志系统

### 阶段D：专题监测预警（2-3周）
- [ ] 增量抓取模块
- [ ] 舆论流体力学模型
- [ ] 阈值判定 + 推送

### 阶段E：态势感知升级（1-2周）
- [ ] 热点库沉淀
- [ ] 趋势判断增强
- [ ] 深度解析联动

---

## 七、文件清单

### 新增文件
```
sona-master/
├── SOUL.md
├── USER.md
├── AGENT.md
├── MEMORY.md
├── TOOLS.md
├── docs/
│   └── skills/
│       ├── extract_search_plan_skill.md
│       ├── build_search_matrix_skill.md
│       ├── collect_dataset_skill.md
│       ├── analyze_timeline_skill.md
│       ├── analyze_sentiment_skill.md
│       ├── enrich_with_graph_rag_skill.md
│       ├── generate_interpretation_skill.md
│       ├── generate_report_skill.md
│       └── monitor_topic_skill.md
├── schemas/
│   ├── __init__.py
│   ├── event_analysis_state.py
│   ├── monitor_state.py
│   └── hotspot_state.py
└── memory/
    └── LTM/  # 新增长期记忆目录
        ├── __init__.py
        └── neo4j_client.py
```

### 修改文件
```
sona-master/
├── agent/reactagent.py  # 改为决策循环架构
├── utils/session_manager.py  # 支持LTM
├── tools/hottopics.py  # 增强态势感知
└── cli/main.py  # 新增工作流入口
```

---

*文档版本: v2*  
*基于原方案优化，增加了OpenClaw风格架构设计*
