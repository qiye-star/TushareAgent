"""快报按源判定失败：工具名 → 源类别，以及各源互相矛盾的成功码。

背景（2026-09-08 实测）：网关把五个源聚合到一条 MCP 连接上，协议里不带 source_id，
而各源的「成功」约定互相矛盾——Tushare `code:0` 成功，**iFind `code:1` 成功**，
Wind 无 `code` 即成功，免费源无 code 约定、失败是 `{"error": …}`。
沿用单一 `code != 0` 判据会把 iFind 的每次成功都判成失败（实测把一次调用从
0.65s 放大到 6.20s 并双倍消耗 iFind 的套餐并发配额）。

本文件全部离线（纯函数，不连网关）。
"""

from __future__ import annotations

import json

from demomcp.quickreport.pipeline import _biz_fail_reason
from demomcp.quickreport.source import (
    KIND_FREE,
    KIND_IFIND,
    KIND_TUSHARE,
    KIND_WIND,
    source_kind,
    source_label,
)

# ---- 工具名 → 源类别 ----


def test_source_kind_routes_by_prefix_and_name_set() -> None:
    assert source_kind("wind_query") == KIND_WIND
    assert source_kind("wind_list_apis") == KIND_WIND
    assert source_kind("ifind_query") == KIND_IFIND
    assert source_kind("ifind_get_api_info") == KIND_IFIND
    # 免费源无前缀，靠名字集合
    assert source_kind("get_market_headlines") == KIND_FREE
    assert source_kind("get_stock_news") == KIND_FREE
    assert source_kind("get_index_data") == KIND_FREE
    assert source_kind("search_stock") == KIND_FREE
    # Tushare 原生名 + 未知名都兜底 tushare（与改造前判定一致）
    assert source_kind("daily") == KIND_TUSHARE
    assert source_kind("forecast") == KIND_TUSHARE
    assert source_kind("anns_d") == KIND_TUSHARE
    assert source_kind("some_future_tool") == KIND_TUSHARE


def test_source_label_falls_back_to_kind() -> None:
    assert source_label(KIND_IFIND) == "同花顺 iFind"
    assert source_label("unknown-kind") == "unknown-kind"  # 不编造


# ---- 默认 kind 必须与改造前逐字节一致 ----


def test_biz_fail_reason_default_kind_is_byte_identical() -> None:
    """整张旧判定表用**单参**回放一遍——这是 kind 重构的回归网。

    尤其 `{"code": 1}`：Tushare 语义下它确实是失败，
    test_quickreport_pipeline.py::test_biz_fail_reason_classification 锁了这条。
    """
    assert _biz_fail_reason('{"code": 0, "data": []}') is None
    assert _biz_fail_reason('{"code": 1, "msg": ""}') == "业务失败：code 非 0"
    assert _biz_fail_reason('{"code": 40203, "msg": "没有接口访问权限"}') == (
        "权限受限：没有接口访问权限"
    )
    assert _biz_fail_reason("[]") is None
    assert _biz_fail_reason("调用 news 失败：该接口需更高积分").startswith("权限受限：")
    assert _biz_fail_reason("Error calling daily after 2 tries").startswith("权限受限：")
    # 正常数据里含「失败」二字不能误判
    assert _biz_fail_reason('{"code": 0, "data": [{"content": "某某公司失败案例"}]}') is None


# ---- iFind：code:1 是成功（BLOCKER 的回归锁）----


def test_biz_fail_reason_ifind_success_code1_is_not_failure() -> None:
    """实测信封：{"code":1,"msg":"success","subCode":null,"data":{"data":"<JSON 串>"}}。

    改造前这里返回 '业务失败：success' —— 每次成功都被当失败重试一遍。
    """
    ok = json.dumps(
        {"code": 1, "msg": "success", "subCode": None, "data": {"data": "[]"}},
        ensure_ascii=False,
    )
    assert _biz_fail_reason(ok, KIND_IFIND) is None
    # 同一段文本在 Tushare 语义下**仍然**是失败（两套语义必须并存）
    assert _biz_fail_reason(ok, KIND_TUSHARE) == "业务失败：success"


def test_biz_fail_reason_ifind_code0_is_failure() -> None:
    assert _biz_fail_reason('{"code": 0, "msg": "参数缺失"}', KIND_IFIND) == "业务失败：参数缺失"


def test_biz_fail_reason_ifind_reads_submsg() -> None:
    """iFind 信封有 subMsg 字段；msg 空时用它，不要吐出无信息的「code 非 0」。"""
    body = '{"code": 0, "msg": "", "subMsg": "并发超限"}'
    assert _biz_fail_reason(body, KIND_IFIND) == "业务失败：并发超限"


# ---- 免费源：{"error": …} 是失败，且必须先于信封探测 ----


def test_biz_fail_reason_free_error_envelope_is_failure_for_every_kind() -> None:
    """`error` 键判定与源无关。

    不这样做的后果（实测）：`_extract_rows` 的信封探测不认 `error` 键，
    会把 {"error": …} 当成**一行数据**返回 → 硬失败被误报成 empty，
    甚至让 `_is_usable` 为真、把错误字典当数据往下游送。
    """
    err = '{"error": "get_industry_stocks failed: curl exit 56"}'
    for kind in (KIND_FREE, KIND_TUSHARE, KIND_IFIND, KIND_WIND):
        reason = _biz_fail_reason(err, kind)
        assert reason is not None, kind
        assert reason.startswith("取数失败："), (kind, reason)
        assert "curl exit 56" in reason


def test_biz_fail_reason_free_bare_array_is_success() -> None:
    """免费源成功返回裸数组（records 形态），没有任何 code 字段。"""
    assert _biz_fail_reason('[{"标题": "x", "发布时间": "2026-09-08 10:00:00"}]', KIND_FREE) is None


def test_biz_fail_reason_free_no_code_convention() -> None:
    """免费源即便返回带 code 的 dict 也不按 code 判（它没有这个约定）。"""
    assert _biz_fail_reason('{"code": 7, "data": []}', KIND_FREE) is None


# ---- Wind：无 code 即成功 ----


def test_biz_fail_reason_wind_no_code_is_success() -> None:
    body = '{"data": {"items": [{"title": "t", "date": "2026-09-04"}]}}'
    assert _biz_fail_reason(body, KIND_WIND) is None


def test_biz_fail_reason_wind_permission_words_still_flagged() -> None:
    assert _biz_fail_reason('{"code": 2, "msg": "积分不足"}', KIND_WIND) == "权限受限：积分不足"
