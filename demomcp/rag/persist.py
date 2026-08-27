"""RAG 持久化：SQLite 关系库 RelStore（chunk/节/BM25/元数据）+ 索引 save/load/has_index。

与 store.MilvusVectorStore 搭配：向量存 Milvus（落盘），文本/元数据/BM25 存本库；启动时
`load_index` 秒级加载，避免每次重打 bge-m3 嵌入 API。离线（rag_use_real=False）不触发本层。
"""

from __future__ import annotations

import json
import math
import sqlite3
from pathlib import Path

from demomcp.config.settings import Settings
from demomcp.rag.bm25 import BM25
from demomcp.rag.embedder import default_tokenizer
from demomcp.rag.schemas import ScoredDoc

_REL_DB = "rag_rel.db"
_MILVUS_DB = "milvus.db"


class RelStore:
    """SQLite 关系库：chunks / sections / bm25_docs / meta。仅存文本与元数据，向量在 Milvus。"""

    def __init__(self, base: str | Path) -> None:
        self.base = Path(base)
        self.base.mkdir(parents=True, exist_ok=True)
        self._db = str(self.base / _REL_DB)
        self._conn = sqlite3.connect(self._db, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._init_tables()

    def _init_tables(self) -> None:
        c = self._conn
        c.execute(
            "CREATE TABLE IF NOT EXISTS chunks(doc_id TEXT, chunk_index INT, text TEXT, metadata_json TEXT, "
            "PRIMARY KEY(doc_id, chunk_index))"
        )
        c.execute(
            "CREATE TABLE IF NOT EXISTS sections(doc_id TEXT, idx INT, text TEXT, metadata_json TEXT, "
            "PRIMARY KEY(doc_id, idx))"
        )
        c.execute(
            "CREATE TABLE IF NOT EXISTS bm25_docs(section_id TEXT PRIMARY KEY, doc_id TEXT, text TEXT, "
            "metadata_json TEXT, tf_json TEXT, doc_len INT)"
        )
        c.execute("CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT)")
        c.commit()

    # —— chunks ——
    def add_chunk(self, doc_id: str, chunk_index: int, text: str, metadata: dict) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO chunks VALUES(?,?,?,?)",
            (doc_id, int(chunk_index), text, json.dumps(metadata, ensure_ascii=False)),
        )
        self._conn.commit()

    def get_chunk(self, doc_id: str, chunk_index: int) -> tuple[str | None, dict | None]:
        row = self._conn.execute(
            "SELECT text, metadata_json FROM chunks WHERE doc_id=? AND chunk_index=?",
            (doc_id, int(chunk_index)),
        ).fetchone()
        if not row:
            return None, None
        return row[0], json.loads(row[1])

    def iter_chunks(self):
        for doc_id, cidx, text, mj in self._conn.execute("SELECT doc_id, chunk_index, text, metadata_json FROM chunks"):
            yield doc_id, cidx, text, json.loads(mj)

    # —— sections ——
    def add_section(self, doc_id: str, idx: int, text: str, metadata: dict) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO sections VALUES(?,?,?,?)",
            (doc_id, int(idx), text, json.dumps(metadata, ensure_ascii=False)),
        )
        self._conn.commit()

    def get_section(self, doc_id: str, idx: int) -> tuple[str | None, dict | None]:
        row = self._conn.execute(
            "SELECT text, metadata_json FROM sections WHERE doc_id=? AND idx=?",
            (doc_id, int(idx)),
        ).fetchone()
        if not row:
            return None, None
        return row[0], json.loads(row[1])

    def iter_sections(self):
        for doc_id, idx, text, mj in self._conn.execute("SELECT doc_id, idx, text, metadata_json FROM sections"):
            yield doc_id, idx, text, json.loads(mj)

    def delete_doc(self, doc_id: str) -> None:
        self._conn.execute("DELETE FROM chunks WHERE doc_id=?", (doc_id,))
        self._conn.execute("DELETE FROM sections WHERE doc_id=?", (doc_id,))
        self._conn.execute("DELETE FROM bm25_docs WHERE doc_id=?", (doc_id,))
        self._conn.commit()

    def has_chunks(self) -> bool:
        return self._conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0] > 0

    # —— BM25 ——
    def save_bm25(self, state: dict) -> None:
        self._conn.execute("DELETE FROM bm25_docs")
        docs = state["docs"]
        tokens = state["tokens"]
        doc_len = state["doc_len"]
        for sid, doc_id, text, meta in docs:
            self._conn.execute(
                "INSERT OR REPLACE INTO bm25_docs VALUES(?,?,?,?,?,?)",
                (sid, doc_id, text, json.dumps(meta, ensure_ascii=False), json.dumps(tokens.get(sid, {})), doc_len.get(sid, 0)),
            )
        self.set_meta("bm25_k1", str(state["k1"]))
        self.set_meta("bm25_b", str(state["b"]))
        self.set_meta("bm25_df", json.dumps(state["df"]))
        self.set_meta("bm25_total_len", str(state["total_len"]))
        self._conn.commit()

    def load_bm25(self) -> BM25:
        rows = self._conn.execute("SELECT section_id, doc_id, text, metadata_json, tf_json, doc_len FROM bm25_docs").fetchall()
        k1 = float(self.get_meta("bm25_k1") or "1.5")
        b = float(self.get_meta("bm25_b") or "0.75")
        df = json.loads(self.get_meta("bm25_df") or "{}")
        total = int(self.get_meta("bm25_total_len") or 0)
        state = {"k1": k1, "b": b, "docs": [], "tokens": {}, "df": df, "doc_len": {}, "total_len": total}
        for sid, doc_id, text, mj, tfj, dl in rows:
            state["docs"].append((sid, doc_id, text, json.loads(mj)))
            state["tokens"][sid] = json.loads(tfj)
            state["doc_len"][sid] = dl
        return BM25.from_state(state)

    def bm25_count(self) -> int:
        return self._conn.execute("SELECT COUNT(*) FROM bm25_docs").fetchone()[0]

    def search_bm25(self, query: str, *, top_k: int = 10, filters: dict | None = None) -> list[ScoredDoc]:
        """SQLite 实时 BM25：每查询读 bm25_docs(tf) + meta(df/total_len/k1/b) 打分，内存不重建 corpus。"""
        q_tokens = default_tokenizer()(query)
        if not q_tokens:
            return []
        k1 = float(self.get_meta("bm25_k1") or "1.5")
        b = float(self.get_meta("bm25_b") or "0.75")
        df = json.loads(self.get_meta("bm25_df") or "{}")
        total = int(self.get_meta("bm25_total_len") or 0)
        rows = self._conn.execute(
            "SELECT section_id, doc_id, text, metadata_json, tf_json, doc_len FROM bm25_docs"
        ).fetchall()
        n = len(rows)
        if n == 0:
            return []
        avgdl = total / max(n, 1)
        scored: list[ScoredDoc] = []
        for sid, doc_id, text, mj, tfj, dl in rows:
            meta = json.loads(mj)
            if not _match_meta(meta, filters):
                continue
            tf = json.loads(tfj)
            score = 0.0
            for t in set(q_tokens):
                n_t = df.get(t, 0)
                if not n_t:
                    continue
                idf = math.log((n - n_t + 0.5) / (n_t + 0.5) + 1.0)
                f = tf.get(t, 0)
                score += idf * (f * (k1 + 1)) / (f + k1 * (1 - b + b * dl / max(avgdl, 1e-3)))
            scored.append(ScoredDoc(section_id=sid, doc_id=doc_id, text=text, score=score, metadata=meta))
        scored.sort(key=lambda d: d.score, reverse=True)
        return scored[:top_k]

    # —— meta ——
    def set_meta(self, key: str, value: str) -> None:
        self._conn.execute("INSERT OR REPLACE INTO meta VALUES(?,?)", (key, value))
        self._conn.commit()

    def get_meta(self, key: str) -> str | None:
        row = self._conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row[0] if row else None

    def close(self) -> None:
        self._conn.close()


