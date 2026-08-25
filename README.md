# demo-mcp：可扩展的 LLM+MCP 数据对话助手

demo-mcp 是一个 **LLM + MCP** 数据对话助手：你用自然语言提问，内置的智能体会自己决定调用哪个 MCP 工具、从 Tushare 数据代理取数，再总结成中文回答。支持 **CLI 终端** 与 **Web 控制台**（流式输出、思考轨迹、Markdown、会话日志与恢复）。

- **LLM 后端 DeepSeek**（OpenAI 兼容端点，可换）；agent 循环、工具调用、错误处理都在本地。
- **自带 uv 环境**：`uv sync` 生成项目自己的 `.venv`，独立运行、互不干扰。
- **自带 MCP server**：项目内置一个 MCP 数据服务器（`mcp_server/server.py`，把 Tushare 数据代理暴露成 `list_apis / get_api_info / query` 三个工具），连你提供的 `TUSHARE_PROXY_URL` 数据代理取数。
- **可 Docker 化**：`docker compose up` 一键起 Web 控制台。
- **SQLAlchemy 数据层**：每次会话的用户/助手/工具消息与完整对话上下文落到自己的库（`DEMO_DATABASE_URL`，默认 SQLite `demo.db`）。

## 目录与层

```
demo-mcp/
├── pyproject.toml          依赖（运行时 + dev 组）+ uv 镜像
├── pyrightconfig.json      类型检查配置
├── .env / .env.example     项目配置
├── Dockerfile / docker-compose.yml   容器化部署
├── mcp_server/             内置 MCP 数据服务器（把 Tushare 数据代理暴露成 tools）
├── demomcp/
│   ├── interfaces/   契约层：类型 + ToolProvider / LLMClient 两协议（纯契约）
│   ├── agents/       核心层：agent 循环 + 工具注册（只依赖 interfaces）
│   ├── providers/    实现层：可插拔适配器（tools: mcp/fake；llm: deepseek/mock）
│   ├── db/           数据层：SQLAlchemy 2.0 异步，会话历史存取（独立库）
│   ├── config/       配置层：Settings + 项目根 + stdio 参数
│   └── entry/        入口层：CLI / Web（SSE 流式）
├── tests/            离线 gate（FakeToolProvider + MockLLM + 内存 SQLite）
├── scripts/          real 端到端冒烟
└── web/              前端（SSE + Markdown + 侧栏历史会话）
```

依赖方向自上而下：`entry → agents → interfaces`；`providers → interfaces`；`config` 为叶子；`db` 被 entry 使用。

## 前置要求

