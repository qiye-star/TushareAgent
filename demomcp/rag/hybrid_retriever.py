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
    async def retrieve(self, plan: RetrievalPlan, *, on_funnel=None) -> list[RagChunk]: ...


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

    async def retrieve(self, plan: RetrievalPlan, *, on_funnel=None) -> list[RagChunk]:
        return await asyncio.to_thread(self._retrieve_sync, plan, on_funnel=on_funnel)

    def _retrieve_sync(self, plan: RetrievalPlan, *, on_funnel=None) -> list[RagChunk]:
        cfg = self._cfg
        index = self._index
        self.last_plan = plan
        filters = _filters_dict(plan)
        f = _strict_filters(filters, cfg.rag_strict_scope)
        qs = query_build.build_queries(plan)
        dense_q = index.embedder.encode_query(_query_prefix(filters) + qs.dense_query)

        # —— 按 strategy 收窄候选路（docstring 分派）——
        #    chunk 路**始终**参与：表格块在 chunk 库里，structural 若省掉会丢表格召回（离线标注验证过）。
        #    节级两路（语义+词法，给节加分）仅 factual 跳过 → 纯 chunk 事实查询。
        #    auto（或缺省）→ 全量；factual → 仅 chunk；structural → 节级两路 + chunk（保表格覆盖）。
        ranks: list = []
        recall = 0
        if plan.strategy != "factual":
            sec_sem = index.section_store.search(dense_q, top_k=cfg.rag_candidate_k, filters=f)
            sec_lex = index.bm25_section.search(qs.bm25_query, top_k=cfg.rag_candidate_k, filters=f)
            ranks += [_section_ranklist(sec_sem), _section_ranklist(sec_lex)]
            recall += len(sec_sem) + len(sec_lex)
        chunk_hits = index.chunk_store.search(dense_q, top_k=cfg.rag_candidate_k, filters=f)
        ranks.append(_chunk_ranklist(chunk_hits))
        recall += len(chunk_hits)
        recall_raw = recall

        fused = _rrf_fuse(ranks, k=cfg.rag_rrf_k)
        n_fused = len(fused)

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
        n_sections = len(top_sections)

        # —— 每候选节取 top-K chunk，合并去重后作为重排候选池（扩大召回、控制单节泛滥）——
        per_section = max(cfg.rag_top_k, -(-cfg.rag_rerank_candidates // max(1, n_sections)))
        best: list[ScoredChunk] = []
        best_tables: list[ScoredChunk] = []   # 每候选节保底 1 个最优表格块（财务数值常在此）
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
            # 节内表格保底：该节若有表格块，至少一并交出最优一块
            tbl = next((c for c in chunk_cands if c.metadata.get("block_type") == "table"), None)
            if tbl is not None:
                best_tables.append(
                    ScoredChunk(
                        doc_id=tbl.metadata.get("doc_id", ""),
                        chunk_index=max(tbl.chunk_index, 0),
                        text=tbl.text,
                        score=tbl.score,
                        metadata=tbl.metadata,
                    )
                )
        best = best[: cfg.rag_rerank_candidates]  # 硬顶重排候选池
        # 若池顶把表格块全部挤出，把各节保底表格块并回池（只补表格）
        if best_tables and not any(c.metadata.get("block_type") == "table" for c in best):
            for t in best_tables:
                kkey = (t.metadata.get("doc_id", ""), t.chunk_index)
                if kkey in seen:
                    continue
                seen.add(kkey)
                best.append(t)
            best = best[: cfg.rag_rerank_candidates]
        n_pool = len(best)

        # —— 语义 rerank + 阈值 + top_k（rerank 失败 → RRF 序兜底）——
        rerank_ok = True
        try:
            reranked = self._reranker.rerank(plan.rewritten_query, best)
        except Exception:  # noqa: BLE001 - rerank API 失败 → RRF 顺序兜底，不整路置空
            reranked = best
            rerank_ok = False
        n_reranked = len(reranked)
        if rerank_ok:
            final = [
                c for c in reranked if c.score >= cfg.rag_rerank_threshold
            ][: cfg.rag_top_k]
        else:
            final = reranked[: cfg.rag_top_k]  # 兜底时跳过 threshold（RRF 分 ~1/60，0.2 阈值会清空）
        # —— 表格保底：final 无表格块而候选节有 → 补最优节表格块（保住财务数值）——
        if best_tables and not any(c.metadata.get("block_type") == "table" for c in final):
            inj = next((c for c in reranked if c.metadata.get("block_type") == "table"), None) or best_tables[0]
            if (inj.metadata.get("doc_id", ""), inj.chunk_index) not in {
                (c.metadata.get("doc_id", ""), c.chunk_index) for c in final
            }:
                final = (final + [inj])[: cfg.rag_top_k]
        if on_funnel is not None:
            on_funnel(
                {
                    "recall_raw": recall_raw,
                    "fused": n_fused,
                    "sections": n_sections,
                    "pool": n_pool,
                    "reranked": n_reranked,
                    "final": len(final),
                    "rerank_degraded": not rerank_ok,
                }
            )
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
