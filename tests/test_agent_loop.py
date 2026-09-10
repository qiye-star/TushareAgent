"""核心层离线 gate：用 FakeToolProvider + MockLLM 驱动 LangGraph 四节点 Agent（无网络、无代理、无真 LLM）。

覆盖：意图路由→取数→整合生成(引用+免责声明) 全链路；越界兜底；无进展兜底；无证据兜底；工具异常转 is_error。
"""

from __future__ import annotations

from demomcp.agents.agent import Agent
from demomcp.interfaces.types import ChatResponse, ToolResult, ToolSpec, ToolUse
from demomcp.providers.llm.mock import MockLLM
from demomcp.providers.tools.fake import FakeToolProvider

SPECS = [
    ToolSpec(name="stock_available", description="列出可用标的", input_schema={"type": "object"}),
    ToolSpec(name="stock_realtime_quote", description="实时行情", input_schema={"type": "object"}),
    ToolSpec(name="stock_price_range", description="区间价格", input_schema={"type": "object"}),
    ToolSpec(name="stock_financials", description="财报", input_schema={"type": "object"}),
]


def _router_response(text: str) -> ChatResponse:
    """router 输出：一段 JSON（意图 + 越界）。"""
    return ChatResponse(stop_reason="end_turn", text=text)


def _tool_use_response(call_id: str, name: str, args: dict) -> ChatResponse:
    """tool_rag 的取数选择轮：返回一次工具调用。"""
    return ChatResponse(
        stop_reason="tool_use",
        tool_uses=[ToolUse(id=call_id, name=name, input=args)],
        raw_content={
            "content": None,
            "tool_calls": [{"id": call_id, "type": "function", "function": {"name": name, "arguments": str(args).replace("'", '"')}}],
        },
    )


def _stop_response(text: str = "数据已足。") -> ChatResponse:
    """模拟 LLM 在看过一轮返回后判定「数据已足够」的停止轮（无 tool_calls → 去合成器）。"""
    return ChatResponse(stop_reason="end_turn", text=text)


async def test_router_tool_synthesize_end_to_end(make_settings) -> None:
    tools = FakeToolProvider(
        SPECS,
        {"stock_price_range": ToolResult(content='[{"close": 1604.9}]', is_error=False)},
    )
    mock = MockLLM(
        [
            _router_response('{"intent":"market","out_of_scope":false}'),
            _tool_use_response("c1", "stock_price_range", {"name": "比亚迪"}),
            _stop_response(),  # 看到返回后判定数据已足 → 停止取数
            ChatResponse(stop_reason="end_turn", text="比亚迪区间约 1604.9 元。"),
        ]
    )
    agent = Agent(llm=mock, tools=tools, config=make_settings())
    result = await agent.run("比亚迪最近价格")

    assert result.stopped_reason == "end_turn"
    assert "1604" in result.final_text
    assert "不构成投资建议" in result.final_text  # 免责声明
    assert [name for name, _ in tools.calls] == ["stock_price_range"]
    assert len(result.tool_results) == 1
    assert result.tool_results[0].is_error is False
    assert result.citations == ["stock_price_range"]
    assert len(mock.calls) == 4  # router / tool选择（market 意图跳过 rewrite_query）/ 停止判定 / synthesizer
    # 决策与生成步骤都应确定性（temperature=0），同问应得同答
    assert all(c.get("temperature") == 0 for c in mock.calls)


async def test_out_of_scope_fallback(make_settings) -> None:
    mock = MockLLM([_router_response('{"intent":"report","out_of_scope":true}')])
    agent = Agent(llm=mock, tools=FakeToolProvider(SPECS, {}), config=make_settings())
    result = await agent.run("腾讯股价")

    assert result.stopped_reason == "fallback"
    assert "超出可查询范围" in result.final_text
    assert "不构成投资建议" in result.final_text
    assert result.citations == []
    assert result.tool_results == []
    assert len(mock.calls) == 1  # 只走了 router


async def test_no_tool_call_fallback(make_settings) -> None:
    mock = MockLLM(
        [
            _router_response('{"intent":"market","out_of_scope":false}'),
            ChatResponse(stop_reason="end_turn", text="我不知道要用什么工具。"),
        ]
    )
    agent = Agent(llm=mock, tools=FakeToolProvider(SPECS, {}), config=make_settings())
    result = await agent.run("比亚迪")

    assert result.stopped_reason == "fallback"
    assert "未调用取数工具且没有获取到可校验的数据" in result.final_text
    assert result.tool_results == []
    assert len(mock.calls) == 2  # router + tool选择（market 意图跳过 rewrite_query）


# ---- 零证据停止轮「直接作答」（同题二答回显，f2d8b91b 线上事故） ----

_ECHO_HISTORY = [
    {"role": "user", "content": "请计算 2024 年沪深 300 指数的全年涨跌幅"},
    {"role": "assistant", "content": "我来查行情。",
     "tool_calls": [{"id": "c0", "type": "function", "function": {"name": "stock_price_range", "arguments": "{}"}}]},
    {"role": "tool", "tool_call_id": "c0", "content": '[{"trade_date": "20241231", "close": 3934.91}]'},
    {"role": "assistant", "content": "（快速问答模式）2024 年沪深 300 指数全年涨跌幅为 **14.68%**。"},
]

_ECHO_ANSWER = (
    "（快速问答模式）2024 年沪深 300 指数全年涨跌幅为 **14.68%**。计算依据："
    "2024 年首个交易日（2024-01-02）收盘 3386.35 点、最后一个交易日（2024-12-31）收盘 3934.91 点。"
)


async def test_no_tool_direct_answer_echo_passthrough(make_settings) -> None:
    """同题二答回显（f2d8b91b 事故）：history 已含上一轮完整交换，LLM 不再调工具、直接回显上一轮
    答案 → end_turn 定稿（免调合成器 LLM），而非「未识别出可用工具」的误导兜底。"""
    mock = MockLLM([
        _router_response('{"intent":"market","out_of_scope":false}'),
        ChatResponse(stop_reason="end_turn", text=_ECHO_ANSWER),  # select：无 tool_uses + 直接作答
    ])
    agent = Agent(llm=mock, tools=FakeToolProvider(SPECS, {}), config=make_settings())
    result = await agent.run("请计算 2024 年沪深 300 指数的全年涨跌幅", history=_ECHO_HISTORY, mode="agent")

    assert result.stopped_reason == "end_turn"
    assert result.mode == "agent"
    assert "14.68%" in result.final_text
    assert "未调用取数工具" not in result.final_text
    assert len(mock.calls) == 2  # router + select（market 意图跳过 rewrite_query）；合成器免调
    assert result.structured is not None
    assert result.structured["answer"] == result.final_text
    assert result.structured["metadata"]["direct_answer"] is True
    assert result.structured["sources"] == []
    assert result.citations == []


async def test_no_tool_direct_answer_quick_variant(make_settings) -> None:
    """同一回显在 quick 模式下同样定稿（select 轮 system 含「快速问答模式」后缀）。"""
    mock = MockLLM([
        _router_response('{"intent":"market","out_of_scope":false}'),
        ChatResponse(stop_reason="end_turn", text=_ECHO_ANSWER),
    ])
    agent = Agent(llm=mock, tools=FakeToolProvider(SPECS, {}), config=make_settings())
    result = await agent.run("请计算 2024 年沪深 300 指数的全年涨跌幅", history=_ECHO_HISTORY, mode="quick")

    assert result.stopped_reason == "end_turn"
    assert result.mode == "quick"
    assert "14.68%" in result.final_text
    assert len(mock.calls) == 2  # router + select（market 意图跳过 rewrite_query）
    assert "快速问答模式" in mock.calls[1]["system"]  # quick 后缀仍在 select 轮生效


async def test_no_tool_failure_phrasing_still_falls_back(make_settings) -> None:
    """零证据停止轮输出含「取不到」类措辞的长句（>=30 字符）→ 仍诚实兜底，不转正为答案。"""
    mock = MockLLM([
        _router_response('{"intent":"market","out_of_scope":false}'),
        ChatResponse(stop_reason="end_turn", text="我没有找到 2024 年沪深 300 指数的相关行情数据，请稍后重试或改用其他数据源。"),
    ])
    result = await Agent(llm=mock, tools=FakeToolProvider(SPECS, {}), config=make_settings()).run("沪深300 2024 涨跌幅", mode="agent")
    assert result.stopped_reason == "fallback"
    assert "未调用取数工具且没有获取到可校验的数据" in result.final_text
    assert len(mock.calls) == 2  # router + select（market 意图跳过 rewrite_query）


async def test_no_tool_short_text_still_falls_back(make_settings) -> None:
    """30 字符下限：极短停止文本不转正为答案（test_empty_evidence_fallback 的「数据已足。」同理）。"""
    mock = MockLLM([
        _router_response('{"intent":"market","out_of_scope":false}'),
        ChatResponse(stop_reason="end_turn", text="好的。"),
    ])
    result = await Agent(llm=mock, tools=FakeToolProvider(SPECS, {}), config=make_settings()).run("沪深300 2024 涨跌幅", mode="agent")
    assert result.stopped_reason == "fallback"
    assert len(mock.calls) == 2  # router + select（market 意图跳过 rewrite_query）


