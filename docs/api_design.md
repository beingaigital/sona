# Sona HTTP API 设计草案（任务 19）

本文描述 **机器可调用的 HTTP API** 形态，用于 Opinion System 或其它服务触发 Sona 工作流。实现状态以代码为准；未实现的端点标注为 **计划中**。

---

## 1. 与 Streamlit Web UI 的分工

| 维度 | Streamlit（`streamlit_app.py`） | HTTP API（已实现核心端点） |
|------|--------------------------------|-------------------|
| 主要用户 | 人在浏览器里操作 | 其它系统、脚本、`curl` |
| 交互形态 | 会话、表单、后台线程跑任务 | 请求 / 响应 JSON，可选轮询任务状态 |
| 工作流 | 已接入 `run_event_analysis_workflow`、`route_query`、热点等 | **复用同一套 Python 工作流**，不复制业务逻辑 |
| 产物路径 | 从会话与工具结果中解析报告等 | 通过 `artifacts` 字段返回 `report_path` 等 |

原则：**业务逻辑只在 Python 工作流层维护一份**；API 层只做参数校验、任务登记、错误包装与路径回传。

---

## 2. 约定

- **Base URL（占位）**：`http://127.0.0.1:8765`（实现时可配置 `SONA_API_HOST` / `SONA_API_PORT`）
- **版本前缀**：`/v1`
- **Content-Type**：`application/json`（文件下载类端点除外）
- **认证**：结课版本可为空；生产环境建议由网关或 `Authorization` 头扩展，本文不展开。

---

## 3. 统一 JSON 字段

### 3.1 任务状态 `status`

| 值 | 含义 |
|----|------|
| `queued` | 已接受，尚未执行 |
| `running` | 执行中 |
| `succeeded` | 成功结束 |
| `failed` | 失败（见 `error`） |

### 3.2 任务详情（GET 任务时使用）

```json
{
  "task_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "succeeded",
  "artifacts": {
    "report_path": "path/to/report.html",
    "trace_path": "",
    "sandbox_dir": "",
    "session_hint": ""
  },
  "error": null
}
```

- **`task_id`**：字符串，UUID 或与 `SessionManager` 一致的会话任务 ID（实现时二选一并写死文档）。
- **`artifacts`**：键可根据工作流扩展；**至少预留**：
  - `report_path`：最终 HTML 报告文件系统路径（或空字符串表示未生成）
  - `trace_path`：可选，调试/追踪文件路径
  - `sandbox_dir`：可选，本次任务 sandbox 根目录
- **`error`**：成功时为 `null`；失败时为对象，见下。

### 3.3 错误对象 `error`

```json
{
  "error_code": "WORKFLOW_ERROR",
  "error_message": "人类可读说明"
}
```

| `error_code`（示例） | 说明 |
|---------------------|------|
| `VALIDATION_ERROR` | 请求体缺字段或非法 |
| `WORKFLOW_ERROR` | 工作流执行抛错 |
| `NOT_FOUND` | 未知 `task_id` |
| `NOT_READY` | 任务尚未结束，报告不可用 |

---

## 4. 端点列表

### 4.1 `GET /health`

**用途**：探活。

**响应示例（200）**：

```json
{
  "status": "ok",
  "service": "sona-api",
  "version": "0.1.0"
}
```

---

### 4.2 `POST /v1/analyze-event`

**用途**：触发 **事件分析**（对齐 CLI / Streamlit 事件模式）。

**请求体**：

```json
{
  "query": "用户自然语言描述的事件或指令",
  "prefer_existing_data": true,
  "disable_blocking_prompts": false
}
```

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `query` | string | 是 | 事件分析输入 |
| `prefer_existing_data` | boolean | 否 | 是否优先复用历史数据，默认 `true` |
| `disable_blocking_prompts` | boolean | 否 | 是否禁用阻塞式交互提示，默认 `false` |

**响应（202 或 200，由实现决定）**：

