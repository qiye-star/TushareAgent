# 工具调用层设计：限定「比亚迪 / 宁德时代」双票数据助手

> 本文档为 **设计文档**，描述 demo-mcp 新增的**客户端语义工具调用层**。它把底层 MCP 取数能力打包成一组**针对两家上市公司**的高级语义工具，并在其上加**参数校验与容错**（代码自动补全、日期格式容错等）。本文是前瞻设计，**不含实现代码**。
>
> 需求来源为 `docs/` 下的两份材料：架构文档 `ARCHITECTURE.md` 与命题 PDF。本文聚焦「工具调用层」，沿用 `ARCHITECTURE.md` 定义的分层（新增 `graph`/`rag` 等为另一演进线，此处不复述）。

---

## 1. 概述与范围

### 1.1 定位

在现有「LLM→ `ToolProvider` → MCP 取数」链路上，插入一层**语义化的工具面**：LLM 不再面对通用的 `list_apis / get_api_info / query`（那会让它想调哪个接口调哪个、想查哪只股票查哪只），而是面对一组**按业务意图命名、参数已被校验、输出已归一化**的工具。这层在 demo-mcp 进程内作为新的 `ToolProvider` 实现。

### 1.2 限定范围（硬约束）

- **上市公司硬允许清单**：仅 **比亚迪（`002594.SZ`）** 与 **宁德时代（`300750.SZ`）**。任何其它股票代码/名称都会被拒绝并给出友好说明。
- **语义工具面**：只向 LLM 暴露这组精选工具，**隐藏**底层通用工具，让「只查这两家」成为不可绕过的约束（否则 LLM 可直接 `query(api_name="daily", params={ts_code:"any"})` 绕过 allowlist）。

### 1.3 能力清单

| 能力 | 语义工具 | 覆盖 |
|---|---|---|
| 标的列举 | `stock_available` | 列出允许的两家 |
| 实时/最新行情（价格、当日涨跌、PE、PB、市值） | `stock_realtime_quote` | `realtime_quote`；实时不可用回落 `daily`+`daily_basic` |
| 区间行情（区间涨跌幅、首尾、极值、量额） | `stock_price_range` | `daily`+`adj_factor`（前/后复权）+ 可选 `daily_basic` |
| 核心财务指标（营收、归母净利、毛利率、资产负债率） | `stock_financials` | `income` + `fina_indicator` |
| 参数校验与容错 | `StockInputValidator` | 代码自动补全、日期格式容错、复权/期次归一化、错误约定 |

---

## 2. 分层与现有层衔接

### 2.1 分层图

```
entry  cli.py / web.py（SSE）
   │  在 async with <底层 mcp 连接> as mcp: 里构造 Agent(tools=StockToolProvider(mcp))
   ▼
Agent  （demomcp/agents/agent.py，手动 loop，只依赖 ToolProvider）
   │  list_tools() / call_tool(name, args)
   ▼
StockToolProvider（新增：客户端语义工具层）   ← ★ 本文焦点
   │  ✅ 代码解析（硬 allowlist）   ✅ 日期/复权/期次归一化   ✅ 输出归一化
   │  list_tools() 只吐语义工具；call_tool() 把语义调用翻译成底层接口
   ▼
MCPToolProvider（providers/tools/mcp.py，现有，复用其重试/超时/错误语义）
   ▼
mcp_server / Tushare MCP（官方端点 TUSHARE_MCP_URL 或本地代理）
   ▼
Tushare 数据
```

### 2.2 为什么是「客户端语义层 + 包裹式」而不是服务端限制

1. **扩展点是 `ToolProvider` 协议**（`interfaces/tool_provider.py`，结构化 Protocol，无需继承），天然是放「策略/便利」的位置。
2. **`mcp_server/server.py` 是刻意通用且解耦**（只依赖 mcp+httpx，把所有接口、所有股票看成数据），硬编码「只查这两家」会破坏其通用代理角色。
3. **可真正闭环 allowlist**：语义层 `list_tools()` 只返回精选工具、`call_tool()` 内部翻译到底层 `query`，通用工具不暴露给 LLM → 无法绕过。这是服务端受限做不到的「从工具面上藏掉」。
4. **归一化/校验发生在参数产出处**：代码解析、日期归一化属于「请求成形」问题，就近放在 provider 里，让 MCP 层保持机械。

### 2.3 注入点与可扩展性