async def test_direct_answer_ignored_when_evidence_exists(make_settings) -> None:
    """证据在场时停止轮走正常合成器路径（长回显文本不参与），metadata.direct_answer=False。"""
    tools = FakeToolProvider(
        SPECS, {"stock_price_range": ToolResult(content='[{"close": 1604.9}]', is_error=False)}
    )
    mock = MockLLM([
        _router_response('{"intent":"market","out_of_scope":false}'),
        _tool_use_response("c1", "stock_price_range", {"name": "比亚迪"}),
        ChatResponse(stop_reason="end_turn", text="数据已足。区间表现已给出如下。"),  # 停止判定（有证据）
        ChatResponse(stop_reason="end_turn", text="比亚迪区间约 1604.9 元。"),  # synthesizer
    ])
    result = await Agent(llm=mock, tools=tools, config=make_settings()).run("比亚迪最近价格", mode="agent")

    assert result.stopped_reason == "end_turn"
    assert len(mock.calls) == 4  # 合成器正常调用（market 意图跳过 rewrite_query）
    assert "1604" in result.final_text
    assert result.structured["metadata"]["direct_answer"] is False


def test_route_direct_answer_ordering() -> None:
    from demomcp.graph.routes import make_route_after_tool_rag

    route = make_route_after_tool_rag(10)
    assert route({"direct_answer": "x", "want_more": False, "evidence": [], "rag_chunks": []}) == "synthesizer"
    # fallback_reason 优先级最高（direct_answer 与硬失败同存时不可能发生，但保序）
    assert route({"fallback_reason": "no_progress", "direct_answer": "x", "want_more": False, "evidence": [], "rag_chunks": []}) == "fallback"
    # 有证据时直接作答不生效（走正常合成）
    assert route({"direct_answer": "x", "want_more": False, "evidence": [{"source_type": "tool"}], "rag_chunks": []}) == "synthesizer"
    # 无 direct_answer 无证据 → 兜底（原行为）
    assert route({"want_more": False, "evidence": [], "rag_chunks": []}) == "fallback"


def _desc_daily(start: str = "20241231", days: int = 60) -> str:
    """生成降序每日数据（模拟 index_daily 降序返回），保证 >2000 字符。"""
    from datetime import date, timedelta

    end = date.fromisoformat(start)
    rows = []
    for i in range(days):
        d = end - timedelta(days=i)
        rows.append(f'{{"trade_date": "{d.strftime("%Y%m%d")}", "close": {3934.91 - i * 0.1:.2f}}}')
    return "[" + ", ".join(rows) + "]"


async def test_evidence_truncation_keeps_head_tail(make_settings) -> None:
    """Bug 2 回归：长返回（降序 daily，>2000 字符）截断保留首尾两端——合成器能同时拿到区间
    两端日期（基期/期末，否则「全年涨跌幅」算不出）；中部日期在摘要折叠、raw 仍全量供前端。"""
    content = _desc_daily()
    assert len(content) > 2000
    tools = FakeToolProvider(SPECS, {"stock_price_range": ToolResult(content=content, is_error=False)})
    mock = MockLLM([
        _router_response('{"intent":"market","out_of_scope":false}'),
        _tool_use_response("c1", "stock_price_range", {"name": "沪深300"}),
        _stop_response(),
        ChatResponse(stop_reason="end_turn", text="两端数据都已取到。"),
    ])
    result = await Agent(llm=mock, tools=tools, config=make_settings()).run("沪深300 2024 涨跌幅", mode="agent")

    assert result.stopped_reason == "end_turn"
    synth_content = mock.calls[3]["messages"][0]["content"]  # synthesizer user message（market 意图跳过 rewrite_query）
    assert "20241231" in synth_content  # 首（期末）
    assert "20241102" in synth_content  # 尾（第 60 行）
    assert "中间省略" in synth_content
    assert "20241204" not in synth_content  # 中部日期在摘要中被折叠
    data = result.structured["sources"][0]["data"]
    assert "20241204" in data  # raw 全量供前端来源卡


# 真实抓取的完整返回（含全部 17 字段，3959 字符，>_MAX_EVIDENCE_CHARS=2000）——字段宽度必须
# 贴近真实，否则内容长度不超预算就不会触发原字符位置截断，测不出真实事故的触发条件。
_CATL_FINA_INDICATOR_JSON = (
    '[{"ts_code": "300750.SZ", "end_date": "20260630", "ann_date": "20260725", "grossprofit_margin": 23.9284, '
    '"gross_margin": 66261690000.0, "netprofit_margin": 16.9837, "roe": 12.0827, "roe_waa": 12.08, '
    '"debt_to_assets": 63.6525, "or_yoy": 54.8004, "netprofit_yoy": 41.9839, "q_gsprofit_margin": 23.1532, '
    '"q_sales_yoy": 56.9154, "q_profit_yoy": 38.7891, "eps": 9.51, "bps": 81.9932, "ocfps": 13.0152}, '
    '{"ts_code": "300750.SZ", "end_date": "20260331", "ann_date": "20260416", "grossprofit_margin": 24.8156, '
    '"gross_margin": 32044671000.0, "netprofit_margin": 17.6079, "roe": 5.9731, "roe_waa": 5.98, '
    '"debt_to_assets": 62.3223, "or_yoy": 52.4487, "netprofit_yoy": 48.5237, "q_gsprofit_margin": 24.8156, '
    '"q_sales_yoy": 52.4487, "q_profit_yoy": 52.993, "eps": 4.58, "bps": 78.2773, "ocfps": 7.3796}, '
    '{"ts_code": "300750.SZ", "end_date": "20251231", "ann_date": "20260310", "grossprofit_margin": 26.2728, '
    '"gross_margin": 111318537000.0, "netprofit_margin": 18.1227, "roe": 24.7249, "roe_waa": 24.91, '
    '"debt_to_assets": 61.9393, "or_yoy": 17.0406, "netprofit_yoy": 42.2834, "q_gsprofit_margin": 28.2114, '
    '"q_sales_yoy": 36.5765, "q_profit_yoy": 60.339, "eps": 16.14, "bps": 73.8655, "ocfps": 29.1906}, '
    '{"ts_code": "300750.SZ", "end_date": "20250930", "ann_date": "20251021", "grossprofit_margin": 25.3098, '
    '"gross_margin": 71644840000.0, "netprofit_margin": 18.4748, "roe": 17.4754, "roe_waa": 17.76, '
    '"debt_to_assets": 61.2745, "or_yoy": 9.2753, "netprofit_yoy": 36.2018, "q_gsprofit_margin": 25.8022, '
    '"q_sales_yoy": 12.9043, "q_profit_yoy": 43.8635, "eps": 11.02, "bps": 68.8709, "ocfps": 17.6776}, '
    '{"ts_code": "300750.SZ", "end_date": "20250630", "ann_date": "20250731", "grossprofit_margin": 25.023, '
    '"gross_margin": 44762650000.0, "netprofit_margin": 18.0928, "roe": 11.2522, "roe_waa": 11.63, '
    '"debt_to_assets": 62.5927, "or_yoy": 7.2673, "netprofit_yoy": 33.3267, "q_gsprofit_margin": 25.5763, '
    '"q_sales_yoy": 8.2597, "q_profit_yoy": 27.9218, "eps": 6.92, "bps": 64.6859, "ocfps": 12.8719}, '
    '{"ts_code": "300750.SZ", "end_date": "20250331", "ann_date": "20250415", "grossprofit_margin": 24.4077, '
    '"gross_margin": 20674478000.0, "netprofit_margin": 17.5453, "roe": 5.4918, "roe_waa": 5.49, '
    '"debt_to_assets": 64.7433, "or_yoy": 6.185, "netprofit_yoy": 32.8512, "q_gsprofit_margin": 24.4077, '
    '"q_sales_yoy": 6.185, "q_profit_yoy": 32.7448, "eps": 3.18, "bps": 59.3991, "ocfps": 7.4643}, '
    '{"ts_code": "300750.SZ", "end_date": "20241231", "ann_date": "20250315", "grossprofit_margin": 24.4449, '
    '"gross_margin": 88493595000.0, "netprofit_margin": 14.9185, "roe": 22.8252, "roe_waa": 24.13, '
    '"debt_to_assets": 65.2382, "or_yoy": -9.7039, "netprofit_yoy": 15.0119, "q_gsprofit_margin": 15.0355, '
    '"q_sales_yoy": -3.0798, "q_profit_yoy": 7.2221, "eps": 11.58, "bps": 56.0763, "ocfps": 22.0259}, '
    '{"ts_code": "300750.SZ", "end_date": "20240930", "ann_date": "20241019", "grossprofit_margin": 28.185, '
    '"gross_margin": 73011847700.0, "netprofit_margin": 14.9523, "roe": 16.565, "roe_waa": 17.73, '
    '"debt_to_assets": 64.3338, "or_yoy": -12.092, "netprofit_yoy": 15.5901, "q_gsprofit_margin": 31.1698, '
    '"q_sales_yoy": -12.4757, "q_profit_yoy": 25.458, "eps": 8.1894, "bps": 53.8246, "ocfps": 15.3198}, '
    '{"ts_code": "300750.SZ", "end_date": "20240630", "ann_date": "20240727", "grossprofit_margin": 26.5334, '
    '"gross_margin": 44248984800.0, "netprofit_margin": 14.9183, "roe": 11.6084, "roe_waa": 11.39, '
    '"debt_to_assets": 69.264, "or_yoy": -11.8783, "netprofit_yoy": 10.3668, "q_gsprofit_margin": 26.6416, '
    '"q_sales_yoy": -13.1842, "q_profit_yoy": 20.1062, "eps": 5.2017, "bps": 44.6101, "ocfps": 10.1639}, '
    '{"ts_code": "300750.SZ", "end_date": "20240331", "ann_date": "20240416", "grossprofit_margin": 26.4155, '
    '"gross_margin": 21071872300.0, "netprofit_margin": 14.0348, "roe": 5.1845, "roe_waa": 5.18, '
    '"debt_to_assets": 68.4845, "or_yoy": -10.4086, "netprofit_yoy": 7.001, "q_gsprofit_margin": 26.4155, '
    '"q_sales_yoy": -10.4086, "q_profit_yoy": 11.0612, "eps": 2.3909, "bps": 47.2216, "ocfps": 6.4464}]'
)


