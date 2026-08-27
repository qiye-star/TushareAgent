"""RAG 检索 HTTP 客户端：作为 RagRetriever 的远端实现（`retrieve(plan, *, on_funnel=None)`）。

进程不自行打开 Milvus（其单进程独占锁），改为 POST 到运行中的 web `/api/rag/retrieve`；
响应里的 funnel 回传给调用方，保持与本地 HybridRetriever 一致的可观测面。
"""

from __future__ import annotations

from typing import Any

import httpx

from demomcp.rag.schemas import RagChunk, RetrievalPlan


class HttpRetriever:
    def __init__(
        self,
        base_url: str,
        *,
        timeout: float = 20.0,
        token: str = "",
        transport: Any | None = None,
    ) -> None:
        self._url = base_url.rstrip("/") + "/api/rag/retrieve"
        self._timeout = timeout
        self._transport = transport  # 供测试注入 httpx.MockTransport
        self._headers = {"Authorization": f"Bearer {token}"} if token else {}

    async def retrieve(self, plan: RetrievalPlan, *, on_funnel=None) -> list[RagChunk]:
        async with httpx.AsyncClient(timeout=self._timeout, transport=self._transport) as client:
            resp = await client.post(self._url, json=plan.model_dump(), headers=self._headers)
            resp.raise_for_status()
            body = resp.json()
        if on_funnel is not None and body.get("funnel"):
            on_funnel(body["funnel"])
        return [RagChunk(**c) for c in body.get("chunks", [])]
