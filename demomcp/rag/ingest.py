"""摄取索引：RagIndex 九段编排（build_tree → segment → chunk → embed → 双索引写入），幂等。

向量化包含元数据：embed 输入 = {company}{year} {section_path} {heading} {text}。
"""

from __future__ import annotations

from typing import Any

from demomcp.config.settings import Settings
from demomcp.rag.bm25 import BM25
from demomcp.rag.captioner import Captioner, build_captioner
from demomcp.rag.chunking import chunk_blocks, chunk_section
from demomcp.rag.embedder import Embedder, build_embedder
from demomcp.rag.schemas import Block, DocMeta, IngestStats, LayoutResult, SectionNode
from demomcp.rag.section_tree import build_section_tree, detect_headings
from demomcp.rag.segments import segment_blocks
from demomcp.rag.store import VectorStore, build_vector_store


class RagIndex:
    """写/读共享的检索索引：dense(chunk) + section(代表向量语义) + BM25(section 词法)。"""

    def __init__(
        self,
        *,
        embedder: Embedder,
        chunk_store: VectorStore,
        section_store: VectorStore,
        bm25_section: BM25,
        config: Settings,
        captioner: Captioner | None = None,
        rel: Any | None = None,
    ) -> None:
        self.embedder = embedder
        self.chunk_store = chunk_store
        self.section_store = section_store
        self.bm25_section = bm25_section
        self.captioner = captioner
        self.config = config
        self.rel = rel  # SQLite RelStore（Milvus 持久化时），为空则内存索引

    def delete_doc(self, doc_id: str) -> None:
        self.chunk_store.delete_doc(doc_id)
        self.section_store.delete_doc(doc_id)
        self.bm25_section.delete_doc(doc_id)

    def count(self) -> tuple[int, int]:
        return self.chunk_store.count(), self.bm25_section.count()

    def add_doc(self, doc_meta: DocMeta, layout: LayoutResult) -> IngestStats:
        """九段编排：build_tree → segment_blocks → chunk → embed → 双索引写入 → verify。"""
        cfg = self.config
        candidates = detect_headings(layout.blocks)
        tree = build_section_tree(
            candidates, doc_id=doc_meta.doc_id, total_pages=layout.total_pages
        )
        blocks = segment_blocks(
            tree, layout.blocks, layout.tables,
            captioner=self.captioner,
            rows_per_chunk=cfg.rag_table_rows_per_chunk,
            chunk_size=cfg.rag_fin_dense_chunk,
        )
        _populate_leaf_text(tree, blocks)

        chunk_pairs = chunk_blocks(
            blocks,
            chunk_size=cfg.rag_fin_dense_chunk,
            overlap=cfg.rag_fin_dense_overlap,
            rows_per_chunk=cfg.rag_table_rows_per_chunk,
        )

        chunk_embeds = self.embedder.encode(
            [_embed_prefix(doc_meta, blk) + text for text, blk in chunk_pairs]
        )
        for idx, ((text, blk), emb) in enumerate(zip(chunk_pairs, chunk_embeds)):
            meta = _chunk_metadata(doc_meta, blk)
            self.chunk_store.add(doc_meta.doc_id, idx, text, emb, meta)

        # 节级代表向量：先收集所有节，再批量 encode（避免每节一次 API 调用）
        sections: list[tuple[SectionNode, str, dict, str]] = []
        for node in _leaf_nodes(tree):
            section_text = chunk_section(node, max_chars=cfg.rag_section_max_chars)
            if not section_text.strip():
                continue
            sec_meta = {
                "doc_title": doc_meta.title, "company": doc_meta.company,
                "company_code": doc_meta.company_code, "year": doc_meta.year,
                "doc_id": doc_meta.doc_id, "section_path": list(node.path),
                "heading": node.heading, "page_start": node.page_start,
                "page_end": node.page_end, "block_type": "section", "section_id": node.id,
            }
            rep_text = (_embed_prefix(doc_meta, node) + section_text)[:300]
            sections.append((node, section_text, sec_meta, rep_text))
        rep_vecs = self.embedder.encode([rep for _, _, _, rep in sections]) if sections else []
        for idx, ((node, section_text, sec_meta, _), rep_vec) in enumerate(zip(sections, rep_vecs)):
            self.bm25_section.add(node.id, doc_meta.doc_id, section_text, sec_meta)
            # section_store 按 doc_id#idx 每节唯一键，避免与 chunk 不同来源互相覆盖
            self.section_store.add(doc_meta.doc_id, idx, section_text, rep_vec, sec_meta)

        n_chunks = len(chunk_pairs)
        n_table = sum(1 for _, blk in chunk_pairs if blk.block_type == "table")
        return IngestStats(
            doc_id=doc_meta.doc_id, n_sections=len(_leaf_nodes(tree)),
            n_blocks=len(blocks), n_chunks=n_chunks, n_table_chunks=n_table,
            pages=layout.total_pages, skipped=(n_chunks == 0),
        )