```json
{
  "task_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "queued",
  "artifacts": {},
  "error": null
}
```

说明：当前实现为 **同步**：请求在 **`POST /v1/analyze-event`** 内跑完全部工作流后返回 **`200`**，状态为 `succeeded` 或 `failed`。任务摘要保存在 **API 进程内存** 中（重启或多 worker 后不共享）；异步化后可改为 `202` + 轮询。

---

### 4.3 `GET /v1/tasks`

**用途**：列出当前 API 进程内存中已登记的任务（供 Streamlit 任务状态页使用）。

**响应示例（200）**：

```json
{
  "tasks": [
    {
      "task_id": "...",
      "status": "succeeded",
      "artifacts": { "report_path": "...", "trace_path": "", "sandbox_dir": "", "session_hint": "" },
      "error": null
    }
  ]
}
```

---

### 4.4 `GET /v1/tasks/{task_id}`

**用途**：查询单个任务状态与产物路径。

**响应**：见 §3.2。

---

### 4.5 `GET /v1/tasks/{task_id}/report`

**用途**：获取报告——二选一实现（在实现章节中写死一种）：

- **A**：返回 JSON，`{ "report_path": "..." }`；
- **B**（当前实现）：`200` 且 `Content-Type: text/html`，直接返回文件内容（`FileResponse` 下载/预览）。

---

### 4.6 `POST /v1/wiki/query`（计划中）

**用途**：封装本地 Wiki / RAG 问答（与 `workflow/wiki_rag` 或 CLI wiki 对齐）。

**请求体（草案）**：

```json
{
  "query": "string",
  "top_k": 5
}
```

**响应**：在实现时补充 `answer`、`sources` 等字段；未接好前可返回 `501` + `error` 说明依赖未就绪。

---

### 4.7 专题监测相关（计划中）

与 `workflow/topic_monitoring_pipeline`、Postgres 配置对齐后再定路径，例如：

- `POST /v1/monitor/topics`
- `GET /v1/monitor/topics/{topic_id}/snapshot`

当前文档仅保留占位；**本地无数据库时应返回明确 `error_code`，且不影响事件分析主链路**。

---

## 5. OpenAPI

计划使用 **FastAPI** 实现上述端点；服务启动后可访问：

- **Swagger UI**：`http://127.0.0.1:8765/docs`（占位，以实际端口为准）

---

## 6. 启动方式

项目根目录下（已安装依赖、可编辑安装或 `PYTHONPATH` 含项目根）：

```bash
# 方式 A：CLI
sona serve --host 127.0.0.1 --port 8765

# 开发热重载
sona serve --reload

# 方式 B：直接 uvicorn（需在项目根执行，或保证能 import api.server）
uvicorn api.server:app --host 127.0.0.1 --port 8765
```

可选环境变量：`SONA_API_HOST`、`SONA_API_PORT`（作为 `sona serve` 的默认 host/port）。`serve` 会将当前工作目录切换到项目根，便于导入 `api.server`。

交互式 API 文档：服务启动后打开 `http://127.0.0.1:8765/docs`（端口以实际为准）。

---

## 8. 安全与配置

- **禁止**在仓库中提交真实 API Key、Neo4j、数据库密码；仅通过环境变量或本地 `.env`（且 `.env` 在 `.gitignore` 中）配置。
- CORS：若仅本机调用可收紧为 `127.0.0.1`；跨域由实现时在 FastAPI 中配置。

---

## 9. 修订记录

| 日期 | 说明 |
|------|------|
| 2026-05-07 | 初稿：端点与 JSON 约定、与 Streamlit 分工、启动与 curl 占位 |
| 2026-05-07 | 任务 7：依赖说明与可复制验收命令（§10） |

---

## 10. 依赖安装与验收命令（任务 7）

### 10.1 依赖（与仓库一致）

以下已写入 **`pyproject.toml`** 与 **`requirements.txt`**（安装任一方式即可）：

