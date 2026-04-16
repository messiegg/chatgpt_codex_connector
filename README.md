# Codex Bridge Demo

一个最小可用的“ChatGPT 网页端间接驱动本地 Codex”桥接 PoC。

当前仓库包含三层：

1. **REST bridge**
   - 提供本地 REST API，用于创建和查询 job
2. **local worker**
   - 轮询 SQLite 里的 queued job，调用本机 `codex exec`
3. **remote MCP adapter**
   - 暴露 `remote MCP` 端点给 ChatGPT Developer Mode / custom app 接入

当前目标不是重写整个系统，而是在已有 REST PoC 和 worker 外面增加一层很薄的 MCP 适配层。

当前推荐开发目标已经收敛到两件事：

1. **一条命令启动**
2. **一次 MCP tool call 拿聚合结果**

第二阶段在此基础上补了“结果聚合与本地查看体验”：

1. worker 终态后额外写 `result.json`
2. MCP 新增 `get_result` / `get_latest_result`
3. 本地新增 `scripts/open_latest_result.py`

第三阶段先做出了最小可用的只读结果面板；3.5 收尾后，当前采用更接近 Apps SDK 推荐的“数据 tool + render tool”模式：

1. 数据 tool：
   - `run_codex_task`
   - `get_result`
   - `get_latest_result`
2. 渲染 tool：
   - `render_result_widget`

数据 tool 继续返回原有结构化 JSON 和文本结果；widget 绑定只放在 `render_result_widget` 上。如果 UI 没有被渲染，数据 tool 的 JSON 结果仍可照常使用。

## 当前能力

- 已有 REST API：
  - `GET /health`
  - `POST /jobs`
  - `GET /jobs`
  - `GET /jobs/{job_id}`
  - `GET /jobs/{job_id}/artifacts/{name}`
- 已有本地 worker：
  - 轮询 SQLite 中的 queued job
  - 调用本机 `codex exec`
  - 写回 `status / return_code / summary / command`
  - 把 artifact 写到 `artifacts/<job_id>/`
  - 终态后额外写聚合结果文件 `artifacts/<job_id>/result.json`
- 已有 remote MCP server：
  - 默认端点：`http://127.0.0.1:8001/mcp`
  - 当前 MCP tools：
    - `create_job`
    - `get_job`
    - `get_result`
    - `get_latest_result`
    - `list_jobs`
    - `get_artifact`
    - `wait_for_job`
    - `run_codex_task`
    - `render_result_widget`
  - `run_codex_task` / `get_result` / `get_latest_result` 现在是纯数据 tool
  - `render_result_widget` 会复用同一个只读结果面板 widget
- 已有 embedded worker dev 模式：
  - 通过 `BRIDGE_EMBED_WORKER=true` 打开
  - 直接挂在现有 MCP server 启动链路里
  - `scripts/run_mcp_server.py` 和 `scripts/dev_up.py` 都可用

## 核心文件

- [bridge_server/main.py](/Users/meseg/shu/codex/gpt_bridge/bridge_server/main.py)
  - 现有 REST server
- [bridge_server/service.py](/Users/meseg/shu/codex/gpt_bridge/bridge_server/service.py)
  - REST 与 MCP 共享的业务层
- [bridge_server/results.py](/Users/meseg/shu/codex/gpt_bridge/bridge_server/results.py)
  - 共享的结果聚合与 `result.json` 读写逻辑
- [storage/repository.py](/Users/meseg/shu/codex/gpt_bridge/storage/repository.py)
  - SQLite job 读写与状态更新
- [worker/poller.py](/Users/meseg/shu/codex/gpt_bridge/worker/poller.py)
  - 本地 worker 主循环
- [mcp_server/server.py](/Users/meseg/shu/codex/gpt_bridge/mcp_server/server.py)
  - FastMCP remote server 启动入口
- [mcp_server/tools.py](/Users/meseg/shu/codex/gpt_bridge/mcp_server/tools.py)
  - MCP tool 注册与参数校验
- [mcp_server/result_widget.py](/Users/meseg/shu/codex/gpt_bridge/mcp_server/result_widget.py)
  - 第三阶段结果面板 resource 注册、统一 payload 组装，以及数据 tool / render tool 响应封装