现有两条入口都在 `async with mcp_tool_provider(url, timeout, retries) as tools:` 内构造 `Agent(llm, tools, config)`（`entry/cli.py`、`entry/web.py` 的 `_run_agent`）。语义层通过**包裹**该 `tools`（即底层 MCP provider）来实现：

```python
async with mcp_tool_provider(settings.tushare_mcp_url,
                             timeout=settings.mcp_timeout,
                             retries=settings.mcp_retries) as mcp:
    tools = StockToolProvider(underlying=mcp, config=settings)  # 语义层包裹底层
    agent = Agent(llm=llm, tools=tools, config=settings)
```

- 若未来要同时保留通用工具 + 语义工具，可用 `CompositeToolProvider([stock_provider, mcp])`（注意 `setdefault` 语义 + 顺序）。
- **`Agent` 构造时即固定 provider**（无 per-request 换）；`web.py` 每个 `/chat` 请求建一次，`cli.py` 每次 REPL 建一次。

### 2.4 系统提示词随之调整

底层通用场景的 `DEFAULT_SYSTEM_PROMPT`（`config/settings.py`）现在描述「先 list_apis 再 query、字段传 ts_code/日期 YYYYMMDD/code!=0 如实转述」。切换到语义工具面后应同步改写为：**只可用这组语义工具、标的仅两家、日期支持相对/多种格式、复权默认前复权、财务期次如何表达**。设计上新增/调整系统提示词，仍走 `system_prompt`（`DEMO_SYSTEM_PROMPT`）配置项。

---

## 3. 语义工具集

### 3.0 总览

| 工具 | 入参 | 产出 | 底层接口 |
|---|---|---|---|
| `stock_available()` | 无 | 允许的两家 | （本地常量） |
| `stock_realtime_quote(stock)` | `stock` | 最新价/当日涨跌/PE/PB/市值 | `realtime_quote`（回落 `daily`+`daily_basic`） |
| `stock_price_range(stock, start, end, adj="qfq")` | `stock`,`start`,`end`,`adj` | 区间涨跌幅/首尾/极值/量额 | `daily`+`adj_factor`(+`daily_basic`) |
| `stock_financials(stock, period)` | `stock`,`period` | 各期 营收/归母净利/毛利率/资产负债率 | `income`+`fina_indicator` |

> 说明：`stock` 接受「公司名 / 别名 / 代码 / 部分」任意形式，经 `StockInputValidator.resolve_stock` 归一到 `ts_code`（见第 4 节）。

### 3.1 `stock_available()`

- **入参**：无。
- **产出**：允许的上市标清单，供 LLM 知道「能查谁」。
- **实现**：返回常量表：`[{"ts_code":"002594.SZ","name":"比亚迪","aliases":["BYD","002594","byd"]}, {"ts_code":"300750.SZ","name":"宁德时代","aliases":["CATL","300750","catl"]}]`。
- **作用**：让 LLM 明确可用标的，并在系统提示词不足时自举。

### 3.2 `stock_realtime_quote(stock)`

- **签名**：`stock_realtime_quote(stock: str)`
- **入参校验**：`resolve_stock`（硬 allowlist）。
- **底层接口**：优先 `realtime_quote`（`params={ts_code}`）；若实时数据不可用/返回空（需交易时段或更高积分），**回落** `daily`（取最近一个交易日的 `close/trade_date/pct_change`）+ `daily_basic`（同一交易日的 `pe_ttm/pb/total_mv`）。
- **产出字段**：

| 字段 | 说明 | 来源 |
|---|---|---|
| `ts_code` / `name` | 标的 | — |
| `trade_date` | 数据日期 | `realtime_quote` 或 `daily.trade_date` |
| `price` | 最新价/收盘价 | `realtime_quote.price` 或 `daily.close` |
| `pct_change` | 当日涨跌幅 % | `realtime_quote` 或 `daily.pct_change` |
| `pe_ttm` / `pe` | 市盈率（TTM / 静态） | `realtime_quote` 或 `daily_basic` |
| `pb` | 市净率 | `realtime_quote` 或 `daily_basic` |
| `total_mv` | 总市值（万元） | `realtime_quote` 或 `daily_basic.total_mv` |
| `volume` / `amount` | 成交量 / 成交额 | `realtime_quote` 或 `daily` |

- **错误/信息**：若实时与 EOD 都取不到 → 返回 `ok=false` + 友好说明（是该接口积分不足还是无数据），`is_error=False` 让 LLM 如实转述。

### 3.3 `stock_price_range(stock, start=None, end=None, adj="qfq")`