def _match_meta(meta: dict, filters: dict | None) -> bool:
    if not filters:
        return True
    return (filters.get("company") is None or meta.get("company") == filters["company"]) and (
        filters.get("year") is None or meta.get("year") == filters["year"]
    )


class RelBM25:
    """SQLite 实时 BM25 词法检索：不重建内存 corpus，每查询扫 bm25_docs 打分（接口对齐 BM25）。"""

    def __init__(self, rel: RelStore) -> None:
        self._rel = rel

    def search(self, query: str, *, top_k: int = 10, filters: dict | None = None) -> list[ScoredDoc]:
        return self._rel.search_bm25(query, top_k=top_k, filters=filters)

    def count(self) -> int:
        return self._rel.bm25_count()


def _store(base: Path) -> Path:
    return base / _REL_DB


def _milvus(base: Path) -> Path:
    return base / _MILVUS_DB


def has_index(base: str | Path) -> bool:
    """复用判断：目录下有 milvus.db + rag_rel.db 且 chunks 有行。"""
    b = Path(base)
    if not (_milvus(b).exists() and _store(b).exists()):
        return False
    try:
        return RelStore(b).has_chunks()
    except Exception:  # noqa: BLE001 - 损坏/缺表视为未迁移
        return False


def save_index(index, base: str | Path) -> RelStore:
    """保存 BM25 + dim/model 元数据；chunk/节文本已由 MilvusVectorStore.add 写入 RelStore。"""
    rel = getattr(index, "rel", None) or RelStore(base)
    rel.save_bm25(index.bm25_section.iter_state())
    rel.set_meta("dim", str(int(index.embedder.dim)))
    rel.set_meta("model", index.config.rag_embedding_model)
    return rel


def load_index(config: Settings, base: str | Path):
    """从 Milvus + RelStore 重建 RagIndex（秒级，不打嵌入 API）。"""
    from demomcp.rag.captioner import build_captioner
    from demomcp.rag.embedder import build_embedder
    from demomcp.rag.ingest import RagIndex
    from demomcp.rag.store import MilvusVectorStore

    rel = RelStore(base)
    dim = int(rel.get_meta("dim") or config.rag_embedding_dim or 256)
    embedder = build_embedder(config)
    chunk_store = MilvusVectorStore(dim=dim, uri=str(base), collection="rag_chunks", rel=rel, kind="chunk")
    section_store = MilvusVectorStore(dim=dim, uri=str(base), collection="rag_sections", rel=rel, kind="section")
    bm25 = RelBM25(rel)  # SQLite 实时打分，不重建内存 corpus
    return RagIndex(
        embedder=embedder, chunk_store=chunk_store, section_store=section_store,
        bm25_section=bm25, captioner=build_captioner(config), config=config, rel=rel,
    )
