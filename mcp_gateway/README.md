# MCP 网关（`mcp_gateway/`）

**独立进程、独立端口（默认 `:8766`）、独立配置、独立部署生命周期**的服务：它是整个系统里**唯一持有上游数据源凭证与连接**的进程，把当前启用的源的工具聚合起来，对外说**真正的 MCP 协议**（streamable-http `/mcp`），另挂一个管理 REST（`/admin/*`）。

`demomcp`（主应用）只是它的一个**纯 MCP 客户端**——启动 demo 不会带起任何上游连接；停掉网关，demo 照常活着（对话退化为纯 LLM）；停掉 demo，网关照常对外服务。两者故意**不设 `depends_on`**，靠 demo 侧的退避重连收敛。

> 想改「连哪些源」，改这里；别去 `demomcp/` 里找——那边已经删掉了所有上游源字段。

---

## 1. 它做什么

```
demomcp / 任意 MCP 客户端
        │  MCP（streamable-http）
        ▼
   mcp-gateway :8766
   ├── /mcp        真 MCP 端点（list_tools / call_tool，每次现查「当前启用的源」）
   └── /admin/*    管理 REST（按源开关 + 健康度）
        │
   ┌────┴─────┬──────────┬───────────┬─────────────┐
   ▼          ▼          ▼           ▼             ▼
 tushare    wind       ifind      akshare     china_news
 官方 MCP   7 域       7 域        独立进程     独立进程
 247 工具   3 元工具    3 元工具     9 工具       2 工具
           (内部 35)  (内部 32)
```

- **按源开关**：`POST /admin/sources/{id}`，持久化在 `<GATEWAY_DATA_DIR>/sources.json`。生效点是 `/mcp` 的 handler **每次调用现查**，所以改开关**无需重启网关**。
- **动态聚合**：`list_tools` 并发拉取各已启用源的清单再合并；单源不可用只是本轮跳过，不拖垮整体。
- **没有自动降级**：路由是「工具名 → 唯一源」，各源命名互不重叠（`wind_*` / `ifind_*` 带前缀，Tushare 与免费源用各自原生名），所以「同名工具换源重试」这回事不存在。跨源取舍发生在 agent 选工具那一步（某源报无权限 → LLM 改选另一源的等价接口），由 `demomcp` 的提示词与 `graph/tool_select.py` 负责。

---

## 2. 快速开始

```bash
# 1) 配置（唯一配置处）
cp mcp_gateway/.env.example mcp_gateway/.env
#    至少填 TUSHARE_MCP_URL=https://api.tushare.pro/mcp/?token=<你的 token>
#    其余源留空即不注册

# 2) 起（在仓库根目录执行）
uv run uvicorn mcp_gateway.app:app --port 8766
#    Windows 一键（网关带自动重启 + Web）：.\scripts\dev_up.ps1
#    只起网关：.\scripts\dev_up.ps1 -GatewayOnly

# 3) 验证
curl http://127.0.0.1:8766/admin/health
curl http://127.0.0.1:8766/admin/sources
```

容器：`docker compose up` 会同时起 `demo`（8010）与 `mcp-gateway`（8766）两个独立容器。
`mcp_gateway/.env` 是 compose 的 `env_file`，**缺了会直接报 `env file not found`**。
`docker compose stop mcp-gateway` 只停网关。

---

## 3. 配置（`mcp_gateway/.env`，唯一配置处）

只读 `mcp_gateway/.env`，**不回退**读仓库根的 `.env`——「独立成一个项目」连配置边界也独立。

