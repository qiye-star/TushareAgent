"""同花顺 iFind 源 + 通用多域外壳：懒发现三件套、按域路由、单域失败隔离与冷却、错误信封归一化。

全部用假 `mcp_tool_provider` 替掉真连接，不联网。
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager

import pytest

from demomcp.interfaces.types import ToolResult, ToolSpec
from mcp_gateway.providers import multi_domain
from mcp_gateway.providers.ifind import (
    IFIND_DOMAINS,
    IfindToolProvider,
    ifind_business_error,
    ifind_error_signal,
)

_URL_TO_DOMAIN = {url: domain for domain, url in IFIND_DOMAINS.items()}


class _FakeRemote:
    """一个域的假远端：list_tools 给裸名（无前缀），call_tool 回显被调用的名字。"""

    def __init__(self, tool_names: list[str]) -> None:
        self._tool_names = tool_names
        self.calls: list[tuple[str, dict | None]] = []

    async def list_tools(self) -> list[ToolSpec]:
        return [ToolSpec(name=n, description=f"{n} 的说明") for n in self._tool_names]

    async def call_tool(self, name: str, arguments: dict | None = None) -> ToolResult:
        self.calls.append((name, arguments))
        return ToolResult(content=json.dumps({"tool": name, "args": arguments}, ensure_ascii=False))


class _Harness:
    """记录每个域的连接次数 / 入参，并可指定哪些域连接失败。"""

    def __init__(self, domain_tools: dict[str, list[str]], failing: set[str] | None = None) -> None:
        self.remotes = {d: _FakeRemote(t) for d, t in domain_tools.items()}
        self.failing = failing or set()
        self.connect_counts: dict[str, int] = {}
        self.connect_kwargs: list[tuple[str, dict]] = []

    def install(self, monkeypatch) -> None:
        harness = self

        @asynccontextmanager
        async def fake_mcp_tool_provider(url: str, **kwargs):
            domain = _URL_TO_DOMAIN[url]
            harness.connect_counts[domain] = harness.connect_counts.get(domain, 0) + 1
            harness.connect_kwargs.append((domain, kwargs))
            if domain in harness.failing:
                raise RuntimeError(f"{domain} 连不上")
            yield harness.remotes[domain]

        monkeypatch.setattr(multi_domain, "mcp_tool_provider", fake_mcp_tool_provider)


def _provider(token: str = "tok", **kwargs) -> IfindToolProvider:
    return IfindToolProvider(token, **kwargs)


# ---- 域定义 ----


def test_ifind_domains_cover_seven_and_global_stock_uses_hyphen() -> None:
    assert set(IFIND_DOMAINS) == {"stock", "fund", "edb", "news", "bond", "global_stock", "index"}
    # URL 片段里是连字符，域名里是下划线——写成 global_stock 会 404，这是唯一一处不一致
    assert IFIND_DOMAINS["global_stock"].endswith("hexin-ifind-ds-global-stock-mcp")
    assert IFIND_DOMAINS["stock"].endswith("hexin-ifind-ds-stock-mcp")


async def test_auth_header_is_raw_token_not_bearer(monkeypatch) -> None:
    """iFind 要裸 token；抄万得的 `Bearer ` 前缀会全域 401。"""
    h = _Harness({d: ["get_stock_info"] for d in IFIND_DOMAINS})
    h.install(monkeypatch)
    async with _provider("my-token") as p:
        await p.list_tools()
    headers = h.connect_kwargs[0][1]["headers"]
    assert headers == {"Authorization": "my-token"}


async def test_no_auth_header_when_token_empty(monkeypatch) -> None:
    h = _Harness({d: ["get_stock_info"] for d in IFIND_DOMAINS})
    h.install(monkeypatch)
    async with _provider("") as p:
        await p.list_tools()
    assert h.connect_kwargs[0][1]["headers"] is None


# ---- 懒发现三件套 ----


async def test_list_tools_exposes_only_three_meta_tools(monkeypatch) -> None:
    """对外只 3 个元工具——30 个具体 schema 不进每轮 llm.chat(tools=…) 载荷。"""
    h = _Harness({d: [f"{d}_tool_a", f"{d}_tool_b"] for d in IFIND_DOMAINS})
    h.install(monkeypatch)
    async with _provider() as p:
        specs = await p.list_tools()
    assert [s.name for s in specs] == ["ifind_list_apis", "ifind_get_api_info", "ifind_query"]


async def test_list_apis_returns_prefixed_internal_catalog(monkeypatch) -> None:
    h = _Harness({"stock": ["search_stocks"], "news": ["search_news"]})
    monkeypatch.setattr(multi_domain, "DOMAIN_RETRY_COOLDOWN", 60.0)
    h.install(monkeypatch)
    async with IfindToolProvider("tok") as p:
        p._domains = {"stock": IFIND_DOMAINS["stock"], "news": IFIND_DOMAINS["news"]}
        result = await p.call_tool("ifind_list_apis", {})
    body = json.loads(result.content)
    assert body["count"] == 2
    assert {a["name"] for a in body["apis"]} == {"ifind_search_stocks", "ifind_search_news"}


async def test_list_apis_keyword_filter(monkeypatch) -> None:
    h = _Harness({"stock": ["search_stocks", "get_esg_data"]})
    h.install(monkeypatch)
    async with IfindToolProvider("tok") as p:
        p._domains = {"stock": IFIND_DOMAINS["stock"]}
        result = await p.call_tool("ifind_list_apis", {"keyword": "esg"})
    body = json.loads(result.content)
    assert [a["name"] for a in body["apis"]] == ["ifind_get_esg_data"]


async def test_get_api_info_unknown_returns_error(monkeypatch) -> None:
    h = _Harness({"stock": ["search_stocks"]})
    h.install(monkeypatch)
    async with IfindToolProvider("tok") as p:
        p._domains = {"stock": IFIND_DOMAINS["stock"]}
        result = await p.call_tool("ifind_get_api_info", {"api_name": "ifind_nope"})
    assert result.is_error is True
    assert "未知同花顺 iFind接口" in result.content or "未知" in result.content


# ---- 调用分发 ----


async def test_query_strips_prefix_and_routes_to_owning_domain(monkeypatch) -> None:
    h = _Harness({"stock": ["get_stock_info"], "bond": ["bond_market_data"]})
    h.install(monkeypatch)
    async with IfindToolProvider("tok") as p:
        p._domains = {"stock": IFIND_DOMAINS["stock"], "bond": IFIND_DOMAINS["bond"]}
        await p.call_tool("ifind_query", {"api_name": "ifind_bond_market_data", "params": {"query": "26国债01"}})
    # 远端收到的是**裸名**，且只有 bond 域被调到
    assert h.remotes["bond"].calls == [("bond_market_data", {"query": "26国债01"})]
    assert h.remotes["stock"].calls == []


async def test_query_accepts_bare_api_name_without_prefix(monkeypatch) -> None:
    """LLM 经常回传不带前缀的名字，得容忍。"""
    h = _Harness({"stock": ["get_stock_info"]})
    h.install(monkeypatch)
    async with IfindToolProvider("tok") as p:
        p._domains = {"stock": IFIND_DOMAINS["stock"]}
        await p.call_tool("ifind_query", {"api_name": "get_stock_info", "params": {"query": "茅台"}})
    assert h.remotes["stock"].calls == [("get_stock_info", {"query": "茅台"})]


async def test_query_rejects_non_dict_params(monkeypatch) -> None:
    h = _Harness({"stock": ["get_stock_info"]})
    h.install(monkeypatch)
    async with IfindToolProvider("tok") as p:
        p._domains = {"stock": IFIND_DOMAINS["stock"]}
        result = await p.call_tool("ifind_query", {"api_name": "get_stock_info", "params": "茅台"})
    assert result.is_error is True
    assert "params 必须是对象" in result.content


async def test_query_missing_api_name_returns_error(monkeypatch) -> None:
    h = _Harness({"stock": ["get_stock_info"]})
    h.install(monkeypatch)
    async with IfindToolProvider("tok") as p:
        p._domains = {"stock": IFIND_DOMAINS["stock"]}
        result = await p.call_tool("ifind_query", {})
    assert result.is_error is True
    assert "缺少 api_name" in result.content


async def test_unknown_concrete_tool_returns_error(monkeypatch) -> None:
    h = _Harness({"stock": ["get_stock_info"]})
    h.install(monkeypatch)
    async with IfindToolProvider("tok") as p:
        p._domains = {"stock": IFIND_DOMAINS["stock"]}
        await p.list_tools()
        result = await p.call_tool("ifind_does_not_exist", {})
    assert result.is_error is True
    assert "Unknown ifind tool" in result.content


# ---- 单域失败隔离与冷却 ----


async def test_single_domain_failure_does_not_block_others(monkeypatch) -> None:
    h = _Harness({"stock": ["get_stock_info"], "bond": ["bond_market_data"]}, failing={"bond"})
    h.install(monkeypatch)
    async with IfindToolProvider("tok") as p:
        p._domains = {"stock": IFIND_DOMAINS["stock"], "bond": IFIND_DOMAINS["bond"]}
        result = await p.call_tool("ifind_list_apis", {})
    body = json.loads(result.content)
    # bond 挂了，stock 的接口照常可见
    assert [a["name"] for a in body["apis"]] == ["ifind_get_stock_info"]


async def test_failed_domain_is_not_retried_within_cooldown(monkeypatch) -> None:
    """持续故障时不让每次元工具调用都白等一次连接。"""
    monkeypatch.setattr(multi_domain, "DOMAIN_RETRY_COOLDOWN", 300.0)
    h = _Harness({"stock": ["get_stock_info"], "bond": ["bond_market_data"]}, failing={"bond"})
    h.install(monkeypatch)
    async with IfindToolProvider("tok") as p:
        p._domains = {"stock": IFIND_DOMAINS["stock"], "bond": IFIND_DOMAINS["bond"]}
        await p.list_tools()
        await p.list_tools()
        await p.list_tools()
    assert h.connect_counts["bond"] == 1  # 冷却期内不再重试
    assert h.connect_counts["stock"] == 1  # 已成功的域也不重复连


async def test_failed_domain_retried_after_cooldown_elapses(monkeypatch) -> None:
    monkeypatch.setattr(multi_domain, "DOMAIN_RETRY_COOLDOWN", 0.0)
    h = _Harness({"stock": ["get_stock_info"]}, failing={"stock"})
    h.install(monkeypatch)
    async with IfindToolProvider("tok") as p:
        p._domains = {"stock": IFIND_DOMAINS["stock"]}
        await p.list_tools()
        await p.list_tools()
    assert h.connect_counts["stock"] == 2  # 冷却为 0 → 下一轮就重试


# ---- 错误信封归一化（纯函数） ----


def test_error_signal_passes_through_success_payloads() -> None:
    assert ifind_error_signal(json.dumps({"data": [1, 2]})) is None
    assert ifind_error_signal("not json at all") is None
    assert ifind_error_signal(json.dumps([1, 2, 3])) is None
    # error 存在但为空 → 不当失败
    assert ifind_error_signal(json.dumps({"error": ""})) is None


def test_real_success_envelope_is_not_an_error() -> None:
    """2026-09-08 实测的真实成功信封：code 是 **1**（不是 0），必须原样通过。"""
    real = json.dumps(
        {"code": 1, "msg": "success", "subCode": None, "subMsg": None, "data": "{\"answer\":\"…\"}"},
        ensure_ascii=False,
    )
    assert ifind_error_signal(real) is None
    assert ifind_business_error(real) is False


def test_no_data_is_not_an_error() -> None:
    """查不到数据时 iFind 仍返回 code:1/success，提示语在 data.answer 里——那是正常返回。

    翻成错误会让 LLM 以为接口坏了而改道，而不是如实转述「未返回有效结果」。
    """
    no_data = json.dumps(
        {"code": 1, "msg": "success", "data": "{\"answer\":\"# 查询结果\\n抱歉，本次数据查询未返回有效结果\"}"},
        ensure_ascii=False,
    )
    assert ifind_business_error(no_data) is False
    assert ifind_error_signal(no_data) is None


def test_business_error_flags_non_success_code() -> None:
    """非 1 的 code 才算业务失败（含 Tushare 风格的 0——对 iFind 那不是成功）。"""
    assert ifind_business_error(json.dumps({"code": 0, "msg": "fail"})) is True
    assert ifind_business_error(json.dumps({"code": -1, "msg": "boom"})) is True
    assert ifind_business_error(json.dumps({"code": 40203, "msg": "无权限"})) is True


def test_business_error_ignores_payloads_without_code() -> None:
    """没有 code 字段就别猜——交给 error_signal / 上层判断。"""
    assert ifind_business_error(json.dumps({"data": [1, 2]})) is False
    assert ifind_business_error(json.dumps({"error": "boom"})) is False
    assert ifind_business_error("not json") is False
    assert ifind_business_error(json.dumps([1, 2, 3])) is False


def test_error_signal_surfaces_msg_from_non_success_code() -> None:
    msg = ifind_error_signal(json.dumps({"code": 0, "msg": "参数错误", "subMsg": "query 不能为空"}))
    assert msg is not None
    assert "参数错误" in msg
    assert "query 不能为空" in msg


def test_error_signal_detects_permission_from_code_envelope() -> None:
    msg = ifind_error_signal(json.dumps({"code": 0, "msg": "该接口超出套餐范围"}))
    assert msg is not None
    assert "套餐" in msg
    assert "Tushare" in msg  # 提示可改道


def test_provider_passes_its_own_business_error_judge_to_mcp_layer() -> None:
    """判据必须真的传到 mcp.py —— 否则成功调用会被当失败重试，白翻一倍延迟又烧并发配额。"""
    p = IfindToolProvider("tok")
    assert p._business_error is ifind_business_error


async def test_business_error_reaches_mcp_tool_provider_kwargs(monkeypatch) -> None:
    h = _Harness({"stock": ["get_stock_info"]})
    h.install(monkeypatch)
    async with IfindToolProvider("tok") as p:
        p._domains = {"stock": IFIND_DOMAINS["stock"]}
        await p.list_tools()
    assert h.connect_kwargs[0][1]["business_error"] is ifind_business_error


def test_error_signal_detects_auth_failure() -> None:
    msg = ifind_error_signal(json.dumps({"error": "iFind auth_token 未配置"}))
    assert msg is not None
    assert "IFIND_AUTH_TOKEN" in msg


def test_error_signal_detects_concurrency_limit() -> None:
    msg = ifind_error_signal(json.dumps({"error": "并发数超过限制"}))
    assert msg is not None
    assert "IFIND_CONCURRENCY" in msg


def test_error_signal_generic_error_is_reported_verbatim_ish() -> None:
    msg = ifind_error_signal(json.dumps({"error": "iFind 请求失败: timeout"}))
    assert msg is not None
    assert "timeout" in msg


async def test_error_envelope_from_remote_becomes_is_error(monkeypatch) -> None:
    """远端 200 但信封里是 {"error": …} → 归一成 is_error，不让上层把它当数据。"""

    class _ErrRemote(_FakeRemote):
        async def call_tool(self, name, arguments=None):
            return ToolResult(content=json.dumps({"error": "iFind 请求失败: boom"}, ensure_ascii=False))

    h = _Harness({"stock": ["get_stock_info"]})
    h.remotes["stock"] = _ErrRemote(["get_stock_info"])
    h.install(monkeypatch)
    async with IfindToolProvider("tok") as p:
        p._domains = {"stock": IFIND_DOMAINS["stock"]}
        result = await p.call_tool("ifind_query", {"api_name": "get_stock_info"})
    assert result.is_error is True
    assert "boom" in result.content


# ---- 并发上限 ----


def test_default_concurrency_is_conservative() -> None:
    """默认 2 = iFind 免费版上限；调大要用户显式配 IFIND_CONCURRENCY。"""
    p = IfindToolProvider("tok")
    assert p._max_concurrency == 2


async def test_provider_requires_context_manager(monkeypatch) -> None:
    """没进 async with 就调用 → 明确报错，而不是 AttributeError/None 解引用。"""
    h = _Harness({"stock": ["get_stock_info"]})
    h.install(monkeypatch)
    p = IfindToolProvider("tok")
    p._domains = {"stock": IFIND_DOMAINS["stock"]}
    with pytest.raises(RuntimeError, match="缺 async with"):
        await p._get_provider("stock")
