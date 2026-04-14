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
- 已有 remote MCP server：
  - 默认端点：`http://127.0.0.1:8001/mcp`
  - 当前 MCP tools：
    - `create_job`
    - `get_job`
    - `list_jobs`
    - `get_artifact`
    - `wait_for_job`

## 核心文件

- [bridge_server/main.py](/Users/meseg/shu/codex/gpt_bridge/bridge_server/main.py)
  - 现有 REST server
- [bridge_server/service.py](/Users/meseg/shu/codex/gpt_bridge/bridge_server/service.py)
  - REST 与 MCP 共享的业务层
- [storage/repository.py](/Users/meseg/shu/codex/gpt_bridge/storage/repository.py)
  - SQLite job 读写与状态更新
- [worker/poller.py](/Users/meseg/shu/codex/gpt_bridge/worker/poller.py)
  - 本地 worker 主循环
- [mcp_server/server.py](/Users/meseg/shu/codex/gpt_bridge/mcp_server/server.py)
  - FastMCP remote server 启动入口
- [mcp_server/tools.py](/Users/meseg/shu/codex/gpt_bridge/mcp_server/tools.py)
  - MCP tool 注册与参数校验
- [scripts/local_smoke_test.py](/Users/meseg/shu/codex/gpt_bridge/scripts/local_smoke_test.py)
  - REST 级 smoke test
- [scripts/mcp_smoke_test.py](/Users/meseg/shu/codex/gpt_bridge/scripts/mcp_smoke_test.py)
  - MCP 级 smoke test

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
- `BRIDGE_ALLOWED_WORK_ROOT`
- `BRIDGE_HOST`
- `BRIDGE_PORT`
- `BRIDGE_MCP_HOST`
- `BRIDGE_MCP_PORT`
- `BRIDGE_MCP_PATH`

默认值：

- 数据库：`data/bridge.db`
- artifact 目录：`artifacts/`
- 默认工作目录：`data/demo_workspace/`
- 允许执行根目录：`data/demo_workspace/`
- REST server：`http://127.0.0.1:8000`
- MCP server：`http://127.0.0.1:8001/mcp`

最小示例见 [.env.example](/Users/meseg/shu/codex/gpt_bridge/.env.example)。

版本控制说明：

- `.venv/`、`artifacts/`、`logs/`、本地 SQLite 数据库，以及 `.DS_Store` 这类本地运行时产物默认不纳入仓库
- `data/demo_workspace/` 中只保留占位文件和说明文档，执行过程中生成的 smoke / MCP 运行结果不会入库

## 启动顺序

推荐顺序：

1. 启动 worker
2. 启动 MCP server
3. 需要 REST 调试时，再启动 REST server

### 1. 启动 worker

```bash
python3.11 scripts/run_worker.py
```

### 2. 启动 MCP server

```bash
python3.11 scripts/run_mcp_server.py
```

默认 MCP 端点：

```text
http://127.0.0.1:8001/mcp
```

MCP server 额外暴露：

```text
http://127.0.0.1:8001/health
```

### 3. 可选：启动 REST server

```bash
python3.11 scripts/run_server.py
```

默认 REST 地址：

```text
http://127.0.0.1:8000
```

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
- worker 已启动

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
3. `create_job`
4. `wait_for_job`
5. `get_artifact summary.txt`

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

- worker 只领取 `queued` 任务
- `return_code == 0` 时标记为 `succeeded`
- 非零返回码或 Python 异常时标记为 `failed`
- 只有 `data/demo_workspace/` 下的目录允许执行
- 如果 `work_dir` 越界，worker 不会调用 Codex，而会直接写入失败结果

worker 使用的命令形状：

```bash
codex exec --skip-git-repo-check --color never --sandbox workspace-write -C <work_dir> -
```

## 当前限制

- 仅允许 `data/demo_workspace/` 范围内执行
- 当前没有鉴权，不适合暴露到不可信网络
- 当前是单 worker、本地 PoC
- 当前没有 OAuth
- 当前没有 widget
- 当前不是生产级任务系统

更多 MCP tool 细节见 [MCP_TOOLS.md](/Users/meseg/shu/codex/gpt_bridge/MCP_TOOLS.md)。
