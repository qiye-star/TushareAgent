"""RAG 配置：新增 RAG_* 字段的默认值、别名与环境变量读取。"""

from __future__ import annotations


def test_rag_config_defaults(make_settings) -> None:
    """make_settings 的默认值应落在离线优先的 RAG 设计值。"""
    s = make_settings()
    assert s.rag_use_real is False
    assert s.rag_embedding_model == "BAAI/bge-m3"
    assert s.rag_embedding_dim == 256
    assert s.rag_captioner == "deepseek-v4-flash-vision-exp"
    assert s.rag_embedding_api_base == "https://api.siliconflow.cn/v1"
    assert s.rag_embedding_api_key == ""
    assert s.rag_rerank_model == "BAAI/bge-reranker-v2-m3"
    assert s.rag_top_k == 5
    assert s.rag_candidate_k == 30
    assert s.rag_top_k_sections == 3
    assert s.rag_score_threshold == 0.3
    assert s.rag_rerank_threshold == 0.3
    assert s.rag_rerank_candidates == 24
    assert s.rag_fin_dense_chunk == 400
    assert s.rag_fin_dense_overlap == 80
    assert s.rag_section_max_chars == 8000
    assert s.rag_table_rows_per_chunk == 8
    assert s.rag_hybrid_dense_weight == 0.6
    assert s.rag_strict_scope is True
    assert s.rag_hyde is False
    assert s.rag_strategy == "auto"
    assert s.rag_table_engine == "pymupdf"
    assert s.rag_rrf_k == 60
    assert s.rag_bm25_k1 == 1.5
    assert s.rag_bm25_b == 0.75


def test_rag_config_env_override(make_settings, monkeypatch) -> None:
    """RAG_* 环境变量应以别名注入对应字段（大小写不敏感）。"""
    monkeypatch.setenv("RAG_TOP_K", "11")
    monkeypatch.setenv("RAG_STRATEGY", "structural")
    monkeypatch.setenv("RAG_EMBEDDING_MODEL", "hashing")
    s = make_settings()
    assert s.rag_top_k == 11
    assert s.rag_strategy == "structural"
    assert s.rag_embedding_model == "hashing"


def test_rag_config_alias_by_positional_name(make_settings) -> None:
    """字段名构造可用（populate_by_name），便于测试传 rag_top_k 等。"""
    s = make_settings(rag_top_k=9, rag_hybrid_dense_weight=0.7)
    assert s.rag_top_k == 9
    assert s.rag_hybrid_dense_weight == 0.7
