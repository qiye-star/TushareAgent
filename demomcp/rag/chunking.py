"""块感知切片：以 PyMuPDF 原生 block 为段落单元，块边界优先切分，且恒有重叠（overlap）。

返回 (text, source_block) 对；表块每块一个 chunk（split_table_rows 已按行切）。doc 级元数据由
ingest 在组装 RagChunk 时凭 block + doc_meta 注入。
"""

from __future__ import annotations

from demomcp.rag.schemas import Block, SectionNode

SENT_BOUNDARY = "。！？；"


def _split_sentences(text: str) -> list[str]:
    parts: list[str] = []
    cur = ""
    for ch in text:
        cur += ch
        if ch in SENT_BOUNDARY:
            parts.append(cur)
            cur = ""
    if cur.strip():
        parts.append(cur)
    return [p for p in parts if p.strip()]


def chunk_dense_block(text: str, *, chunk_size: int = 400, overlap: int = 80) -> list[str]:
    """句界感知滑动窗口：按句子累积，接近 chunk_size 切句；相邻块带回 overlap 尾部。单句超长硬切。"""
    if not text:
        return []
    if len(text) <= chunk_size:
        return [text]
    sentences = _split_sentences(text) or [text]
    chunks: list[str] = []
    cur = ""
    for idx, sent in enumerate(sentences):
        if len(sent) > chunk_size:
            if cur:
                chunks.append(cur)
                cur = ""
            for i in range(0, len(sent), chunk_size):
                chunks.append(sent[i : i + chunk_size])
            # 仅当后面还有句子时才带 overlap 尾部，避免末尾重复块
            cur = sent[-overlap:] if (overlap > 0 and idx < len(sentences) - 1) else ""
            continue
        if cur and len(cur) + len(sent) > chunk_size:
            chunks.append(cur)
            cur = cur[-overlap:] if overlap > 0 else ""
        cur += sent
    if cur.strip():
        chunks.append(cur)
    return [c for c in chunks if c.strip()]


def chunk_section(section: SectionNode, *, max_chars: int = 8000) -> str:
    """整节正文（BM25 用）：各 block 拼接、截断到 max_chars。"""
    text = "".join(b.text for b in section.blocks)
    return text[:max_chars]


def chunk_blocks(
    blocks: list[Block],
    *,
    chunk_size: int = 400,
    overlap: int = 80,
    rows_per_chunk: int = 8,
) -> list[tuple[str, Block]]:
    """把段落/标题/注释块按块边界聚成 chunk（恒有 overlap）；表格块每块一个 chunk。"""
    text_blocks = [b for b in blocks if b.block_type in ("paragraph", "header", "note")]
    table_blocks = [b for b in blocks if b.block_type == "table"]

    stream: list[tuple[str, Block]] = []
    for b in text_blocks:
        chunk_size_eff = max(chunk_size, 1)
        for t in chunk_dense_block(b.text, chunk_size=chunk_size_eff, overlap=overlap):
            stream.append((t, b))

    out: list[tuple[str, Block]] = []
    buf: list[str] = []
    buf_blocks: list[Block] = []
    buf_len = 0
    for text, blk in stream:
        if buf and buf_len + len(text) > chunk_size:
            out.append(("".join(buf), _rep_block(buf_blocks, blk)))
            tail = "".join(buf)[-overlap:] if overlap > 0 else ""
            buf = [tail] if tail else []
            buf_blocks = []  # 重叠尾部只是文本，不属于新 chunk 的块
            buf_len = len(tail)
        if not buf_blocks:
            buf_blocks.append(blk)
        else:
            buf_blocks.append(blk)
        buf.append(text)
        buf_len += len(text)
    if buf:
        out.append(("".join(buf), _rep_block(buf_blocks, None)))

    for b in table_blocks:
        # 表格块 text 并入题注（heading/table_caption）：dense 检索已带 heading 前缀，
        # 但重排（reranker）与用户可见 text 只看 b.text（行数据）——并入题注才能命中「表题」类查询并保住财务数值。
        prefix = " ".join(x for x in (b.heading, b.table_caption) if x)
        out.append(((prefix + "\n" if prefix else "") + b.text, b))
    return out


def _rep_block(blocks: list[Block], fallback: Block | None) -> Block:
    """选一个代表块用于 chunk 元数据：优先内容块（非 header），否则用第一个。"""
    for b in blocks:
        if b.block_type != "header":
            return b
    return blocks[0] if blocks else fallback  # type: ignore[return-value]