| 变量 | 默认 | 说明 |
|---|---|---|
| `GATEWAY_HOST` / `GATEWAY_PORT` | `0.0.0.0` / `8766` | 监听地址（uvicorn 命令行的 `--host/--port` 优先级更高） |
| `GATEWAY_DATA_DIR` | `mcp_gateway/data` | `sources.json`（按源开关）等运行态数据 |
| `TUSHARE_MCP_URL` | 空 | Tushare 官方 MCP，**token 放 URL query** |
| `WIND_API_KEY` / `WIND_ENABLED` | 空 / `true` | 万得 Wind（鉴权头 `Bearer <key>`） |
| `IFIND_AUTH_TOKEN` / `IFIND_ENABLED` | 空 / `true` | 同花顺 iFind（鉴权头是**裸 token，没有 `Bearer ` 前缀**） |
| `IFIND_CONCURRENCY` | `2` | iFind 并发上限，受**套餐硬限**：免费 2 / 个人 5 / 企业 10。超限远端直接拒，别乱调大 |
| `AKSHARE_MCP_URL` | 空 | AkShare 免费源的 MCP URL（独立进程，见 `external_sources/`） |
| `CHINA_NEWS_MCP_URL` | 空 | 财经新闻免费源的 MCP URL（独立进程） |
| `MCP_TIMEOUT` / `MCP_RETRIES` / `MCP_KEEPALIVE` | `30` / `2` / `45` | 每个上游连接的读超时 / 重试 / 保活 ping 间隔（0 = 关保活） |

**留空 = 该源不注册**：不会出现在 `/admin/sources` 里，不留一个永远连不上的开关项。

**配置没配好不崩进程**：`restart: unless-stopped` 下崩进程会变重启风暴，而且 admin API 必须活着才能让人从页面上看到缺什么。所以 `GatewaySettings.config_problems()` 把问题讲成人话，同时出现在：启动 ERROR 日志、`/admin/health` 的 `config_problems`、前端「设置」页。

---

## 4. API

### 4.1 `/mcp` —— 真 MCP 端点

低层 `mcp.server.lowlevel.Server` + `StreamableHTTPSessionManager(stateless=True)`。任何 MCP 客户端都能连：

```python
from demomcp.providers.tools.mcp import mcp_tool_provider, gateway_business_error

async with mcp_tool_provider(
    "http://127.0.0.1:8766/mcp",
    business_error=gateway_business_error,   # 见 §6，必传
) as tools:
    specs = await tools.list_tools()
    result = await tools.call_tool("daily", {"ts_code": "600519.SH", "trade_date": "20260911"})
```

### 4.2 `/admin/*` —— 管理 REST

| 端点 | 返回 |
|---|---|
| `GET /admin/sources` | `[{id, display_name, enabled, connected, tool_count}]` |
| `POST /admin/sources/{id}` body `{"enabled": bool}` | 该源的最新状态；未知 id → 404 |
| `GET /admin/health` | `{ok, config_problems[], sources[]}`；`ok` = 配置无问题 **且** 每个已启用的源都已连接 |

`demomcp` 的 `GET|POST /api/settings/mcp/sources` 就是转发到这里（前端「设置 → 数据源明细」）。

---

## 5. 代码结构

| 文件 | 职责 |
|---|---|
| `app.py` | ASGI 顶层组装：lifespan 读配置 → `build_sources()` → `GatewayToolProvider` → session manager；`/mcp` 与 `/admin/*` 同端口 |
| `config.py` | `GatewaySettings`（只读 `mcp_gateway/.env`）+ `config_problems()` |
| `sources.py` | 源注册表：`build_sources()` 按配置组出 `SourceDef{id, display_name, connect}` |
| `pool.py` | `SourcePool`（单源连接生命周期）+ `GatewayToolProvider`（跨源聚合 + `status()` / `set_enabled()`） |
| `toggle_store.py` | 按源开关持久化（原子写 `sources.json`） |
| `mcp_endpoint.py` | 把 `GatewayToolProvider` 包成真 MCP server |
| `admin.py` | `/admin` REST 路由 |
| `providers/multi_domain.py` | **通用外壳**：「一个数据商 = N 个 MCP 端点」的懒连接 / 单域隔离 / 60s 冷却 / 目录单飞 / 懒发现三件套 |
| `providers/ifind.py` | iFind 特有部分：7 域表、裸 token 头、并发上限、`code:1` 成功信封判据 |

**扩展新源 = 只在 `build_sources()` 里加一条**：`pool.py` / `admin.py` / `mcp_endpoint.py` 全部按 source id 泛型处理，不需要跟着改。

