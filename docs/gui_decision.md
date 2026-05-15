# GUI 决策文档（任务 20）

本文档与 **任务 19**（`docs/api_design.md`、`sona serve`）对齐，约定轻量 GUI 的边界与启动方式。

## 决策摘要

- 采用 **Streamlit 多页轻量 Viewer**：首页 `streamlit_app.py` + `pages/*.py`。
- **不做**重后台大屏、不做独立权限系统。
- **业务逻辑**：走既有 Python 工作流；新建分析可走 **HTTP API**（与 Opinion System 接入方式一致）或 **「经典会话」子页** 内原完整界面。

## 与任务 19 的边界

| 能力 | 任务 19（API） | 任务 20（GUI） |
|------|----------------|----------------|
| 事件分析执行 | `POST /v1/analyze-event` | 表单收集参数后 **HTTP 调用** |
| 任务列表 | `GET /v1/tasks`（当前进程内存） | 任务状态页展示 |
| 任务详情 / 报告 | `GET /v1/tasks/{id}`、`GET /.../report` | 报告页渲染 HTML |
| 会话式调试 | — | `pages/99_经典会话与路由.py` 加载 `streamlit_legacy_chat` |

默认 API 基址：`http://127.0.0.1:8765`，环境变量 **`API_BASE`** 可覆盖。

## 目录结构（合并后）

```
streamlit_app.py              # 仪表盘：API 探活、最近任务、工作流速查
streamlit_legacy_chat.py      # 原单文件会话逻辑（供子页引用）
pages/
  01_任务状态.py
  02_新建任务.py
  03_报告查看.py
  04_案例检索.py              # 演示数据
  05_专题配置.py              # 写入 config/topics.yaml
  99_经典会话与路由.py
```

## 启动方式

```bash
# 终端 1
sona serve --host 127.0.0.1 --port 8765

# 终端 2（项目根）
streamlit run streamlit_app.py
```

Windows 仍可使用 **`StartSonaWebUI.bat`**（等价于 `streamlit run streamlit_app.py`）。

## API 调用示例（与实现一致）

```python
import requests

API = "http://127.0.0.1:8765"

# 创建并同步跑完事件分析
r = requests.post(
    f"{API}/v1/analyze-event",
    json={
        "query": "事件描述……",
        "prefer_existing_data": True,
        "disable_blocking_prompts": True,
    },
    timeout=None,
)
r.raise_for_status()
task_id = r.json()["task_id"]

# 任务详情
d = requests.get(f"{API}/v1/tasks/{task_id}", timeout=30).json()

# 报告 HTML
html = requests.get(f"{API}/v1/tasks/{task_id}/report", timeout=60).text
```

## 明确不做

- 重复实现调度、报告生成管线。
- 在 GUI 内硬编码数据库密码或模型 Key。

---

*合并说明：雍珍同学提交的 `SonaGUI/` 中 `docs_gui_decision.md` 与 `sona_gui/pages` 已对齐进仓库根目录；本文件为统一入口。*
