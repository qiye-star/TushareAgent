"""多模态图块描述：NoopCaptioner 跳过图块；build_captioner 无 key 时回退 Noop。"""

from __future__ import annotations

from demomcp.rag.captioner import NoopCaptioner, build_captioner


def test_noop_captioner_returns_none() -> None:
    """未配置视觉模型 → 图块跳过（返回 None，不进索引），绝不编造。"""
    cap = NoopCaptioner()
    assert cap.caption(b"png-bytes", context="3.2 研发投入", page=42) is None


def test_build_captioner_no_key_falls_back_noop(make_settings) -> None:
    """rag_captioner 非空但无 ds_api_key → Noop（离线/测试路径）。"""
    s = make_settings(rag_captioner="deepseek-v4-flash-vision-exp", ds_api_key="")
    assert isinstance(build_captioner(s), NoopCaptioner)


def test_build_captioner_no_model_falls_back_noop(make_settings) -> None:
    """rag_captioner 为空 → Noop。"""
    s = make_settings(rag_captioner="", ds_api_key="sk-x")
    assert isinstance(build_captioner(s), NoopCaptioner)
