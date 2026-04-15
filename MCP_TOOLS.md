# MCP Tools

当前 remote MCP 端点默认是：

```text
http://127.0.0.1:8001/mcp
```

推荐启动方式：

```bash
python3.11 scripts/dev_up.py
```

如果你直接跑 MCP server，也可以用：

```bash
BRIDGE_EMBED_WORKER=true python3.11 scripts/run_mcp_server.py
```

这一层直接复用：

- [bridge_server/service.py](/Users/meseg/shu/codex/gpt_bridge/bridge_server/service.py)
- [bridge_server/results.py](/Users/meseg/shu/codex/gpt_bridge/bridge_server/results.py)
- [storage/repository.py](/Users/meseg/shu/codex/gpt_bridge/storage/repository.py)
- [mcp_server/result_widget.py](/Users/meseg/shu/codex/gpt_bridge/mcp_server/result_widget.py)
- [mcp_server/ui/result_widget.html](/Users/meseg/shu/codex/gpt_bridge/mcp_server/ui/result_widget.html)

它不会反向调用本地 REST API。

## 第三阶段 widget

3.5 收尾后，当前采用“数据 tool + render tool”模式：

- 数据 tool：
  - `run_codex_task`
  - `get_result`
  - `get_latest_result`
- 渲染 tool：
  - `render_result_widget`

widget 展示字段：

- `status`
- `job_id`
- `resolved_job_id`（仅 `get_latest_result()` 且与 `job_id` 不同时显示）
- `duration_seconds`
- `work_dir`
- `artifact_dir`
- `artifact_names`
- `summary`
- `stdout_tail`
- `stderr_tail`

统一 widget payload 还会保留：

- `timed_out`
- `result_file_present`
- `return_code`
- `command`
- `created_at`
- `started_at`
- `finished_at`

降级行为：

- `structuredContent` 里的业务 JSON 仍然保持可用
- 如果 ChatGPT 侧没有渲染 UI，数据 tool 结果依然可以按 JSON/文本方式使用

推荐调用链：

1. 先调用数据 tool 拿聚合结果
2. 再调用 `render_result_widget`
3. 由 `render_result_widget` 返回 widget 绑定后的结果

这样更符合 Apps SDK 推荐模式：把业务数据获取和 UI 渲染解耦。

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

## `get_result`

名称：

- `get_result`

输入：

- `job_id: string`

输出：

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

何时使用：

- 希望拿到某个 job 的聚合结果
- 不想自己再分别读 `summary.txt`、`stdout.log`、`stderr.log`

读取策略：

1. 先查 job 是否存在
2. 优先读 `artifacts/<job_id>/result.json`
3. 如果 `result.json` 缺失或损坏，则即时聚合并回退返回
4. tool result 保持纯数据返回，不直接绑定 widget

## `get_latest_result`

名称：

- `get_latest_result`

输入：

- 无

输出：

- 与 `get_result(job_id)` 相同
- 额外包含 `resolved_job_id`

何时使用：

- 希望直接拿到最新一个 job 的聚合结果
- 本地任务调试后快速回看最后一次结果

最新 job 判定规则：

1. 优先按 `created_at` 最大排序
2. 若时间相同，再按 `job_id` 排序
3. 若没有任何 job，则返回 `ToolError`
4. tool result 保持纯数据返回，不直接绑定 widget

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

## `run_codex_task`

名称：

- `run_codex_task`

输入：

- `prompt: string`
- `work_dir?: string`
- `timeout_seconds?: integer = 120`
- `poll_interval?: number = 2.0`

输出：

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
- `created_at`
- `started_at`
- `finished_at`
- `metadata`

何时使用：

- 希望一次 MCP tool call 就拿到聚合后的执行结果
- ChatGPT 网页端优先应该调用它，而不是自己手动编排三连

内部行为：

1. 创建 job
2. 轮询直到终态或超时
3. 聚合 `summary.txt`、`stdout.log`、`stderr.log`、`metadata.json`
4. 列出 artifact 目录下的文件名并排序返回
5. 返回聚合后的业务 JSON 和文本结果，不直接绑定 widget

## `render_result_widget`

名称：

- `render_result_widget`

输入：

- `result: ResultWidgetPayload`

输出：

- `structuredContent`
- `content`
- widget result `_meta["resultWidgetPayload"]`
- tool descriptor 上的 widget template 绑定

何时使用：

- 已经拿到了某个 job 的聚合结果
- 希望把统一 payload 渲染成 ChatGPT 内嵌结果面板

职责边界：

1. 不负责查询业务状态
2. 不负责创建 job
3. 只负责把统一 payload 渲染成 widget 结果

## `result.json`

worker 在 job 到达终态后，会额外写：

```text
artifacts/<job_id>/result.json
```

这是一个聚合后的最终结果文件。`get_result` / `get_latest_result` 会优先复用它。

## 本地查看脚本

推荐本地查看方式：

```bash
python3.11 scripts/open_latest_result.py
```

常见用法：

```bash
python3.11 scripts/open_latest_result.py --print-json
python3.11 scripts/open_latest_result.py --work-dir
python3.11 scripts/open_latest_result.py --job-id <job_id>
python3.11 scripts/open_latest_result.py --no-open
```

额外约束：

- `stdout_tail` / `stderr_tail` 取日志尾部摘要，不是头部截断
- 超时时不会抛 tool error，而是返回结构化结果并把 `timed_out=true`
- 真正抛 `ToolError` 的场景只保留给非法输入或内部异常

最短示例：

```python
await session.call_tool(
    "run_codex_task",
    {
        "prompt": "Create exactly one file named demo.txt in the current working directory and summarize the change.",
        "timeout_seconds": 120,
    },
)
```

## 本地最短验证

```bash
python -m compileall bridge_server mcp_server worker scripts
python scripts/dev_up.py
python scripts/mcp_smoke_test.py --timeout-seconds 180
python scripts/dev_down.py
```

当前 smoke test 会额外校验：

- 三个数据 tool 的业务结构化结果仍然存在
- 三个数据 tool 都不再带 widget template 绑定
- `render_result_widget` 复用同一个 widget resource

## 当前限制

- 当前 MCP server 是 no-auth、本地开发用途
- 当前只允许 demo workspace 范围内执行
- 当前 worker 是本地单进程轮询
- `BRIDGE_EMBED_WORKER=true` 只是在 MCP server 进程内嵌一个后台 worker，不改变底层 job 模型
- 当前主要用于验证“ChatGPT -> remote MCP -> 本地 job 系统 -> 本地 Codex”链路