- **签名**：`stock_price_range(stock, start=None, end=None, adj="qfq")`
- **入参校验**：`resolve_stock`；`start/end` 经 `normalize_date`（缺省 `start`=区间前一日、`end`=最近交易日→默认近一年）；`adj` 经 `normalize_adj`。
- **底层接口**：
  1. `adj_factor(ts_code, start, end)` → 复权因子；
  2. `daily(ts_code, start, end)` → 区间 OHLC/量额；
  3. 按 `adj` 对收盘做复权：`qfq`（默认）用 `close * adj_factor / 区间最新 adj_factor`，`hfq` 用 `close * adj_factor`，`""` 不复权。
  4. 可选 `daily_basic(ts_code, start, end)` 取区间**首尾**的 `pe_ttm/pb`。
- **产出字段**：

| 字段 | 说明 |
|---|---|
| `ts_code` / `name` | 标的 |
| `adj` | 复权口径：`qfq`(默认)/`hfq`/`none` |
| `start` / `end` | 实际使用的区间（可能被对齐到交易日/默认收窄） |
| `sample_days` | 区间样本交易日数 |
| `first` / `last` | 区间首/末交易日（日期） |
| `return_pct` | **区间涨跌幅 %**（按 `adj` 口径：末收盘/首收盘-1）×100 |
| `high` / `low` | 区间最高/最低（adj 口径） |
| `last_close` | 区间末收盘 |
| `total_volume` / `total_amount` | 区间累计成交量 / 成交额 |
| `pe_ttm_start/end`、`pb_start/end` | 区间首尾估值（若 `daily_basic` 可用） |

- **守卫**：样本 < 2 → 返回「区间数据不足」业务提示；起始日期在未来 → 拒绝；`start>end` → 校验错误。

### 3.4 `stock_financials(stock, period="近两年")`

- **签名**：`stock_financials(stock, period="近两年")`
- **入参校验**：`resolve_stock`；`period` 经 `normalize_period` 展开成**期次列表**（`YYYYMMDD` 报告期，季度末 0331/0630/0930/1231，年度 1231）。默认近 8 期（2 年）。
- **底层接口**：
  1. `income(ts_code, end_date=<每期>)` → `revenue`(营业收入)、`n_income_attr_p`(归母净利润)、`total_revenue`；
  2. `fina_indicator(ts_code, end_date=<每期>)` → `grossprofit_margin`(毛利率 %, 同区间口径)、`debt_to_assets`(资产负债率 %)、`netprofit_margin`、`roe`、`eps`。
- **合并规则**：按 `period`（报告期）`income`+`fina_indicator` 对齐合成一行；报告期标签：`20240331`→`2024Q1`、`20241231`→`2024年报`。
- **产出字段（每期一行）**：

| 字段 | 说明 |
|---|---|
| `period` | 报告期 `YYYYMMDD` |
| `label` | 友好标签：`2024年报` / `2024Q1` |
| `revenue` | 营业收入（元） |
| `net_profit_attr_p` | 归母净利润（元） |
| `gross_margin` | 销售毛利率（%） |
| `debt_ratio` | 资产负债率（%） |
| `net_margin`/`roe`/`eps` | （可选）销售净利率 / 净资产收益率 / 每股收益 |

- **错误**：某期无财报 → 该期 `available=false`，保留其它期；全部为空 → `ok=false` + 「无该期财报」提示。

---

## 4. 参数校验与容错（`StockInputValidator`）

### 4.1 股票代码解析 + 硬 allowlist（`resolve_stock`）

| 输入（示例） | 归一后 |
|---|---|
| `比亚迪` / `比亚迪股份` / `BYD` / `byd` / `002594` / `002594.SZ` | `002594.SZ` |
| `宁德时代` / `CATL` / `catl` / `300750` / `300750.SZ` | `300750.SZ` |
| `贵州茅台` / `600519` / `000001` / `平安` | ❌ 友好拒绝：`[仅支持：比亚迪(002594.SZ)、宁德时代(300750.SZ)。未支持：<token>]` |
| `""` / 空 / 纯空白 | ❌ `缺少标的参数` |

- **匹配优先级**：精确 ts_code（`002594.SZ`）> 代码（`002594`）> 名称（`比亚迪`）> 别名（`BYD`/`CATL`）；大小写不敏感、去首尾空白、忽略多余前后缀。
- **硬性**：不在两票内一律拒绝，返回 `is_error=False` 的友好说明（可纠错，不崩循环）。

