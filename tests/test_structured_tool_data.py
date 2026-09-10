"""structured.sources 的工具数据下发：完整原文（供前端渲染表格），且不污染 LLM 上下文。

背景：前端「查看引用来源」要把工具返回渲染成表格，原先 `data` 被砍到 200 字符导致
根本拿不到完整行。改法是证据条目额外挂一份 `raw`（完整原文），只走 structured.sources，
不进 `_evidence_digest`。本文件同时守住「LLM 上下文体积没变」这条不变量。
"""

from __future__ import annotations

import json

from demomcp.agents.agent import Agent
from demomcp.graph.nodes import (
    _MAX_EVIDENCE_CHARS,
    _MAX_SOURCE_RAW,
    _evidence_digest,
    _structured,
    _truncate_json_raw,
)
from demomcp.interfaces.types import ChatResponse, ToolResult, ToolSpec, ToolUse
from demomcp.providers.llm.mock import MockLLM
from demomcp.providers.tools.fake import FakeToolProvider

SPECS = [ToolSpec(name="stock_price_range", description="区间价格", input_schema={"type": "object"})]


def _big_rows(n: int) -> str:
    """构造一份远超 200 字符、也超过 _MAX_EVIDENCE_CHARS 的裸数组返回（官方 MCP 主力形态）。"""
    return json.dumps(
        [{"ts_code": "688256.SH", "name": "寒武纪", "end_date": f"2025{i:04d}", "revenue": 7.2903 + i} for i in range(n)],
        ensure_ascii=False,
    )


def test_structured_tool_source_carries_full_raw() -> None:
    """有 raw 时 sources[].data 用完整原文（不再截到 200），content 仍是喂 LLM 的截断版。"""
    payload = _big_rows(80)
    assert len(payload) > _MAX_EVIDENCE_CHARS  # 前提：够长，能区分两种截断

    ev = [{
        "source_type": "tool",
        "source": "stock_price_range",
        "content": payload[:_MAX_EVIDENCE_CHARS],
        "raw": payload[:_MAX_SOURCE_RAW],
    }]
    st = _structured("答案", "market", "auto", ev, {})
    src = st["sources"][0]

    assert src["type"] == "tool"
    assert src["data"] == payload  # 完整下发，前端才解析得出表格
    assert len(src["data"]) > 200
    assert json.loads(src["data"])  # 完整 JSON，可被前端 parseToolTable 解析
    assert src["title"] == "stock_price_range"  # 接口名仍在（前端收进展开区）


def test_structured_tool_source_falls_back_to_content() -> None:
    """老证据（无 raw，如历史落库数据）回退用 content，不炸。"""
    ev = [{"source_type": "tool", "source": "daily", "content": "[{\"close\": 1}]"}]
    st = _structured("答案", "market", "auto", ev, {})
    assert st["sources"][0]["data"] == "[{\"close\": 1}]"


def test_evidence_digest_ignores_raw() -> None:
    """raw 不进 LLM 上下文——digest 只读 content，体积与改造前一致。"""
    payload = _big_rows(80)
    ev = [{
        "source_type": "tool",
        "source": "stock_price_range",
        "content": payload[:_MAX_EVIDENCE_CHARS],
        "raw": payload,
    }]
    digest = _evidence_digest(ev)
    assert payload[:_MAX_EVIDENCE_CHARS] in digest
    assert payload not in digest  # 完整原文没被塞进上下文
    assert len(digest) < len(payload)


async def test_graph_evidence_keeps_both_truncations(make_settings) -> None:
    """端到端：跑一轮图，证据条目同时带截断 content（≤2000）与完整 raw，且 sources 下发完整。"""
    payload = _big_rows(80)
    tools = FakeToolProvider(SPECS, {"stock_price_range": ToolResult(content=payload, is_error=False)})
    mock = MockLLM([
        ChatResponse(stop_reason="end_turn", text='{"intent":"market","out_of_scope":false}'),
        ChatResponse(
            stop_reason="tool_use",
            tool_uses=[ToolUse(id="c1", name="stock_price_range", input={"name": "寒武纪"})],
            raw_content={"content": None, "tool_calls": [
                {"id": "c1", "type": "function",
                 "function": {"name": "stock_price_range", "arguments": '{"name": "寒武纪"}'}}]},
        ),
        ChatResponse(stop_reason="end_turn", text="数据已足。"),
        ChatResponse(stop_reason="end_turn", text="寒武纪营业收入见上。"),
    ])
    result = await Agent(llm=mock, tools=tools, config=make_settings()).run("寒武纪近5年营业收入")

    src = [s for s in result.structured["sources"] if s["type"] == "tool"]
    assert len(src) == 1
    assert src[0]["data"] == payload  # 完整
    assert json.loads(src[0]["data"])


# —— _truncate_json_raw：把 raw 的「硬切」改成语义截断，保证前端拿到的是可解析 JSON ——


def test_truncate_json_raw_small_unchanged() -> None:
    """未超限：语义截断走快路径，原样返回、零损耗。"""
    payload = _big_rows(10)
    assert len(payload) < _MAX_SOURCE_RAW
    assert _truncate_json_raw(payload) == payload


def test_truncate_json_raw_oversize_is_valid_json() -> None:
    """超限：语义截断返回**可解析** JSON（裸数组），只少尾部几行，而非截在 JSON 中途。"""
    payload = _big_rows(400)  # >20000 字符
    assert len(payload) > _MAX_SOURCE_RAW
    out = _truncate_json_raw(payload)
    parsed = json.loads(out)  # 不抛错 = 仍是合法 JSON，前端才能渲染成表格
    assert parsed  # 非空
    assert len(parsed) < 400  # 行数被压缩
    assert parsed[0] == json.loads(payload)[0]  # 首行完整保留
    assert len(out) < len(payload)


def test_truncate_json_raw_contract_shape_syncs_row_count() -> None:
    """本地代理契约 {code,msg,row_count,data:[…]}：截断后 row_count 同步成保留行数，data 仍是数组。"""
    rows = [{"ts_code": "600519.SH", "trade_date": f"2025{i:04d}", "close": 1604.9 + i} for i in range(400)]
    payload = json.dumps({"code": 0, "msg": "", "row_count": len(rows), "data": rows}, ensure_ascii=False)
    assert len(payload) > _MAX_SOURCE_RAW
    out = _truncate_json_raw(payload)
    parsed = json.loads(out)
    assert isinstance(parsed["data"], list)
    assert parsed["row_count"] == len(parsed["data"])  # 已同步，表头与数据行一致
    assert parsed["row_count"] < 400


def test_truncate_json_raw_plain_text_falls_back_to_slice() -> None:
    """非 JSON（如权限提示纯文本）且超限：无从语义截断，回退原硬切，不抛错。"""
    text = "无权限" * 10000
    assert len(text) > _MAX_SOURCE_RAW
    assert _truncate_json_raw(text) == text[:_MAX_SOURCE_RAW]
