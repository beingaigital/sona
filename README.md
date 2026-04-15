# Sona - 舆情分析智能 Agent

基于 LangChain 和 ReAct 模式的智能舆情分析 Agent，支持从用户自然语言查询到生成模板化 HTML 报告的端到端流程。

## 📋 目录

- [功能特性](#功能特性)
- [核心架构](#核心架构)
- [安装指南](#安装指南)
- [配置说明](#配置说明)
- [使用指南](#使用指南)
- [工具说明](#工具说明)
- [项目结构](#项目结构)
- [常见问题](#常见问题)
- [开发指南](#开发指南)

## ✨ 功能特性

### 核心能力

- **🤖 ReAct Agent**：采用 Reasoning + Acting 模式，智能决策和执行工具调用
- **🔍 智能搜索词提取**：从自然语言查询中提取检索关键词和配置
- **📊 多维度数据分析**：支持时间线分析、情感倾向分析、数据统计
- **📄 HTML 报告生成**：自动生成美观、交互式的舆情分析报告
- **💾 会话管理**：支持会话保存、恢复和消息历史管理
- **📈 Token 追踪**：实时追踪和统计 Token 使用情况
- **🎨 美观的 CLI 界面**：基于 Rich 的现代化命令行界面，支持流式输出

### 支持的模型提供商

- **OpenAI**：GPT-4o、GPT-4o-mini、GPT-4-turbo、o1-preview、o1-mini
- **Google Gemini**：Gemini Pro 系列
- **通义千问（Qwen）**：qwen-max、qwen-plus、qwen-turbo、qwen-coder
- **DeepSeek**：deepseek-chat、deepseek-reasoner
- **Kimi（Moonshot）**：moonshot-v1-8k、moonshot-v1-32k、moonshot-v1-128k

## 🏗️ 核心架构

### ReAct Agent 工作流程

```
用户查询
    ↓
搜索词提取 (extract_search_terms)
    ↓
数据量评估/配额分配 (data_num，可选但推荐)
    ↓
数据采集 (data_collect)
    ↓
数据集摘要 (dataset_summary，可选但推荐)
    ↓
统计分析 (keyword_stats / region_stats / author_stats / volume_stats，可选且推荐)
    ↓
核心分析 (analysis_timeline / analysis_sentiment)
    ↓
解释与研判 (generate_interpretation，可选但推荐)
    ↓
Graph RAG / 智库增强 (graph_rag_query / yqzk，可选)
    ↓
报告生成 (report_html)
    ↓
HTML 报告输出
```

（并行能力）热点态势感知：`/hot`（见 `tools/hottopics.py`，独立于上述舆情分析报告链路）

### 多模型架构

项目采用**多模型架构**，不同场景使用不同的模型：

- **main（主流程）**：ReAct Agent 的底座，负责推理和工具调用决策
- **tools（工具模型）**：用于搜索词提取、数据分析等工具调用
- **report（报告模型）**：专门用于生成 HTML 报告

这种架构设计可以：
- 优化成本：主流程使用高性能模型，工具调用使用性价比更高的模型
- 提升性能：不同场景使用最适合的模型
- 灵活配置：可以根据需求为不同场景配置不同的模型

## 📦 安装指南

### 1. 环境要求

- Python >= 3.10
- 虚拟环境（推荐使用 `venv`）

### 2. 安装依赖

#### 方式一：使用 requirements.txt

```bash
# 创建虚拟环境（推荐）
python -m venv venv

# 激活虚拟环境
# Windows:
venv\Scripts\activate
# Linux/Mac:
source venv/bin/activate

# 安装依赖
pip install -r requirements.txt
```

#### 方式二：使用 pyproject.toml

```bash
pip install -e .
```

#### 方式三：使用 uv（推荐）

项目根目录包含 `pyproject.toml` 与 `uv.lock`，推荐使用 [uv](https://github.com/astral-sh/uv) 管理依赖并启动 CLI（无需先手动创建/激活虚拟环境）：

```bash
cd sona-master
uv sync          # 首次或依赖变更后同步环境（可选）
uv run sona      # 启动交互式 CLI（推荐）
```

安装 uv：见 [官方文档](https://docs.astral.sh/uv/getting-started/installation/)。

### 3. 安装 Playwright 浏览器驱动

**重要**：使用 `data_collect` 工具需要安装 Playwright 浏览器驱动。

```bash
# 安装 Playwright 浏览器驱动（Chromium）
playwright install chromium

# 或者安装所有浏览器（可选）
playwright install
```

**注意**：
- 首次安装需要下载浏览器驱动，可能需要几分钟时间
- 如果网络较慢，可以使用国内镜像：
  ```bash
  # 设置环境变量使用国内镜像
  export PLAYWRIGHT_DOWNLOAD_HOST=https://npmmirror.com/mirrors/playwright
  playwright install chromium
  ```

### 4. 验证安装

```bash
# 运行工具列表脚本，查看已注册的工具
python scripts/list_tools.py

# 测试搜索词提取工具
python scripts/run_extract_search_terms.py

# 测试数据采集工具（需要先配置 NETINSIGHT_USER 和 NETINSIGHT_PASS）
python scripts/run_data_collect.py
```

## ⚙️ 配置说明

### 环境变量配置

创建 `.env` 文件（在项目根目录），配置必要的 API Key 和账号信息：

```env
# =============================================================================
# 模型 API Key（至少配置一个，根据 config/model.yaml 中的配置选择）
# 统一使用 APIKEY 命名（按 openai / gemini / qwen / deepseek 顺序）
# =============================================================================

# OpenAI
# 获取地址：https://platform.openai.com/api-keys
# 说明：是否在主流程使用取决于 config/model.yaml 中 main 的 provider 配置
OPENAI_APIKEY=sk-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx

# Google Gemini
# 获取地址：https://makersuite.google.com/app/apikey
GEMINI_APIKEY=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx

# 通义千问（Qwen）- 使用 DashScope API Key
# 获取地址：https://dashscope.console.aliyun.com/apiKey
QWEN_APIKEY=sk-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
# 或者使用旧的 DASHSCOPE_APIKEY（兼容）
DASHSCOPE_APIKEY=sk-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx

# DeepSeek
# 获取地址：https://platform.deepseek.com/api_keys
DEEPSEEK_APIKEY=sk-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx

# Kimi（Moonshot）
# 获取地址：https://platform.moonshot.cn/console/api-keys
KIMI_APIKEY=sk-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx

# =============================================================================
# 网页搜索 API Key（可选，用于 extract_search_terms 工具）
# =============================================================================

# BoCha AI Search（可选，用于 extract_search_terms 网页搜索）
# 获取地址：https://www.bocha.cn/
BOCHA_API_KEY=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx

# =============================================================================
# NetInsight 登录信息（可选，用于 data_collect 工具）
# =============================================================================

# NetInsight 账号（用于数据采集）
NETINSIGHT_USER=your_username
NETINSIGHT_PASS=your_password

# =============================================================================
# Playwright 浏览器配置（可选）
# =============================================================================

# 控制 Playwright 是否显示浏览器窗口
# true  = 无头模式（不显示浏览器，默认，适合生产环境）
# false = 显示浏览器窗口（便于调试和观察登录过程）
NETINSIGHT_HEADLESS=true
```

**配置说明**：

1. **模型 API Key**：
   - 至少需要配置一个模型 API Key，根据 `config/model.yaml` 中的配置选择
   - 默认配置使用 `DEEPSEEK_APIKEY`（main profile）和 `QWEN_APIKEY`（tools/report）
   - 可以根据需要配置多个，然后在 `model.yaml` 中选择使用

2. **网页搜索**：
   - `BOCHA_API_KEY` 用于 `extract_search_terms` 工具进行网页搜索
   - 如果不使用该工具，可以不配置

3. **NetInsight**：
   - `NETINSIGHT_USER` 和 `NETINSIGHT_PASS` 用于 `data_collect` 工具登录
   - 如果不使用数据采集功能，可以不配置
   - `NETINSIGHT_HEADLESS` 控制浏览器是否显示，调试时可设为 `false`

4. **安全提示**：
   - 请妥善保管 API Key，不要泄露给他人
   - `.env` 文件已添加到 `.gitignore`，不会被提交到版本控制
   - 如果 API Key 泄露，请立即在对应平台重新生成

### 模型配置

项目使用 `config/model.yaml` 配置文件来管理不同场景下的模型选择：

```yaml
# 主流程模型：作为 ReAct Agent 的底座
main:
  provider: deepseek
  model: deepseek-chat
  api_key_env: DEEPSEEK_APIKEY

# 工具模型：用于各种工具调用（搜索词提取、时间线分析、情感分析等）
tools:
  provider: qwen
  model: qwen-plus
  api_key_env: QWEN_APIKEY

# HTML报告生成模型：生成舆情分析HTML报告
report:
  provider: qwen
  model: qwen-plus
  api_key_env: QWEN_APIKEY
```

### 提示词配置

项目使用 `config/prompt.yaml` 配置文件来管理提示词模板：

```yaml
# Agent 底座 system prompt
system_prompt: system_prompt.txt

# 意图/搜索词提取：供 extract 模型使用
extract_search_terms_prompt: extract_search_terms.txt

# 事件时间线分析：供 analysis 模型使用
analysis_timeline_prompt: analysis_timeline.txt

# 情感倾向分析：供 analysis 模型使用
analysis_sentiment_prompt: analysis_sentiment.txt

# HTML报告生成：供 report 模型使用
# HTML报告生成：供 report 模型使用（增强版：集成舆情智库方法论）
report_html_prompt: report_html_enhanced.txt

# 固定 HTML 模板（文件名，位于 prompt/）
report_html_template: report_html_morandi_template.html

# 模板模式叙事填充（模型仅输出 JSON 占位符）
report_html_template_fill: report_html_template_fill.txt
```

提示词模板文件位于 `prompt/` 目录下，可以根据需要进行修改。

## 🚀 使用指南

### 命令行交互式使用

**推荐**在项目根目录使用 `uv run`，自动使用当前锁文件中的依赖，无需单独激活虚拟环境：

```bash
cd /path/to/sona-master
uv run sona              # 启动交互式 CLI（推荐）
# 或
uv run sona interactive

# 查看帮助
uv run sona --help
```

若已通过 `pip install -e .` 或 `uv sync` 安装，且已激活对应虚拟环境，也可直接使用：

```bash
sona
sona interactive
sona --help
```

#### 可用命令

在交互式界面中，可以使用以下命令：

- `/new` - 开启新的分析会话
- `/memory` - 查看并恢复之前的会话
- `/models` - 查看所有模型配置
- `/tools` - 查看所有可用工具
- `/hot` - 运行独立的热点抓取与态势感知流程（可选参数：配置路径）
- `/clear` - 清除 memory 和 sandbox
- `/exit` - 退出程序

**`/hot` 热点流程说明**：
- 从公网聚合接口拉取各平台热搜（需本机可访问外网），再在本地用 **OpenAI 兼容 API** 做归纳与报告。
- `.env` 中**至少配置一个**即可自动映射：`QWEN_APIKEY`（Qwen coding plan/OpenAI 兼容，默认）、`OPENAI_APIKEY`、`DEEPSEEK_APIKEY`、`KIMI_APIKEY`（Moonshot）；也可显式设置 `INSIGHT_ENGINE_API_KEY`、`QUERY_ENGINE_API_KEY`（及对应的 `*_BASE_URL`、`*_MODEL_NAME`）。
- 报告与缓存默认写在项目根目录：`output_langgraph/`、`data_langgraph/`、`data_langgraph_hourly/`。
- 可选：自建 `config/config.yaml`，在 `platforms` 中配置 `{id, name}` 列表覆盖默认平台；无文件或 `platforms` 为空时使用内置平台列表。

#### 使用示例

1. **启动程序**（推荐）：
   ```bash
   uv run sona
   ```

2. **创建新会话**：
   ```
   user: /new
   ```

3. **输入查询**：
   ```
   user: 分析最近一周小米汽车的舆情
   ```

4. **Agent 自动执行**：
   - 提取搜索词
   - 采集数据
   - 分析时间线和情感
   - 生成 HTML 报告

5. **查看结果**：
   - 工具执行结果会实时显示
   - HTML 报告路径会在工具结果中显示

### 作为 Python 模块使用

```python
from agent.reactagent import stream

# 流式运行 Agent
for chunk in stream("分析最近一周小米汽车的舆情", task_id="task-001"):
    # 处理每个状态更新
    if isinstance(chunk, dict):
        chunk_type = chunk.get("type")
        if chunk_type == "token":
            # Token 级别的流式输出
            print(chunk.get("content"), end="", flush=True)
        elif chunk_type == "tool_call":
            # 工具调用
            print(f"\n调用工具: {chunk.get('tool_name')}")
        elif chunk_type == "tool_result":
            # 工具结果
            print(f"\n工具结果: {chunk.get('result')}")
```

## 🛠️ 工具说明

项目工具分为三类：**采集与预处理**、**统计/分析**、**增强与报告**。Agent 会根据 query 与当前数据情况自动组合调用。

### 1. extract_search_terms（搜索词提取）

**功能**：从用户自然语言查询中提取用于舆情检索的搜索关键词和配置。

**输入**：
- `query`：用户自然语言查询

**输出**：
- `eventIntroduction`：事件基础介绍
- `searchWords`：搜索关键词列表
- `timeRange`：时间范围（开始时间、结束时间）

**使用时机**：Agent 首先调用此工具，从用户查询中提取检索配置。

### 2. data_collect（数据采集）

**功能**：根据搜索矩阵和时间范围循环抓取微博渠道的舆情数据。

**输入**：
- `searchMatrix`：搜索矩阵（包含多个搜索词及其对应的数量）
- `timeRange`：时间范围（开始时间、结束时间）

**输出**：
- `result_file_path`：采集结果 CSV 文件路径
- `total_count`：采集的数据总数

**使用时机**：在提取搜索词后，Agent 调用此工具采集舆情数据。

**依赖**：
- 需要配置 `NETINSIGHT_USER` 和 `NETINSIGHT_PASS`
- 需要安装 Playwright 浏览器驱动

### 3. data_num（数据数量查询）

**功能**：查询不同搜索词在微博渠道的数据数量，并智能分配数量。

**输入**：
- `searchWords`：搜索词列表
- `timeRange`：时间范围

**输出**：
- `search_matrix`：包含每个搜索词及其分配数量的搜索矩阵

**使用时机**：在采集数据前，Agent 可能调用此工具查询数据数量并分配搜索词权重。

### 4. dataset_summary（数据集摘要）

**功能**：为 `data_collect` 产出的 CSV 生成数据集摘要（行数、字段名、时间覆盖范围），并写入 `dataset_summary.json`（任务过程文件）。

**输入**：
- `save_path`：CSV 路径（来自 `data_collect` 返回的 `save_path` 字段）

**输出**：
- `result_file_path`：摘要 JSON 文件路径
- `dataset_summary`：摘要对象（row_count / fieldnames / time_coverage）

**使用时机**：采集完成后，建议先生成摘要，便于后续研判与报告使用“数据边界信息”。

### 5. keyword_stats（关键词热度统计）

**功能**：对 CSV 内容列做分词与停用词过滤，输出 TopN 关键词词频，生成 `keyword_stats.json`（用于报告可视化）。

### 6. region_stats（地域分布统计）

**功能**：统计 `IP属地` 省份 Top10（过滤未知与非省级地域），生成 `region_stats.json`（用于报告可视化）。

### 7. author_stats（发布者分布统计）

**功能**：统计 `作者/发布者` Top10（过滤未知），生成 `author_stats.json`（用于报告可视化）。

### 8. volume_stats（声量趋势统计）

**功能**：按日聚合 `发布时间` 得到每日声量序列，生成 `volume_stats.json`（用于报告折线图）。

### 9. analysis_timeline（时间线分析）

**功能**：从舆情数据中提取时间相关信息，生成事件时间线。

**输入**：
- `eventIntroduction`：事件基础介绍
- `dataFile`：数据文件路径（CSV 格式）

**输出**：
- `result_file_path`：时间线分析结果 JSON 文件路径
- `timeline`：事件时间线数据

**使用时机**：当热点事件可能包含时间线（跨度比较长）时，Agent 调用此工具分析时间线。

### 10. analysis_sentiment（情感倾向分析）

**功能**：从舆情数据中分析情感倾向，统计占比并总结主要观点。

**输入**：
- `eventIntroduction`：事件基础介绍
- `dataFile`：数据文件路径（CSV 格式）

**输出**：
- `result_file_path`：情感分析结果 JSON 文件路径
- `sentiment_statistics`：情感统计信息
- `content_by_sentiment`：按情感分类的内容

**使用时机**：一般均可使用本工具，Agent 会调用此工具分析情感倾向。

### 11. generate_interpretation（解释与研判）

**功能**：基于时间线、情感结果与数据集摘要生成 `interpretation.json`，用于**报告叙事骨架**与 **Graph RAG 参数抽取**（event_type/domain/stage 等）。

### 12. graph_rag_query（Graph RAG 知识库查询）

**功能**：查询 Neo4j 知识库，提供相似案例/理论规律/分析指标/案例详情，辅助“对照研判”与“方法论落地”。

### 13. yqzk（舆情智库工具集）

**功能**：提供方法论框架、理论片段、本地参考资料检索与外部复核入口生成等能力（例如 `search_reference_insights`、`append_expert_judgement`、`build_event_reference_links`）。

### 14. report_html（HTML 报告生成）

**功能**：根据分析结果生成美观、交互式的 HTML 舆情分析报告（默认模板模式）。

**输入**：
- `eventIntroduction`：事件基础介绍
- `analysisResultsDir`：分析结果文件夹路径（包含所有分析结果的 JSON 文件）

**输出**：
- `html_file_path`：生成的 HTML 文件路径
- `file_url`：本地文件访问地址（file:// 协议）

**使用时机**：当需要生成最终的 HTML 报告时，Agent 调用本工具。

**特性**：
- 默认使用固定模板 `prompt/report_html_morandi_template.html`，版式与交互结构稳定
- 程序确定性抽取图表数据并注入模板（`__REPORT_JSON_DATA__`）
- 模型仅填充叙事占位符 JSON（`prompt/report_html_template_fill.txt`）
- 生命周期研判采用热度指数规则：2 小时切片、互动加权（发文+点赞+3*评论+5*转发）、峰值归一化、移动平均平滑
- 生命周期阶段固定为：`潜伏期 / 成长期 / 成熟期 / 衰退期`，并由规则自动给出 `PHASE_STATUS`
- 报告文本默认约束为中文，包含英文时会触发兜底清洗
- 模板不可用时自动回退旧逻辑（整页 HTML 生成）与 fallback 兜底
- 使用 ECharts 进行数据可视化，响应式适配不同屏幕尺寸
- 所有样式和脚本内嵌在 HTML 中，可直接在浏览器中打开

## 📁 项目结构

```
sona/
├── agent/                    # ReAct Agent 实现
│   └── reactagent.py        # Agent 核心逻辑，支持流式输出和会话管理
├── tools/                    # 工具定义
│   ├── extract_search_terms.py  # 搜索词提取
│   ├── data_collect.py          # 数据采集（NetInsight）
│   ├── data_num.py              # 数据数量查询
│   ├── analysis_timeline.py     # 时间线分析
│   ├── analysis_sentiment.py    # 情感倾向分析
│   ├── keyword_stats.py         # 关键词热度统计
│   ├── region_stats.py          # 地域分布统计
│   ├── author_stats.py          # 发布者分布统计
│   ├── volume_stats.py          # 声量趋势统计
│   ├── dataset_summary.py       # 数据集摘要
│   ├── generate_interpretation.py # 解释与研判（interpretation.json）
│   ├── graph_rag_query.py       # Graph RAG（Neo4j 知识库查询）
│   ├── yqzk.py                  # 舆情智库（本地参考检索/专家研判/外部入口）
│   ├── hottopics.py             # 全网热点态势感知（LangGraph 流程）
│   ├── report_html.py           # HTML 报告生成（模板分支 + 回退分支）
│   └── report_html_template.py  # 模板注入与数据抽取
├── model/                   # 模型管理
│   └── factory.py           # 模型工厂，支持多模型配置
├── config/                  # 配置文件
│   ├── model.yaml           # 模型配置（main/tools/report）
│   └── prompt.yaml          # 提示词配置
├── prompt/                  # 提示词模板
│   ├── system_prompt.txt    # Agent 系统提示词
│   ├── extract_search_terms.txt
│   ├── analysis_timeline.txt
│   ├── analysis_sentiment.txt
│   ├── report_html_enhanced.txt
│   ├── report_html_morandi_template.html
│   ├── report_html_template_fill.txt
│   └── interpretation.txt
├── cli/                     # 命令行界面
│   ├── main.py              # CLI 主入口
│   ├── interactive.py       # 交互式会话管理
│   ├── display.py           # 显示辅助函数（Rich）
│   ├── session_ui.py        # 会话选择 UI
│   ├── models_ui.py         # 模型配置 UI
│   └── tools_ui.py          # 工具列表 UI
├── utils/                   # 工具函数
│   ├── session_manager.py   # 会话管理
│   ├── token_tracker.py     # Token 追踪
│   ├── message_utils.py     # 消息工具（格式转换、会话恢复、消息压缩）
│   ├── prompt_loader.py    # 提示词加载
│   ├── path.py              # 路径管理
│   └── env_loader.py        # 环境变量加载
├── mcps/                    # MCP 集成
│   └── web_search.py        # 网页搜索 MCP
├── scripts/                 # 脚本工具
│   ├── list_tools.py        # 列出所有工具
│   ├── run_extract_search_terms.py
│   ├── run_data_collect.py
│   ├── run_report_html.py
│   └── run_hottopics.py
├── memory/                  # 会话存储目录（自动创建）
├── sandbox/                 # 任务工作目录（自动创建）
├── pyproject.toml           # 项目配置
├── requirements.txt         # 依赖列表
└── README.md               # 本文档
```

## ❓ 常见问题

### Q: Playwright 安装失败怎么办？

A: 
1. 检查网络连接，确保能访问 Playwright 下载服务器
2. 使用国内镜像（见上方安装指引）
3. 手动下载：访问 [Playwright 下载页面](https://playwright.dev/python/docs/browsers) 查看详细说明

### Q: 使用 data_collect 工具时提示"未配置 NetInsight 登录信息"

A: 请在 `.env` 文件中配置 `NETINSIGHT_USER` 和 `NETINSIGHT_PASS` 环境变量。

### Q: 浏览器驱动安装在哪里？

A: Playwright 会将浏览器驱动安装到用户目录：
- Windows: `%USERPROFILE%\AppData\Local\ms-playwright\`
- Linux/Mac: `~/.cache/ms-playwright/`

### Q: 如何调试 Playwright 登录过程？

A: 设置环境变量 `NETINSIGHT_HEADLESS=false`，这样会显示浏览器窗口，便于观察登录过程。

### Q: 如何查看 Token 使用情况？

A: 在交互式界面中，每次工具执行完成后会自动显示 Token 使用情况。也可以在会话完成后查看总计 Token 使用量。

### Q: 会话数据存储在哪里？

A: 会话数据存储在 `memory/` 目录下，每个会话对应一个 JSON 文件。任务工作文件存储在 `sandbox/{task_id}/` 目录下。

### Q: 如何清理会话和临时文件？

A: 在交互式界面中使用 `/clear` 命令，会清除 `memory/` 和 `sandbox/` 目录下的所有数据。

### Q: HTML 报告在哪里？

A: HTML 报告保存在 `sandbox/{task_id}/结果文件/report_*.html`，工具结果中会显示完整的文件路径和 file:// URL。

## 🔧 开发指南

### 运行测试

```bash
pytest tests/
```

测试脚本目录约定（`scripts/`）：
- 默认测试任务统一使用 `task_id=测试`
- 过程文件统一写入 `sandbox/测试/过程文件`
- 报告输出统一写入 `sandbox/测试/结果文件`

### 代码风格

项目遵循 PEP 8 风格指南，使用类型注解增强代码可读性和类型安全性。

### 添加新工具

1. 在 `tools/` 目录下创建新的工具文件
2. 使用 `@tool` 装饰器定义工具函数
3. 在 `tools/__init__.py` 中导入并导出工具
4. 在 `agent/reactagent.py` 中注册工具到 `AGENT_TOOLS`

### 添加新模型提供商

1. 在 `model/factory.py` 中添加创建函数（如 `_create_xxx`）
2. 在 `_PROVIDER_CREATORS` 字典中注册提供商
3. 在 `_DEFAULT_API_KEY_ENV_BY_PROVIDER` 中添加默认环境变量名
4. 更新 `config/model.yaml` 配置示例

### 消息压缩机制

项目实现了自动消息压缩机制，当上下文 Token 数超过 `max_context_tokens`（默认 20000）时，会自动压缩旧消息，保留最新的对话内容。压缩后的摘要会显示给用户。

### Token 追踪

项目实现了细粒度的 Token 追踪机制：
- 按步骤追踪 Token 使用（agent_processing、tool_xxx 等）
- 实时显示每个步骤的 Token 消耗
- 累计统计总 Token 使用量
- 会话级别的 Token 使用记录

## 📄 许可证

[添加许可证信息]

## 🙏 致谢

- [LangChain](https://github.com/langchain-ai/langchain) - LLM 应用开发框架
- [Rich](https://github.com/Textualize/rich) - 美观的终端输出
- [Playwright](https://playwright.dev/) - 浏览器自动化
- [ECharts](https://echarts.apache.org/) - 数据可视化

---

**Sona** - 让舆情分析更智能、更简单 🚀
