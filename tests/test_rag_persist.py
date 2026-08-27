"""RAG 持久化：RelStore（SQLite 关系库）、BM25 往返、has_index、MilvusVectorStore、save_index。"""

from __future__ import annotations

import pytest

from demomcp.rag.bm25 import BM25
from demomcp.rag.persist import RelStore, has_index, save_index


def test_relstore_chunks_sections_roundtrip(tmp_path) -> None:
    r = RelStore(tmp_path)
    r.add_chunk("byd", 0, "研发投入X", {"company": "比亚迪", "year": 2025})
    r.add_section("byd", 0, "整节文本", {"company": "比亚迪"})
    text, meta = r.get_chunk("byd", 0)
    assert text == "研发投入X"
    assert meta["company"] == "比亚迪"
    assert r.has_chunks()
    assert next(iter(r.iter_chunks()))[0] == "byd"
    assert next(iter(r.iter_sections()))[1] == 0
    r.delete_doc("byd")
    assert not r.has_chunks()


def test_bm25_save_load_roundtrip(tmp_path) -> None:
    b = BM25()
    b.add("s1", "byd", "研发投入增长", {"company": "比亚迪"})
    b.add("s2", "catl", "主营电池业务", {"company": "宁德时代"})
    r = RelStore(tmp_path)
    r.save_bm25(b.iter_state())
    b2 = r.load_bm25()
    assert b2.count() == 2
    assert b2.score("研发投入")[0].section_id == "s1"
    assert b2.score("电池")[0].section_id == "s2"


def test_relstore_search_bm25(tmp_path) -> None:
    """SQLite 实时 BM25：从 bm25_docs/meta 打分，不重建内存 corpus；支持 company/year 过滤。"""
    b = BM25()
    b.add("s_byd", "byd", "研发投入增长", {"company": "比亚迪", "year": 2025})
    b.add("s_catl", "catl", "主营电池业务", {"company": "宁德时代", "year": 2025})
    r = RelStore(tmp_path)
    r.save_bm25(b.iter_state())
    hits = r.search_bm25("研发投入", top_k=2)
    assert hits and hits[0].section_id == "s_byd"
    assert hits[0].text == "研发投入增长"
    assert hits[0].metadata["company"] == "比亚迪"
    only_catl = r.search_bm25("电池", top_k=5, filters={"company": "宁德时代"})
    assert [h.doc_id for h in only_catl] == ["catl"]


def test_milvus_kind_routing(tmp_path) -> None:
    """MilvusVectorStore kind 把节/块分别写入 RelStore.sections / chunks，互不覆盖。"""
    pytest.importorskip("pymilvus")
    from demomcp.rag.persist import RelStore
    from demomcp.rag.store import MilvusVectorStore

    d = tmp_path
    rel = RelStore(d)
    cst = MilvusVectorStore(dim=4, uri=str(d), collection="t_chunk", rel=rel, kind="chunk")
    sst = MilvusVectorStore(dim=4, uri=str(d), collection="t_section", rel=rel, kind="section")
    cst.add("byd", 0, "块文本", [1, 0, 0, 0], {"company": "比亚迪"})
    sst.add("byd", 0, "节文本", [1, 0, 0, 0], {"company": "比亚迪"})
    assert rel.get_chunk("byd", 0)[0] == "块文本"
    assert rel.get_section("byd", 0)[0] == "节文本"
    assert len(list(rel.iter_sections())) == 1


def test_has_index_requires_milvus_and_chunks(tmp_path) -> None:
    d = tmp_path
    assert not has_index(d)
    (d / "milvus.db").write_bytes(b"")  # 占位 milvus 文件
    r = RelStore(d)
    assert not has_index(d)  # 有 milvus 无 chunks
    r.add_chunk("byd", 0, "文本", {"company": "比亚迪"})
    assert has_index(d)  # 两者齐备


async def test_save_index_writes_bm25_and_dim(make_settings, tmp_path) -> None:
    """save_index 把 BM25 + dim 元数据落 RelStore（hashing 无 Milvus，仅验证关系库写入）。"""
    from demomcp.rag.ingest import build_index, ingest
    from demomcp.rag.pdf_parser import layout_from_synthetic
    from demomcp.rag.schemas import DocMeta

    s = make_settings(rag_embedding_model="hashing", rag_use_real=False)
    index = build_index(s)
    dm = DocMeta(doc_id="fy2024_byd", title="t", company="比亚迪", company_code="002594.SZ", year=2024, source_pdf="x", total_pages=1)
    await ingest(s, index=index, doc_meta=dm, layout=layout_from_synthetic(
        blocks=[
            {"page": 1, "text": "第三节", "size": 16.0, "kind": "text", "block_no": 0, "bbox": (0, 0, 100, 5)},
            {"page": 1, "text": "3.2 研发投入", "size": 14.0, "kind": "text", "block_no": 1, "bbox": (0, 5, 100, 10)},
            {"page": 1, "text": "研发投入占比上升。", "size": 10.5, "kind": "text", "block_no": 2, "bbox": (0, 10, 100, 20)},
        ],
        tables=[], total_pages=1,
    ))
    save_index(index, tmp_path)
    r = RelStore(tmp_path)
    assert int(r.get_meta("dim")) == index.embedder.dim
    reloaded = r.load_bm25()
    assert reloaded.count() == index.bm25_section.count()
    assert reloaded.score("研发投入")


def test_milvus_vector_store_add_search_filter_count(tmp_path) -> None:
    pytest.importorskip("pymilvus")
    from demomcp.rag.store import MilvusVectorStore

    d = tmp_path
    v = MilvusVectorStore(dim=4, uri=str(d), collection="test_c")
    v.add("byd", 0, "比亚迪研发投入", [1, 0, 0, 0], {"company": "比亚迪", "year": 2025, "section_path": ["3.2"]})
    v.add("catl", 0, "宁德时代主营", [0, 1, 0, 0], {"company": "宁德时代", "year": 2025})
    v.add("byd", 1, "营业收入", [0.8, 0.2, 0, 0], {"company": "比亚迪", "year": 2025})
    assert v.count() == 3
    hits = v.search([1, 0, 0, 0], top_k=2)
    assert hits[0].doc_id == "byd" and hits[0].chunk_index == 0
    assert hits[0].metadata["company"] == "比亚迪"
    byd = v.search([1, 0, 0, 0], top_k=5, filters={"company": "比亚迪"})
    assert all(h.doc_id == "byd" for h in byd)
    catl = v.search([0, 1, 0, 0], top_k=5, filters={"company": "宁德时代", "year": 2025})
    assert len(catl) == 1 and catl[0].doc_id == "catl"
    v.delete_doc("byd")
    assert v.count() == 1