async def test_multi_period_evidence_labels_every_period_not_positional(make_settings) -> None:
    """回归线上真实事故：宁德时代 fina_indicator 10 期数据（真实抓取，见 D:\\TushareAgent\\demo.db
    session 4bb1229e3dbb turn 614）里，2025H1/2025Q1 曾被原字符位置截断折进「中间省略」，
    模型转而用视野内可见的 2024H1/2024Q1 数值顶替、换标签充数（用户用 Wind 数据交叉核对发现）。

    确定性表格化后，synthesizer 收到的证据摘要必须完整覆盖全部 10 期、按标签而非数组位置
    展示，不再出现覆盖这两期的匿名省略。"""
    tools = FakeToolProvider(
        SPECS, {"stock_financials": ToolResult(content=_CATL_FINA_INDICATOR_JSON, is_error=False)}
    )
    mock = MockLLM([
        _router_response('{"intent":"compare","out_of_scope":false}'),
        ChatResponse(stop_reason="end_turn", text="宁德时代 毛利率 多期对比"),  # rewrite_query（compare 不跳过）
        _tool_use_response("c1", "stock_financials", {"ts_code": "300750.SZ"}),
        _stop_response(),
        ChatResponse(stop_reason="end_turn", text="各期毛利率如上。"),
    ])
    result = await Agent(llm=mock, tools=tools, config=make_settings()).run("宁德时代多期毛利率对比")

    assert result.stopped_reason == "end_turn"
    synth_content = mock.calls[4]["messages"][0]["content"]
    for label in ("2026H1", "2026Q1", "2025年报", "2025前三季度", "2025H1", "2025Q1", "2024年报", "2024前三季度", "2024H1", "2024Q1"):
        assert label in synth_content, f"缺少期次标签 {label}"
    assert "中间省略" not in synth_content  # 不应再出现覆盖任意期次的匿名省略
    assert "25.023" in synth_content and "17.5453" in synth_content  # 真实 2025H1 数值（曾被顶替丢失）
    assert "24.4077" in synth_content  # 真实 2025Q1 毛利率（曾被顶替丢失）


async def test_empty_evidence_fallback(make_settings) -> None:
    tools = FakeToolProvider(
        SPECS, {"stock_price_range": ToolResult(content="[]", is_error=False)}
    )
    mock = MockLLM(
        [
            _router_response('{"intent":"market","out_of_scope":false}'),
            _tool_use_response("c1", "stock_price_range", {"name": "比亚迪"}),
            _stop_response(),  # 看到空结果后判定停止 → 仍无证据 → no_evidence 兜底
        ]
    )
    agent = Agent(llm=mock, tools=tools, config=make_settings())
    result = await agent.run("比亚迪最近价格")

    assert result.stopped_reason == "fallback"
    assert "未能取到可靠数据" in result.final_text
    # "[]" 不是错误，但为空数据 → 不算证据
    assert result.tool_results[0].is_error is False
    assert result.citations == []
    assert len(mock.calls) == 3  # router + tool选择 + 停止判定（market 意图跳过 rewrite_query）


async def test_tool_error_becomes_fallback(make_settings) -> None:
    tools = FakeToolProvider(SPECS, {}, raise_on={"stock_price_range"})
    mock = MockLLM(
        [
            _router_response('{"intent":"market","out_of_scope":false}'),
            _tool_use_response("c1", "stock_price_range", {"name": "比亚迪"}),
            _stop_response(),  # 看到失败结果后判定停止 → 仍无证据 → no_evidence 兜底
        ]
    )
    agent = Agent(llm=mock, tools=tools, config=make_settings())
    result = await agent.run("比亚迪")

    assert result.stopped_reason == "fallback"
    assert "未能取到可靠数据" in result.final_text
    assert len(result.tool_results) == 1
    assert result.tool_results[0].is_error is True  # 异常 → is_error，不算证据
    assert len(mock.calls) == 3  # router + tool选择 + 停止判定（market 意图跳过 rewrite_query）


class _RaisingLLM:
    """LLM 逐次调用都抛异常（模拟瞬时网络/流失败）。"""

    def __init__(self) -> None:
        self.calls = 0

    async def chat(self, *, messages, tools, system=None, max_tokens=8192, stream=True, temperature=None, on_text=None, on_thinking=None):
        self.calls += 1
        raise ConnectionError("boom")

    def assistant_message(self, resp):
        return {"role": "assistant", "content": resp.text or None}

    def tool_results_messages(self, results):
        return []


class _LLMRaisesOnToolFrames:
    """chat 正常返回，但渲染工具结果帧时抛错（模拟节点内非 LLM 异常 → 触发 Agent.run 兜底）。"""

    def __init__(self) -> None:
        self.calls = 0

    async def chat(self, *, messages, tools, system=None, max_tokens=8192, stream=True, temperature=None, on_text=None, on_thinking=None):
        self.calls += 1
        if self.calls == 1:  # router
            return ChatResponse(stop_reason="end_turn", text='{"intent":"market","out_of_scope":false}')
        return _tool_use_response("c1", "stock_price_range", {"stock": "比亚迪"})  # tool_rag 选工具

    def assistant_message(self, resp):
        return {"role": "assistant", "content": resp.text or None}

    def tool_results_messages(self, results):
        raise RuntimeError("frame boom")


async def test_llm_error_routes_to_fallback(make_settings) -> None:
    """节点内 llm.chat 抛错 → 就地兜底 → fallback，不抛 ExceptionGroup。"""
    llm = _RaisingLLM()
    agent = Agent(llm=llm, tools=FakeToolProvider(SPECS, {}), config=make_settings())
    result = await agent.run("比亚迪最近价格")  # 不应抛异常

    assert result.stopped_reason == "fallback"
    assert "不构成投资建议" in result.final_text
    assert llm.calls >= 2  # router 默认 market 继续 → tool_rag 兜底 node_error → fallback


async def test_agent_run_guard_catches_node_error(make_settings) -> None:
    """节点内非 LLM 异常（渲染 tool 帧抛错）→ TaskGroup/ExceptionGroup → Agent.run 兜底转 error，不外泄。"""
    llm = _LLMRaisesOnToolFrames()
    agent = Agent(llm=llm, tools=FakeToolProvider(SPECS, {}), config=make_settings())
    result = await agent.run("比亚迪")  # 不应抛异常

    assert result.stopped_reason == "error"
    assert "不构成投资建议" in result.final_text


def _fake_retriever(results: list[dict]):
    """duck-type retriever（对齐 RagRetriever.retrieve(plan)），记录调用次数。"""

    class _Fake:
        def __init__(self) -> None:
            self.calls = 0
            self.last_plan = None

        async def retrieve(self, plan, *, on_funnel=None):
            self.calls += 1
            self.last_plan = plan
            if on_funnel is not None:
                on_funnel({"recall_raw": 1, "fused": 1, "sections": 1, "pool": 1, "reranked": 1, "final": 1, "rerank_degraded": False})
            from demomcp.rag.schemas import RagChunk

            return [RagChunk(**r) for r in results]

    return _Fake()


async def test_rag_retrieval_adds_evidence(make_settings) -> None:
    """retriever 返回 RagChunk → 进入 synthesizer，structured.sources/citations 含 rag 溯源。"""
    from demomcp.graph.builder import build_research_graph

    cfg = make_settings()
    tools = FakeToolProvider(SPECS, {"stock_price_range": ToolResult(content='[{"close":1604.9}]', is_error=False)})
    mock = MockLLM(
        [
            _router_response('{"intent":"report","out_of_scope":false}'),
            ChatResponse(stop_reason="end_turn", text="比亚迪 2024 研发投入"),  # rewrite_query
            _tool_use_response("c1", "stock_price_range", {"stock": "比亚迪"}),
            _stop_response(),  # 数据已足够 → 去合成
            ChatResponse(stop_reason="end_turn", text="研发投入见比亚迪2024年报。"),
        ]
    )
    retriever = _fake_retriever(
        [{
            "doc_id": "d1", "doc_title": "比亚迪2024年报", "chunk_index": 0, "text": "研发投入约 X 亿元", "score": 0.9,
            "metadata": {"company": "比亚迪", "year": 2024, "section_path": ["3.2 研发投入"], "page_start": 42,
                         "page_end": 42, "heading": "研发投入", "block_type": "paragraph", "doc_title": "比亚迪2024年报"},
        }]
    )
    graph = build_research_graph(mock, tools, SPECS, max_tokens=cfg.ds_max_tokens, disclaimer=cfg.disclaimer, base_system=cfg.system_prompt, retriever=retriever)
    state = {"messages": [{"role": "user", "content": "比亚迪研发投入"}], "original_query": "比亚迪研发投入", "out_of_scope": False,
             "tool_results": [], "evidence": [], "rag_chunks": [], "citations": [], "usage": None}
    out = await graph.ainvoke(state, config={"configurable": {}})

    assert out["stopped_reason"] == "end_turn"
    assert retriever.calls == 1
    structured = out["structured"]
    assert structured is not None
    rag_sources = [s for s in structured["sources"] if s["type"] == "rag"]
    assert rag_sources and "比亚迪2024年报" in rag_sources[0]["title"]
    assert rag_sources[0]["page"] == "42"
    rag_cites = [c for c in structured["citations"] if c["type"] == "rag"]
    assert rag_cites and rag_cites[0]["inline"]  # 非空内联标记