**复用而非重写**：Tushare 与免费源直接用 `demomcp/providers/tools/mcp.py::mcp_tool_provider`，万得用 `demomcp/providers/tools/wind.py::WindToolProvider`（有测试锁定其行为，故**不动它**，只把同一套模式泛化成 `multi_domain.py` 给新源用）。这些模块只依赖 `demomcp` 的**纯契约**（`interfaces.types`）与 MCP 客户端机器，不反向依赖 `agents/graph/entry` 任何一层。

---

## 6. 关键坑

### 6.1 接新源先确认它的「成功码」

各源的成功约定互相矛盾，而聚合后它们流在**同一条**连接上：

| 源 | 成功信封 |
|---|---|
| Tushare | `{"code": 0, ...}`（也常见直接返回裸数组） |
| **iFind** | **`{"code": 1, "msg": "success", ...}`——与 Tushare 恰好相反** |
| Wind | 无 `code` 字段即成功 |
| 免费源 | 无 `code` 约定，失败是 `{"error": "..."}` |

`MCPToolProvider` 默认判据是 Tushare 约定（`code != 0` 即业务失败），并且**业务失败也会重试**。沿用默认判据接 iFind 的后果：**每一次成功调用都被当失败重试一遍**——不报错、只是慢一倍且烧配额，极难发现。实测同一次调用（输出逐字节相同）默认判据 1.97s → 聚合判据 0.82s。

两跳都要给判据：

- 网关 → iFind 那一跳：`IfindToolProvider` 传 `business_error=ifind_business_error`；
- demomcp → 网关那一跳：客户端传 `business_error=gateway_business_error`（`code` 不属于 `{0, 1}` 才算失败）。三处构造点（`entry/web.py` / `entry/cli.py` / `scripts/generate_quickreport.py`）都已传。

**接新源时若它的成功码不是 0/1，记得加进 `gateway_business_error` 的白名单。**

### 6.2 跨源撞名 = 先声明的源赢 + 告警

`pool.list_tools` 对同名工具只保留 `build_sources()` 里先声明那条并丢掉重复的，同时打 WARNING。两件事都必要：同名不去重会让 `call_tool` 的路由随写入顺序漂，且重复的 tool 定义塞进 `llm.chat(tools=…)` 是非法载荷。目前 5 个源恰好不撞名——**看到这条告警就说明某源改了命名，去 `sources.py` 给它加前缀，别指望 pool 兜住。**

### 6.3 `call_tool` 必须返回 `types.CallToolResult`

`mcp_endpoint.py` 的 handler 返回裸 list 会被 SDK 恒置 `isError=False`，业务错就丢了。

### 6.4 iFind 的四个差异（抄万得会全踩）

1. **鉴权头是裸 token**：`Authorization: <token>`，**没有** `Bearer ` 前缀（万得是有的）。抄串了会全域 401。标准 TLS 校验即可连通——参考实现里的 `verify=False` 是多余的，别跟着抄。
2. **URL 里 `global_stock` 域写作连字符** `hexin-ifind-ds-global-stock-mcp`（域名本身是下划线，唯一一处不一致，写错 404）。
3. **并发受套餐硬限**（免费 2 / 个人 5 / 企业 10），`IFIND_CONCURRENCY` 默认取最保守的 2，超限远端直接拒——那个 Semaphore 是必需的而非优化。
4. **成功是 `code:1`**（见 §6.1）。

另外两条返回语义别搞反：**查不到数据仍是 `code:1/success`**，提示语在 `data.answer` 里（正常返回，要让 LLM 如实转述，不能翻成错误）；而参数缺失/类型错这类硬错误远端会**挂住直到超时**、不返回错误信封（由 `mcp.py` 的超时 + 重试兜成 `is_error=True`）。`{"error": …}` 那个形状是参考实现自己代理层加的，iFind 远端从不返回。

iFind 接口**只吃一个自然语言 `query` 串**（`"科大讯飞2025年三季度的ROE"`），不是结构化字段。`data` 是**双重编码的 JSON 串**（里层 `{"answer": "<markdown 表格>"}`），前端表格解析器认不出 → 安全退化成 `<pre>` 原文，不是 bug。