### 4.2 日期格式化容错（`normalize_date`）

| 输入（示例） | 归一后（`YYYYMMDD`） |
|---|---|
| `20260801` / `2026-08-01` / `2026/08/01` / `2026.08.01` / `2026-8-1` | `20260801` |
| `today` / `今天` | 当日 |
| `近一月` / `近1月` | 今日 − 30 天 |
| `近一年` / `最近一年` / `过去一年` | 今日 − 365 天 |
| `本季` / `上季` / `今年` / `去年` | 对应季初/年末的日期 |

- **规则**：统一转 `YYYYMMDD` 给 Tushare；同时保留展示用日期。
- **校验**：`start ≤ end`；`end ≤ 今日`（历史场景拒绝未来）；缺省给出合理默认（区间默认近一年）；**非交易日对齐**到最近可用交易日（用 `trade_cal` 对齐，或取接口返回数组的首/末行作为有效边）。
- **相对词支持**：`近一月/近一季度/近一年/近两年/本季/上季/今年/去年/今天/today`。

### 4.3 复权归一化（`normalize_adj`）

| 输入 | 归一后 |
|---|---|
| （缺省）/ `qfq` / `前复权` | `qfq`（默认） |
| `hfq` / `后复权` | `hfq` |
| `none` / `不复权` / `""` | `none` |
| 其它 | ❌ 校验错误：`adj 仅支持 qfq/hfq/none` |

### 4.4 期次归一化（`normalize_period`）

| 输入 | 展开后的报告期列表 |
|---|---|
| `近一年` | 最近 4 个季度末（`YYYY0331/0630/0930/1231`） |
| `近两年`（默认） | 最近 8 个季度末 |
| `2024年报` / `2024` | `20241231` |
| `2024Q1` / `20241Q` | `20240331` |
| `20240331` | `20240331` |

- **输出**：升序的 `period`（`YYYYMMDD`）列表，逐个取 `income`+`fina_indicator`。

### 4.5 错误与业务结果约定（`is_error` 语义）

| 情形 | 返回 `is_error` | `content` 内容 |
|---|---|---|
| 校验类错误（非 allowlist 代码 / 非法日期 / `start>end` / 未来日期） | `False` | 友好说明 + 修正建议（让 LLM 自纠） |
| 业务 `code!=0`（无权限/需积分） | `False` | 复用 `_permission_signal` 语义：明确「接口需更高积分/无权限」，让 LLM 如实转述，不假装取到数 |
| 业务 `code==0` 但 `row_count==0`（空数据） | `False` | 「该区间/该期无数据」友好提示 |
| 底层异常耗尽重试 | `True` | `Error calling … after N tries: …` |
| 实时回落 EOD 仍失败 | `False` | 「实时与最新 EOD 均不可用」+ 原因 |

> 与现有 `mcp.py` 一致：**校验/业务类失败不崩图、可被 LLM 自纠**；仅**真正取数异常**标 `is_error=True`。校验类错误刻意用 `is_error=False`，让 LLM 收到后可修正重试，而不是当作工具崩溃。

### 4.6 数值与区间守卫

- 区间样本 < 2 → 业务提示「区间内交易日不足，无法计算涨跌幅」。
- 财务某期缺数 → 该期 `available=false`；全部缺数 → 友好提示。
- 超大区间（如 >5 年）→ `daily` 可能受单次条数限制，设计上**分段查询**（按年切段）并合并，或提示「区间过大，建议分段」。

---

## 5. 输出与溯源（JSON 契约）

每个语义工具的 `ToolResult.content` 渲染为**紧凑 JSON**（`ensure_ascii=False`），统一字段：

```json
{
  "ok": true,
  "tool": "stock_price_range",
  "company": "比亚迪",
  "ts_code": "002594.SZ",
  "data": {
    "adj": "qfq",
    "start": "20260801",
    "end": "20260825",
    "sample_days": 18,
    "return_pct": 3.42,
    "first": "20260801",
    "last": "20260825",
    "high": 300.1,
    "low": 280.4,
    "last_close": 296.8,
    "total_volume": 320000,
    "total_amount": 950000
  },
  "source": {
    "api": ["daily", "adj_factor"],
    "params": {"ts_code": "002594.SZ", "start_date": "20260801", "end_date": "20260825", "adj": "qfq"},
    "period": null
  }
}
```