async def test_rag_retrieval_no_tool_use(make_settings) -> None:
    """纯知识问题（LLM 不选任何工具）也应走 RAG 检索 → 进入 synthesizer。

    回归：此前 tool_rag 在「无工具」时提前以 no_progress 返回，RAG 分支根本跑不到
    （retriever.calls==0、无 rag 证据、stopped_reason=fallback）——即「智能体接入 RAG 无法命中」。
    """
    from demomcp.graph.builder import build_research_graph

    cfg = make_settings()
    tools = FakeToolProvider(SPECS, {})
    mock = MockLLM(
        [
            _router_response('{"intent":"report","out_of_scope":false}'),
            ChatResponse(stop_reason="end_turn", text="比亚迪 2025 研发投入"),  # rewrite_query
            ChatResponse(stop_reason="end_turn", text="研发投入情况如下："),  # 无 tool_uses
            ChatResponse(stop_reason="end_turn", text="比亚迪2025年研发投入。"),
        ]
    )
    retriever = _fake_retriever(
        [{
            "doc_id": "d1", "doc_title": "比亚迪2024年报", "chunk_index": 0, "text": "研发投入约 X 亿元", "score": 0.9,
            "metadata": {"company": "比亚迪", "year": 2024, "section_path": ["3.2 研发投入"], "page_start": 42,
                         "page_end": 42, "heading": "研发投入", "block_type": "paragraph", "doc_title": "比亚迪2024年报"},
        }]
    )
    graph = build_research_graph(mock, tools, SPECS, max_tokens=cfg.ds_max_tokens, disclaimer=cfg.disclaimer, base_system=cfg.system_prompt, retriever=retriever)
    state = {"messages": [{"role": "user", "content": "比亚迪2025年研发投入"}], "original_query": "比亚迪2025年研发投入",
             "out_of_scope": False, "tool_results": [], "evidence": [], "rag_chunks": [], "citations": [], "usage": None}
    out = await graph.ainvoke(state, config={"configurable": {}})

    assert out["stopped_reason"] == "end_turn"  # 不是 fallback（修复前是 no_progress→fallback）
    assert retriever.calls == 1  # RAG 确实被检索（修复前 calls==0）
    structured = out["structured"]
    rag_sources = [s for s in structured["sources"] if s["type"] == "rag"]
    assert rag_sources and "比亚迪2024年报" in rag_sources[0]["title"]
    assert rag_sources[0]["page"] == "42"
    rag_cites = [c for c in structured["citations"] if c["type"] == "rag"]
    assert rag_cites and rag_cites[0]["inline"]


def _rag_chunk() -> dict:
    return {"doc_id": "d1", "doc_title": "比亚迪2025年报", "chunk_index": 0, "text": "研发投入约 X 亿元", "score": 0.9,
            "metadata": {"company": "比亚迪", "year": 2025, "section_path": ["3.2 研发投入"], "page_start": 42,
                         "page_end": 42, "heading": "研发投入", "block_type": "paragraph", "doc_title": "比亚迪2025年报"}}


async def test_tool_fail_rag_succeeds(make_settings) -> None:
    """工具抛异常(is_error)→非证据、不崩；RAG 命中 → 靠 RAG 兜底走合成器（req#1：工具失败不直接返回）。"""
    from demomcp.graph.builder import build_research_graph

    cfg = make_settings()
    tools = FakeToolProvider(SPECS, {}, raise_on={"stock_price_range"})  # 该工具抛异常 → is_error
    mock = MockLLM(
        [
            _router_response('{"intent":"report","out_of_scope":false}'),
            ChatResponse(stop_reason="end_turn", text="比亚迪 2025 研发投入"),  # rewrite_query
            _tool_use_response("c1", "stock_price_range", {"stock": "比亚迪"}),
            _stop_response(),  # 工具失败但有 RAG 兜底 → 数据已足 → 去合成
            ChatResponse(stop_reason="end_turn", text="据年报，研发投入约X亿元。"),
        ]
    )
    retriever = _fake_retriever([_rag_chunk()])
    graph = build_research_graph(mock, tools, SPECS, max_tokens=cfg.ds_max_tokens, disclaimer=cfg.disclaimer, base_system=cfg.system_prompt, retriever=retriever)
    state = {"messages": [{"role": "user", "content": "比亚迪2025年研发投入"}], "original_query": "比亚迪2025年研发投入",
             "out_of_scope": False, "tool_results": [], "evidence": [], "rag_chunks": [], "citations": [], "usage": None}
    out = await graph.ainvoke(state, config={"configurable": {}})

    assert out["stopped_reason"] == "end_turn"  # 工具失败不崩、不直接返回
    assert retriever.calls == 1
    st = out["structured"]
    rag = [s for s in st["sources"] if s["type"] == "rag"]
    assert rag and rag[0]["title"] == "比亚迪2025年报"  # 靠 RAG 证据兜底


async def test_tool_and_rag_parallel_merge(make_settings) -> None:
    """工具成功 + RAG 命中 → structured.sources 同时含 tool 与 rag（两路并行→汇总，req#2）。"""
    from demomcp.graph.builder import build_research_graph

    cfg = make_settings()
    tools = FakeToolProvider(SPECS, {"stock_price_range": ToolResult(content='[{"close":1604.9}]', is_error=False)})
    mock = MockLLM(
        [
            _router_response('{"intent":"report","out_of_scope":false}'),
            ChatResponse(stop_reason="end_turn", text="比亚迪 2025 研发投入"),  # rewrite_query
            _tool_use_response("c1", "stock_price_range", {"stock": "比亚迪"}),
            _stop_response(),  # 工具成功 + RAG 命中 → 数据已足 → 去合成
            ChatResponse(stop_reason="end_turn", text="价格与研发信息如下。"),
        ]
    )
    retriever = _fake_retriever([_rag_chunk()])
    graph = build_research_graph(mock, tools, SPECS, max_tokens=cfg.ds_max_tokens, disclaimer=cfg.disclaimer, base_system=cfg.system_prompt, retriever=retriever)
    state = {"messages": [{"role": "user", "content": "比亚迪2025年研发投入"}], "original_query": "比亚迪2025年研发投入",
             "out_of_scope": False, "tool_results": [], "evidence": [], "rag_chunks": [], "citations": [], "usage": None}
    out = await graph.ainvoke(state, config={"configurable": {}})

    st = out["structured"]
    assert any(s["type"] == "tool" for s in st["sources"])
    assert any(s["type"] == "rag" for s in st["sources"])
    assert retriever.calls == 1


class _SelectRaiseMock:
    """router/合成器（空 tools）正常；选工具轮（tools 非空）抛异常（模拟 LLM 选工具失败）。"""

    def __init__(self, responses) -> None:
        self._empty = list(responses)
        self._i = 0
        self.calls = []

    async def chat(self, *, messages, tools, system=None, max_tokens=8192, stream=True, temperature=None, on_text=None, on_thinking=None):
        self.calls.append(bool(tools))
        if tools:
            raise RuntimeError("select boom")
        resp = self._empty[self._i]
        self._i += 1
        return resp

    def assistant_message(self, resp):
        return {"role": "assistant", "content": resp.text}

    def tool_results_messages(self, results):
        return [{"role": "tool", "tool_call_id": tu.id, "content": tr.content} for tu, tr in results]


async def test_tool_select_error_uses_rag(make_settings) -> None:
    """选工具 LLM 抛异常 → 仍先试 RAG；RAG 命中 → 进合成器（不是 node_error/直接返回）。"""
    from demomcp.graph.builder import build_research_graph

    cfg = make_settings()
    tools = FakeToolProvider(SPECS, {})
    mock = _SelectRaiseMock(
        [
            _router_response('{"intent":"report","out_of_scope":false}'),
            ChatResponse(stop_reason="end_turn", text="比亚迪 2025 研发投入"),  # rewrite_query
            ChatResponse(stop_reason="end_turn", text="据年报，研发投入约X亿元。"),
        ]
    )
    retriever = _fake_retriever([_rag_chunk()])
    graph = build_research_graph(mock, tools, SPECS, max_tokens=cfg.ds_max_tokens, disclaimer=cfg.disclaimer, base_system=cfg.system_prompt, retriever=retriever)
    state = {"messages": [{"role": "user", "content": "比亚迪2025年研发投入"}], "original_query": "比亚迪2025年研发投入",
             "out_of_scope": False, "tool_results": [], "evidence": [], "rag_chunks": [], "citations": [], "usage": None}
    out = await graph.ainvoke(state, config={"configurable": {}})

    assert out["stopped_reason"] == "end_turn"  # 选工具失败被 RAG 兜底，未 node_error
    assert retriever.calls == 1
    rag = [s for s in out["structured"]["sources"] if s["type"] == "rag"]
    assert rag


def test_structured_one_card_per_evidence() -> None:
    """每条证据各出一条 source/citation（不按来源去重），编号与正文 [n] 对齐；rag 卡带内容摘要区分重复来源。"""
    from demomcp.graph.nodes import _structured

    ev = [
        {"source_type": "rag", "source": "比亚迪 2025", "content": "A",
         "cite": {"inline": "[比亚迪2025年报 - 第42页 3.2 研发投入]", "title": "比亚迪2025年报",
                  "company": "比亚迪", "year": 2025, "page": "42", "section": "3.2"}},
        {"source_type": "rag", "source": "比亚迪 2025", "content": "B",
         "cite": {"inline": "[比亚迪2025年报 - 第42页 3.2 研发投入]", "title": "比亚迪2025年报",
                  "company": "比亚迪", "year": 2025, "page": "42", "section": "3.2"}},
        {"source_type": "tool", "source": "stock_price_range", "content": "[]"},
    ]
    st = _structured("答案", "report", "factual", ev, {})
    rag_sources = [s for s in st["sources"] if s["type"] == "rag"]
    rag_cites = [c for c in st["citations"] if c["type"] == "rag"]
    assert len(rag_sources) == 2  # 不按来源去重：两条同来源证据各出一张卡
    assert len(rag_cites) == 2
    assert rag_sources[0]["excerpt"] == "A"
    assert rag_sources[1]["excerpt"] == "B"  # 内容摘要区分同来源重复块
    assert [c["ref_index"] for c in rag_cites] == [1, 2]  # 1 基，与正文 [n] 对齐
    assert len([s for s in st["sources"] if s["type"] == "tool"]) == 1
    assert len(st["claims"]) == 3  # claims 逐证据


