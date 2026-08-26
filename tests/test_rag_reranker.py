"""重排：build_reranker 后端选择 + ApiReranker 按分重排（monkeypatch httpx，不触网）+ Noop 透传。"""

from __future__ import annotations

from demomcp.rag.reranker import ApiReranker, NoopReranker, build_reranker
from demomcp.rag.schemas import ScoredChunk


def _chunk(i: int, text: str) -> ScoredChunk:
    return ScoredChunk(doc_id="d", chunk_index=i, text=text, score=0.0, metadata={})


def test_build_reranker_api_when_configured(make_settings) -> None:
    s = make_settings(
        rag_rerank_model="BAAI/bge-reranker-v2-m3",
        rag_embedding_api_base="http://127.0.0.1:9/v1", rag_embedding_api_key="sk-x",
    )
    assert isinstance(build_reranker(s), ApiReranker)  # 构造不发网络


def test_build_reranker_no_key_falls_back_noop(make_settings) -> None:
    s = make_settings(
        rag_rerank_model="BAAI/bge-reranker-v2-m3",
        rag_embedding_api_base="http://127.0.0.1:9/v1", rag_embedding_api_key="",
    )
    assert isinstance(build_reranker(s), NoopReranker)


def test_noop_reranker_passthrough() -> None:
    cands = [_chunk(0, "a"), _chunk(1, "b")]
    assert NoopReranker().rerank("q", cands) == cands  # 按入参序原样返回


def test_api_reranker_reorders_by_score(make_settings, monkeypatch) -> None:
    class _FakeResp:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {"results": [{"index": 1, "relevance_score": 0.9}, {"index": 0, "relevance_score": 0.6}]}

    monkeypatch.setattr("demomcp.rag.reranker.httpx.post", lambda *a, **k: _FakeResp())
    cands = [_chunk(0, "第一个"), _chunk(1, "第二个")]
    r = ApiReranker("BAAI/bge-reranker-v2-m3", base_url="http://x/v1", api_key="k")
    out = r.rerank("q", cands)
    # index1 分数更高 → 排前面；分数被写回 score
    assert out[0].chunk_index == 1
    assert abs(out[0].score - 0.9) < 1e-9
    assert out[1].chunk_index == 0
    assert abs(out[1].score - 0.6) < 1e-9


def test_api_reranker_empty_candidates() -> None:
    r = ApiReranker("BAAI/bge-reranker-v2-m3", base_url="http://x/v1", api_key="k")
    assert r.rerank("q", []) == []
