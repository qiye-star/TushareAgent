"""同花顺 iFind 数据源：经 streamable-http 连 iFind 的 7 个远程 MCP 域。

形态与万得（Wind）同构——数据商按业务域各开一个 MCP 端点、用同一个密钥鉴权，所以连接/隔离/
冷却/懒发现那套机器直接复用 `multi_domain.MultiDomainMCPProvider`，本模块只放 iFind 特有的三件事：

1. **域名与 URL**：`https://api-mcp.51ifind.com:8643/ds-mcp-servers/hexin-ifind-ds-<域>-mcp`
   （注意 `global_stock` 域在 URL 里是连字符 `global-stock`，不是下划线）；
2. **鉴权头是裸 token**：`Authorization: <IFIND_AUTH_TOKEN>`——**没有** `Bearer ` 前缀
   （万得那边是 `Bearer <key>`，两者别抄串了，这是接错就全域 401 的那种坑）；
3. **并发上限低**：iFind 按套餐限并发（免费版 2 / 个人版 5 / 企业版 10），默认取最保守的 2，
   经 `IFIND_CONCURRENCY` 放宽；超限时远端直接拒绝，所以这里的 Semaphore 是必需的而非优化。

4. **成功信封是 `code:1`**（Tushare 是 `code:0`，正好相反），所以必须给 `mcp.py` 传自己的
   `ifind_business_error` 判据——否则每次成功都被当业务失败重试一遍，见该函数的说明。

对外暴露 `ifind_list_apis` / `ifind_get_api_info` / `ifind_query` 三个元工具，具体接口
（选股/财务/股东/风险/ESG、基金、宏观 EDB、新闻公告、债券、港美股、指数板块）只进内部目录——
2026-09-08 实测 7 域共 32 个。刻意**不硬编码**这份清单：参考实现
（claude-for-financial-services-cn）把 31 个接口写死成 wrapper，已经与远端漂移——它暴露了
远端已下线的 `search_funds`/`search_edb`/`search_global_stocks`/`search_trending_news`，
又缺了远端新增的 `get_stock_performance` 与四个 `*_highfreq_quotes`。懒发现自动跟着远端走。

iFind 的接口参数普遍是**单个自然语言 query 串**（如 "茅台2025年三季度的ROE"），与 Tushare 的
结构化参数风格不同；这条约定由 `settings.IFIND_USAGE_GUIDE` 讲给 LLM。
实测提醒：**查不到数据仍返回 `code:1/success`**，提示语在 `data.answer` 里（不是错误）；而参数
缺失/类型错这类硬错误远端会**挂住直到超时**，不返回错误信封。
"""

from __future__ import annotations

import json

from mcp_gateway.providers.multi_domain import MultiDomainMCPProvider

PREFIX = "ifind_"
LABEL = "同花顺 iFind"

_BASE_URL = "https://api-mcp.51ifind.com:8643/ds-mcp-servers"

# 域名 → URL 片段（片段用连字符；global_stock 是唯一一处与域名写法不同的）
_DOMAIN_SLUGS: dict[str, str] = {
    "stock": "stock",
    "fund": "fund",
    "edb": "edb",
    "news": "news",
    "bond": "bond",
    "global_stock": "global-stock",
    "index": "index",
}

IFIND_DOMAINS: dict[str, str] = {
    domain: f"{_BASE_URL}/hexin-ifind-ds-{slug}-mcp" for domain, slug in _DOMAIN_SLUGS.items()
}


# iFind 成功信封的 code 值。**是 1，不是 0** —— 这与 Tushare（`code:0` 成功）相反。
# 2026-09-08 实测：`{"code":1,"msg":"success","subCode":null,"subMsg":null,"data":"<json 串>"}`。
_SUCCESS_CODE = 1


def _parse(text: str) -> dict | None:
    try:
        body = json.loads(text)
    except (ValueError, TypeError):
        return None
    return body if isinstance(body, dict) else None


