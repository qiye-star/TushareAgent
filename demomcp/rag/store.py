"""向量库：VectorStore 协议 + 内存余弦实现（默认）+ Faiss（可选、惰性导入）。

内存实现用纯 Python 余弦（已归一化向量 → 直接点积），零新依赖；Faiss 仅在
RAG_USE_REAL 且 faiss 可导入时使用。本模块顶层不 import faiss。
"""

from __future__ import annotations

import math
from dataclasses import replace
from typing import Any

from demomcp.config.settings import Settings
from demomcp.rag.schemas import ScoredChunk


def _normalize(vec: list[float]) -> list[float]:
    norm = math.sqrt(sum(x * x for x in vec))
    if norm < 1e-8:
        return vec
    return [x / norm for x in vec]


def _match(meta: dict, filters: dict | None) -> bool:
    """filters 预筛：company/year 已知即精确匹配（全未知则是任意）。"""
    if not filters:
        return True
    return (filters.get("company") is None or meta.get("company") == filters["company"]) and (
        filters.get("year") is None or meta.get("year") == filters["year"]
    )


class VectorStore:
    """向量库协议：add/search/delete_doc/count，search 带元数据过滤。"""

    dim: int

    def add(
        self,
        doc_id: str,
        chunk_index: int,
        text: str,
        embedding: list[float],
        metadata: dict | None = None,
    ) -> None:
        raise NotImplementedError

    def search(
        self,
        query: list[float],
        *,
        top_k: int = 10,
        filters: dict | None = None,
    ) -> list[ScoredChunk]:
        raise NotImplementedError

    def delete_doc(self, doc_id: str) -> None:
        raise NotImplementedError

    def count(self) -> int:
        raise NotImplementedError


class InMemoryVectorStore(VectorStore):
    """纯净 Python 余弦检索；filters 对 company/year 做精确预筛。"""

    def __init__(self, *, dim: int = 256) -> None:
        self.dim = dim
        # key = f"{doc_id}#{chunk_index}"
        self._rows: dict[str, ScoredChunk] = {}
        self._emb: dict[str, list[float]] = {}  # 已归一化
        self._meta_doc: dict[str, dict] = {}  # doc_id -> {company, year, ...}

    def add(
        self,
        doc_id: str,
        chunk_index: int,
        text: str,
        embedding: list[float],
        metadata: dict | None = None,
    ) -> None:
        if len(embedding) != self.dim:
            raise ValueError(f"expected dim={self.dim}, got {len(embedding)}")
        meta = metadata or {}
        self._meta_doc.setdefault(doc_id, {}).update(meta)
        key = _key(doc_id, chunk_index)
        self._rows[key] = ScoredChunk(
            doc_id=doc_id, chunk_index=chunk_index, text=text, score=0.0, metadata=meta
        )
        self._emb[key] = _normalize(embedding)

    def search(
        self,
        query: list[float],
        *,
        top_k: int = 10,
        filters: dict | None = None,
    ) -> list[ScoredChunk]:
        q = _normalize(query)
        if len(q) != self.dim:
            raise ValueError(f"expected dim={self.dim}, got {len(q)}")
        allowed = (
            {d for d, m in self._meta_doc.items() if _match(m, filters)} if filters else None
        )
        scored: list[tuple[float, str]] = []
        for key, emb in self._emb.items():
            doc_id = key.split("#", 1)[0]
            if allowed is not None and doc_id not in allowed:
                continue
            dot = sum(a * b for a, b in zip(q, emb))
            scored.append((dot, key))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [replace(self._rows[key], score=dot) for dot, key in scored[:top_k]]

    def delete_doc(self, doc_id: str) -> None:
        prefix = f"{doc_id}#"
        for key in [k for k in self._rows if k.startswith(prefix)]:
            del self._rows[key]
            del self._emb[key]
        self._meta_doc.pop(doc_id, None)

    def count(self) -> int:
        return len(self._rows)


class FaissVectorStore(VectorStore):
    """真实向量后端（可选）：构造时惰性 import faiss，写入时才建索引。"""

    def __init__(self, *, dim: int, index_path: str = "") -> None:
        self.dim = dim
        self._index_path = index_path
        self._records: dict[int, dict] = {}  # fid -> {doc_id, chunk_index, text, metadata}
        self._next_id = 0
        self._index: Any = None  # faiss 索引（惰性）；Any 避开可选成员调用报错

    def _ensure_index(self) -> None:
        if self._index is None:
            import faiss  # 惰性import

            self._index = faiss.IndexFlatIP(self.dim)
            self._index_ids: list[tuple[int, dict]] = []

    def add(
        self,
        doc_id: str,
        chunk_index: int,
        text: str,
        embedding: list[float],
        metadata: dict | None = None,
    ) -> None:
        self._ensure_index()
        fid = self._next_id
        self._next_id += 1
        self._records[fid] = {
            "doc_id": doc_id, "chunk_index": chunk_index, "text": text,
            "metadata": metadata or {},
        }
        self._index.add(_normalize(embedding))
        self._index_ids.append((fid, self._records[fid]))

    def search(
        self,
        query: list[float],
        *,
        top_k: int = 10,
        filters: dict | None = None,
    ) -> list[ScoredChunk]:
        if not self._records:
            return []
        scores, ids = self._index.search(_normalize(query), min(top_k, len(self._records)))
        out: list[ScoredChunk] = []
        for s, fid in zip(scores[0], ids[0]):
            rec = self._records[int(fid)]
            if not _match(rec["metadata"], filters):
                continue
            out.append(
                ScoredChunk(
                    doc_id=rec["doc_id"], chunk_index=rec["chunk_index"],
                    text=rec["text"], score=float(s), metadata=rec["metadata"],
                )
            )
        return out[:top_k]

    def delete_doc(self, doc_id: str) -> None:
        keep = {fid: r for fid, r in self._records.items() if r["doc_id"] != doc_id}
        if len(keep) == len(self._records):
            return
        self._records = keep
        # 重建索引（可选后端，相对低频）
        import faiss  # 惰性import

        self._index = faiss.IndexFlatIP(self.dim)
        self._index_ids = []
        for fid, rec in self._records.items():
            self._index_ids.append((fid, rec))

    def count(self) -> int:
        return len(self._records)


def build_vector_store(config: Settings, dim: int) -> VectorStore:
    """按配置选后端：rag_use_real 且 faiss 可导入 → Faiss；否则内存。"""
    if config.rag_use_real and config.rag_vector_store_path:
        try:
            import faiss  # noqa: F401  # 仅探测可用性

            return FaissVectorStore(dim=dim, index_path=config.rag_vector_store_path)
        except ImportError:
            pass
    return InMemoryVectorStore(dim=dim)


def _key(doc_id: str, chunk_index: int) -> str:
    return f"{doc_id}#{chunk_index}"
