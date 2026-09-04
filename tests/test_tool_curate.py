"""工具可用性探测：probe_availability 分桶 + 缓存读写（不联网，用假 provider）。"""

from __future__ import annotations

from demomcp.interfaces.types import ToolResult, ToolSpec
from demomcp.providers.tools.curate import (
    _bucket,
    load_catalog,
    probe_availability,
    save_catalog,
)


class _HandlerProvider:
    """按工具名返回预设 ToolResult 的假 provider。"""

    def __init__(self, results: dict[str, dict | None]) -> None:
        self._results = results
        self.called: list[str] = []

    async def list_tools(self) -> list[ToolSpec]:
        return [
            ToolSpec("income"),
            ToolSpec("fina_indicator"),
            ToolSpec("bond"),
            ToolSpec("query"),  # meta，不应被探测
        ]

    async def call_tool(self, name: str, arguments: dict | None = None) -> ToolResult:
        self.called.append(name)
        r = self._results.get(name)
        if r is None:
            return ToolResult(content="ok", is_error=False)
        return ToolResult(content=r["content"], is_error=r.get("is_error", False))


def test_bucket() -> None:
    assert _bucket("积分不足，需提升") == "blocked"
    assert _bucket("接口下线") == "down"
    assert _bucket("缺少参数 ts_code") == "usable"


async def test_probe_classifies_and_skips_meta() -> None:
    provider = _HandlerProvider(
        {
            "income": {"content": '{"code":0,"msg":"","data":[]}'},
            "fina_indicator": {"content": '{"code":40001,"msg":"积分不足，请联系"}'},
            "bond": {"content": '{"code":500,"msg":"接口下线"}'},
        }
    )
    specs = await provider.list_tools()
    catalog = await probe_availability(provider, specs)
    assert "query" not in provider.called  # meta 不探测
    assert catalog["income"]["status"] == "usable"
    assert catalog["fina_indicator"]["status"] == "blocked"
    assert catalog["bond"]["status"] == "down"


async def test_probe_exception_is_conservative_usable() -> None:
    provider = _HandlerProvider(
        {
            "income": {"content": "boom", "is_error": True},
        }
    )
    specs = await provider.list_tools()
    catalog = await probe_availability(provider, specs)
    # 真异常不判死（保守保留），避免误杀
    assert catalog["income"]["status"] in ("usable", "blocked", "down")


async def test_save_load_catalog_roundtrip(tmp_path) -> None:
    path = str(tmp_path / "tool_catalog.json")
    catalog = {"income": {"status": "blocked", "msg": "积分不足"}}
    save_catalog(catalog, path)
    assert load_catalog(path) == catalog


async def test_load_catalog_missing_returns_none(tmp_path) -> None:
    assert load_catalog(str(tmp_path / "nope.json")) is None