| 包 | 用途 |
|----|------|
| `fastapi` | HTTP API 框架 |
| `uvicorn[standard]` | ASGI 服务器 |
| `typer` | `sona` / `sona serve` CLI |

`pydantic` 由 `fastapi` 引入，无需单独写一行。

**安装示例（项目根目录）：**

```bash
python -m venv .venv
```

Windows（PowerShell）：

```powershell
.\.venv\Scripts\Activate.ps1
pip install -U pip
pip install -r requirements.txt
pip install -e .
```

Linux / macOS：

```bash
source .venv/bin/activate
pip install -U pip
pip install -r requirements.txt
pip install -e .
```

### 10.2 启动 API（终端 1）

```bash
sona serve --host 127.0.0.1 --port 8765
```

看到进程占用端口后，再进行下面的探测。**注意**：`POST /v1/analyze-event` 会跑完整事件分析链路，耗时长且依赖模型与采集配置；验收探活可只用 `GET /health`。

### 10.3 可复制命令：Bash（Git Bash / WSL / Linux / macOS）

将下列整段复制到 **第二个终端**（需已安装 `curl`；若已安装 `jq` 可取消注释解析 `task_id`）：

```bash
export BASE=http://127.0.0.1:8765

curl -sS "$BASE/health"
echo

# 以下为完整事件分析，仅在有环境与时间时执行（可能运行很久）
# RESP=$(curl -sS -X POST "$BASE/v1/analyze-event" \
#   -H "Content-Type: application/json" \
#   -d '{"query":"示例：某舆情事件简要描述","prefer_existing_data":true,"disable_blocking_prompts":true}')
# echo "$RESP"
# TASK_ID=$(echo "$RESP" | jq -r .task_id)
# curl -sS "$BASE/v1/tasks/$TASK_ID"
# echo
# curl -sS -o report.html "$BASE/v1/tasks/$TASK_ID/report"
# echo "Saved report.html if status was succeeded and report_path was set."
```

不依赖 `jq`、手动替换 `task_id` 的示例：

```bash
curl -sS http://127.0.0.1:8765/health

curl -sS -X POST http://127.0.0.1:8765/v1/analyze-event \
  -H "Content-Type: application/json" \
  -d '{"query":"示例：某舆情事件简要描述","prefer_existing_data":true,"disable_blocking_prompts":true}'

curl -sS http://127.0.0.1:8765/v1/tasks/你的task_id

curl -sS -o report.html http://127.0.0.1:8765/v1/tasks/你的task_id/report
```

### 10.4 可复制命令：Windows PowerShell

```powershell
$Base = "http://127.0.0.1:8765"
Invoke-RestMethod -Uri "$Base/health" -Method Get

# 完整事件分析（耗时长，按需执行）
# $body = @{
#   query = "示例：某舆情事件简要描述"
#   prefer_existing_data = $true
#   disable_blocking_prompts = $true
# } | ConvertTo-Json
# $r = Invoke-RestMethod -Uri "$Base/v1/analyze-event" -Method Post -Body $body -ContentType "application/json; charset=utf-8"
# $r
# $tid = $r.task_id
# Invoke-RestMethod -Uri "$Base/v1/tasks/$tid" -Method Get
# Invoke-WebRequest -Uri "$Base/v1/tasks/$tid/report" -OutFile "report.html"
```

### 10.5 预期现象（验收）

| 步骤 | 预期 |
|------|------|
| `GET /health` | HTTP 200，JSON 含 `"status":"ok"` |
| `GET /v1/tasks/{未知id}` | HTTP 404 |
| `POST /v1/analyze-event` | HTTP 200，JSON 含 `task_id`；成功时 `status` 为 `succeeded`，`artifacts.report_path` 可能为非空路径 |
| `GET /v1/tasks/{id}/report` | 成功且已有报告文件时 HTTP 200，正文为 HTML；否则 404 或 409 |

---