- **溯源字段** `source`：保留底层接口名 + 入参 + 口径 + 期次，供前端「思考轨迹」/归档/人工核对引用。
- `ok=false` 时 `data` 可省，改为 `message`（友好错误/业务提示）。
- 渲染约定：优先输出 JSON，便于结构化；若 LLM 偏好表格，`content` 可再附一段 markdown 表格摘要（由语义层决定，默认 JSON）。

---

## 6. 配置

新增配置（沿用 `config/settings.py` 的 `Field(default, alias="ENV_NAME")` 风格）；底层取数复用现有 MCP 配置。

| 字段 | 默认 | 环境变量 | 说明 |
|---|---|---|---|
| `stock_allowlist` | `"002594.SZ,300750.SZ"` | `DEMO_STOCKS` | 硬 allowlist（逗号分隔 ts_code） |
| `stock_default_adj` | `"qfq"` | `STOCK_DEFAULT_ADJ` | 区间涨跌幅默认复权口径 |
| `stock_financial_periods` | `8` | `STOCK_FINANCIAL_PERIODS` | 财务默认期数（近 2 年=8 期） |
| （复用）`tushare_mcp_url` | `https://api.tushare.pro/mcp/` | `TUSHARE_MCP_URL` | 底层 MCP 端点（token 在 URL query） |
| （复用）`mcp_timeout` | `30.0` | `DEMO_MCP_TIMEOUT` | 单次工具读超时（秒） |
| （复用）`mcp_retries` | `2` | `DEMO_MCP_RETRIES` | 底层工具重试次数 |

> 一并需在 `.env.example` 补新变量；`allowlist` 默认即两票，配置仅作兜底可改。

---

## 7. 边界与假设

### 7.1 底层端点契约（关键假设）

当前 `settings.tushare_mcp_url` 默认指向 **Tushare 官方 MCP**（`https://api.tushare.pro/mcp/`），而 `mcp.py` 的 `{code,msg,row_count,data}` 解析与 `_permission_signal` 是为**本地 `mcp_server` 代理**写的。语义层若调官方端点，其返回文本未必是这一契约。

- **取向 A**（推荐）：语义层把「底层接口调用」抽象为 `_fetch_interface(api_name, params, fields) -> rows`，内部委托给被注入的底层 provider，**沿用** `{code,msg,...}` 解析；实现时**验证**目标端点（官方 MCP 工具名/返回形状）是否匹配。
- **取向 B**：语义层直接以本地 `mcp_server` 代理为目标（其返回确定性契约），把 `mcp_server` 作为准生产数据源；官方端点作为备选。
- **结论**：文档假定底层返回 `{code,msg,row_count,data}` 契约，并在实现前核对端点/工具名/返回形状。

### 7.2 实时行情

- `realtime_quote` 依赖交易时段与接口积分；设计上**必须**提供「回落最新 EOD（`daily`+`daily_basic`）」路径，避免非交易时段返回空。
- 实时快照的 PE/PB/市值单位需与 `daily_basic` 单位对齐（`total_mv` 单位为万元），输出统一说明。

### 7.3 复权与涨跌幅口径

- 区间涨跌幅默认**前复权**（最直观）。前复权因子随最新交易日变化，跨区间端点以「区间最新因子」为基准；文档说明该口径即可。后复权/不复权作为 `adj` 参数。

### 7.4 数据缺失/停牌/空档

- `daily` 空数组 = 无该区间交易；`row_count==0` 语义已约定。
- 财报按报告期逐期取；某期缺失不阻塞其它期。

---

## 8. 与现有代码复用（路径）

| 现有构件 | 路径 | 在本层的角色 |
|---|---|---|
| `ToolProvider` 协议 | `demomcp/interfaces/tool_provider.py` | `StockToolProvider` 实现它（`list_tools` / `call_tool`） |
| `ToolSpec` / `ToolResult` | `demomcp/interfaces/types.py` | 工具面与返回文本的中立契约 |
| `MCPToolProvider` / `mcp_tool_provider` | `demomcp/providers/tools/mcp.py` | 底层取数：重试 + 超时 + `is_error` 语义复用 |
| `_parse_result` / `_is_business_error` / `_permission_signal` / `_short_msg` | `demomcp/providers/tools/mcp.py` | 解析 `{code,msg,...}`、识别权限类失败 |
| `CompositeToolProvider` | `demomcp/agents/registry.py` | 可扩展性：语义层 + 底层 组合（注意 `setdefault` 顺序） |
| `FakeToolProvider` | `demomcp/providers/tools/fake.py` | 离线测试替身（记录 `calls` / `raise_on`） |
| `Settings` | `demomcp/config/settings.py` | 新增配置落点（`Field(default, alias="ENV_NAME")` 风格） |
| 注入点 | `demomcp/entry/cli.py`、`demomcp/entry/web.py` | 在 `async with mcp_tool_provider(...)` 内注入语义层 |

