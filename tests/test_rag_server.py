"""RAG 检索服务端逻辑：retrieve_from / health_from 纯函数 + web /api/rag/retrieve 端点（stub retriever，不打开 Milvus）。"""

from __future__ import annotations

import pytest

from demomcp.rag.schemas import RagChunk, RetrievalPlan
from demomcp.rag.server import health_from, retrieve_from


class _StubRetriever:
    """duck-type HybridRetriever：retrieve(plan, *, on_funnel) 回固定 chunks；_index.count 回计数。"""

    def __init__(self, chunks: list[RagChunk], count: int = 2) -> None:
        self._chunks = chunks
        self._index = type("_I", (), {"count": lambda self: (count, count // 2)})()  # type: ignore

    async def retrieve(self, plan: RetrievalPlan, *, on_funnel=None) -> list[RagChunk]:
        if on_funnel is not None:
            on_funnel(
                {"recall_raw": 1, "fused": 1, "sections": 1, "pool": 1, "reranked": 1,
                 "final": len(self._chunks), "rerank_degraded": False}
            )
        return self._chunks


def _chunk(text: str) -> RagChunk:
    return RagChunk(doc_id="d1", doc_title="比亚迪 2025 年报", chunk_index=0, text=text, score=0.9,
                    metadata={"company": "比亚迪", "year": 2025, "section_path": ["3.2 研发投入"]})


@pytest.mark.asyncio
async def test_retrieve_from_returns_chunks_and_funnel() -> None:
    retriever = _StubRetriever([_chunk("研发投入约 X 亿"), _chunk("营业收入构成")])
    plan = RetrievalPlan(rewritten_query="比亚迪研发投入", strategy="factual")
    out = await retrieve_from(retriever, plan)
    assert out["degraded"] is False
    assert len(out["chunks"]) == 2
    assert out["chunks"][0]["text"] == "研发投入约 X 亿"  # 已 JSON 化（dict）
    assert out["funnel"]["final"] == 2


@pytest.mark.asyncio
async def test_retrieve_from_none_is_degraded() -> None:
    out = await retrieve_from(None, RetrievalPlan(rewritten_query="x"))
    assert out == {"chunks": [], "funnel": {}, "degraded": True}


def test_health_reports_counts() -> None:
    assert health_from(_StubRetriever([_chunk("x")], count=3)) == {"ok": True, "chunks": 3}
    assert health_from(None) == {"ok": False, "chunks": None}


def test_web_retrieve_endpoint(monkeypatch) -> None:
    from fastapi.testclient import TestClient

    from demomcp.entry.web import app

    stub = _StubRetriever([_chunk("研发投入约 X 亿")])

    async def fake_build(config):
        return stub

    monkeypatch.setattr("demomcp.rag.runtime.build_runtime_retriever", fake_build)
    with TestClient(app) as client:
        resp = client.post(
            "/api/rag/retrieve",
            json={"rewritten_query": "比亚迪研发投入", "filters": {"company": "比亚迪", "year": 2025}, "strategy": "factual"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["degraded"] is False
        assert len(data["chunks"]) == 1
        assert data["funnel"]["final"] == 1
        hp = client.get("/api/rag/health").json()
        assert hp["ok"] is True
        assert hp["chunks"] == 2