### 6.5 刻意不硬编码接口清单

万得/iFind 对外只暴露 3 个懒发现元工具（`*_list_apis` / `*_get_api_info` / `*_query`），具体接口只进内部目录、经元工具按需查询——既避免把几十个 schema 塞进每轮 `llm.chat(tools=…)` 的载荷，也让清单自动跟着远端走。参考实现把 31 个接口写死成 wrapper，已与远端漂移（暴露了已下线的 `search_funds` 等，又缺了新增的 `get_stock_performance` 与四个 `*_highfreq_quotes`）。

**M2 之后具体 Wind 工具必须经 `wind_query(api_name=…)` 调用**，裸名 `wind_get_financial_news` 会 `Unknown tool`。

### 6.6 免费源是「独立进程 + HTTP」，网关不负责拉起

AkShare / 财经新闻是 `import akshare` 的**本地库型** FastMCP server，自带 `requirements.txt`、不进主仓库 `pyproject.toml`。网关只经 URL 当普通 MCP 客户端连它们，**与连 Tushare 官方 MCP 是同一条代码路径，没有特殊分支**。它们没起来就是该源连不上，网关与 demomcp 照常工作。

它们的工具名**没有前缀**，且**代码格式是 6 位裸代码**（`600519`，不带 `.SH`）。细节与已知反爬坑见 `external_sources/README.md`。

### 6.7 `WIND_API_KEY` 这类环境变量不是工具名

（这条坑在 `demomcp/graph/skill_loader.py` 侧，但与网关的命名约定直接相关）工具名扫描**必须区分大小写**——工具名一律小写、环境变量一律大写。不做这层会翻出 `wind_query(api_name="api_key")` 之类根本不存在的接口喂给 LLM。

---

## 7. 连接池语义

`pool.py` 是 `demomcp/entry/web.py` 那套池生命周期按 source_id 泛化的一份：

- **懒建 + 双检锁**：绝不重复建连。
- **热启动**：`start()` 后逐源 `hot_start()` 退避重试（5s → 60s 封顶）直到成功，互不阻塞——远端启动时不可用也能无流量自愈。`enabled=false` 的源不启动，等手动打开再连。
- **周期重建**（`RECYCLE_INTERVAL = 1800s`）：最后一道自愈网；关闭期间跳过，不偷偷重连。
- **开关切换即摘池**：关掉时把池标记退休并放进后台，**等在途请求归零**再真正关闭；打开时后台 `_reconnect_once()` + `warm_up()`。
- **单源故障隔离**：`list_tools` / `call_tool` 对单源异常只记 WARNING 并跳过/转 `is_error`，永不抛穿。
- 各源有**独立的 `SourcePool._lock`**，`list_tools` 并发拉取——避免某源恰好冷连接时拖慢其它已就绪源。

底层 `MCPToolProvider`（owned 模式）还带保活与断线重建：每 `MCP_KEEPALIVE` 秒 `send_ping()`；传输层失败（httpx / OSError / 超时 / `McpError -32000|408`）→ 作废会话并单飞重建；协议级错误（如 `-32601` 服务器没实现 ping）不重建，防重启风暴；业务 `code!=0` 永不重建。

---

## 8. 测试

```bash
# 在仓库根目录
.venv/Scripts/python.exe -m pytest tests -q -k mcp_gateway
.venv/Scripts/python.exe -m pytest tests/test_ifind_provider.py tests/test_wind_provider.py -q
.venv/Scripts/ruff.exe check mcp_gateway
```

网关相关测试：`test_mcp_gateway_{admin,pool,protocol,proxy_web,sources,toggle_store}.py`（真 MCP 协议往返、按源开关、跨源撞名、池退休、demomcp 侧转发）、`test_ifind_provider.py`、`test_wind_provider.py`、`test_mcp_retry.py`（含 `gateway_business_error` 的判据锁定）。

全部离线，无需 key / 网络。
