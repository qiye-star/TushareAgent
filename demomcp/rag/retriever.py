"""RAG 检索器（空实现占位）。

无向量库/语料时 `NullRetriever.retrieve` 恒返回空，保证四节点流程现在就能端到端跑通；
后续接向量库时实现同签名（query -> list[RagChunk]）即可替换，不改 Tool/RAG 节点。
"""

from __future__ import annotations

from demomcp.rag.schemas import RagChunk


class NullRetriever:
    """检索恒返回空（无索引/语料）。"""

    async def retrieve(self, query: str, *, top_k: int = 5) -> list[RagChunk]:
        return []
