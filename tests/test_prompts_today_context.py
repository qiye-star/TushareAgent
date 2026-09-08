"""当前时间锚点（today_context）测试：四个 LLM 节点的 system 提示都必须带上真实的「今天」。

背景：修复前全链路没有任何一处把真实日期喂给 LLM，模型用训练知识里的默认时间感去猜，
导致「最新数据」停在半年/数年前的陈旧日期。这里守住每一处注入点都生效。
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from demomcp.agents.agent import Agent
from demomcp.graph.prompts import select_system, today_context
from demomcp.interfaces.types import ChatResponse, ToolResult, ToolSpec, ToolUse
from demomcp.providers.llm.mock import MockLLM
from demomcp.providers.tools.fake import FakeToolProvider

# 与实现共用同一套固定 UTC+8（不依赖 zoneinfo/tzdata——Windows 无 IANA 时区库），
# 测试内部独立算一遍期望日期做精确比对。
_BEIJING = timezone(timedelta(hours=8))


def _today_iso() -> str:
    return datetime.now(_BEIJING).date().isoformat()


def test_today_context_contains_today_date() -> None:
    ctx = today_context()
    assert re.search(r"\d{4}-\d{2}-\d{2}", ctx)
    assert _today_iso() in ctx
    assert "北京时间" in ctx


def test_select_system_includes_today_context() -> None:
    system = select_system("base", "market")
    assert "【当前时间】" in system
    assert _today_iso() in system


def test_select_system_includes_today_context_for_all_intents() -> None:
    for intent in ("market", "report", "compare", None):
        assert _today_iso() in select_system("base", intent)


SPECS = [
    ToolSpec(name="stock_available", description="列出可用标的", input_schema={"type": "object"}),
    ToolSpec(name="stock_realtime_quote", description="实时行情", input_schema={"type": "object"}),
    ToolSpec(name="stock_price_range", description="区间价格", input_schema={"type": "object"}),
    ToolSpec(name="stock_financials", description="财报", input_schema={"type": "object"}),
]


async def test_agent_run_passes_today_context_to_every_llm_call(make_settings) -> None:
    """全链路每一条 LLM 调用（router/rewrite_query/tool选择/停止判定/synthesizer）的 system 都带当前日期。"""
    tools = FakeToolProvider(
        SPECS,
        {"stock_price_range": ToolResult(content='[{"close": 1604.9}]', is_error=False)},
    )
    mock = MockLLM(
        [
            ChatResponse(stop_reason="end_turn", text='{"intent":"market","out_of_scope":false}'),
            ChatResponse(stop_reason="end_turn", text="比亚迪 最近价格 区间"),  # rewrite_query
            ChatResponse(
                stop_reason="tool_use",
                tool_uses=[ToolUse(id="c1", name="stock_price_range", input={"name": "比亚迪"})],
            ),
            ChatResponse(stop_reason="end_turn", text="数据已足。"),  # 停止判定
            ChatResponse(stop_reason="end_turn", text="比亚迪区间约 1604.9 元。"),  # synthesizer
        ]
    )
    agent = Agent(llm=mock, tools=tools, config=make_settings())
    result = await agent.run("比亚迪最近价格")

    assert result.stopped_reason == "end_turn"
    assert len(mock.calls) == 5  # router / rewrite_query / tool选择 / 停止判定 / synthesizer
    today = _today_iso()
    for i, call in enumerate(mock.calls):
        assert call["system"] is not None, f"第 {i + 1} 次调用未传 system"
        assert today in call["system"], f"第 {i + 1} 次调用的 system 未带当前日期：{call['system'][:200]}"