- [mcp_server/ui/result_widget.html](/Users/meseg/shu/codex/gpt_bridge/mcp_server/ui/result_widget.html)
  - 只读结果面板 widget
- [scripts/local_smoke_test.py](/Users/meseg/shu/codex/gpt_bridge/scripts/local_smoke_test.py)
  - REST 级 smoke test
- [scripts/mcp_smoke_test.py](/Users/meseg/shu/codex/gpt_bridge/scripts/mcp_smoke_test.py)
  - MCP 级 smoke test
- [scripts/open_latest_result.py](/Users/meseg/shu/codex/gpt_bridge/scripts/open_latest_result.py)
  - 本地查看最新聚合结果并打开对应目录
- [scripts/gui_app.py](/Users/meseg/shu/codex/gpt_bridge/scripts/gui_app.py)
  - 本地 GUI 控制面板入口
- [scripts/gui_helpers.py](/Users/meseg/shu/codex/gpt_bridge/scripts/gui_helpers.py)
  - GUI 复用的配置、状态、任务结果辅助逻辑

## 安装依赖

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## 配置

常用环境变量：

- `BRIDGE_DATABASE_PATH`
- `BRIDGE_ARTIFACTS_DIR`
- `BRIDGE_LOGS_DIR`
- `BRIDGE_CODEX_COMMAND`
- `BRIDGE_WORKER_POLL_INTERVAL_SECONDS`
- `BRIDGE_DEFAULT_WORK_DIR`
- `BRIDGE_ALLOWED_WORK_ROOTS`
- `CODEX_BRIDGE_ALLOWED_WORK_ROOTS`
- `BRIDGE_ALLOWED_WORK_ROOT`
- `CODEX_BRIDGE_ALLOWED_WORK_ROOT`
- `BRIDGE_HOST`
- `BRIDGE_PORT`
- `BRIDGE_MCP_HOST`
- `BRIDGE_MCP_PORT`
- `BRIDGE_MCP_PATH`
- `BRIDGE_MCP_PUBLIC_BASE_URL`
- `BRIDGE_MCP_AUTH_ENABLED`
- `BRIDGE_MCP_AUTH_ISSUER_URL`
- `BRIDGE_MCP_AUTH_AUDIENCE`
- `BRIDGE_MCP_AUTH_REQUIRED_SCOPES`
- `BRIDGE_EMBED_WORKER`
- `BRIDGE_TUNNEL_COMMAND`

默认值：

- 数据库：`data/bridge.db`
- artifact 目录：`artifacts/`
- 默认工作目录：`data/demo_workspace/`
- 允许执行根目录列表（默认只有一个）：`data/demo_workspace/`
- REST server：`http://127.0.0.1:8000`
- MCP server：`http://127.0.0.1:8001/mcp`
- MCP public base URL：默认空；需要公网 OAuth / remote MCP 时手动设置
- embedded worker：默认关闭；`scripts/dev_up.py` 会默认帮你打开

最小示例见 [.env.example](/Users/meseg/shu/codex/gpt_bridge/.env.example)。

### MCP Auth0 认证配置

当前 MCP server 已支持可选的 **Auth0 OAuth bearer token 校验**。

推荐方案：

1. 在 Auth0 里创建一个 **API**
   - Identifier 例如：`https://codex-bridge-mcp`
   - Scope 至少加一个：`mcp:use`
2. 在 Auth0 里创建一个 **Application**
   - 给它授权访问上面的 API
3. 在本地配置 MCP auth 环境变量

最小配置示例：

```dotenv
BRIDGE_MCP_AUTH_ENABLED=true
BRIDGE_MCP_AUTH_ISSUER_URL=https://your-tenant.us.auth0.com/
BRIDGE_MCP_AUTH_AUDIENCE=https://codex-bridge-mcp
BRIDGE_MCP_AUTH_REQUIRED_SCOPES=mcp:use
```

如果你的 MCP server 是通过公网地址暴露给 ChatGPT remote MCP app，而不是只在本机 localhost 上访问，还应设置：

```dotenv
BRIDGE_MCP_PUBLIC_BASE_URL=https://your-public-mcp-domain.example.com
```

这样 server 会把 OAuth Protected Resource Metadata 里的 resource URL 指向公网地址，而不是本地 `127.0.0.1`。

当前实现方式：

