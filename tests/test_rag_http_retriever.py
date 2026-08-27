"""RAG 检索 HTTP 客户端：HttpRetriever 经 MockTransport 验证请求体、重构 RagChunk、转发 funnel。"""

from __future__ import annotations

import json

import httpx
import pytest

from demomcp.rag.http_retriever import HttpRetriever
from demomcp.rag.schemas import RagChunk, RetrievalPlan


def _chunk(text: str) -> RagChunk:
    return RagChunk(doc_id="d1", doc_title="比亚迪 2025 年报", chunk_index=0, text=text, score=0.9,
                    metadata={"company": "比亚迪", "year": 2025, "section_path": ["3.2 研发投入"]})


@pytest.mark.asyncio
async def test_http_retriever_posts_plan_and_rebuilds_chunks() -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={"chunks": [_chunk("研发投入约 X 亿").model_dump()], "funnel": {"final": 1, "rerank_degraded": False}},
        )

    retriever = HttpRetriever("http://127.0.0.1:8010", transport=httpx.MockTransport(handler))
    plan = RetrievalPlan(rewritten_query="比亚迪研发投入", strategy="factual")
    seen: dict = {}
    chunks = await retriever.retrieve(plan, on_funnel=seen.update)

    assert captured["path"] == "/api/rag/retrieve"
    assert captured["body"]["rewritten_query"] == "比亚迪研发投入"
    assert captured["body"]["strategy"] == "factual"
    assert len(chunks) == 1
    assert chunks[0].text == "研发投入约 X 亿"
    assert chunks[0].metadata["company"] == "比亚迪"
    assert seen == {"final": 1, "rerank_degraded": False}


@pytest.mark.asyncio
async def test_http_retriever_sends_bearer_token() -> None:
    seen_headers: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen_headers.update(dict(request.headers))
        return httpx.Response(200, json={"chunks": [], "funnel": {}})

    retriever = HttpRetriever("http://x", token="secret", transport=httpx.MockTransport(handler))
    await retriever.retrieve(RetrievalPlan(rewritten_query="x"))
    assert seen_headers.get("authorization") == "Bearer secret"