**测试约定**（未来实现时）：`tests/test_stock_input.py`（resolve/date/adj/period 纯函数用例）、`tests/test_stock_provider.py`（用 `FakeToolProvider` 桩住底层 `query`，验证语义工具的 allowlist、错误约定、字段映射），沿用 `tests/` 现有 `FakeToolProvider` + `MockLLM` 风格。

---

## 9. 扩展点

| 方向 | 做法 |
|---|---|
| 扩标的 | 增 `stock_allowlist` 常量 + 补 `resolve_stock` 别名；语义工具逻辑不变 |
| 换底层端点 | 适配 `_fetch_interface` 与返回契约（见 §7.1 取向 A/B） |
| 加指标 | `stock_financials` 增 `fina_indicator` 字段（如 `roe/net_margin/eps`）即可 |
| 加估值/股息 | 复用 `daily_basic` 的 `ps/dv_ratio/dv_ttm`，扩 `stock_realtime_quote`/`stock_price_range` |
| 落档溯源 | 把 `source` 并入 `ChatTurn` 扩展字段或结构化输出，供前端/追溯 |

---

## 附录 A：底层接口字段表

| 接口 | 用途 | 关键字段 |
|---|---|---|
| `realtime_quote` | 实时快照 | `ts_code,name,price,pre_close,open,high,low,change,pct_change,volume,amount,market_cap,pe,pe_ttm,pb,ps,total_share,float_share` |
| `daily` | 日线 | `ts_code,trade_date,open,high,low,close,pre_close,change,pct_change,vol(手),amount(千元)` |
| `adj_factor` | 复权因子 | `ts_code,trade_date,adj_factor` |
| `daily_basic` | 每日指标 | `ts_code,trade_date,close,pe,pe_ttm,pb,ps,ps_ttm,total_share,float_share,free_share,total_mv(万元),circ_mv(万元),turnover_rate` |
| `income` | 利润表 | `ts_code,end_date,revenue(营收),total_revenue,n_income(净利),n_income_attr_p(归母净利),ann_date,f_ann_date` |
| `fina_indicator` | 财务指标 | `ts_code,end_date,eps,roe,grossprofit_margin(毛利率%),netprofit_margin(净利率%),debt_to_assets(资产负债率%),current_ratio,quick_ratio` |
| `stock_basic` | 股票列表 | `ts_code,symbol,name,area,industry,market,list_date,is_hs` |
| `trade_cal` | 交易日历 | `exchange,cal_date,is_open`（非交易日对齐用） |

## 附录 B：调用序列示例（`stock_price_range("比亚迪", "近一年")`）

1. `resolve_stock("比亚迪")` → `002594.SZ`。
2. `normalize_date("近一年")` → `[start=20250825, end=20260825]`；`normalize_adj(缺省)` → `qfq`。
3. 底层 `_fetch_interface("adj_factor", {ts_code, start_date, end_date})` → 复权因子。
4. 底层 `_fetch_interface("daily", {ts_code, start_date, end_date})` → 区间行情。
5. 按 `qfq` 调整 close → 计算 `return_pct`、首尾、极值、量额；可选 `daily_basic` 取区间首尾 `pe_ttm/pb`。
6. 渲染 JSON（含 `source.{api,params,adj,period}`）→ `ToolResult(content=<json>, is_error=False)`。

## 附录 C：校验用例表（抽查）

| 分支 | 输入 | 期望 |
|---|---|---|
| 非白名单代码 | `stock="600519"` | `is_error=False` + `[仅支持：比亚迪(002594.SZ)、宁德时代(300750.SZ)...]` |
| 别名解析 | `stock="CATL"` | `ts_code=300750.SZ` |
| 日期多格式 | `start="2026-8-1"` | `20260801` |
| 相对词 | `period="近一年"` | 最近 4 个季度末期次 |
| 起止颠倒 | `start>end` | 校验错误（友好+可修正） |
| 未来日期 | `end` 在未来 | 校验错误（历史场景拒绝） |
| 空数据 | 区间无交易 | `row_count==0` → `ok=false`+`无数据` 业务提示 |
| 积分不足 | `code!=0` 权限类 | `is_error=False` + 如实转述积分/权限不足 |