- Python 3.11+
- [uv](https://docs.astral.sh/uv/)
- 一个 **Tushare 数据代理**，暴露 `GET /api/registry` 与 `POST /api/query`（demo-mcp 内置的 MCP server 经它取数）。把它的地址填到 `TUSHARE_PROXY_URL` 即可。

## 快速开始（本地）

```bash
cd demo-mcp
uv sync                                    # 生成 .venv + uv.lock（首次联网装依赖）
cp .env.example .env                       # 填 DS_API_KEY / TUSHARE_PROXY_URL 等
```

运行：

```bash
# CLI（终端对话）
.venv/Scripts/python.exe -m demomcp.entry.cli        # Windows
# .venv/bin/python -m demomcp.entry.cli              # Linux / macOS

# Web 控制台（SSE 流式；打开 http://127.0.0.1:8010）
.venv/Scripts/python.exe -m uvicorn demomcp.entry.web:app --port 8010
```

示例会话：`贵州茅台最近一个月的日线` —— 助手依次调 `list_apis → get_api_info → query`，最后总结输出，并把每轮消息与完整上下文落到 `demo.db`（可用侧栏查看/恢复）。

## Docker 运行

```bash
cd demo-mcp
cp .env.example .env        # 填 DS_API_KEY / TUSHARE_PROXY_URL 等
docker compose up           # 构建镜像并启动 Web，打开 http://localhost:8010
```

或手动：

```bash
docker build -t demo-mcp .
docker run --rm -p 8010:8010 --env-file .env demo-mcp
```

> 容器内置 MCP server；只需外部 `TUSHARE_PROXY_URL` 代理可达即可取数。会话历史库通过卷 `demo_data` 持久化到 `/app/data/demo.db`。

## 配置（.env）

| 变量 | 默认 | 说明 |
|---|---|---|
| `DS_API_KEY` | — | DeepSeek API key（必填） |
| `DS_BASE_URL` | `https://api.deepseek.com` | DeepSeek 端点（网关/自定义部署时改） |
| `DS_MODEL` | `deepseek-chat` | 模型 id（推理类可换能出 `reasoning_content` 的） |
| `DS_STREAMING` | `true` | 是否流式 |
| `DS_MAX_TOKENS` | `8192` | 单次回复最大 token |
| `DEMO_MAX_ITERATIONS` | `10` | 一次提问内 agent 最大循环轮数 |
| `DEMO_DATABASE_URL` | 空→`demo.db` | 会话历史库；也支持 `mysql/asyncmy`、`postgres/asyncpg` |
| `MCP_PYTHON` | 空→当前解释器 | 运行内置 MCP server 的 python（默认 demo-mcp 自己的 .venv，含 mcp+httpx） |
| `MCP_SERVER_PATH` | 空→`mcp_server/server.py` | MCP 服务器脚本；留空用内置的 |
| `MCP_ARGS` | `[]` | 追加给 MCP server 的参数（JSON 数组） |
| `DEMO_MCP_TIMEOUT` | `30` | 单次 MCP 工具调用读超时（秒） |
| `DEMO_MCP_RETRIES` | `2` | 工具调用重试次数（只重试抛出的异常，业务 `is_error` 不重试） |
| `TUSHARE_PROXY_URL` | `http://127.0.0.1:8000` | 你的 Tushare 数据代理地址 |
| `TUSHARE_API_KEY` | — | 数据代理分发的 api_key（`query` 需；`list_apis/get_api_info` 无需；空则取数友好报错） |
| `TUSHARE_PROXY_TIMEOUT` | `20` | 数据代理单次请求超时（秒） |

> 配置只读 `demo-mcp/.env`（pydantic-settings）。不要提交 `.env`（已被 `.gitignore` 忽略）。

## Web 端功能（`/chat` 为 SSE 流式）

- **流式输出**：`thinking`（模型 reasoning_content，若有）/ `text`（内容增量）/ `tool`（工具调用轨迹）/ `done` / `error` 事件。
- **思考轨迹**：工具调用以 chip 展示（✔/✖ + 名称）；`reasoning_content` 渲染为折叠斜体块。
- **Markdown**：助手/用户消息用 `marked` + `DOMPurify`（在 `web/vendor/`，离线可用）渲染。
- **日志/恢复**：侧栏列出历史会话（`GET /api/sessions`），点开看消息（`GET /api/sessions/{id}`）并可**继续**（服务端用完整上下文恢复）；也可删除（`DELETE /api/sessions/{id}`）。出错时工具 `is_error` 与 agent 异常以 `role=tool/error` 落库，侧栏可定位。

## 离线测试（无需代理 / key / 网络）

```bash
cd demo-mcp
.venv/Scripts/python.exe -m pytest tests -q       # 或 .venv/bin/python
```

覆盖：agent 循环全链路（`FakeToolProvider` + `MockLLM`）、`code!=0` 作为正常结果、工具异常转 `is_error`、迭代上限、MCP 重试/超时；配置层（默认值/路径/stdio 参数/DB URL）；数据层（内存 SQLite：日志 + `ChatTurn` 恢复往返）。

## 真实端到端冒烟（可选）

需数据代理在跑 + 真 `TUSHARE_API_KEY` + 真 `DS_API_KEY`：

```bash
.venv/Scripts/python.exe scripts/smoke_e2e.py
```

## 静态检查

```bash
cd demo-mcp
.venv/Scripts/ruff.exe check demomcp tests scripts
PYRIGHT_PYTHON_NODE_VERSION=22.23.2 .venv/Scripts/pyright
```

## 扩展：层 = 扩展点

| 层 | 职责 | 扩展方式 |
|---|---|---|
| `entry` | 启动方式（CLI / Web / 批量 / HTTP API） | 加一个入口文件 |
| `agents` | 编排循环、工具调度 | 改 `agent.py` 或 `registry.py` |
| `interfaces` | 类型 + 两协议 | 改协议即改所有适配器（谨慎） |
| `providers/tools` | 具体工具来源（MCP / 假 / 自有） | 加 `xxx.py` 或组合多个来源 |
| `providers/llm` | 具体 LLM 后端 | 加 `xxx.py` 实现 `interfaces.llm_client` |
| `db` | 会话历史持久化 | 加模型 / 扩展 `store.py` |
| `config` | 环境变量、路径、透传参数 | 加字段即可 |
