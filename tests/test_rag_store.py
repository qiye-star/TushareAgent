"""向量库：内存余弦 add/search/delete/count + 元数据过滤 + 维度校验。"""

from __future__ import annotations

import pytest

from demomcp.rag.store import InMemoryVectorStore


def _vec(*vals: float, dim: int = 256) -> list[float]:
    v = [0.0] * dim
    for i, val in enumerate(vals):
        v[i] = val
    return v


def test_add_and_count() -> None:
    s = InMemoryVectorStore(dim=256)
    s.add("fy2024_byd", 0, "文本", _vec(1.0), {"company": "比亚迪", "year": 2024})
    s.add("fy2024_byd", 1, "另一段", _vec(0.0, 1.0), {"company": "比亚迪", "year": 2024})
    s.add("fy2024_catl", 0, "宁德文本", _vec(1.0, 1.0), {"company": "宁德时代", "year": 2024})
    assert s.count() == 3


def test_search_cosine_descending() -> None:
    s = InMemoryVectorStore(dim=256)
    s.add("a", 0, "X", _vec(1.0), {})
    s.add("a", 1, "Y", _vec(0.0, 1.0), {})
    hits = s.search(_vec(1.0), top_k=2)
    assert len(hits) == 2
    # 命中顺序按余弦降序
    assert hits[0].text == "X"
    assert hits[1].text == "Y"
    assert hits[0].score > hits[1].score


def test_delete_doc_idempotent() -> None:
    s = InMemoryVectorStore(dim=256)
    s.add("byd", 0, "A", _vec(1.0), {"company": "比亚迪"})
    s.add("catl", 0, "B", _vec(1.0), {"company": "宁德时代"})
    s.delete_doc("byd")
    assert s.count() == 1
    s.delete_doc("byd")  # 再删一次不报错
    assert s.count() == 1


def test_search_filters_company_isolation() -> None:
    s = InMemoryVectorStore(dim=256)
    s.add("byd", 0, "比亚迪研发", _vec(1.0), {"company": "比亚迪", "year": 2024})
    s.add("catl", 0, "宁德时代研发", _vec(1.0), {"company": "宁德时代", "year": 2024})
    hits = s.search(_vec(1.0), top_k=5, filters={"company": "宁德时代"})
    assert len(hits) == 1
    assert hits[0].doc_id == "catl"
    assert hits[0].text == "宁德时代研发"


def test_dim_mismatch_raises() -> None:
    s = InMemoryVectorStore(dim=256)
    with pytest.raises(ValueError):
        s.add("a", 0, "X", [1.0, 2.0], {})
    s.add("a", 0, "X", _vec(1.0), {})
    with pytest.raises(ValueError):
        s.search([1.0, 2.0], top_k=1)
