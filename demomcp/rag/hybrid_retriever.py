"""检索核心：向量检索(chunk dense) + 语义检索(section 代表向量) + 词法(section BM25) 三路 RRF 融合，
再按 section_path 聚合出候选节、节内取最优 chunk、语义 rerank、阈值截断，返回自带溯源元数据的 RagChunk。

策略路由：structural → ②③（节级）；factual → ①（chunk dense）；auto → 三路全走。
query 向量带元数据前缀（company/year），与「向量化包含元数据」对齐。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Protocol

from demomcp.config.settings import Settings
from demomcp.rag import query_build
from demomcp.rag.ingest import RagIndex, build_index
from demomcp.rag.reranker import NoopReranker, Reranker, build_reranker
from demomcp.rag.schemas import RagChunk, RetrievalPlan, ScoredChunk


class RagRetriever(Protocol):
    async def retrieve(self, plan: RetrievalPlan) -> list[RagChunk]: ...


@dataclass
class _Cand:
    key: tuple
    section_path: tuple
    text: str
    metadata: dict
    chunk_index: int  # -1 表示节级派生
    score: float = 0.0


class HybridRetriever:
    def __init__(
        self,
        *,
        index: RagIndex,
        reranker: Reranker | None = None,
        config: Settings,
    ) -> None:
        self._index = index
        self._reranker = reranker or NoopReranker()
        self._cfg = config
        self.last_plan: RetrievalPlan | None = None

    async def retrieve(self, plan: RetrievalPlan) -> list[RagChunk]:
        return await asyncio.to_thread(self._retrieve_sync, plan)

    def _retrieve_sync(self, plan: RetrievalPlan) -> list[RagChunk]:
        cfg = self._cfg
        index = self._index
        self.last_plan = plan
        filters = _filters_dict(plan)
        f = _strict_filters(filters, cfg.rag_strict_scope)
        qs = query_build.build_queries(plan)
        dense_q = index.embedder.encode_query(_query_prefix(filters) + qs.dense_query)

        # —— 三路检索（节级语义 + 节级词法 + chunk 密集）全量参与 ——
        #    节级两路给节加分（RRF 天然偏向节），chunk 路负责节内最优 chunk/表格可被召回；
        #    strategy 为前瞻权重字段，此处全量以保证结构/事实查询都能落到 chunk 与表格。
        sec_sem = index.section_store.search(dense_q, top_k=cfg.rag_candidate_k, filters=f)
        sec_lex = index.bm25_section.search(qs.bm25_query, top_k=cfg.rag_candidate_k, filters=f)
        chunk_hits = index.chunk_store.search(dense_q, top_k=cfg.rag_candidate_k, filters=f)
        ranks = [
            _section_ranklist(sec_sem),
            _section_ranklist(sec_lex),
            _chunk_ranklist(chunk_hits),
        ]

        fused = _rrf_fuse(ranks, k=cfg.rag_rrf_k)

        # —— 按 (doc_id, section_path) 聚合出候选节（跨公司同名节不合并）——
        def _sec_key(c: _Cand) -> tuple:
            return (c.metadata.get("doc_id", ""), c.section_path)

        by_section: dict[tuple, float] = {}
        for c in fused.values():
            if not c.section_path:
                continue
            key = _sec_key(c)
            by_section[key] = max(by_section.get(key, 0.0), c.score)
        top_sections = sorted(by_section, key=lambda k: by_section[k], reverse=True)[: cfg.rag_top_k_sections]

        # —— 每候选节取 top-K chunk，合并去重后作为重排候选池（扩大召回）——
        per_section = max(1, cfg.rag_rerank_candidates // max(cfg.rag_top_k_sections, 1))
        best: list[ScoredChunk] = []
        seen: set[tuple] = set()
        for key in top_sections:
            cands = [c for c in fused.values() if _sec_key(c) == key]
            chunk_cands = sorted(
                (c for c in cands if c.chunk_index >= 0), key=lambda c: c.score, reverse=True
            )
            picks = chunk_cands[:per_section] or cands[:1]  # 无 chunk 则节级派生兜底
            for p in picks:
                kkey = (p.metadata.get("doc_id", ""), max(p.chunk_index, 0))
                if kkey in seen:
                    continue
                seen.add(kkey)
                best.append(
                    ScoredChunk(
                        doc_id=p.metadata.get("doc_id", ""),
                        chunk_index=max(p.chunk_index, 0),
                        text=p.text,
                        score=p.score,
                        metadata=p.metadata,
                    )
                )

        # —— 语义 rerank + 阈值 + top_k ——
        reranked = self._reranker.rerank(plan.rewritten_query, best)
        final = [
            c for c in reranked if c.score >= cfg.rag_rerank_threshold
        ][: cfg.rag_top_k]
        return [_to_chunk(c) for c in final]


def build_retriever(config: Settings) -> HybridRetriever:
    index = build_index(config)
    return HybridRetriever(index=index, reranker=build_reranker(config), config=config)


def _filters_dict(plan: RetrievalPlan) -> dict:
    return {"company": plan.filters.company, "year": plan.filters.year}


def _strict_filters(filters: dict, strict: bool) -> dict | None:
    """仅当存在已知维度且 strict → 过滤；否则 None 靠相关度兜底。"""
    known = filters.get("company") or filters.get("year")
    return filters if (strict and known) else None


def _query_prefix(filters: dict) -> str:
    company = filters.get("company") or ""
    year = filters.get("year") or ""
    return f"{company}{year} " if (company or year) else ""


def _chunk_ranklist(hits: list[ScoredChunk]) -> list[tuple[tuple, _Cand]]:
    out: list[tuple[tuple, _Cand]] = []
    for h in hits:
        key = ("c", h.doc_id, h.chunk_index)
        out.append((key, _Cand(key=key, section_path=tuple(h.metadata.get("section_path", ())), text=h.text, metadata=h.metadata, chunk_index=h.chunk_index)))
    return out


def _section_ranklist(hits) -> list[tuple[tuple, _Cand]]:
    out: list[tuple[tuple, _Cand]] = []
    for h in hits:
        meta = h.metadata
        section_id = meta.get("section_id", "")
        key = ("s", h.doc_id, section_id)
        out.append((key, _Cand(key=key, section_path=tuple(meta.get("section_path", ())), text=h.text, metadata=meta, chunk_index=-1)))
    return out


def _rrf_fuse(ranks: list[list[tuple[tuple, _Cand]]], *, k: int) -> dict[tuple, _Cand]:
    fused: dict[tuple, _Cand] = {}
    for ranked in ranks:
        for rank, (key, cand) in enumerate(ranked):
            if key in fused:
                fused[key].score += 1.0 / (rank + k)
            else:
                cand.score = 1.0 / (rank + k)
                fused[key] = cand
    return fused


def _to_chunk(c: ScoredChunk) -> RagChunk:
    return RagChunk(
        doc_id=c.doc_id,
        doc_title=c.metadata.get("doc_title", ""),
        chunk_index=c.chunk_index,
        text=c.text,
        score=c.score,
        metadata=c.metadata,
    )