async def test_structured_output_present(make_settings) -> None:
    tools = FakeToolProvider(SPECS, {"stock_price_range": ToolResult(content='[{"close":1604.9}]', is_error=False)})
    mock = MockLLM(
        [
            _router_response('{"intent":"market","out_of_scope":false}'),
            _tool_use_response("c1", "stock_price_range", {"stock": "比亚迪"}),
            _stop_response(),  # 数据已足 → 去合成
            ChatResponse(stop_reason="end_turn", text="比亚迪区间约 1604.9 元。"),
        ]
    )
    result = await Agent(llm=mock, tools=tools, config=make_settings()).run("比亚迪")
    assert result.structured is not None
    assert {"answer", "sources", "citations", "claims", "metadata"} <= set(result.structured)
    assert result.structured["answer"]
    assert result.structured["metadata"]["tool_results"] == 1
    assert result.structured["sources"][0]["type"] == "tool"


async def test_params_normalized_and_validation(make_settings) -> None:
    # A) 契约 JSON 带 data → 证据入账，请求参数取自工具调用的真实入参（tu.input），
    # 归一化进 structured.metadata.request（不是从返回体里挖 source.params——官方
    # Tushare/万得/iFind 的真实信封里从来没有这个字段，见 nodes.py 的注释）。
    tools = FakeToolProvider(
        SPECS,
        {"stock_price_range": ToolResult(content='{"ok":true,"data":{"return_pct":2.65}}', is_error=False)},
    )
    mock = MockLLM(
        [
            _router_response('{"intent":"market","out_of_scope":false}'),
            _tool_use_response("c1", "stock_price_range", {"ts_code": "002594.SZ", "start_date": "20260601", "end_date": "20260731"}),
            _stop_response(),  # 数据已足 → 去合成
            ChatResponse(stop_reason="end_turn", text="涨幅 2.65%。"),
        ]
    )
    r = await Agent(llm=mock, tools=tools, config=make_settings()).run("比亚迪")
    req = r.structured["metadata"]["request"]
    assert req.get("ts_code") == "002594.SZ"
    assert req.get("start_date") == "20260601"
    assert r.structured["metadata"]["validation"]["normalized"] is True

    # B) 友好校验失败（ok=false、无 data）→ 记入 validation.errors、不入证据 → fallback，且文本/结构化可见
    tools2 = FakeToolProvider(SPECS, {"stock_price_range": ToolResult(content='{"ok":false,"msg":"无法解析日期：近3月"}', is_error=False)})
    mock2 = MockLLM(
        [
            _router_response('{"intent":"market","out_of_scope":false}'),
            _tool_use_response("c1", "stock_price_range", {"stock": "比亚迪", "start": "近3月"}),
            _stop_response(),  # 仍有校验失败、无证据 → 停止 → no_evidence 兜底
        ]
    )
    r2 = await Agent(llm=mock2, tools=tools2, config=make_settings()).run("比亚迪")
    assert r2.stopped_reason == "fallback"
    st2 = r2.structured
    assert st2 is not None
    assert any("无法解析日期" in e for e in st2["metadata"]["validation"]["errors"])
    assert "无法解析日期" in r2.final_text  # 校验问题进正文


async def test_strategy_mapping(make_settings) -> None:
    from demomcp.graph.nodes import _strategy

    assert _strategy("report") == "factual"
    assert _strategy("compare") == "structural"
    assert _strategy("market") == "auto"
    assert _strategy(None) == "auto"


def test_infer_filters() -> None:
    from demomcp.rag.query_build import infer_filters

    f1 = infer_filters("比亚迪2025年研发投入")
    assert f1.company == "比亚迪" and f1.year == 2025
    f2 = infer_filters("宁德时代2024年报毛利率")
    assert f2.company == "宁德时代" and f2.year == 2024
    f3 = infer_filters("最近涨幅怎么样")
    assert f3.company is None and f3.year is None


async def test_on_process_events(make_settings) -> None:
    """节点经 on_process 抛处理过程事件：intent/plan/stage 依序可用。"""
    tools = FakeToolProvider(SPECS, {"stock_price_range": ToolResult(content='[{"close":1604.9}]', is_error=False)})
    mock = MockLLM(
        [
            _router_response('{"intent":"report","out_of_scope":false}'),
            ChatResponse(stop_reason="end_turn", text="比亚迪 研发投入"),  # rewrite_query
            _tool_use_response("c1", "stock_price_range", {"stock": "比亚迪"}),
            _stop_response(),  # 数据已足 → 去合成
            ChatResponse(stop_reason="end_turn", text="研发投入见年报。"),
        ]
    )
    events: list[tuple[str, dict]] = []

    async def on_process(kind: str, data: dict) -> None:
        events.append((kind, data))

    await Agent(llm=mock, tools=tools, config=make_settings()).run("比亚迪研发投入", on_process=on_process)

    kinds = [k for k, _ in events]
    assert kinds[0] == "intent"           # 意图最先
    assert "intent" in kinds and "plan" in kinds and "stage" in kinds
    intent_ev = next(d for k, d in events if k == "intent")
    assert intent_ev["intent"] == "report"
    assert intent_ev["strategy"] == "factual"


async def test_rag_funnel_process_event(make_settings) -> None:
    """retriever.retrieve 暴露漏斗计数 → 节点 emit on_process("funnel", counts) 供前端观察。"""
    from demomcp.graph.builder import build_research_graph

    cfg = make_settings()
    tools = FakeToolProvider(SPECS, {})
    # 无工具分支（纯知识问题），仅 RAG 一路证据；retriever 会调 on_funnel
    mock = MockLLM(
        [
            _router_response('{"intent":"report","out_of_scope":false}'),
            ChatResponse(stop_reason="end_turn", text="比亚迪 2024 研发投入"),  # rewrite_query
            ChatResponse(stop_reason="end_turn", text="研发投入见年报。"),  # 无 tool_uses → 仅 RAG
            ChatResponse(stop_reason="end_turn", text="比亚迪2024年研发投入。"),  # synthesizer
        ]
    )
    retriever = _fake_retriever([{
        "doc_id": "d1", "doc_title": "比亚迪2024年报", "chunk_index": 0, "text": "研发投入约 X 亿元", "score": 0.9,
        "metadata": {"company": "比亚迪", "year": 2024, "section_path": ["3.2 研发投入"], "page_start": 42,
                     "page_end": 42, "heading": "研发投入", "block_type": "paragraph", "doc_title": "比亚迪2024年报"},
    }])
    graph = build_research_graph(mock, tools, SPECS, max_tokens=cfg.ds_max_tokens, disclaimer=cfg.disclaimer, base_system=cfg.system_prompt, retriever=retriever)
    events: list[tuple[str, dict]] = []

    async def on_process(kind: str, data: dict) -> None:
        events.append((kind, data))

    state = {"messages": [{"role": "user", "content": "比亚迪研发投入"}], "original_query": "比亚迪研发投入", "out_of_scope": False,
             "tool_results": [], "evidence": [], "rag_chunks": [], "citations": [], "usage": None}
    out = await graph.ainvoke(state, config={"configurable": {"on_process": on_process}})

    assert out["stopped_reason"] == "end_turn"
    assert retriever.calls == 1
    funnel_ev = next((d for k, d in events if k == "funnel"), None)
    assert funnel_ev is not None
    assert set(funnel_ev) == {"recall_raw", "fused", "sections", "pool", "reranked", "final", "rerank_degraded", "unique_sources"}


async def test_rewrite_query_composes_query() -> None:
    """rewrite_query 节点确定性产出检索子句：原句 + 公司 + 年份 + 扩词，去重、异常回退。"""
    from demomcp.graph.nodes import make_rewrite_query

    node = make_rewrite_query()
    out = await node({"original_query": "比亚迪2024研发投入", "out_of_scope": False}, {})
    rq = out["rewritten_query"]
    assert rq.startswith("比亚迪2024研发投入")
    assert "比亚迪" in rq
    assert "2024" in rq
    # dedup：重复片段不叠加
    assert len(rq.split()) == len(set(rq.split())) or "重复" not in rq
    # 异常/空查询回退原句
    out_empty = await node({"original_query": "", "out_of_scope": False}, {})
    assert out_empty["rewritten_query"] == ""
    # 无公司/年份时不追加空 token
    out_plain = await node({"original_query": "研发投入情况", "out_of_scope": False}, {})
    assert out_plain["rewritten_query"] == "研发投入情况"


async def test_rewrite_query_llm_semantic() -> None:
    """LLM 语义改写胜出：提供 llm 时用其改写结果；LLM 抛错回退确定性（不崩）。"""
    from demomcp.graph.nodes import make_rewrite_query
    from demomcp.interfaces.types import ChatResponse
    from demomcp.providers.llm.mock import MockLLM

    # intent="report"：_should_rag 为 True，rewrite_query 才会真正调用 LLM
    # （market 等不跑 RAG 的意图下该节点直接跳过 LLM 调用，见 make_rewrite_query 的门控逻辑）。
    llm = MockLLM([ChatResponse(stop_reason="end_turn", text="比亚迪 2024 年研发投入 研发费用")])
    node = make_rewrite_query(llm)
    out = await node({"original_query": "比亚迪去年研发花多少", "out_of_scope": False, "intent": "report"}, {})
    assert out["rewritten_query"] == "比亚迪 2024 年研发投入 研发费用"

    class _Boom:
        async def chat(self, **kwargs):
            raise ConnectionError("boom")

    out2 = await make_rewrite_query(_Boom())(
        {"original_query": "比亚迪去年研发花多少", "out_of_scope": False, "intent": "report"}, {}
    )
    assert out2["rewritten_query"]  # 兜底到确定性改写，仍非空