- Auth0 负责签发 access token
- 你的 MCP server 作为 resource server
- server 通过 Auth0 的 JWKS 校验 JWT
- server 会校验：
  - `iss`
  - `aud`
  - `exp`
  - 必需 scope

当前默认必需 scope 是：

```text
mcp:use
```

工作目录约束配置：

- `BRIDGE_ALLOWED_WORK_ROOTS` / `CODEX_BRIDGE_ALLOWED_WORK_ROOTS` 优先于旧的单路径变量
- 如果新的 `*_ALLOWED_WORK_ROOTS` 未设置或是空字符串，会回退到 `BRIDGE_ALLOWED_WORK_ROOT` / `CODEX_BRIDGE_ALLOWED_WORK_ROOT`
- `work_dir` 只要位于任一允许根目录下即可
- `default_work_dir` 也必须位于允许根目录之一内
- service 会在创建 job 时先校验、后 `mkdir`
- worker 在真正执行 `codex exec` 前会再次校验，作为兜底防线

逗号分隔示例：

```dotenv
BRIDGE_ALLOWED_WORK_ROOTS=/Users/me/project_a,/Users/me/project_b
```

JSON 数组示例：

```dotenv
BRIDGE_ALLOWED_WORK_ROOTS=["/Users/me/project_a", "/Users/me/project_b"]
```

版本控制说明：

- `.env` 是本地运行配置文件，默认只保留在本地，不纳入仓库
- `.venv/`、`artifacts/`、`logs/`、本地 SQLite 数据库，以及 `.DS_Store` 这类本地运行时产物默认不纳入仓库
- `data/demo_workspace/` 中只保留占位文件和说明文档，执行过程中生成的 smoke / MCP 运行结果不会入库
- GUI 联调过程中在 `data/demo_workspace/` 下临时创建的 `gui_*` 工作目录也默认只保留在本地，不纳入仓库

## 启动方式

### 推荐：一条命令启动 MCP dev 环境

```bash
python3.11 scripts/dev_up.py
```

这个脚本会：

1. 加载 `.env`
2. 默认把 `BRIDGE_EMBED_WORKER` 设为 `true`（如果你没有显式覆盖）
3. 启动现有 MCP server
4. 可选启动 tunnel
5. 打印本地地址、可用的公网地址、日志路径和 artifacts 路径
6. 写入 `logs/dev_session.json`

补充说明：

- 如果 `ngrok` 启动比 MCP server 慢，`dev_up.py` 现在会在启动后继续补发现公网地址
- 一旦拿到公网地址，会自动更新 `logs/dev_session.json`，并在终端补打印 `public MCP URL discovered: ...`

配套关闭脚本：

```bash
python3.11 scripts/dev_down.py
```

`dev_down.py` 会优先给前台 supervisor 发终止信号，由 supervisor 统一清理 MCP server、tunnel、session 文件和日志句柄，再退出。

### 兼容旧方式：分别启动

先启动 worker：

```bash
python3.11 scripts/run_worker.py
```

再启动 MCP server：

```bash
python3.11 scripts/run_mcp_server.py
```

如果你是直接运行 `scripts/run_mcp_server.py`，而不是通过 `scripts/dev_up.py` 启动，记得先把 `.env` 里的变量加载到当前 shell：

```bash
set -a
source .env
set +a
python3.11 scripts/run_mcp_server.py
```

如果你希望 `scripts/run_mcp_server.py` 自己带 worker 一起跑，可以直接：

```bash
BRIDGE_EMBED_WORKER=true python3.11 scripts/run_mcp_server.py
```

默认 MCP 端点：

```text
http://127.0.0.1:8001/mcp
```

MCP health 端点：

```text
http://127.0.0.1:8001/health
```

### 可选：启动 REST server

```bash
python3.11 scripts/run_server.py
```

默认 REST 地址：

```text
http://127.0.0.1:8000
```

## Local GUI Control Panel

启动命令：

```bash
python scripts/gui_app.py
```

当前 GUI 支持：