def build_index(config: Settings) -> RagIndex:
    """按配置组装 embedder / 两个向量库 / BM25 / captioner。离线默认纯 Python；real 时用 Milvus+RelStore。"""
    from demomcp.rag.persist import RelStore

    embedder = build_embedder(config)
    rel = None
    if config.rag_use_real and config.rag_vector_store_path:
        try:
            import pymilvus  # noqa: F401  # 仅探测

            rel = RelStore(config.rag_vector_store_path)
        except ImportError:
            rel = None
    chunk_store = build_vector_store(config, embedder.dim, rel=rel, name="rag_chunks")
    section_store = build_vector_store(config, embedder.dim, rel=rel, name="rag_sections")
    bm25 = BM25(k1=config.rag_bm25_k1, b=config.rag_bm25_b)
    captioner = build_captioner(config)
    return RagIndex(
        embedder=embedder, chunk_store=chunk_store, section_store=section_store,
        bm25_section=bm25, captioner=captioner, config=config, rel=rel,
    )


async def ingest(config: Settings, *, index: RagIndex, doc_meta: DocMeta, layout: LayoutResult) -> IngestStats:
    """幂等：先 index.delete_doc(doc_id) 再 add_doc（重跑等同全量重建该 doc）。"""
    index.delete_doc(doc_meta.doc_id)
    return index.add_doc(doc_meta, layout)


def _leaf_nodes(tree: SectionNode) -> list[SectionNode]:
    out: list[SectionNode] = []

    def walk(node: SectionNode) -> None:
        if not node.children:
            out.append(node)
        for child in node.children:
            walk(child)

    walk(tree)
    return out


def _populate_leaf_text(tree: SectionNode, blocks: list[Block]) -> None:
    leaf_map = {tuple(node.path): node for node in _leaf_nodes(tree)}
    for blk in blocks:
        node = leaf_map.get(tuple(blk.section_path))
        if node is not None:
            node.blocks.append(blk)
    for node in leaf_map.values():
        node.text = "".join(b.text for b in node.blocks)


def _embed_prefix(doc_meta: DocMeta, blk: Block | SectionNode) -> str:
    heading = getattr(blk, "heading", "")
    path = getattr(blk, "section_path", None) or getattr(blk, "path", [])
    return f"{doc_meta.company}{doc_meta.year} {'>'.join(path)} {heading} "


def _chunk_metadata(doc_meta: DocMeta, blk: Block) -> dict:
    return {
        "doc_title": doc_meta.title, "company": doc_meta.company,
        "company_code": doc_meta.company_code, "year": doc_meta.year, "doc_id": doc_meta.doc_id,
        "section_path": list(blk.section_path),
        "section_level": len(blk.section_path),
        "heading": blk.heading,
        "page_start": blk.page_start, "page_end": blk.page_end,
        "block_type": blk.block_type,
        "table_headers": blk.table_headers, "table_caption": blk.table_caption,
        "position": blk.position, "ingested_at": doc_meta.ingested_at or "",
    }
