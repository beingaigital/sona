# Sona GUI - 轻量 Viewer

任务 20：Streamlit 轻量面板，用于舆情分析任务的可视化管理和报告查看。

## 快速开始

### 1. 安装依赖

```bash
pip install streamlit requests pyyaml
```

### 2. 启动 GUI

```bash
cd sona_gui
streamlit run streamlit_app.py
```

浏览器自动打开 `http://localhost:8501`

---

## 功能模块

| 页面 | 功能 |
|------|------|
| 📊 首页 | 系统概览、快速导航、最近活动 |
| 📋 任务状态 | 任务列表、状态筛选、进度查看 |
| ➕ 新建任务 | 创建舆情分析任务（需后端API） |
| 📄 报告查看 | 渲染 HTML 分析报告 |
| 🔍 案例检索 | 历史案例搜索（演示数据） |
| ⚙️ 专题配置 | YAML 配置文件编辑 |

---

## 与后端 API 的关系

```
GUI (Streamlit)  <--HTTP-->  API (FastAPI)  -->  业务逻辑
     :8501                     :8765
```

### 场景 A：仅展示界面（截图/演示）
- 不启动 API 也能运行
- 使用演示数据，页面会显示 "API 离线"

### 场景 B：完整功能（新建任务、查看真实报告）
- 需要先启动任务 19 的 API

```bash
# 在另一个终端启动后端
cd /path/to/sona_project
pip install -e .
sona serve --port 8765
```

---

## 文件说明

```
sona_gui/
├── streamlit_app.py          # 主入口
├── README.md                 # 本文件
└── pages/
    ├── 01_任务状态.py
    ├── 02_新建任务.py
    ├── 03_报告查看.py
    ├── 04_案例检索.py
    └── 05_专题配置.py
```

---

## 常见问题

**Q: 页面显示 "API 离线"？**  
A: 正常，不影响界面展示。如需完整功能，启动 `sona serve --port 8765`。

**Q: 如何修改 API 地址？**  
A: 设置环境变量 `API_BASE`，如：`set API_BASE=http://127.0.0.1:8765`

**Q: 端口冲突？**  
A: 换端口启动：`streamlit run streamlit_app.py --server.port 8502`

---

## 设计决策

- ❌ 不做重 GUI（复杂大屏、拖拽工作流）
- ❌ 不做纯聊天机器人（效率低，不适合数据密集型场景）
- ✅ 采用 Streamlit 轻量面板 + 侧边栏助手导航
- ✅ 所有业务逻辑走 API，GUI 仅做 viewer/config

详见 `docs/gui_decision.md`

---

*作者：雍珍（任务 20）*  
*日期：2026-05-07*
