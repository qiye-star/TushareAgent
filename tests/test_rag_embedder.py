"""向量化：HashingEmbedder 确定性 / 归一化 / 同空间，以及 build_embedder 后端选择。"""

from __future__ import annotations

import math

from demomcp.rag.embedder import (
    HashingEmbedder,
    _char_bigram_tokenizer,
    build_embedder,
)


def _cos(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


def test_char_bigram_tokenizer_chinese() -> None:
    """中文「研发投入」应产出单字 + 相邻二连（直接测该 tokenizer，不依赖环境是否装了 jieba）。"""
    assert _char_bigram_tokenizer("研发投入") == ["研", "发", "投", "入", "研发", "发投", "投入"]


def test_hashing_embedder_deterministic_across_instances() -> None:
    """同一文本跨实例编码结果一致（确定性，无内置 hash 随机盐）。"""
    a = HashingEmbedder(dim=256)
    b = HashingEmbedder(dim=256)
    text = "比亚迪 2024 年研发投入与研发费用率变化"
    assert a.encode([text])[0] == b.encode([text])[0]


def test_hashing_embedder_dim_and_norm() -> None:
    """dim 用配置值；向量 L2 归一（norm≈1，非零文本）。"""
    emb = HashingEmbedder(dim=256)
    vec = emb.encode_query("研发投入 2024")
    assert len(vec) == 256
    norm = math.sqrt(sum(x * x for x in vec))
    assert abs(norm - 1.0) < 1e-6


def test_hashing_embedder_same_gt_different() -> None:
    """query/doc 同空间；相同文本余弦应显著高于不同文本。"""
    emb = HashingEmbedder(dim=256)
    same = emb.encode_query("研发投入与研发费用率")
    diff = emb.encode_query("主营业务构成 毛利率")
    v = emb.encode_query("研发投入与研发费用率")
    assert _cos(same, v) > 0.99
    assert _cos(same, diff) < _cos(same, v)


def test_build_embedder_hashing_when_configured(make_settings) -> None:
    """rag_embedding_model=hashing → HashingEmbedder（离线/测试路径）。"""
    s = make_settings(rag_embedding_model="hashing")
    emb = build_embedder(s)
    assert isinstance(emb, HashingEmbedder)
    assert emb.dim == s.rag_embedding_dim


def test_build_embedder_offline_falls_back_to_hashing(make_settings) -> None:
    """rag_use_real=False 时即使模型为 bge-m3 也回退 hashing（离线不访问 API）。"""
    s = make_settings(rag_embedding_model="BAAI/bge-m3", rag_use_real=False)
    emb = build_embedder(s)
    assert isinstance(emb, HashingEmbedder)


def test_build_embedder_real_uses_api_when_configured(make_settings) -> None:
    """rag_use_real + 配置了 API base/key → ApiEmbedder（不开网络：仅构造 client）。"""
    from demomcp.rag.embedder import ApiEmbedder

    s = make_settings(
        rag_embedding_model="BAAI/bge-m3", rag_use_real=True,
        rag_embedding_api_base="http://127.0.0.1:9/v1", rag_embedding_api_key="sk-x",
    )
    emb = build_embedder(s)
    assert isinstance(emb, ApiEmbedder)
    assert emb.model == "BAAI/bge-m3"
    assert emb._dim is None  # 未触发真实请求（.dim 属性会发 warmup，勿在此访问）


def test_build_embedder_api_missing_key_falls_back_hashing(make_settings) -> None:
    """rag_use_real=True 但缺 api_key → 回退 HashingEmbedder。"""
    s = make_settings(
        rag_embedding_model="BAAI/bge-m3", rag_use_real=True,
        rag_embedding_api_base="http://127.0.0.1:9/v1", rag_embedding_api_key="",
    )
    emb = build_embedder(s)
    assert isinstance(emb, HashingEmbedder)