- 启动 / 停止 / 重启本地服务
- 刷新并查看 `logs/dev_session.json` 对应的服务状态信息
- 编辑 `allowed_work_roots`
- 编辑 `default_work_dir`
- 直接提交本地 Codex 任务
- 查看最近任务和聚合结果
- 查看 job 的 `prompt.txt`
- 查看 job 的 `metadata.json`
- 查看 artifact 列表并打开选中文件
- 按状态过滤最近任务
- 查看 `server log` / `tunnel log` / `service summary`
- 复制 `local MCP URL` / `public MCP URL` / `ChatGPT Developer Mode address`
- 打开 `logs` 目录 / `server log` / `tunnel log`
- 打开 `work_dir` / `artifact_dir` / `result.json`

实现说明：

- GUI 是本地单窗口桌面控制台，只作为 `dev_up.py` / `dev_down.py` / 本地 SQLite / `result.json` 的可视化壳
- GUI 直接复用现有 `allowed_work_roots`、SQLite job 和聚合结果逻辑，不替代 ChatGPT 中的 MCP / widget
- 修改 `allowed_work_roots` 或 `default_work_dir` 后，配置会写回 `.env`
- 修改这两项配置后需要重启服务，GUI 保存成功后也会提示是否立即重启
- 提交任务前必须先启动服务；服务停止时 GUI 不会创建 job
- Run Task 面板里，`prompt` 为必填；`work_dir` 留空时会自动使用当前 `default_work_dir`
- 任务提交后会自动出现在最近任务列表中，并自动选中、自动刷新详情
- Job Inspector 会在同一窗口内显示 Prompt / Summary / Stdout Tail / Stderr Tail / Metadata
- Prompt / Metadata / artifact 列表都是本地查看能力，直接读取本地 artifact 目录，不依赖 ChatGPT widget
- 最近任务支持按状态过滤，自动刷新时也会保留当前过滤条件
- GUI 还会显示本地连接信息，包括 local MCP URL、public MCP URL 和 ChatGPT Developer Mode address
- Logs 区会显示 server log、tunnel log 和本地 service summary，便于排障
- GUI 只负责本地提交与查看；真正执行任务的仍然是现有 service / worker 链路
- 服务状态区会定时刷新本地 health / session 信息；任务列表可手动刷新

## 运行 smoke test

### REST 级 smoke test

依赖：

- REST server 已启动
- worker 已启动

运行：

```bash
python3.11 scripts/local_smoke_test.py
```

### MCP 级 smoke test

依赖：

- MCP server 已启动
- 如果未启用 embedded worker，则外部 worker 也已启动

运行：

```bash
python3.11 scripts/mcp_smoke_test.py
```

可选参数：

```bash
python3.11 scripts/mcp_smoke_test.py \
  --mcp-url http://127.0.0.1:8001/mcp \
  --timeout-seconds 120 \
  --poll-interval 2
```

这个脚本会在 MCP client 层面完成：

1. `initialize`
2. `list_tools`
3. `list_resources`
4. `run_codex_task`
5. `render_result_widget`
6. `get_result(job_id)`
7. `render_result_widget`
8. `get_latest_result()`
9. `render_result_widget`
10. 校验数据 tool 无 widget、render tool 有 widget，以及 `result.json`、`summary`、`stdout_tail`、`stderr_tail`

最短本地验证步骤：

```bash
python -m compileall bridge_server mcp_server worker scripts
python scripts/dev_up.py
python scripts/mcp_smoke_test.py --timeout-seconds 180
python scripts/dev_down.py
```

## 第二阶段结果聚合

worker 在 job 到达终态后，除了原有 artifact，还会额外写：

```text
artifacts/<job_id>/result.json
```

它是一个聚合后的最终结果文件，至少包含：

- `job_id`
- `status`
- `summary`
- `stdout_tail`
- `stderr_tail`
- `work_dir`
- `artifact_dir`
- `artifact_names`
- `return_code`
- `command`
- `duration_seconds`
- `created_at`
- `started_at`
- `finished_at`

## 第三阶段内嵌结果面板

当前结果面板采用“数据 tool + render tool”模式：

- 数据 tool：
  - `run_codex_task`
  - `get_result(job_id)`
  - `get_latest_result()`
- 渲染 tool：
  - `render_result_widget(...)`

只有 `render_result_widget` 会绑定 ChatGPT 内嵌 widget；前三个业务 tool 只负责返回聚合结果。

widget 展示字段：

