"""检索核心：RRF 融合数学、跨公司隔离、节级语义混入、阈值截断、策略路由。"""

from __future__ import annotations

from demomcp.rag.hybrid_retriever import (
    HybridRetriever,
    _Cand,
    _rrf_fuse,
)
from demomcp.rag.ingest import build_index, ingest
from demomcp.rag.pdf_parser import layout_from_synthetic
from demomcp.rag.schemas import DocMeta, RagFilters, RetrievalPlan


def _layout(company_note: str, caption: str):
    return layout_from_synthetic(
        blocks=[
            {"page": 1, "text": "第三节 管理层讨论与分析", "size": 16.0, "kind": "text", "block_no": 0, "bbox": (0, 0, 100, 10)},
            {"page": 1, "text": "公司应对外部环境变化。", "size": 10.5, "kind": "text", "block_no": 1, "bbox": (0, 10, 100, 20)},
            {"page": 1, "text": "3.2 研发投入", "size": 14.0, "kind": "text", "block_no": 2, "bbox": (0, 20, 100, 30)},
            {"page": 1, "text": company_note, "size": 10.5, "kind": "text", "block_no": 3, "bbox": (0, 30, 100, 40)},
            {"page": 1, "text": "其余业务稳步发展。", "size": 10.5, "kind": "text", "block_no": 4, "bbox": (0, 40, 100, 50)},
        ],
        tables=[
            {"page": 1, "caption": caption, "headers": ["项目", "金额"], "rows": [["研发投入", "100"], ["营业收入", "2000"]], "top": 52.0, "left": 10.0},
        ],
        total_pages=1,
    )


def _docmeta(doc_id: str, company: str, code: str, title: str) -> DocMeta:
    return DocMeta(doc_id=doc_id, title=title, company=company, company_code=code, year=2024, source_pdf="x.pdf", total_pages=1)


def test_rrf_fuse_accumulates_by_rank() -> None:
    a = _Cand(key=("c", "a", 0), section_path=("S",), text="t1", metadata={}, chunk_index=0)
    b = _Cand(key=("c", "b", 0), section_path=("S2",), text="t2", metadata={}, chunk_index=0)
    list_a = [(a.key, a), (b.key, b)]
    list_b = [(b.key, b)]
    fused = _rrf_fuse([list_a, list_b], k=60)
    assert abs(fused[a.key].score - 1 / 60) < 1e-9
    assert abs(fused[b.key].score - (1 / 60 + 1 / 61)) < 1e-9


async def _make_corpus(make_settings):
    s = make_settings(rag_embedding_model="hashing", rag_rerank_threshold=0.0)
    index = build_index(s)
    await ingest(
        s, index=index,
        doc_meta=_docmeta("fy2024_byd", "比亚迪", "002594.SZ", "比亚迪 2024 年报"),
        layout=_layout("报告期内比亚迪研发投入占营业收入比例约百分之五。", "表 12 比亚迪研发投入"),
    )
    await ingest(
        s, index=index,
        doc_meta=_docmeta("fy2024_catl", "宁德时代", "300750.SZ", "宁德时代 2024 年报"),
        layout=_layout("报告期内宁德时代研发投入占营业收入比例约百分之四。", "表 12 宁德时代研发投入"),
    )
    return s, index, HybridRetriever(index=index, config=s)


class _RecordingReranker:
    """记录传给重排的候选池（透传不排序），用于断言「扩大候选池」。"""

    def __init__(self) -> None:
        self.last: list = []

    def rerank(self, query, candidates):
        self.last = list(candidates)
        return candidates


async def test_rerank_receives_wider_pool(make_settings) -> None:
    """同一节含多个 chunk 时，重排候选池应 >1（而非每节只 1 个）。"""
    s = make_settings(
        rag_embedding_model="hashing", rag_rerank_threshold=0.0,
        rag_rerank_candidates=8, rag_top_k_sections=1,
    )
    index = build_index(s)
    await ingest(
        s, index=index,
        doc_meta=_docmeta("fy2024_byd", "比亚迪", "002594.SZ", "比亚迪 2024 年报"),
        layout=_layout("研发投入占营业收入比例百分之五。", "表 12"),
    )
    rec = _RecordingReranker()
    retriever = HybridRetriever(index=index, reranker=rec, config=s)
    await retriever.retrieve(RetrievalPlan(rewritten_query="研发投入"))
    assert len(rec.last) >= 2  # 段落 chunk + 表格 chunk 都进入候选池


async def test_cross_company_isolation(make_settings) -> None:
    _, _, retriever = await _make_corpus(make_settings)
    plan = RetrievalPlan(rewritten_query="研发投入", filters=RagFilters(company="宁德时代", year=2024))
    chunks = await retriever.retrieve(plan)
    assert len(chunks) > 0
    for c in chunks:
        assert c.metadata["company"] == "宁德时代"
        assert c.doc_title == "宁德时代 2024 年报"


async def test_no_filters_returns_both_companies(make_settings) -> None:
    _, _, retriever = await _make_corpus(make_settings)
    plan = RetrievalPlan(rewritten_query="研发投入")
    chunks = await retriever.retrieve(plan)
    companies = {c.metadata["company"] for c in chunks}
    assert companies == {"比亚迪", "宁德时代"}


async def test_threshold_filters_low_scores(make_settings) -> None:
    s = make_settings(rag_embedding_model="hashing", rag_rerank_threshold=0.999)
    index = build_index(s)
    await ingest(s, index=index, doc_meta=_docmeta("fy2024_byd", "比亚迪", "002594.SZ", "比亚迪 2024 年报"), layout=_layout("研发投入占营业收入比例百分之五。", "表 12"))
    retriever = HybridRetriever(index=index, config=s)
    chunks = await retriever.retrieve(RetrievalPlan(rewritten_query="研发投入"))
    # 阈值过滤掉低分段落；但命中节的表格块被「表格保底」保留（财务数值常在此，不被阈值误杀）
    assert chunks and all(c.metadata.get("block_type") == "table" for c in chunks)


async def test_strategy_route_smoke(make_settings) -> None:
    _, _, retriever = await _make_corpus(make_settings)
    for strategy in ("structural", "factual", "auto"):
        plan = RetrievalPlan(rewritten_query="研发投入", strategy=strategy)  # type: ignore[arg-type]
        chunks = await retriever.retrieve(plan)
        assert len(chunks) > 0, f"strategy={strategy} 应有返回"
        assert retriever.last_plan is plan


class _BoomReranker:
    def rerank(self, query, candidates):
        raise ConnectionError("rerank boom")


async def test_rerank_failure_falls_back_to_rrf(make_settings) -> None:
    """rerank API 失败 → 回退 RRF 序并跳过阈值（不再整路置空为无证据）。"""
    s = make_settings(rag_embedding_model="hashing", rag_rerank_threshold=0.999)  # 阈值极高：正常会清空
    index = build_index(s)
    await ingest(
        s, index=index,
        doc_meta=_docmeta("fy2024_byd", "比亚迪", "002594.SZ", "比亚迪 2024 年报"),
        layout=_layout("研发投入占营业收入比例百分之五。", "表 12"),
    )
    retriever = HybridRetriever(index=index, reranker=_BoomReranker(), config=s)
    seen: dict = {}
    chunks = await retriever.retrieve(RetrievalPlan(rewritten_query="研发投入"), on_funnel=seen.update)
    assert chunks  # 即使 threshold=0.999 也返回 top_k（兜底去阈值）
    assert seen.get("rerank_degraded") is True
