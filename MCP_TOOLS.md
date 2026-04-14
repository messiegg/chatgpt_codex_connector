# MCP Tools

当前 remote MCP 端点默认是：

```text
http://127.0.0.1:8001/mcp
```

这一层直接复用：

- [bridge_server/service.py](/Users/meseg/shu/codex/gpt_bridge/bridge_server/service.py)
- [storage/repository.py](/Users/meseg/shu/codex/gpt_bridge/storage/repository.py)

它不会反向调用本地 REST API。

## `create_job`

名称：

- `create_job`

输入：

- `prompt: string`
- `work_dir?: string`

输出：

- 完整 job 信息
- 字段与现有 `JobResponse` 基本一致：
  - `job_id`
  - `status`
  - `prompt`
  - `work_dir`
  - `created_at`
  - `started_at`
  - `finished_at`
  - `return_code`
  - `error_message`
  - `summary`
  - `artifact_dir`
  - `command`

何时使用：

- 创建一个新的本地 Codex 执行任务
- ChatGPT 侧通常把它作为第一步

REST 对应关系：

- `POST /jobs`

## `get_job`

名称：

- `get_job`

输入：

- `job_id: string`

输出：

- 单个 job 的完整信息

何时使用：

- 查询某个 job 当前状态
- 读取执行完成后的 `summary`、`return_code`、`command`

REST 对应关系：

- `GET /jobs/{job_id}`

## `list_jobs`

名称：

- `list_jobs`

输入：

- `status?: string`
- `limit?: integer`
- `offset?: integer`

输出：

- `jobs`
- `limit`
- `offset`
- `status`

何时使用：

- 查看最近任务
- 按 `queued / running / succeeded / failed` 过滤

REST 对应关系：

- `GET /jobs`

## `get_artifact`

名称：

- `get_artifact`

输入：

- `job_id: string`
- `name: string`

输出：

- `job_id`
- `name`
- `content`
- `truncated`

何时使用：

- 读取某个 job 的文本 artifact
- 常见场景是读取 `summary.txt`、`stdout.log`、`stderr.log`

REST 对应关系：

- `GET /jobs/{job_id}/artifacts/{name}`

额外限制：

- 只允许以下文本 artifact：
  - `summary.txt`
  - `stdout.log`
  - `stderr.log`
  - `prompt.txt`
  - `metadata.json`
- 按 UTF-8 读取
- 超长内容会截断，并返回 `truncated=true`

## `wait_for_job`

名称：

- `wait_for_job`

输入：

- `job_id: string`
- `timeout_seconds?: integer`
- `poll_interval?: number`

输出：

- 终态后的完整 job 信息

何时使用：

- `create_job` 之后直接等待任务结束
- ChatGPT 侧通常优先用它，而不是自己写轮询逻辑

REST 对应关系：

- 没有单独 REST endpoint
- 它本质上是对 `GET /jobs/{job_id}` 的轮询包装

## 当前限制

- 当前 MCP server 是 no-auth、本地开发用途
- 当前只允许 demo workspace 范围内执行
- 当前 worker 是本地单进程轮询
- 当前主要用于验证“ChatGPT -> remote MCP -> 本地 job 系统 -> 本地 Codex”链路