- `status`
- `job_id`
- `resolved_job_id`（只有 `get_latest_result()` 且与 `job_id` 不同时才显示）
- `duration_seconds`
- `work_dir`
- `artifact_dir`
- `artifact_names`
- `summary`
- `stdout_tail`
- `stderr_tail`

统一 widget payload 还会额外保留这些只读字段，方便 UI 或后续兼容：

- `timed_out`
- `result_file_present`
- `return_code`
- `command`
- `created_at`
- `started_at`
- `finished_at`

实现原则：

- 服务端继续是唯一权威来源
- widget 只消费统一 payload，不自己推断业务状态
- 结构化 JSON 仍保留在 `structuredContent`
- 数据 tool 与 widget 绑定解耦，更接近 Apps SDK 推荐模式
- widget 只是增强层；即使 UI 未渲染，数据 tool 的 JSON 结果仍然可用
- widget 页面会先显示等待态，再监听父窗口的 `ui/notifications/tool-result` 消息并重渲染

推荐调用链：

1. 先调用 `run_codex_task(...)`、`get_result(job_id)` 或 `get_latest_result()`
2. 再把结果转换成统一 payload
3. 调用 `render_result_widget(...)`

## 聚合结果 MCP tools

### `get_result(job_id)`

用于读取某个 job 的聚合结果。

返回字段至少包括：

- `job_id`
- `status`
- `summary`
- `stdout_tail`
- `stderr_tail`
- `work_dir`
- `artifact_dir`
- `artifact_names`
- `return_code`
- `command`
- `duration_seconds`
- `created_at`
- `started_at`
- `finished_at`
- `metadata`
- `result_file_present`

行为：

- 优先读取 `result.json`
- 如果 `result.json` 不存在或损坏，就即时聚合并回退返回
- 不直接绑定 widget；需要再把结果交给 `render_result_widget(...)`

### `get_latest_result()`

用于读取“最新一个 job”的聚合结果。

返回字段与 `get_result(job_id)` 基本一致，另外还会包含：

- `resolved_job_id`
- 不直接绑定 widget；需要再把结果交给 `render_result_widget(...)`

## 高层 MCP tool：`run_codex_task`

用于把原先常见的三连：

1. `create_job`
2. `wait_for_job`
3. `get_artifact`

折叠成一次调用：

```text
run_codex_task(prompt, work_dir=None, timeout_seconds=120, poll_interval=2.0)
```

返回字段至少包括：

- `job_id`
- `status`
- `timed_out`
- `summary`
- `stdout_tail`
- `stderr_tail`
- `work_dir`
- `artifact_dir`
- `artifact_names`
- `return_code`
- `command`
- `duration_seconds`

补充说明：

- `stdout_tail` / `stderr_tail` 返回尾部摘要，而不是头部截断
- `artifact_names` 直接列出 artifact 目录下文件名并排序
- `command` / `duration_seconds` 优先从 `metadata.json` 聚合，拿不到时再回填 job 信息
- 超时不会抛 MCP tool error，而是返回结构化结果并把 `timed_out=true`
- 不直接绑定 widget；需要再把结果交给 `render_result_widget(...)`

## 渲染 tool：`render_result_widget`

这个 tool 只负责渲染 widget，不做业务查询。

输入：

- 统一结果 payload（与 `ResultWidgetPayload` 兼容）

输出：

- `structuredContent` 直接返回统一 payload
- `content` 返回一段简短文本说明
- tool descriptor 上带同一个 `RESULT_WIDGET_URI` 绑定
- tool result `_meta` 继续带 `resultWidgetPayload`

推荐用途：

1. 先拿聚合结果
2. 再渲染 widget

这样比“业务 tool 直接绑 widget”更接近 Apps SDK 推荐模式。

最短示例：

```python
result = await session.call_tool(
    "run_codex_task",
    {
        "prompt": "In the current working directory, create exactly one file named demo.txt and explain what changed.",
        "timeout_seconds": 120,
    },
)
```

## 推荐工作流

1. 启动：

```bash
python3.11 scripts/dev_up.py
```

2. ChatGPT 侧优先调用：

```text
run_codex_task(...)
```

3. 再调用：

```text
render_result_widget(...)
```

如果是在 ChatGPT Developer Mode 里联调，应该是“先拿数据，再渲染 widget”，而不是让业务 tool 直接带 widget。

