# CLAUDE.md — `mcp_gateway/`

目录级作用域说明，补充仓库根 `CLAUDE.md`（根文件仍然适用；本文只讲在这个目录里改代码要先知道的事）。
面向用户/运维的说明在同目录 [`README.md`](README.md)，本文不重复。

## 这是什么

**独立进程、独立端口（`:8766`）、独立配置（只读 `mcp_gateway/.env`）、独立部署生命周期**的 MCP 网关。
它是整个系统里**唯一持有上游数据源凭证与连接**的服务：按源开关 + 动态聚合五个上游源，对外说真 MCP 协议（`/mcp`）+ 一个管理 REST（`/admin/*`）。

`demomcp` 侧**已删除** `tushare_mcp_url` / `wind_api_key` / `wind_enabled` / `wind_configured` / `effective_system_prompt` 与 `wind.agent_tool_provider`——多源装配是这里的职责。**要改「连哪些源」，改这里，别在 `demomcp/` 里找。**

与 `mcp_server/`（`:8765`，本地 Tushare 代理，历史遗留后备通道，profile 门控默认停用）是**两个不同的东西**，别混淆。

## 常用命令（在仓库根目录执行）

```bash
uv run uvicorn mcp_gateway.app:app --port 8766          # 起网关
.\scripts\dev_up.ps1 -GatewayOnly                       # Windows：带自动重启地起网关

.venv/Scripts/python.exe -m pytest tests -q -k mcp_gateway
.venv/Scripts/python.exe -m pytest tests/test_ifind_provider.py tests/test_mcp_retry.py -q
.venv/Scripts/ruff.exe check mcp_gateway                # 门禁之一（全仓 ruff 必须干净）
```

## 文件职责与改动边界

| 文件 | 职责 | 改它意味着 |
|---|---|---|
| `app.py` | ASGI 组装 + lifespan（配置 → 源 → 池 → session manager） | 改启动顺序/挂载点 |
| `config.py` | `GatewaySettings` + `config_problems()` | 加配置项**必须**同步 `.env.example`；加新源还要加进 `any_source_configured` |
| `sources.py` | `build_sources()` 源注册表 | **接新源只改这里**（其余按 id 泛型处理） |
| `pool.py` | `SourcePool` 生命周期 + `GatewayToolProvider` 跨源聚合 | 多源合并逻辑只在这里（不是 `providers/tools/composite.py`） |
| `toggle_store.py` | `sources.json` 原子写 | — |
| `mcp_endpoint.py` | 低层 MCP server 包装 | 返回类型见下方坑 §3 |
| `admin.py` | `/admin` REST | 前端「设置」页与 `demomcp` 的 `/api/settings/mcp/sources` 依赖其形状 |
| `providers/multi_domain.py` | 「一个数据商 = N 个 MCP 端点」的通用外壳 | 新的多域源复用它 |
| `providers/ifind.py` | iFind 特有：域表 / 裸 token / 并发上限 / `code:1` 判据 | — |

**依赖方向**：本包只依赖 `demomcp` 的**纯契约**（`interfaces.types` 的 `ToolSpec`/`ToolResult`、`interfaces.tool_provider`）与 MCP 客户端机器（`providers.tools.{mcp,wind}`），**不反向依赖** `agents` / `graph` / `entry` 任何一层。新写的源实现一律落在 `mcp_gateway/providers/` 下，不要再往 `demomcp/providers/tools/` 堆。

**`demomcp/providers/tools/wind.py` 不要动**：有测试锁定其实现细节。需要同一套模式时用已经泛化好的 `providers/multi_domain.py`。

## 接新源的固定流程

1. 在 `config.py` 加字段 + `*_configured` 属性，并加进 `any_source_configured` 与 `config_problems()`；
2. 在 `.env.example` 加对应行（留空 = 不注册的语义要写清）；
3. 在 `sources.py::build_sources()` 加一条 `SourceDef`；
4. **确认它的成功码**（见下方坑 §1）——不是 `code:0` 就必须传 `business_error`；不是 `0/1` 还要加进 `gateway_business_error` 白名单；
5. **确认它的工具命名**——与现有源撞名就给它加前缀（见坑 §2）；
6. 如果它的参数风格与 Tushare 不同（自然语言 query、裸 6 位代码…），去 `demomcp/config/settings.py` 加一段 `*_USAGE_GUIDE`，并在 `agents/agent.py::base_system_for` 里按「本轮工具清单里有没有它的工具」注入；`tests/test_source_usage_guides.py` 锁住每一处注入点。

## 关键坑（改了会踩）

