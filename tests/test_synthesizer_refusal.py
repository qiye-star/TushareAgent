"""synthesizer 拒答防护：DeepSeek 偶发返回"抱歉，我无法完成这个请求。"这类拒答/空话时，
不应被当成功答案下发（曾在线上发生：证据充分、stopped_reason=end_turn，答案却是拒答 + 免责声明 +
万得来源声明——见 demomcp/graph/nodes.py 的 make_synthesizer 与 _is_degenerate_answer）。
"""

from __future__ import annotations

from demomcp.graph.nodes import (
    _accept_as_direct_answer,
    _is_degenerate_answer,
    make_synthesizer,
)
from demomcp.interfaces.types import ChatResponse
from demomcp.providers.llm.mock import MockLLM

DISCLAIMER = "以上内容基于公开数据整理，仅供研究参考，不构成投资建议。"

WIND_EVIDENCE = [
    {"source_type": "tool", "source": "wind_get_stock_quote", "content": '{"close": 271.18}'},
]


def _state(evidence: list[dict]) -> dict:
    return {
        "evidence": evidence,
        "intent": "market",
        "skill": None,
        "original_query": "苹果公司最近30日K线",
        "messages": [],
        "usage": None,
    }


async def test_synthesizer_retries_on_refusal_then_succeeds() -> None:
    mock = MockLLM([
        ChatResponse(stop_reason="end_turn", text="抱歉，我无法完成这个请求。"),
        ChatResponse(stop_reason="end_turn", text="苹果最近30日收盘价约271.18美元，波动平稳。"),
    ])
    synthesizer = make_synthesizer(mock, max_tokens=2048, disclaimer=DISCLAIMER, base_system="系统提示")
    out = await synthesizer(_state(WIND_EVIDENCE), config={"configurable": {}})

    assert len(mock.calls) == 2  # 原始一次 + 重试一次
    assert out["stopped_reason"] == "end_turn"
    assert "苹果最近30日收盘价约271.18美元" in out["structured"]["answer"]
    assert DISCLAIMER in out["structured"]["answer"]
    assert "数据来源于万得 Wind 金融数据服务。" in out["structured"]["answer"]


async def test_synthesizer_falls_back_when_refusal_persists() -> None:
    mock = MockLLM([
        ChatResponse(stop_reason="end_turn", text="抱歉，我无法完成这个请求。"),
        ChatResponse(stop_reason="end_turn", text="很抱歉，我无法完成这个请求。"),
    ])
    synthesizer = make_synthesizer(mock, max_tokens=2048, disclaimer=DISCLAIMER, base_system="系统提示")
    out = await synthesizer(_state(WIND_EVIDENCE), config={"configurable": {}})

    assert len(mock.calls) == 2
    assert out["stopped_reason"] == "fallback"
    answer = out["structured"]["answer"]
    assert "已取到 1 条数据" in answer
    assert DISCLAIMER in answer
    assert "数据来源于万得" not in answer  # 没有真实总结内容时不误导标来源
    assert out["structured"]["sources"]  # 证据不清空：取数本身成功，只是文字总结失败


async def test_synthesizer_falls_back_without_evidence_wording() -> None:
    mock = MockLLM([
        ChatResponse(stop_reason="end_turn", text="抱歉，我无法完成这个请求。"),
        ChatResponse(stop_reason="end_turn", text="抱歉，我无法完成这个请求。"),
    ])
    synthesizer = make_synthesizer(mock, max_tokens=2048, disclaimer=DISCLAIMER, base_system="系统提示")
    out = await synthesizer(_state([]), config={"configurable": {}})

    assert out["stopped_reason"] == "fallback"
    assert "已取到" not in out["structured"]["answer"]  # 无证据时不提"已取到 0 条数据"这种怪话


def test_is_degenerate_answer_matches_known_refusal() -> None:
    assert _is_degenerate_answer("抱歉，我无法完成这个请求。") is True
    assert _is_degenerate_answer("很抱歉，我无法完成这个请求。") is True
    assert _is_degenerate_answer("") is True
    assert _is_degenerate_answer("   ") is True


def test_is_degenerate_answer_keeps_legit_short_answers() -> None:
    assert _is_degenerate_answer("比亚迪2025年研发投入约10亿元。") is False
    assert _is_degenerate_answer("比亚迪的研发投入主要集中在电池技术。") is False  # 短但非拒答措辞
    assert _is_degenerate_answer("数据已足。") is False  # 现有测试里用到的短占位文本


def test_accept_as_direct_answer_rules() -> None:
    """零证据停止轮的「直接作答」判定：真答案接受；拒答/失败措辞/短句一律拒绝。"""
    # 接受：上下文回显式真答案（f2d8b91b 事故形态，长、无失败措辞）
    echo = (
        "（快速问答模式）2024 年沪深 300 指数全年涨跌幅为 **14.68%**。计算依据："
        "2024-01-02 收盘 3386.35 点、2024-12-31 收盘 3934.91 点。"
    )
    assert _accept_as_direct_answer(echo) is True
    # 拒绝：空/空白
    assert _accept_as_direct_answer("") is False
    assert _accept_as_direct_answer("   ") is False
    # 拒绝：短句（30 字符下限）
    assert _accept_as_direct_answer("数据已足。") is False
    assert _accept_as_direct_answer("好的。") is False
    assert _accept_as_direct_answer("我不知道要用什么工具。") is False
    # 拒绝：长拒答（>=30 字符但含「我无法完成」，直接作答路径无重试，硬拒）
    assert _accept_as_direct_answer("非常抱歉，由于安全策略限制，我无法完成这个请求，建议您咨询专业人士。") is False
    # 拒绝：取不到类措辞（>=30 字符，失败语义）
    assert _accept_as_direct_answer("我没有找到 2024 年沪深 300 指数的相关行情数据，请稍后重试。") is False
    assert _accept_as_direct_answer("该接口无权限，取不到这份数据，建议检查权限配置后重试。") is False
    # 2026-09-07 审查回归：窄词表漏判的变体措辞（都不命中旧表：无「无法回答/无权限/取不到」等）
    assert _accept_as_direct_answer(
        "不好意思，由于当前账号权限限制，我暂时无法查询到该数据，建议您联系管理员检查配置。"
    ) is False
    assert _accept_as_direct_answer("抱歉，因为数据源不可用，我无法查询到您要的行情，请换个时间再试。") is False
    # 短但含数字的完整答案：不应被 30 字符门槛弹回兜底（审查发现的误伤方向）
    assert _accept_as_direct_answer("2024年沪深300涨跌幅为14.68%。") is True