3. 本地查看最新结果：

```bash
python3.11 scripts/open_latest_result.py
```

## 本地查看脚本

默认查看最新 job，并优先打开 `artifact_dir`：

```bash
python3.11 scripts/open_latest_result.py
```

只打印不打开：

```bash
python3.11 scripts/open_latest_result.py --no-open
```

打印完整聚合 JSON：

```bash
python3.11 scripts/open_latest_result.py --print-json
```

打开 `work_dir`：

```bash
python3.11 scripts/open_latest_result.py --work-dir
```

查看指定 job：

```bash
python3.11 scripts/open_latest_result.py --job-id <job_id>
```

## 如何暴露 HTTPS 给 ChatGPT

ChatGPT 当前只能连接 **remote MCP server**，不能直接连接本地 `localhost`。本地开发时需要先把本地 MCP 服务通过隧道暴露成 HTTPS。

### 用 ngrok

```bash
ngrok http 8001
```

然后把公开地址里的 `/mcp` 作为 remote MCP URL，例如：

```text
https://your-subdomain.ngrok-free.app/mcp
```

### 用 Cloudflare Tunnel

```bash
cloudflared tunnel --url http://127.0.0.1:8001
```

同样把公开 HTTPS 地址后面的 `/mcp` 作为 remote MCP URL。

### 用 `BRIDGE_TUNNEL_COMMAND`

如果你希望 `scripts/dev_up.py` 直接代管隧道，可以在 `.env` 里配置：

```bash
BRIDGE_TUNNEL_COMMAND=ngrok http 8001
```

如果没有配置，但系统里存在 `ngrok`，`scripts/dev_up.py` 会自动尝试 `ngrok http <mcp_port>`。
如果两者都没有，服务仍会正常启动，只是不会启用公网隧道。

### 421 Invalid Host Header

本地通过 ngrok / cloudflared 接入 ChatGPT 时，如果创建 connector 时报 `421 Invalid Host Header`、`421 Misdirected Request`，原因通常不是 ChatGPT 表单本身，而是 MCP Python SDK 默认开启的 DNS rebinding protection 拒绝了隧道转发进来的 Host header。

当前本地开发配置已经关闭这个检查，便于通过公开隧道联调 remote MCP server。生产环境不应长期关闭，应该改成显式的 Host allowlist。

## 如何在 ChatGPT 网页端接入

Developer Mode 的可用性和权限目前与账号计划、工作区设置有关，并且只支持 **remote MCP server**。

1. 打开 ChatGPT，并启用 Developer Mode
2. 进入 app / connector 创建界面
3. 创建一个 remote MCP app
4. 填入公开的 MCP 地址，例如 `https://<your-public-host>/mcp`
5. 本地开发阶段使用 no-auth 方式
6. 保存后先测试 `list_jobs` 或 `create_job`

如果只是本地联调，建议先跑一遍 [scripts/mcp_smoke_test.py](/Users/meseg/shu/codex/gpt_bridge/scripts/mcp_smoke_test.py:1)，确认链路正常，再接入 ChatGPT。

## Worker 执行边界

- service 创建 job 时会先校验 `work_dir` 是否位于任一允许根目录下，只有通过后才会创建目录
- worker 只领取 `queued` 任务
- `return_code == 0` 时标记为 `succeeded`
- 非零返回码或 Python 异常时标记为 `failed`
- `default_work_dir` 也必须位于任一允许根目录下
- `work_dir` 只要落在任一允许根目录下就允许执行
- 如果 `work_dir` 越界，service 会直接拒绝；即使数据库里已有异常 job，worker 也不会调用 Codex，而会直接写入失败结果

worker 使用的命令形状：

```bash
codex exec --skip-git-repo-check --color never --sandbox workspace-write -C <work_dir> -
```

## 当前限制

- 仅允许配置的 allowed work roots 范围内执行
- 当前没有鉴权，不适合暴露到不可信网络
- 当前是单 worker、本地 PoC
- 当前没有 OAuth
- 当前 widget 只做单个 job 的只读展示，不做复杂交互、轮询或历史列表
- 当前不是生产级任务系统

更多 MCP tool 细节见 [MCP_TOOLS.md](/Users/meseg/shu/codex/gpt_bridge/MCP_TOOLS.md)。
