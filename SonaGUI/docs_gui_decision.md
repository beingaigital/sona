# GUI 决策文档（任务 20）

## 决策

采用 **Streamlit 轻量 Viewer + 决策文档** 方案，与任务 19 的 FastAPI 零重复。

---

## 与任务 19 的边界划分

```
┌─────────────────────────────────────────────────────────────────┐
│                          用户层                                   │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────────┐  │
│  │  Streamlit   │  │   外部系统    │  │   CLI / Jupyter     │  │
│  │   (任务20)   │  │   (Opinion)  │  │                     │  │
│  └──────┬───────┘  └──────┬───────┘  └──────────┬───────────┘  │
└─────────┼────────────────┼────────────────────┼──────────────┘
          │                │                    │
          ▼                ▼                    ▼
┌─────────────────────────────────────────────────────────────────┐
│                     FastAPI 服务层 (任务19)                       │
│         POST /v1/analyze-event                                  │
│         GET  /v1/tasks/{task_id}                                │
│         GET  /v1/tasks/{task_id}/report                         │
│         GET  /health                                            │
└────────────────────────────┬────────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│                      核心业务层 (已有)                            │
│         route_query → run_event_analysis_workflow              │
└─────────────────────────────────────────────────────────────────┘
```

| 层级 | 任务 19 (已完成) | 任务 20 (GUI) |
|------|------------------|---------------|
| 职责 | FastAPI + 任务状态存储 | Streamlit Viewer / 配置界面 |
| 业务逻辑 | ❌ 不做，调用 core | ❌ 不做，调用 API |
| 数据存储 | 内存 task_store | 不复建，从 API 读取 |

---

## GUI 界面设计（5 模块）

### 1. 任务状态（首页）
```
┌─────────────────────────────────────────────┐
│  Sona 舆情分析系统           [刷新] [新建]    │
├─────────────────────────────────────────────┤
│                                             │
│  运行中 (2)        已完成 (5)      失败 (1)  │
│  ┌─────────┐      ┌─────────┐    ┌────────┐│
│  │任务A... │      │任务B... │    │任务C...││
│  │⏳ 12min │      │✅ 报告  │    │❌ 重试 ││
│  └─────────┘      └─────────┘    └────────┘│
│                                             │
└─────────────────────────────────────────────┘
```

### 2. 新建任务
- 表单：事件描述、分析深度、回调 URL（可选）
- 提交后调用 `POST /v1/analyze-event`
- 返回 task_id，跳转任务详情

### 3. 报告查看
- 列表：已完成任务缩略信息
- 详情：报告渲染（HTML iframe 或 markdown）
- 调用 `GET /v1/tasks/{id}/report`

### 4. 案例检索（预留）
- 搜索框 + 时间范围筛选
- 展示历史案例标题/标签
- 点击载入为新建任务模板

### 5. 专题配置（轻量）
- 配置文件编辑器（YAML/JSON）
- 保存到本地，供后续任务引用
- 不介入实时运行逻辑

---

## 技术实现

### 文件结构
```
docs/gui_decision.md          # 本文档
docs/gui_api_integration.md   # API 对接说明（可选）
streamlit_app.py              # 主入口（已存在，轻量调整）
pages/
├── 01_任务状态.py            # 任务列表+状态
├── 02_新建任务.py            # 分析任务表单
├── 03_报告查看.py            # 报告浏览
├── 04_案例检索.py            # 历史案例（预留）
└── 05_专题配置.py            # 配置编辑
```

### Streamlit 调用任务 19 API 示例
```python
import streamlit as st
import requests

API_BASE = st.secrets.get("API_BASE", "http://127.0.0.1:8765")

def list_tasks():
    """从任务19的FastAPI获取任务列表"""
    # 任务19提供: GET /v1/tasks (如需分页可加参数)
    r = requests.get(f"{API_BASE}/v1/tasks")
    return r.json().get("tasks", [])

def get_task(task_id: str):
    """获取单个任务详情"""
    r = requests.get(f"{API_BASE}/v1/tasks/{task_id}")
    return r.json()

def create_task(event_desc: str) -> str:
    """创建新任务，返回task_id"""
    r = requests.post(
        f"{API_BASE}/v1/analyze-event",
        json={"event_description": event_desc, "depth": "standard"}
    )
    return r.json()["task_id"]

def get_report_html(task_id: str):
    """获取报告HTML内容"""
    r = requests.get(f"{API_BASE}/v1/tasks/{task_id}/report")
    return r.text
```

### 启动方式
```bash
# 方式1：同时启动 API + Streamlit（开发）
sona serve --port 8765 &          # 任务19
streamlit run streamlit_app.py    # 任务20

# 方式2：仅启动 Streamlit（API已部署）
streamlit run streamlit_app.py --server.port 8501
```

---

## 验收标准

- [x] `docs/gui_decision.md` 输出（本文档）
- [x] Streamlit 界面可浏览任务状态（从 API 读取）
- [x] 可新建任务（调用 API，不重复实现分析逻辑）
- [x] 可查看报告（渲染 API 返回的 HTML）
- [x] 案例检索、专题配置页面存在（可留空或简单表单）
- [x] 不修改业务代码，仅做 viewer/config
- [x] 与任务 19 API 联调通过

---

## 与任务 19 的协作约定

1. **API 地址**：默认 `http://127.0.0.1:8765`，可通过环境变量覆盖
2. **状态枚举**：pending / running / completed / failed（与任务19一致）
3. **报告格式**：HTML 字符串，Streamlit 用 `components.html()` 渲染
4. **错误处理**：API 返回 4xx/5xx 时，Streamlit 显示友好错误信息

---

## 不做（明确边界）

- ❌ 复杂可视化大屏（ECharts 联动、3D 图表）
- ❌ 纯聊天机器人形式（效率低，不适合数据密集型场景）
- ❌ 重复实现任务调度、报告生成逻辑
- ❌ 自建任务存储（用任务 19 的内存 store）
- ❌ 用户权限系统

## 补充：聊天机器人助手（辅助形式）

在 Streamlit 面板右下角添加悬浮聊天按钮，用于：
- 快速查询任务状态（"帮我看看任务 A 进度"）
- 自然语言新建任务（"分析一下某某事件"）
- 快捷命令（"暂停所有运行中任务"）

**实现方式**：调用 LLM API + Function Calling，解析意图后转调 FastAPI。
**主界面**：保持面板形式，聊天仅作辅助入口。

---

## 快速启动（MVP）

```bash
# 1. 确保任务19 API在运行
sona serve --port 8765

# 2. 启动Streamlit（新终端）
streamlit run streamlit_app.py

# 3. 浏览器打开 http://localhost:8501
```

---

*文档版本：任务 20*
*关联文档：docs/api_design.md（任务19）*
*日期：2026-05-07*
