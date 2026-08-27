"""向量化：Embedder 协议 + API 嵌入（OpenAI 兼容 /embeddings，服务端跑 bge-m3）+ HashingEmbedder（测试/离线兜底）。

默认真实向量模型为 BAAI/bge-m3，经 api_key 调 OpenAI 兼容 /embeddings 服务，本地不加载模型。
HashingEmbedder 仅在显式 rag_embedding_model="hashing" 或未配置 API 时使用，不是生产后端。
"""



from __future__ import annotations

import hashlib
import math
from collections.abc import Callable
from typing import Protocol, runtime_checkable

from demomcp.config.settings import Settings

Tokenizer = Callable[[str], list[str]]

# 无意义标点（tokenizer 过滤用）
_PUNCT = set("，。！？；：、（）()《》“”‘’「」【】　")


def _is_cjk(ch: str) -> bool:
    code = ord(ch)
    return 0x4E00 <= code <= 0x9FFF or 0x3400 <= code <= 0x4DBF


def _char_bigram_tokenizer(text: str) -> list[str]:
    """中文：CJK 单字 + 相邻二连；英文/数字按原词保留；过滤空白与标点。先单字后二连。"""
    singles: list[str] = []
    bigrams: list[str] = []
    prev_cjk: str | None = None
    buf: list[str] = []

    def _flush() -> None:
        if buf:
            singles.append("".join(buf))
            buf.clear()

    for ch in text:
        if ch.isspace() or ch in _PUNCT:
            prev_cjk = None
            _flush()
            continue
        if _is_cjk(ch):
            _flush()
            singles.append(ch)
            if prev_cjk:
                bigrams.append(prev_cjk + ch)
            prev_cjk = ch
        elif ch.isalnum():
            prev_cjk = None  # 数字/英文打断 CJK 二连
            buf.append(ch)
    _flush()
    return singles + bigrams


def default_tokenizer() -> Tokenizer:
    """优选 jieba（懒加载，装了就用），否则回退 char-bigram。"""
    try:
        import jieba  # 可选依赖，惰性import

        return lambda s: [t for t in jieba.cut(s) if t.strip() and t not in _PUNCT]
    except ImportError:
        return _char_bigram_tokenizer


@runtime_checkable
class Embedder(Protocol):
    """向量化协议：doc 与 query 同空间（L2 归一，余弦即点积）。"""

    dim: int

    def encode(self, texts: list[str]) -> list[list[float]]: ...

    def encode_query(self, text: str) -> list[float]: ...


class HashingEmbedder:
    """确定性纯 Python 向量化（测试/离线兜底）：特征哈希到固定维度，L2 归一。

    禁用内置 hash()（进程随机盐），统一 hashlib.blake2b；有符号哈希抵消方向偏置。
    """

    def __init__(self, *, dim: int = 256, tokenizer: Tokenizer | None = None) -> None:
        self.dim = dim
        self._tokenizer = tokenizer or default_tokenizer()

    def encode(self, texts: list[str]) -> list[list[float]]:
        return [self._encode_one(t) for t in texts]

    def encode_query(self, text: str) -> list[float]:
        return self._encode_one(text)

    def _encode_one(self, text: str) -> list[float]:
        vec = [0.0] * self.dim
        for token in self._tokenizer(text):
            h = int(hashlib.blake2b(token.encode("utf-8"), digest_size=4).hexdigest(), 16)
            idx = h % self.dim
            sign = 1 if (h // self.dim) % 2 == 0 else -1
            vec[idx] += sign
        norm = math.sqrt(sum(x * x for x in vec))
        if norm < 1e-8:
            return vec
        return [x / norm for x in vec]


_EMBED_BATCH = 32  # 单次嵌入请求的文本条数（平衡 SiliconFlow 限流与往返次数）


class ApiEmbedder:
    """OpenAI 兼容 /embeddings 嵌入服务（服务端跑 bge-m3，本地不加载模型）。

    dim 由首次响应延迟锁定（只在 build_index 读取 .dim 时发一次请求，测试不受影响）。
    encode 按 _EMBED_BATCH 分片、按 index 拼接，保证顺序与完整；client 带短超时+重试。
    """

    def __init__(self, model: str = "BAAI/bge-m3", *, base_url: str, api_key: str) -> None:
        from openai import OpenAI  # 惰性import；openai 为既有依赖

        self.model = model
        self._client = OpenAI(api_key=api_key, base_url=base_url, timeout=30.0, max_retries=2)
        self._dim: int | None = None

    @property
    def dim(self) -> int:
        if self._dim is None:
            self._dim = len(self.encode_query("warmup"))
        return self._dim

    def encode(self, texts: list[str]) -> list[list[float]]:
        vecs: list[list[float]] = []
        for i in range(0, len(texts), _EMBED_BATCH):
            batch = list(texts[i : i + _EMBED_BATCH])
            resp = self._client.embeddings.create(model=self.model, input=batch)
            data = sorted(resp.data, key=lambda d: d.index)
            vecs.extend([list(map(float, d.embedding)) for d in data])
        if self._dim is None and vecs:
            self._dim = len(vecs[0])
        return [_normalize(v) for v in vecs]

    def encode_query(self, text: str) -> list[float]:
        return self.encode([text])[0]


def build_embedder(config: Settings) -> Embedder:
    """按配置选后端：hashing=测试兜底；rag_use_real 且配置 API → ApiEmbedder；否则回退 hashing。"""
    if config.rag_embedding_model == "hashing":
        return HashingEmbedder(dim=config.rag_embedding_dim)
    if config.rag_use_real and config.rag_embedding_api_base and config.rag_embedding_api_key:
        try:
            return ApiEmbedder(
                config.rag_embedding_model,
                base_url=config.rag_embedding_api_base,
                api_key=config.rag_embedding_api_key,
            )
        except Exception as exc:  # noqa: BLE001 - 回退兜底
            print(f"[rag] API 嵌入配置失败，回退 HashingEmbedder：{exc}")
    return HashingEmbedder(dim=config.rag_embedding_dim)


def _normalize(vec: list[float]) -> list[float]:
    norm = math.sqrt(sum(x * x for x in vec))
    if norm < 1e-8:
        return vec
    return [x / norm for x in vec]
