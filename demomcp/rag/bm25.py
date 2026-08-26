"""自包含 OKAPI BM25（节级词法检索）。tokenizer 缺省用 default_tokenizer（jieba 惰性，否则 char-bigram）。

同 section_id 覆盖 = 幂等；零 token 文本跳过；BM25+ 平滑防负 idf。
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import replace

from demomcp.rag.embedder import Tokenizer, default_tokenizer
from demomcp.rag.schemas import ScoredDoc


class BM25:
    def __init__(
        self,
        *,
        k1: float = 1.5,
        b: float = 0.75,
        tokenizer: Tokenizer | None = None,
    ) -> None:
        self._k1 = k1
        self._b = b
        self._tok = tokenizer or default_tokenizer()
        self._docs: dict[str, ScoredDoc] = {}
        self._tokens: dict[str, Counter[str]] = {}
        self._doc_len: dict[str, int] = {}
        self._df: Counter[str] = Counter()
        self._total_len = 0

    def add(
        self,
        section_id: str,
        doc_id: str,
        text: str,
        metadata: dict | None = None,
    ) -> None:
        self._remove(section_id)
        tokens = self._tok(text)
        if not tokens:
            return
        tf = Counter(tokens)
        self._tokens[section_id] = tf
        self._doc_len[section_id] = len(tokens)
        self._docs[section_id] = ScoredDoc(
            section_id=section_id, doc_id=doc_id, text=text, score=0.0, metadata=metadata or {}
        )
        self._total_len += len(tokens)
        for t in tf:
            self._df[t] += 1

    def delete_doc(self, doc_id: str) -> None:
        for sid in [sid for sid, d in self._docs.items() if d.doc_id == doc_id]:
            self._remove(sid)

    def count(self) -> int:
        return len(self._docs)

    def search(
        self,
        query: str,
        *,
        top_k: int = 10,
        filters: dict | None = None,
    ) -> list[ScoredDoc]:
        n = self.count()
        avgdl = self._total_len / max(n, 1)
        q_tokens = self._tok(query)
        candidates = [
            (sid, d)
            for sid, d in self._docs.items()
            if _match(d.metadata, filters)
        ]
        scored = [
            replace_scored(d, self._score(self._tokens[sid], self._doc_len[sid], q_tokens, n, avgdl))
            for sid, d in candidates
        ]
        scored.sort(key=lambda d: d.score, reverse=True)
        return scored[:top_k]

    def score(self, query: str) -> list[ScoredDoc]:
        """全量打分（供测试断言排序）。"""
        n = self.count()
        avgdl = self._total_len / max(n, 1)
        q_tokens = self._tok(query)
        scored = [
            replace_scored(d, self._score(self._tokens[sid], self._doc_len[sid], q_tokens, n, avgdl))
            for sid, d in self._docs.items()
        ]
        scored.sort(key=lambda d: d.score, reverse=True)
        return scored

    def _score(self, tf: Counter[str], dl: int, q_tokens: list[str], n: int, avgdl: float) -> float:
        s = 0.0
        for t in set(q_tokens):
            n_t = self._df.get(t, 0)
            if n_t == 0:
                continue
            idf = math.log((n - n_t + 0.5) / (n_t + 0.5) + 1.0)
            f = tf.get(t, 0)
            s += idf * (f * (self._k1 + 1)) / (
                f + self._k1 * (1 - self._b + self._b * dl / max(avgdl, 1e-3))
            )
        return s

    def _remove(self, section_id: str) -> None:
        tf = self._tokens.pop(section_id, None)
        if tf is None:
            return
        for t in tf:
            self._df[t] -= 1
            if self._df[t] == 0:
                del self._df[t]
        self._total_len -= self._doc_len.pop(section_id, 0)
        self._docs.pop(section_id, None)


def replace_scored(doc: ScoredDoc, score: float) -> ScoredDoc:
    return replace(doc, score=score)


def _match(meta: dict, filters: dict | None) -> bool:
    if not filters:
        return True
    return (filters.get("company") is None or meta.get("company") == filters["company"]) and (
        filters.get("year") is None or meta.get("year") == filters["year"]
    )