def test_rewrite_query_domain_expansion() -> None:
    """口语→年报措辞：海外/乘用车/拓展规划 应补出 汽车/整车/出海/全球化布局 等报告术语。"""
    from demomcp.graph.nodes import _deterministic_rewrite

    rq = _deterministic_rewrite("比亚迪2025年海外乘用车业务拓展规划")
    assert "比亚迪" in rq and "2025" in rq
    for term in ("汽车", "新能源汽车", "整车", "海外市场", "全球化布局", "出海"):
        assert term in rq, f"缺 {term}: {rq}"


def test_fin_terms_cover_domain_vocab() -> None:
    """FIN_TERMS 扩展后：海外/乘用车/产销量 进入 concepts（供 BM25 关键词扩展）。"""
    from demomcp.rag.query_build import extract_concepts

    concepts = extract_concepts("海外乘用车出口产销量")
    assert "海外" in concepts and "乘用车" in concepts and "产销量" in concepts


# ---------------------------------------------------------------------------
# 报告 skill（快报）注册表 + router 命中
# ---------------------------------------------------------------------------


def _tracker_system(base: str = "base") -> str:
    from demomcp.graph.skills import get_skill

    skill = get_skill("ai_supply_chain_tracker")
    assert skill is not None
    return skill.system_prompt(base)


def test_skill_registry_tracker_template() -> None:
    """快报 skill 的合成器提示词含六段固定结构 + 「数据未接入/不编造」约束，且可扩展注册表能按 id 命中。"""
    from demomcp.graph.skills import SKILLS, get_skill

    assert get_skill("ai_supply_chain_tracker") is not None
    assert get_skill("not_a_skill") is None
    assert get_skill(None) is None
    # SKILLS = 内置（tracker 恒为首位）+ claude-for 导入技能库（63 条，见 tests/test_skill_loader.py）
    ids = [s.id for s in SKILLS]
    assert ids[0] == "ai_supply_chain_tracker"
    assert len(ids) == len(set(ids)), "skill id 必须全局唯一"
    assert get_skill("china-dcf") is not None, "导入技能应可按 id 命中"

    text = _tracker_system()
    for marker in ("板块概览", "标的池行情速览", "关键公告", "业绩预告异动", "产业链催化", "一句话研判", "数据未接入", "不编造"):
        assert marker in text, f"缺 {marker}: {text}"


def test_synth_system_not_polluted_by_tracker() -> None:
    """普通意图（无 skill）的 synth_system 不夹带快报模板字段——skill 只在命中时生效。"""
    from demomcp.graph.prompts import synth_system

    assert "一句话研判" not in synth_system("base", intent="market")
    assert "板块概览" not in synth_system("base", intent="report")


def test_should_rag_strategy_with_skill() -> None:
    """skill 命中时 _should_rag/_strategy 由 skill 覆盖；无 skill 时保持原行为。"""
    from demomcp.graph.nodes import _should_rag, _strategy
    from demomcp.graph.skills import get_skill

    tracker = get_skill("ai_supply_chain_tracker")
    assert tracker is not None
    assert _should_rag("report", skill=tracker) is False  # 快报跳过年报 RAG
    assert _strategy("report", skill=tracker) == "auto"   # 快报用默认检索策略
    # 原行为保持
    assert _should_rag("market", skill=None) is False
    assert _should_rag("report", skill=None) is True
    assert _strategy("report", skill=None) == "factual"
    assert _strategy(None, skill=None) == "auto"


async def test_router_selects_skill(make_settings) -> None:
    """router 识别到快报意图 → on_process("intent", …) 携带 skill 字段；无证据时稳定走 fallback（不崩）。"""
    from demomcp.interfaces.types import ChatResponse

    tools = FakeToolProvider(SPECS, {})
    mock = MockLLM([ChatResponse(stop_reason="end_turn", text='{"intent":"report","skill":"ai_supply_chain_tracker","out_of_scope":false}')])
    events: list[tuple[str, dict]] = []

    async def on_process(kind: str, data: dict) -> None:
        events.append((kind, data))

    result = await Agent(llm=mock, tools=tools, config=make_settings()).run(
        "生成 AI 算力产业链今日跟踪快报", on_process=on_process
    )

    intent_ev = next((d for k, d in events if k == "intent"), None)
    assert intent_ev is not None
    assert intent_ev["skill"] == "ai_supply_chain_tracker"
    assert intent_ev["intent"] == "report"
    # 快报应跳过年报 RAG → 空证据 → fallback（稳定、不崩）
    assert result.stopped_reason == "fallback"


async def test_synthesizer_skips_llm_when_render() -> None:
    """skill 提供 render 时，synthesizer 不走 LLM（no llm.chat），由纯代码把 evidence 渲染成六段，structured 带 skill。"""
    from demomcp.graph.nodes import make_synthesizer
    from demomcp.graph.skills import SKILLS

    mock = MockLLM([ChatResponse(stop_reason="end_turn", text="不应被调用")])
    node = make_synthesizer(mock, max_tokens=128, disclaimer="仅供研究参考", base_system="base", skills=SKILLS)
    state = {
        "messages": [{"role": "user", "content": "生成 AI 算力产业链今日跟踪快报"}],
        "original_query": "生成 AI 算力产业链今日跟踪快报",
        "intent": "report",
        "skill": "ai_supply_chain_tracker",
        "evidence": [{"source_type": "tool", "source": "daily", "content": '[{"name":"新易盛","pct_change":4.5,"close":448.08}]'}],
        "tool_results": [],
        "rag_chunks": [],
        "usage": None,
    }
    out = await node(state, {"configurable": {"on_text": None, "on_thinking": None}})

    assert mock.calls == []  # 渲染路径不调用 LLM
    assert "板块概览" in out["final_answer"] and "标的池行情速览" in out["final_answer"]
    assert "数据未接入" in out["final_answer"]  # 板块段无数据 → 显式标注
    assert "新易盛" in out["final_answer"] and "+4.50%" in out["final_answer"]  # 行情行由代码渲染
    assert "仅供研究参考" in out["final_answer"]  # 免责声明已追加
    assert out["structured"]["skill"] == "ai_supply_chain_tracker"
    assert out["structured"]["strategy"] == "auto"
    assert out["stopped_reason"] == "end_turn"


async def test_tool_rag_reveals_only_curated_subset(make_settings) -> None:
    """tool_rag 只把相关性子集喂给 LLM：meta 恒在、相关工具在、不相关被剪、数量受限。"""
    full = [
        ToolSpec("list_apis"), ToolSpec("get_api_info"), ToolSpec("query"), ToolSpec("stock_basic"),
        ToolSpec("daily"), ToolSpec("adj_factor"), ToolSpec("income"), ToolSpec("fina_indicator"),
        ToolSpec("bond_basic"), ToolSpec("index_daily"), ToolSpec("top_list"),
    ]
    tools = FakeToolProvider(
        full,
        {"income": ToolResult(content='{"ok":true,"data":{"revenue":123},"source":{"params":{"period":"20241231"}}}', is_error=False)},
    )
    mock = MockLLM([
        _router_response('{"intent":"report","out_of_scope":false}'),
        ChatResponse(stop_reason="end_turn", text="贵州茅台 营收 财务"),
        _tool_use_response("c1", "income", {"period": "20241231"}),
        ChatResponse(stop_reason="end_turn", text="营收数据已返回。"),
    ])
    agent = Agent(llm=mock, tools=tools, config=make_settings(tool_max_revealed=4))
    await agent.run("贵州茅台营收")

    # call 1 = tool_rag 选工具轮：收到的是裁剪后的子集
    revealed = mock.calls[2]["tools"]
    rnames = [t.name for t in revealed]
    assert "list_apis" in rnames and "query" in rnames  # meta 恒在
    assert "income" in rnames  # 相关工具保留
    assert "bond_basic" not in rnames  # 不相关被剪
    assert len(rnames) <= 4 + 4  # meta(4) + cap(4)


async def test_tool_rag_always_reveals_wind_meta_trio(make_settings) -> None:
    """万得懒发现三件套（wind_list_apis/wind_get_api_info/wind_query）恒被揭示，
    不受 tool_max_revealed 收紧、也不受查询相关性打分影响——它们和 Tushare 的 meta 走同一机制。"""
    full = [
        ToolSpec("list_apis"), ToolSpec("get_api_info"), ToolSpec("query"), ToolSpec("stock_basic"),
        ToolSpec("wind_list_apis"), ToolSpec("wind_get_api_info"), ToolSpec("wind_query"),
        ToolSpec("daily"), ToolSpec("bond_basic"),
    ]
    tools = FakeToolProvider(full, {})
    mock = MockLLM([
        _router_response('{"intent":"market","out_of_scope":false}'),
        ChatResponse(stop_reason="end_turn", text="没有工具可用。"),
    ])
    agent = Agent(llm=mock, tools=tools, config=make_settings(tool_max_revealed=0))
    await agent.run("和金融毫不相关的一句话")

    revealed = mock.calls[1]["tools"]  # market 意图跳过 rewrite_query，select 轮是第 2 次调用
    rnames = {t.name for t in revealed}
    assert {"wind_list_apis", "wind_get_api_info", "wind_query"} <= rnames
    assert {"list_apis", "get_api_info", "query", "stock_basic"} <= rnames


