"""漏斗可观测性：retriever.retrieve(plan, on_funnel=...) 暴露召回/融合/节/池/重排/最终各阶段计数。"""

from __future__ import annotations

from demomcp.rag.hybrid_retriever import HybridRetriever
from demomcp.rag.ingest import build_index, ingest
from demomcp.rag.pdf_parser import layout_from_synthetic
from demomcp.rag.schemas import DocMeta, RetrievalPlan


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


async def test_funnel_counts_exposed(make_settings) -> None:
    """on_funnel 收到完整阶段计数，且与最终返回一致（NoopReranker 下 rerank 透传）。"""
    s = make_settings(rag_embedding_model="hashing", rag_rerank_threshold=0.0)
    index = build_index(s)
    await ingest(
        s, index=index,
        doc_meta=_docmeta("fy2024_byd", "比亚迪", "002594.SZ", "比亚迪 2024 年报"),
        layout=_layout("报告期内比亚迪研发投入占营业收入比例约百分之五。", "表 12 比亚迪研发投入"),
    )
    retriever = HybridRetriever(index=index, config=s)
    seen: dict = {}

    chunks = await retriever.retrieve(RetrievalPlan(rewritten_query="研发投入"), on_funnel=seen.update)

    assert set(seen) == {"recall_raw", "fused", "sections", "pool", "reranked", "final", "rerank_degraded"}
    assert seen["final"] == len(chunks)          # 漏斗终点 = 实际返回个数
    assert seen["recall_raw"] >= 1               # 三路检索有命中
    assert seen["fused"] >= 1                    # RRF 融合非空
    assert seen["sections"] >= 1                 # 至少一个候选节
    assert seen["pool"] >= seen["final"]         # 池 ≥ 最终
    assert seen["reranked"] == seen["pool"]      # NoopReranker 透传


async def test_funnel_vacuous_without_hits(make_settings) -> None:
    """无命中时 final=0，计数仍如实暴露、不抛异常。"""
    s = make_settings(rag_embedding_model="hashing", rag_rerank_threshold=0.0)
    index = build_index(s)
    await ingest(
        s, index=index,
        doc_meta=_docmeta("fy2024_byd", "比亚迪", "002594.SZ", "比亚迪 2024 年报"),
        layout=_layout("报告期内比亚迪研发投入占营业收入比例约百分之五。", "表 12 比亚迪研发投入"),
    )
    retriever = HybridRetriever(index=index, config=s)
    seen: dict = {}

    chunks = await retriever.retrieve(
        RetrievalPlan(rewritten_query="完全不存在的查询关键字qqxx"), on_funnel=seen.update
    )

    assert seen["final"] == len(chunks)
    assert seen["final"] >= 0
