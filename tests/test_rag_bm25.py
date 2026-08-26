"""BM25：排序 / filters 隔离 / count / 零 token 跳过 / 中文键词命中。"""

from __future__ import annotations

from demomcp.rag.bm25 import BM25


def test_bm25_ranks_matching_doc_first() -> None:
    b = BM25()
    b.add("s1", "fy2024_byd", "研发投入增长三成，继续加大在新能源汽车领域的投入", {"company": "比亚迪"})
    b.add("s2", "fy2024_byd", "主营业务以动力电池为核心，客户集中度较高", {"company": "比亚迪"})
    b.add("s3", "fy2024_byd", "毛利率同比变动主要受原材料价格影响", {"company": "比亚迪"})
    scored = b.score("研发投入")
    assert scored[0].section_id == "s1"
    assert scored[0].score > 0


def test_bm25_search_filters_isolation() -> None:
    b = BM25()
    b.add("a", "byd", "比亚迪研发投入增长", {"company": "比亚迪", "year": 2024})
    b.add("c", "catl", "宁德时代研发投入增长", {"company": "宁德时代", "year": 2024})
    hits = b.search("研发投入", top_k=5, filters={"company": "宁德时代"})
    assert len(hits) == 1
    assert hits[0].doc_id == "catl"


def test_bm25_count_and_zero_token_skipped() -> None:
    b = BM25()
    b.add("a", "byd", "正常文本", {"company": "比亚迪"})
    b.add("b", "byd", "！！！，，，", {"company": "比亚迪"})  # 纯标点 → 零 token 跳过
    assert b.count() == 1


def test_bm25_avgdl_and_params_affect_score() -> None:
    """长文档/短文档对同一词打分受 avgdl 影响：短文档匹配更“聚焦”故得分更高。"""
    b = BM25(k1=1.5, b=0.75)
    b.add("short", "byd", "研发投入 研发投入", {"company": "比亚迪"})
    b.add("long", "byd", "研发投入" + "重复内容" * 40, {"company": "比亚迪"})
    scored = b.score("研发投入")
    assert scored[0].section_id == "short"
    assert scored[0].score > 0


def test_bm25_delete_doc() -> None:
    b = BM25()
    b.add("a", "byd", "研发投入", {"company": "比亚迪"})
    b.add("c", "catl", "主营业务", {"company": "宁德时代"})
    b.delete_doc("catl")
    assert b.count() == 1
    assert "catl" not in [d.doc_id for d in b.score("主营业务")]
    assert b.count() == 1  # 幂等：再删无影响