async def test_curation_intent_treats_active_skill_as_report(make_settings) -> None:
    """命中 skill 时即便 router 猜成 market 意图，取数引导与工具揭示也按 report 力度处理。

    实测过的真实坑：forced_skill 场景下 router 看不到技能清单，对「寒武纪」这类裸标的词几乎总猜成
    market；而 claude-for 导入技能各自的 tool_families 用的是原环境工具名（get_financials/wind_/ifind_
    等），跟本部署 Tushare 官方接口名（income/balancesheet/cashflow…）对不上——两者叠加会让模型连
    Tushare 财务接口都看不到，整份「业绩点评报告」查不到任何财报数据。这里用一个刻意只声明错误
    tool_families 的测试 skill 复现该坑，验证 curation_intent 能兜底。"""
    from demomcp.graph.builder import build_research_graph
    from demomcp.graph.skills import Skill

    full = SPECS + [
        ToolSpec("income", "利润表"), ToolSpec("balancesheet", "资产负债表"),
        ToolSpec("cashflow", "现金流量表"), ToolSpec("fina_indicator", "财务指标"),
        ToolSpec("daily_basic", "每日指标"), ToolSpec("forecast", "业绩预告"),
        ToolSpec("express", "业绩快报"),
    ]
    tools = FakeToolProvider(full, {})
    test_skill = Skill(
        id="test-earnings-skill", name="测试业绩点评", description="测试用",
        system_prompt=lambda base: base, tool_families=("get_financials",),  # 刻意用错工具名，模拟真实坑
    )
    cfg = make_settings()
    mock = MockLLM([
        _router_response('{"intent":"market","skill":"test-earnings-skill","out_of_scope":false}'),
        ChatResponse(stop_reason="end_turn", text="没有更多工具可用。"),
    ])
    graph = build_research_graph(
        mock, tools, full, max_tokens=cfg.ds_max_tokens, disclaimer=cfg.disclaimer,
        base_system=cfg.system_prompt, skills=[test_skill],
    )
    state = {"messages": [{"role": "user", "content": "寒武纪"}], "original_query": "寒武纪",
             "out_of_scope": False, "tool_results": [], "evidence": [], "rag_chunks": [], "citations": [], "usage": None}
    await graph.ainvoke(state, config={"configurable": {}})

    revealed = mock.calls[1]["tools"]  # skill 命中（should_rag 默认 False）跳过 rewrite_query
    rnames = {t.name for t in revealed}
    for fam in ("income", "balancesheet", "cashflow", "fina_indicator", "daily_basic", "forecast", "express"):
        assert fam in rnames, f"{fam} 应因 skill 命中被恒揭示，即便 router 猜成 market 且 skill 自身 tool_families 用错名"


# ---------------------------------------------------------------------------
# agentic tool loop：多轮取数（tool_rag → tool_rag 条件自环）
# ---------------------------------------------------------------------------


async def test_agentic_loop_two_round_then_synthesize(make_settings) -> None:
    """真实两轮取数：LLM 第 1 轮选 A，看到结果后再选 B，再判定「数据已足够」停止 → 合成。

    验证：证据跨轮累积、RAG 只查一次、loop_turn 事件状态序、两工具都进 structured.sources。
    """
    from demomcp.graph.builder import build_research_graph

    cfg = make_settings()
    tools = FakeToolProvider(
        SPECS,
        {
            "stock_price_range": ToolResult(content='[{"close":1604.9}]', is_error=False),
            "stock_financials": ToolResult(content='[{"revenue":100}]', is_error=False),
        },
    )
    mock = MockLLM(
        [
            _router_response('{"intent":"report","out_of_scope":false}'),
            ChatResponse(stop_reason="end_turn", text="比亚迪 区间 财务"),  # rewrite_query
            _tool_use_response("c1", "stock_price_range", {"name": "比亚迪"}),  # 第 1 轮 → A
            _tool_use_response("c2", "stock_financials", {"stock": "比亚迪"}),   # 第 2 轮 → B
            _stop_response(),  # 第 3 轮：看到 A、B 数据后判定已足够 → 停止
            ChatResponse(stop_reason="end_turn", text="区间与财务如下。"),
        ]
    )
    retriever = _fake_retriever([_rag_chunk()])
    graph = build_research_graph(mock, tools, SPECS, max_tokens=cfg.ds_max_tokens, disclaimer=cfg.disclaimer, base_system=cfg.system_prompt, retriever=retriever)
    state = {"messages": [{"role": "user", "content": "比亚迪区间与财务"}], "original_query": "比亚迪区间与财务",
             "out_of_scope": False, "tool_results": [], "evidence": [], "rag_chunks": [], "citations": [], "usage": None}
    events: list[tuple[str, dict]] = []

    async def on_process(kind: str, data: dict) -> None:
        events.append((kind, data))

    out = await graph.ainvoke(state, config={"configurable": {"on_process": on_process}})

    assert out["stopped_reason"] == "end_turn"
    assert [name for name, _ in tools.calls] == ["stock_price_range", "stock_financials"]
    assert len(out["tool_results"]) == 2
    assert retriever.calls == 1  # RAG 只查一次（跨两轮不重复检索，防重复证据）
    assert len(mock.calls) == 6  # router / rewrite / 轮1 / 轮2 / 停止判定 / synthesizer
    st = out["structured"]
    assert any(s["type"] == "tool" and s["title"] == "stock_price_range" for s in st["sources"])
    assert any(s["type"] == "tool" and s["title"] == "stock_financials" for s in st["sources"])
    loop_turns = [d for k, d in events if k == "loop_turn"]
    assert [d["status"] for d in loop_turns] == ["continue", "continue", "stop"]
    assert [d["round"] for d in loop_turns] == [1, 2, 3]


async def test_agentic_loop_max_iterations_cutoff(make_settings) -> None:
    """max_iterations=2：第 2 轮后即达上限 → 强制收尾生成，不会再多调一轮工具。"""
    cfg = make_settings(max_iterations=2)
    tools = FakeToolProvider(
        SPECS,
        {
            "stock_price_range": ToolResult(content='[{"close":1604.9}]', is_error=False),
            "stock_financials": ToolResult(content='[{"revenue":100}]', is_error=False),
        },
    )
    mock = MockLLM(
        [
            _router_response('{"intent":"market","out_of_scope":false}'),
            _tool_use_response("c1", "stock_price_range", {"name": "比亚迪"}),  # 第 1 轮 (continue)
            _tool_use_response("c2", "stock_financials", {"stock": "比亚迪"}),   # 第 2 轮 (max-reached)
            ChatResponse(stop_reason="end_turn", text="结果如下。"),  # synthesizer（上限后强制收尾）
        ]
    )
    events: list[tuple[str, dict]] = []

    async def on_process(kind: str, data: dict) -> None:
        events.append((kind, data))

    result = await Agent(llm=mock, tools=tools, config=cfg).run("比亚迪区间与财务", on_process=on_process)

    assert result.stopped_reason == "end_turn"
    assert [name for name, _ in tools.calls] == ["stock_price_range", "stock_financials"]  # 没有第 3 轮
    assert len(result.tool_results) == 2
    assert len(mock.calls) == 4  # router / 轮1 / 轮2 / synthesizer（market 跳过 rewrite，无停止判定轮）
    loop_turns = [d for k, d in events if k == "loop_turn"]
    assert [d["status"] for d in loop_turns] == ["continue", "max-reached"]
    assert [d["round"] for d in loop_turns] == [1, 2]


async def test_quick_mode_caps_at_one_tool_round(make_settings) -> None:
    """mode="quick"：无论 Settings.max_iterations 配多大，只跑一轮取数就强制收尾；
    system prompt 带上快速模式声明；AgentResult.mode 标记为 quick。"""
    cfg = make_settings(max_iterations=10)  # 刻意配成很大的上限，验证 quick 模式不受它影响
    tools = FakeToolProvider(
        SPECS,
        {"stock_price_range": ToolResult(content='[{"close":1604.9}]', is_error=False)},
    )
    mock = MockLLM(
        [
            _router_response('{"intent":"market","out_of_scope":false}'),
            _tool_use_response("c1", "stock_price_range", {"name": "比亚迪"}),  # 唯一一轮
            ChatResponse(stop_reason="end_turn", text="（快速问答模式）区间价格如下。"),  # synthesizer（无停止判定轮）
        ]
    )
    result = await Agent(llm=mock, tools=tools, config=cfg).run("比亚迪最新价格", mode="quick")

    assert [name for name, _ in tools.calls] == ["stock_price_range"]  # 只查了一次，没有第 2 轮
    assert len(mock.calls) == 3  # router / 唯一一轮 / synthesizer（market 跳过 rewrite，比多轮模式少一次停止判定）
    assert result.mode == "quick"
    assert result.stopped_reason == "end_turn"
    assert "快速问答模式" in mock.calls[1]["system"]  # tool_rag 选工具轮的 system 带上了快速模式声明


async def test_agent_mode_default_unaffected_by_quick_mode_addition(make_settings) -> None:
    """mode 默认值 "agent"：行为、system prompt 与新增 quick 分支之前完全一致（互不干扰的直接验证）。"""
    cfg = make_settings(max_iterations=2)
    tools = FakeToolProvider(
        SPECS,
        {
            "stock_price_range": ToolResult(content='[{"close":1604.9}]', is_error=False),
            "stock_financials": ToolResult(content='[{"revenue":100}]', is_error=False),
        },
    )
    mock = MockLLM(
        [
            _router_response('{"intent":"market","out_of_scope":false}'),
            _tool_use_response("c1", "stock_price_range", {"name": "比亚迪"}),  # 第 1 轮 (continue)
            _tool_use_response("c2", "stock_financials", {"stock": "比亚迪"}),  # 第 2 轮 (max-reached)
            ChatResponse(stop_reason="end_turn", text="结果如下。"),  # synthesizer（上限后强制收尾）
        ]
    )
    result = await Agent(llm=mock, tools=tools, config=cfg).run("比亚迪区间与财务")  # 不传 mode → 默认 agent

    assert [name for name, _ in tools.calls] == ["stock_price_range", "stock_financials"]  # 两轮都跑了
    assert len(mock.calls) == 4  # market 意图跳过 rewrite_query
    assert result.mode == "agent"
    assert "快速问答模式" not in mock.calls[1]["system"]  # agent 模式的 system 不带快速模式声明


