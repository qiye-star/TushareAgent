"""RAG 检索服务端逻辑：把「HybridRetriever 检索」暴露为可测的纯函数，供 web /api/rag/retrieve 复用。

复用 `HybridRetriever.retrieve(plan, *, on_funnel=...)`；检索过程与进程无关（Milvus 由持有进程独占）。
对外返回 JSON 化的 chunks + funnel，便于 HTTP 端点/测试消费；retriever 为 None 时显式降级（degraded）。
"""

from __future__ import annotations

from typing import Any

from demomcp.rag.schemas import RetrievalPlan


async def retrieve_from(retriever: Any, plan: RetrievalPlan) -> dict[str, Any]:
    """执行一次检索，返回 {chunks, funnel, degraded}（chunks 为 dict 列表，可直接 JSON 化）。"""
    if retriever is None:
        return {"chunks": [], "funnel": {}, "degraded": True}
    funnel: dict[str, Any] = {}
    chunks = await retriever.retrieve(plan, on_funnel=funnel.update)
    return {
        "chunks": [c.model_dump() for c in chunks],
        "funnel": funnel,
        "degraded": False,
    }


def health_from(retriever: Any) -> dict[str, Any]:
    """检索健康度：{ok, chunks}；chunks 为索引 chunk 总数（取不到则 None）。"""
    if retriever is None:
        return {"ok": False, "chunks": None}
    try:
        chunk_count, _ = retriever._index.count()  # 同包访问 RagIndex.count
        return {"ok": True, "chunks": chunk_count}
    except Exception:  # noqa: BLE001 - 取不到计数仍视为存活
        return {"ok": True, "chunks": None}
