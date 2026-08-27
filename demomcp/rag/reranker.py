"""重排：Reranker 协议 + Noop（默认，按 RRF 序）+ ApiReranker（SiliconFlow /v1/rerank，bge-reranker-v2-m3）。

真实重排走服务端 /v1/rerank（非 OpenAI 兼容），本地不加载任何重排模型。失败/未配置 → Noop。
"""

from __future__ import annotations

from dataclasses import replace
from typing import Protocol

import httpx

from demomcp.config.settings import Settings
from demomcp.rag.schemas import ScoredChunk


class Reranker(Protocol):
    def rerank(self, query: str, candidates: list[ScoredChunk]) -> list[ScoredChunk]: ...


class NoopReranker:
    """默认：按入参顺序（即 RRF 聚合后的顺序）原样返回。"""

    def rerank(self, query: str, candidates: list[ScoredChunk]) -> list[ScoredChunk]:
        return list(candidates)


class ApiReranker:
    """SiliconFlow /v1/rerank：服务端跑 bge-reranker-v2-m3，返回按分降序的重排结果。"""

    def __init__(
        self,
        model: str = "BAAI/bge-reranker-v2-m3",
        *,
        base_url: str,
        api_key: str,
    ) -> None:
        self.model = model
        self._base = base_url.rstrip("/")
        self._key = api_key

    def rerank(self, query: str, candidates: list[ScoredChunk]) -> list[ScoredChunk]:
        if not candidates:
            return list(candidates)
        resp = httpx.post(
            f"{self._base}/rerank",
            headers={"Authorization": f"Bearer {self._key}"},
            json={
                "model": self.model, "query": query,
                "documents": [c.text for c in candidates], "top_n": len(candidates),
            },
            timeout=20.0,
        )
        resp.raise_for_status()
        results = sorted(resp.json().get("results", []), key=lambda r: r["index"])
        ranked = sorted(results, key=lambda r: r["relevance_score"], reverse=True)
        return [
            replace(candidates[r["index"]], score=float(r["relevance_score"])) for r in ranked
        ]


def build_reranker(config: Settings) -> Reranker:
    """rag_rerank_model 且配置了 SiliconFlow base/key → ApiReranker；否则 Noop。"""
    if config.rag_rerank_model and config.rag_embedding_api_base and config.rag_embedding_api_key:
        try:
            return ApiReranker(
                config.rag_rerank_model,
                base_url=config.rag_embedding_api_base,
                api_key=config.rag_embedding_api_key,
            )
        except Exception as exc:  # noqa: BLE001 - 回退 Noop
            print(f"[rag] API 重排配置失败，回退 Noop：{exc}")
    return NoopReranker()
