"""可信溯源：RagChunk → CiteRef → Citation，确定性生成（不靠 LLM 编页码），含内联标记与参考文献列表。"""

from __future__ import annotations

from demomcp.rag.schemas import Citation, CiteRef, RagChunk


def chunk_to_cite_ref(chunk: RagChunk, *, ref_index: int = 0) -> CiteRef:
    """从 chunk.metadata 确定性生成引用（还原页码/章节路径，绝不编造）。"""
    meta = chunk.metadata
    company = meta.get("company", "")
    year = meta.get("year", 0)
    heading = meta.get("heading", "")
    block_type = meta.get("block_type", "")
    caption = meta.get("table_caption")
    path = list(meta.get("section_path", []))
    page = _page_str(meta)
    title = chunk.doc_title or meta.get("doc_title") or _default_title(company, year)
    inline = build_inline(company, year, page, heading, block_type, caption)
    return CiteRef(
        title=title, page=page, section=" → ".join(path), heading=heading,
        company=company, year=year, block_type=block_type, inline=inline, ref_index=ref_index,
    )


def build_inline(company, year, page, heading, block_type, caption) -> str:
    """内联标记：普通块 [公司year年报 - 第X页 heading]；表格块追加题注。"""
    label = f"{company}{year}年报"
    mark = f"第{page}页 {heading}"
    if block_type == "table" and caption:
        mark += f" · {caption}"
    return f"[{label} - {mark}]"


def to_citation(claim_id: str, chunk: RagChunk, *, ref_index: int) -> Citation:
    """RagChunk → Citation（填充溯源字段 + 内联标记）。"""
    meta = chunk.metadata
    ref = chunk_to_cite_ref(chunk, ref_index=ref_index)
    return Citation(
        claim_id=claim_id,
        source_type="rag",
        source_id=f"{chunk.doc_id}#{chunk.chunk_index}",
        title=ref.title,
        excerpt=chunk.text[:80],
        page=ref.page,
        link=meta.get("source_url"),
        section_path=" → ".join(meta.get("section_path", [])),
        page_end=_page_end_str(meta),
        block_type=meta.get("block_type"),
        company=meta.get("company"),
        year=meta.get("year"),
        ref_index=ref_index,
        inline_marker=ref.inline,
    )


def format_reflist(refs: list[CiteRef]) -> str:
    """参考文献列表：[i] title，第 page 页，section_path。"""
    return "\n".join(
        f"[{i}] {r.title}，第 {r.page} 页，{r.section}" for i, r in enumerate(refs, start=1)
    )


def _page_str(meta: dict) -> str:
    start = meta.get("page_start")
    end = meta.get("page_end") or start
    if start is None:
        return ""
    return str(start) if end == start else f"{start}-{end}"


def _page_end_str(meta: dict) -> str | None:
    end = meta.get("page_end")
    return str(end) if end is not None else None


def _default_title(company: str, year) -> str:
    return f"{company}{year}年报" if company else ""
