# external_sources —— 免费数据源（独立进程）

对标 `claude-for-financial-services-cn` 的免费数据面，补齐 Tushare 官方 MCP 的两个空洞：
基础行情/财报的免费兜底，以及**新闻**（官方 `news`/`cctv_news` 实测 40203 无权限）。

## 这是什么，不是什么

- **是**：两个各自独立的 MCP server 进程，自带依赖、自己启停。网关只是它们的 MCP 客户端，
  走的是和连 Tushare 官方 MCP **完全相同**的代码路径（`mcp_tool_provider(url)`），没有特殊分支。
- **不是** `mcp_server/`（那是遗留的内置 Tushare 代理，`:8765`，默认停用），也**不是** `mcp_gateway/`
  （那是聚合网关，`:8766`）。三者互不相关，别混。
- 依赖**不在**主仓库 `pyproject.toml` 里 —— `akshare` 会拖进 pandas/lxml 一大串，主仓库刻意保持精简。

## 安装

```bash
python -m venv external_sources/.venv
external_sources/.venv/Scripts/pip install -r external_sources/requirements.txt   # Windows
```

## 启动（两个进程，各自独立）

```bash
external_sources/.venv/Scripts/python external_sources/akshare_server.py --port 8000
```

```bash
external_sources/.venv/Scripts/python external_sources/china_news_server.py --port 8001
```

默认传输是 **streamable-http**，端点在 `/mcp`。也支持 `--transport stdio`（给 Claude Desktop 之类
直连场景用），但网关走的是 HTTP。

## 让网关连上它们

在 `mcp_gateway/.env` 里填 URL（留空即不注册该源，`/admin/sources` 里也不会出现）：

```env
AKSHARE_MCP_URL=http://127.0.0.1:8000/mcp
CHINA_NEWS_MCP_URL=http://127.0.0.1:8001/mcp
```

网关**不负责**拉起这两个进程：它们没起来时该源只是连不上（`/admin/health` 会照实说），
网关和 demomcp 都照常工作。这是有意的——「完全解耦、各自启停」。

## 工具面

| 源 | 工具 |
|---|---|
| `akshare` | `search_stock` / `get_quote` / `get_historical_data` / `get_financials` / `get_industry_stocks` / `get_index_data` / `get_stock_info` / `get_market_overview` / `get_fund_data` |
| `china_news` | `get_stock_news` / `get_market_headlines` |

工具名与返回契约刻意与 `claude-for-financial-services-cn` 的原版逐字一致（对标的就是它的数据面）。
返回是 records 形态 JSON 数组，前端 `lib/table.ts` 的 `parseToolTable` 认「裸数组」这一形态，
所以工具卡能直接渲染成表格。失败返回 `{"error": "..."}`。

## 已知坑

- **akshare 是同步阻塞的**（内部 requests + pandas 抓东方财富）。工具函数保持 `def` 而非
  `async def`——FastMCP 会把同步函数丢线程池；写成 `async def` 反而会把阻塞调用搬到事件循环上堵死整个 server。
- **数据来自网页抓取**，东方财富改版会让某个接口突然失效，属于免费源的固有代价。它的定位是
  兜底与交叉验证，不是主源。
- **东财行情 CDN 的反爬有层层墙（2026-09-08 实测）**，`akshare_server.py` 已按此打了两个补丁，
  但有一层是环境性的、补不了：
  1. **UA 墙**：`82.push2` / `push2his` / `datacenter-web` 等行情域拒绝非浏览器 UA——
     akshare 1.18.x 的行情接口是裸 `requests.get`（默认 `python-requests/x.y` UA），整片
     `RemoteDisconnected`。→ 补丁：全局给 requests 会话注入浏览器 UA（`Session.__init__` patch）。
  2. **TLS 指纹墙**：UA 修好后部分环境仍拒 python requests/httpx/curl_cffi（OpenSSL 系指纹），
     而系统 `curl.exe`（Windows Schannel 栈）放行。→ 补丁：对 `eastmoney.com` 域名的请求透明改道
     `curl.exe` 子进程（`requests.get/post/Session.request` 的域名分流，其它域名走原库；
     响应按 `requests.Response` 复刻，akshare 内部无感知）。
  3. **IP 级封锁**（**补不了，属环境**）：东财对高频/云服务器出口 IP 间歇性封禁行情 CDN——
     封禁窗口内即便 `curl.exe` 也 `Connection closed`；窗口外偶发 200。新闻/搜索域
     （`search-api-web` / `finance.eastmoney.com`）不做此检测，始终可用。同花顺（`ths`）与
     新浪（`stock_zh_index_daily`）也不受影响，所以 9 个工具里 `search_stock` / `get_financials` /
     `get_index_data` 稳定可用。封禁时接口返回 `{"error": ...}`，属业务结果（`is_error=False`），
     LLM 会如实转述并可改用 Tushare/万得的等价接口——**不要把这个当 bug 反复排查**。
- **`get_financials` 的「报告期」列随 akshare 版本漂移**：年值可能是纯年份（`"1998"`）也可能是
  `"1998-12-31"`，`str.contains("12-31")` 只在后一种形态下匹配，前一种会整表过滤成空。
  `akshare_server.py` 已兼容两种（`12-31|^\d{4}$`）；升级 akshare 后再看到 annual 输出为空，先查这列。
- **`get_financials` 走同花顺网页接口**（`stock_financial_abstract_ths`），字段口径与 Tushare 的
  `income`/`balancesheet` 不同，**不要**直接拿两边的数做同一张表。