### 1. 各源的「成功码」互相矛盾，而它们流在同一条连接上

| 源 | 成功信封 |
|---|---|
| Tushare | `{"code": 0, …}`（也常见裸数组） |
| **iFind** | **`{"code": 1, "msg": "success", …}`——与 Tushare 恰好相反** |
| Wind | 无 `code` 字段即成功 |
| 免费源 | 无 `code` 约定，失败是 `{"error": …}` |

`MCPToolProvider` 默认判据是 Tushare 约定（`code != 0` 即业务失败）**且业务失败也会重试**。沿用默认判据接 iFind = **每次成功调用都被静默重试一遍**（不报错、只是慢一倍且烧掉本就只有 2 的并发配额，极难发现）。实测同一次调用、输出逐字节相同：1.97s → 0.82s。

**两跳都要给判据**：网关 → iFind 用 `ifind_business_error`；`demomcp` → 网关用 `gateway_business_error`（`code ∉ {0,1}` 才算失败），三处构造点 `entry/web.py` / `entry/cli.py` / `scripts/generate_quickreport.py` 都已传。

刻意**不改** `_is_business_error`：它被 `tests/test_mcp_retry.py` 用 `{"code":1,"msg":"参数缺失"}` 当业务失败的标准样例锁定，而在 Tushare 语义下那是对的。同一段文本的含义取决于它来自哪个源——这里是「多一个判据」而不是「改判据」。

### 2. 跨源撞名 = 先声明的源赢 + WARNING

`pool.list_tools` 对同名工具只保留 `build_sources()` 里先声明那条并丢掉重复的。两件事都必要：同名不去重会让 `call_tool` 的路由随写入顺序漂；重复的 tool 定义塞进 `llm.chat(tools=…)` 是非法载荷。目前 5 个源恰好不撞名。**看到这条 WARNING 就说明某源改了命名，去 `sources.py` 给它加前缀，别指望 pool 兜住。**

### 3. `call_tool` 必须返回 `types.CallToolResult(isError=…)`

返回裸 list 会被 SDK 恒置 `isError=False`，业务错就丢了。

### 4. 配置没配好绝不崩进程

`restart: unless-stopped` 下崩进程 = 重启风暴，而且 admin API 必须活着才能让人从页面上看到缺什么。所以 `config_problems()` 返回人话列表，同时出现在启动 ERROR 日志 / `/admin/health` / 前端「设置」页。**不要**改成抛异常或 `sys.exit`。

### 5. 开关生效点是 handler 每次现查

`mcp_endpoint.py` 的 `list_tools` / `call_tool` 是每次调用现查 `GatewayToolProvider`（内部按「当前已启用的源」聚合）。这是「改开关无需重启网关」的全部原因——**不要**把结果缓存到模块级或 lifespan 级。

### 6. `mcp` 钉 `>=1.28,<2`

`ClientSession.call_tool` 的 `read_timeout_seconds` 是 **`timedelta`，不是秒**。

### 7. iFind 的四个差异（抄万得会全踩）

裸 token（无 `Bearer `）/ `global_stock` 域 URL 写作连字符 / 并发套餐硬限（默认 2）/ `code:1` 成功。
另：**查不到数据仍是 `code:1/success`**，提示在 `data.answer` 里（正常返回，要让 LLM 如实转述）；参数硬错误远端**挂住直到超时**、不返回错误信封。`{"error": …}` 是参考实现代理层的形状，远端从不返回。

### 8. 免费源没起来不是 bug

网关**不负责拉起** `external_sources/` 的两个进程，它只当普通 MCP 客户端连 URL——与连 Tushare 官方 MCP 是同一条代码路径，没有特殊分支。它们没起来就是该源连不上，网关与 demomcp 照常工作。

### 9. 池的语义别简化

懒建双检锁 / 热启动退避（5s→60s）/ 30min 周期重建 / 开关关闭时**等在途请求归零再关**——每一条都对应过真实故障。单源异常一律 WARNING + 跳过或转 `is_error`，永不抛穿到 MCP 协议层。

## 实测基线（2026-09-08）

- 工具面：Tushare 247 + Wind 3 元工具（内部编目 35）+ iFind 3 元工具（内部编目 32，7/7 域连通）+ AkShare 9 + 财经新闻 2。
- Tushare 官方 MCP **无** `list_apis`/`get_api_info`/`query` meta 三件套（那是内置 `mcp_server/` 的东西）。
- 官方 MCP 权限矩阵、快报可用接口等细节见仓库根 `CLAUDE.md` 的「快报关键坑」一节。