def ifind_business_error(text: str) -> bool:
    """iFind 版的「算不算业务失败」判据，替掉 `mcp.py` 默认的 Tushare 约定（`code != 0`）。

    **不换判据会让每一次成功调用都被重试一遍**：iFind 成功是 `code:1`，默认判据看到非 0 就当失败，
    白翻一倍延迟、并烧掉本就只有 2 的并发配额（免费版）。

    只有「解析出 JSON 对象、带 code 字段、且 code 不是 1」才算业务失败。注意**无数据不算失败**：
    实测查不到时仍返回 `code:1/success`，提示语写在 `data.answer` 里（如「抱歉，本次数据查询未返回
    有效结果」）——那是正常返回，要让 LLM 自己读到并如实转述，不能在这里翻成错误。
    """
    body = _parse(text)
    if body is None or "code" not in body:
        return False
    code = body.get("code")
    return isinstance(code, (int, float)) and code != _SUCCESS_CODE


def ifind_error_signal(text: str) -> str | None:
    """把 iFind 的失败信封翻成一句人话；成功/无数据返回 None（原样通过）。

    认两种形状：
    1. `{"code": <非 1>, "msg": …, "subMsg": …}` —— 远端真实的失败信封；
    2. `{"error": …}` —— 参考实现（claude-for-financial-services-cn 的 ifind-mcp 代理层）会产出
       这个形状。iFind 远端本身**不返回**它，但留着以防经代理转发时出现。

    实测提醒：参数缺失/类型不对这类硬错误，远端表现为**请求挂住直到超时**而不是返回错误信封，
    那条路由由 `mcp.py` 的超时+重试兜（`is_error=True`），不经过这里。
    """
    body = _parse(text)
    if body is None:
        return None

    err = body.get("error")
    if err:
        msg = err if isinstance(err, str) else str(err.get("message") or err) if isinstance(err, dict) else str(err)
    elif ifind_business_error(text):
        msg = " ".join(
            str(body.get(k) or "") for k in ("msg", "subMsg", "subCode") if body.get(k)
        ).strip() or f"code={body.get('code')}"
    else:
        return None

    low = msg.lower()
    if "auth" in low or "token" in low or "密钥" in msg or "401" in msg:
        return f"{LABEL}鉴权失败，请检查 IFIND_AUTH_TOKEN。"
    if "并发" in msg or "concurren" in low or "limit" in low:
        return f"{LABEL}并发超限，请降低并发或调高 IFIND_CONCURRENCY（受套餐上限约束）。"
    if "权限" in msg or "积分" in msg or "套餐" in msg:
        return f"{LABEL}该接口无权限或超出套餐范围：{msg[:160]}。请如实转述，并可改用 Tushare/万得的等价接口。"
    return f"{LABEL}调用返回错误：{msg[:200]}"


class IfindToolProvider(MultiDomainMCPProvider):
    """iFind 的 7 域聚合 ToolProvider（前缀 `ifind_`，对外 3 个懒发现元工具）。"""

    def __init__(
        self,
        auth_token: str,
        *,
        timeout: float = 30.0,
        retries: int = 2,
        max_concurrency: int = 2,
        keepalive_interval: float | None = None,
    ) -> None:
        super().__init__(
            IFIND_DOMAINS,
            prefix=PREFIX,
            label=LABEL,
            # 裸 token，无 Bearer 前缀（见模块 docstring）
            headers={"Authorization": auth_token} if auth_token else None,
            timeout=timeout,
            retries=retries,
            max_concurrency=max_concurrency,
            keepalive_interval=keepalive_interval,
            error_signal=ifind_error_signal,
            # 必须覆盖：iFind 成功是 code:1，用默认的 Tushare 判据会把每次成功都当失败重试
            business_error=ifind_business_error,
            api_name_example="ifind_get_stock_info",
        )
