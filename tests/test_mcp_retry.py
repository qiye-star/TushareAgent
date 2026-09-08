"""MCP 工具重试/超时离线测试：仅重试抛出的异常，isError 业务结果不重试。"""

from __future__ import annotations

from datetime import timedelta

from mcp.types import CallToolResult, TextContent

from demomcp.providers.tools.mcp import MCPToolProvider


class _FlakySession:
    def __init__(self, fails: int = 2) -> None:
        self.fails = fails
        self.calls = 0
        self.timeouts: list[timedelta | None] = []

    async def call_tool(self, name, arguments=None, read_timeout_seconds=None):
        self.calls += 1
        self.timeouts.append(read_timeout_seconds)
        if self.calls <= self.fails:
            raise TimeoutError("slow")
        return CallToolResult(content=[TextContent(type="text", text="ok")], isError=False)


class _AlwaysFail:
    def __init__(self) -> None:
        self.calls = 0

    async def call_tool(self, name, arguments=None, read_timeout_seconds=None):
        self.calls += 1
        raise TimeoutError("boom")


class _BusinessError:
    def __init__(self) -> None:
        self.calls = 0

    async def call_tool(self, name, arguments=None, read_timeout_seconds=None):
        self.calls += 1
        return CallToolResult(content=[TextContent(type="text", text="无权限")], isError=True)


class _BusinessNoPermission:
    def __init__(self) -> None:
        self.calls = 0

    async def call_tool(self, name, arguments=None, read_timeout_seconds=None):
        self.calls += 1
        return CallToolResult(
            content=[TextContent(type="text", text='{"code": 1, "msg": "参数缺失", "row_count": 0, "data": []}')],
            isError=False,
        )


async def test_call_tool_retries_then_succeeds() -> None:
    s = _FlakySession(fails=2)
    provider = MCPToolProvider(s, timeout=30.0, retries=3)
    result = await provider.call_tool("query", {})
    assert result.is_error is False
    assert result.content == "ok"
    assert s.calls == 3  # 2 次失败 + 1 次成功
    assert all(t == timedelta(seconds=30) for t in s.timeouts)  # 超时报成 timedelta


async def test_call_tool_exhausts_retries() -> None:
    s = _AlwaysFail()
    provider = MCPToolProvider(s, timeout=5.0, retries=2)
    result = await provider.call_tool("query", {})
    assert result.is_error is True
    assert "after 2 tries" in result.content
    assert s.calls == 2


async def test_business_permission_error_retried_then_friendly_message() -> None:
    s = _BusinessError()
    provider = MCPToolProvider(s, retries=3)
    result = await provider.call_tool("query", {})
    assert result.is_error is False  # 权限类失败转友好提示（非错误），由 LLM 如实转述
    assert "积分不足" in result.content
    assert s.calls == 3  # 业务失败也重试


async def test_business_nonpermission_retried_then_raw_result() -> None:
    s = _BusinessNoPermission()
    provider = MCPToolProvider(s, retries=2)
    result = await provider.call_tool("daily", {})
    assert result.is_error is False  # 非权限类业务失败 → 保留原始结果（含 code/msg）
    assert '"code": 1' in result.content
    assert s.calls == 2


# ---------------------------------------------------------------------------
# 网关聚合连接的业务失败判据（gateway_business_error）
# ---------------------------------------------------------------------------


def test_gateway_business_error_accepts_both_success_codes() -> None:
    """网关一条连接上流着五个源的混合信封，各源「成功」约定互相矛盾：
    Tushare code:0 成功，而 **iFind code:1 成功**。聚合判据必须同时接受 0 与 1。

    实测不这样做的后果：每次成功的 iFind 调用都被判业务失败并重试一遍，
    输出逐字节相同却慢一倍以上，还把 iFind「套餐硬限并发 2」的配额双倍烧掉。
    """
    from demomcp.providers.tools.mcp import gateway_business_error

    assert gateway_business_error('{"code": 0, "data": []}') is False           # Tushare 成功
    assert gateway_business_error('{"code": 1, "msg": "success"}') is False     # iFind 成功
    assert gateway_business_error('{"data": {"items": []}}') is False           # Wind 无 code
    assert gateway_business_error("[]") is False                               # 裸数组
    assert gateway_business_error("not json") is False                         # 非 JSON 交给别处判
    # 真失败仍要判出来
    assert gateway_business_error('{"code": 40203, "msg": "无权限"}') is True
    assert gateway_business_error('{"code": 50101, "msg": "参数错误"}') is True
    assert gateway_business_error('{"code": -1}') is True


def test_gateway_business_error_ignores_non_numeric_code() -> None:
    """code 不是数字（业务数据里恰好有个叫 code 的字符串列）→ 不当失败。"""
    from demomcp.providers.tools.mcp import gateway_business_error

    assert gateway_business_error('{"code": "600519.SH"}') is False
    assert gateway_business_error('{"code": null}') is False
    assert gateway_business_error('{"code": true}') is False  # bool 不算数字码


def test_default_business_error_unchanged_for_tushare_semantics() -> None:
    """默认判据**不能**跟着改：Tushare 语义下 code:1 确实是失败，本文件上方的用例依赖它。"""
    from demomcp.providers.tools.mcp import _is_business_error

    assert _is_business_error('{"code": 1, "msg": "参数缺失"}') is True
    assert _is_business_error('{"code": 0, "data": []}') is False