async def test_agentic_loop_max_zero_evidence_fallback(make_settings) -> None:
    """max_iterations=2 且所有工具都返回空 → 到达上限仍无证据 → 未取到可靠数据兜底（不崩、不无限循环）。"""
    cfg = make_settings(max_iterations=2)
    tools = FakeToolProvider(
        SPECS,
        {
            "stock_price_range": ToolResult(content="[]", is_error=False),  # 空数据
            "stock_financials": ToolResult(content="[]", is_error=False),
        },
    )
    mock = MockLLM(
        [
            _router_response('{"intent":"market","out_of_scope":false}'),
            _tool_use_response("c1", "stock_price_range", {"name": "比亚迪"}),  # 第 1 轮 (continue)
            _tool_use_response("c2", "stock_financials", {"stock": "比亚迪"}),   # 第 2 轮 (max-reached)
        ]
    )
    events: list[tuple[str, dict]] = []

    async def on_process(kind: str, data: dict) -> None:
        events.append((kind, data))

    result = await Agent(llm=mock, tools=tools, config=cfg).run("比亚迪区间", on_process=on_process)

    assert result.stopped_reason == "fallback"
    assert "未能取到可靠数据" in result.final_text
    assert [name for name, _ in tools.calls] == ["stock_price_range", "stock_financials"]
    assert len(result.tool_results) == 2
    assert len(mock.calls) == 3  # router / 轮1 / 轮2（market 跳过 rewrite，不到 synthesizer）
    loop_turns = [d for k, d in events if k == "loop_turn"]
    assert [d["status"] for d in loop_turns] == ["no-progress", "max-reached"]


async def test_agentic_loop_events_carry_round(make_settings) -> None:
    """循环可视化：每轮 stage/plan/aggregate/loop_turn 都携带轮次号（round），前端可按轮渲染。"""
    cfg = make_settings()
    tools = FakeToolProvider(SPECS, {"stock_price_range": ToolResult(content='[{"close":1604.9}]', is_error=False)})
    mock = MockLLM(
        [
            _router_response('{"intent":"market","out_of_scope":false}'),
            _tool_use_response("c1", "stock_price_range", {"name": "比亚迪"}),  # 第 1 轮
            _stop_response(),  # 停止
            ChatResponse(stop_reason="end_turn", text="价格如下。"),  # synthesizer
        ]
    )
    events: list[tuple[str, dict]] = []

    async def on_process(kind: str, data: dict) -> None:
        events.append((kind, data))

    await Agent(llm=mock, tools=tools, config=cfg).run("比亚迪价格", on_process=on_process)

    kinds = [k for k, _ in events]
    assert "loop_turn" in kinds
    assert [d.get("round") for k, d in events if k == "stage" and d.get("stage") == "tool_rag"] == [1, 2]
    assert [d.get("round") for k, d in events if k == "plan"] == [1]
    assert [d.get("round") for k, d in events if k == "aggregate"] == [1, 2]
    loop_turn = next(d for k, d in events if k == "loop_turn")
    assert loop_turn["round"] == 1
    assert loop_turn["tools"] == ["stock_price_range"]
    assert loop_turn["status"] == "continue"


class _AlwaysWantMoreLLM:
    """每轮选工具都返回 tool_use（模拟「总想要更多、从不停下」的 LLM）——用于测进展守卫。"""

    def __init__(self) -> None:
        self.calls = 0

    async def chat(self, *, messages, tools, system=None, max_tokens=8192, stream=True, temperature=None, on_text=None, on_thinking=None):
        self.calls += 1
        if not tools:  # router（首个）/ rewrite_query
            return ChatResponse(
                stop_reason="end_turn",
                text='{"intent":"market","out_of_scope":false}' if self.calls == 1 else "海光信息 财务状况",
            )
        name = tools[0].name
        cid = f"c{self.calls}"
        return ChatResponse(
            stop_reason="tool_use",
            tool_uses=[ToolUse(id=cid, name=name, input={})],
            raw_content={"content": None, "tool_calls": [{"id": cid, "type": "function", "function": {"name": name, "arguments": "{}"}}]},
        )

    def assistant_message(self, resp):
        return {"role": "assistant", "content": resp.text or None}

    def tool_results_messages(self, results):
        return [{"role": "tool", "tool_call_id": tu.id, "content": tr.content} for tu, tr in results]


async def test_agentic_loop_stops_on_no_progress(make_settings) -> None:
    """工具反复返回「无权限/空数据」→ 进展守卫：2 轮无进展即止（不烧满 max_iterations，防死循环）。

    复现场景：LLM 每轮仍要工具，但工具恒回 `{code!=0, data:[]}`（无可用数据）。改前会烧满 10 轮（12 次 LLM 调用），
    改后 2 轮即 no_evidence 兜底。回归「修死循环」。
    """
    cfg = make_settings(max_iterations=10)
    tools = FakeToolProvider(
        SPECS,
        {s.name: ToolResult(content='{"code":-9000,"msg":"无权限","row_count":0,"data":[]}', is_error=False) for s in SPECS},
    )
    llm = _AlwaysWantMoreLLM()
    events: list[tuple[str, dict]] = []

    async def on_process(kind: str, data: dict) -> None:
        events.append((kind, data))

    result = await Agent(llm=llm, tools=tools, config=cfg).run("海光信息最近一年的财务状况", on_process=on_process)

    assert result.stopped_reason == "fallback"
    assert "未能取到可靠数据" in result.final_text
    assert llm.calls == 3  # router / 轮1 / 轮2（market 跳过 rewrite_query，不再烧满 10 轮）
    loop_turns = [d for k, d in events if k == "loop_turn"]
    assert [d["status"] for d in loop_turns] == ["no-progress", "no-progress"]
    assert [d["round"] for d in loop_turns] == [1, 2]


async def test_rag_skipped_for_non_corpus_company(make_settings) -> None:
    """问非比亚迪/宁德时代公司（如贵州茅台）→ RAG 直接跳过（不返回别家年报切片），仅工具路作答。"""
    from demomcp.graph.builder import build_research_graph

    cfg = make_settings()
    tools = FakeToolProvider(SPECS, {"stock_price_range": ToolResult(content='[{"close":1604.9}]', is_error=False)})
    mock = MockLLM(
        [
            _router_response('{"intent":"report","out_of_scope":false}'),
            ChatResponse(stop_reason="end_turn", text="贵州茅台 研发投入"),  # rewrite_query
            _tool_use_response("c1", "stock_price_range", {"stock": "600519.SH"}),
            _stop_response(),
            ChatResponse(stop_reason="end_turn", text="贵州茅台研发投入相关数据（来自工具）。"),
        ]
    )
    retriever = _fake_retriever(
        [{
            "doc_id": "d1", "doc_title": "比亚迪2024年报", "chunk_index": 0, "text": "研发投入约 X 亿元", "score": 0.9,
            "metadata": {"company": "比亚迪", "year": 2024, "section_path": ["3.2 研发投入"], "page_start": 42,
                         "page_end": 42, "heading": "研发投入", "block_type": "paragraph", "doc_title": "比亚迪2024年报"},
        }]
    )
    graph = build_research_graph(mock, tools, SPECS, max_tokens=cfg.ds_max_tokens, disclaimer=cfg.disclaimer, base_system=cfg.system_prompt, retriever=retriever)
    state = {"messages": [{"role": "user", "content": "贵州茅台研发投入"}], "original_query": "贵州茅台研发投入", "out_of_scope": False,
             "tool_results": [], "evidence": [], "rag_chunks": [], "citations": [], "usage": None}
    out = await graph.ainvoke(state, config={"configurable": {}})

    assert out["stopped_reason"] == "end_turn"
    assert retriever.calls == 0  # RAG 被跳过（语料无贵州茅台）
    structured = out["structured"]
    rag_sources = [s for s in structured["sources"] if s["type"] == "rag"]
    assert rag_sources == []  # 不出现别家年报来源


async def test_agent_synth_llm_only_used_by_synthesizer(make_settings) -> None:
    """深度思考模型路由：Agent(synth_llm=...) 时 router/rewrite_query/tool_rag 选工具三步恒用 llm（快模型），
    只有 synthesizer 走 synth_llm——两个 mock 队列互不干扰、各自计数吻合。"""
    tools = FakeToolProvider(SPECS, {"stock_price_range": ToolResult(content='[{"close":1604.9}]', is_error=False)})
    fast = MockLLM(
        [
            _router_response('{"intent":"report","out_of_scope":false}'),
            ChatResponse(stop_reason="end_turn", text="比亚迪 价格"),  # rewrite_query（report 意图不跳过）
            _tool_use_response("c1", "stock_price_range", {"name": "比亚迪"}),
            _stop_response(),
        ]
    )
    reasoner = MockLLM([ChatResponse(stop_reason="end_turn", text="比亚迪区间约 1604.9 元（深度思考）。")])
    agent = Agent(llm=fast, tools=tools, config=make_settings(), synth_llm=reasoner)
    result = await agent.run("比亚迪价格")

    assert result.stopped_reason == "end_turn"
    assert "深度思考" in result.final_text
    assert len(fast.calls) == 4  # router / rewrite_query / tool选择 / 停止判定
    assert len(reasoner.calls) == 1  # 只有 synthesizer 走 reasoner
