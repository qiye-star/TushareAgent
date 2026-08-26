"""把 PyMuPDF 原生 block 归到章节叶节点，产出 Block（段落/标题/图/注释），并注入溯源字段。

图块（kind==image）无内嵌文本时经 captioner 描述成 figure 块；无 captioner → 跳过不编造。
"""

from __future__ import annotations

from statistics import median
from typing import Literal

from demomcp.rag.captioner import Captioner
from demomcp.rag.schemas import Block, LayoutBlock, SectionNode, TableRegion
from demomcp.rag.table_split import split_table_rows

_TextType = Literal["paragraph", "header", "note"]


def classify_block(text: str, *, size: float, body_size: float) -> _TextType:
    """文本块类型：字号显著大于正文 → header；注：/来源： → note；否则 paragraph。"""
    stripped = text.strip()
    if stripped.startswith(("注：", "注:", "来源：", "来源:", "表格来源：")):
        return "note"
    if size >= body_size + 1.5:
        return "header"
    return "paragraph"


def _collect(nodes: list[SectionNode], node: SectionNode) -> None:
    nodes.append(node)
    for child in node.children:
        _collect(nodes, child)


def _leaf_for_page(nodes: list[SectionNode], page: int) -> SectionNode:
    covering = [n for n in nodes if n.page_start <= page <= n.page_end]
    if covering:
        # 最具体优先：层级最高 → 页码跨度最小 → 起始页最前（区分同页同级兄弟节）
        return min(covering, key=lambda n: (-n.level, n.page_end - n.page_start, n.page_start))
    assert nodes
    return nodes[0]


def segment_blocks(
    tree: SectionNode,
    blocks: list[LayoutBlock],
    tables: list[TableRegion],
    *,
    captioner: Captioner | None = None,
    rows_per_chunk: int = 8,
    chunk_size: int = 400,
) -> list[Block]:
    """沿树把 text/image 原生块与表格归为 Block，附加 section_path/heading/page_*。"""
    nodes: list[SectionNode] = []
    _collect(nodes, tree)
    text_sizes = [b.size for b in blocks if b.kind == "text" and b.size > 0]
    body_size = median(text_sizes) if text_sizes else 0.0

    out: list[Block] = []
    ordered = sorted(blocks, key=lambda b: (b.page, b.bbox[1]))
    for blk in ordered:
        leaf = _leaf_for_page(nodes, blk.page)
        pos = {"top": blk.bbox[1], "left": blk.bbox[0]}
        if blk.kind == "text":
            bt: _TextType = classify_block(blk.text, size=blk.size, body_size=body_size)
            out.append(_block(bt, blk.text, leaf, pos, None, None, len(out)))
        elif blk.kind == "image":
            text = blk.text.strip()
            if not text and captioner is not None and blk.image:
                text = captioner.caption(blk.image, context="→".join(leaf.path), page=blk.page) or ""
            if text:  # 无描述 → 跳过不进索引（不编造）
                out.append(_block("figure", text, leaf, pos, None, None, len(out)))
        # kind == "table" 原生块不在此处理 —— 表格统一由 tables 参数生成

    for t in tables:
        leaf = _leaf_for_page(nodes, t.page)
        out.extend(
            split_table_rows(
                t.rows, caption=t.caption, headers=t.headers, section_path=leaf.path,
                page_start=leaf.page_start, page_end=leaf.page_end,
                rows_per_chunk=rows_per_chunk, chunk_size=chunk_size,
            )
        )
    return out


def _block(
    bt: _TextType,
    text: str,
    leaf: SectionNode,
    pos: dict[str, float] | None,
    table_headers: list[str] | None,
    table_caption: str | None,
    _seq: int,
) -> Block:
    del _seq  # 占位
    return Block(
        block_type=bt,
        heading=leaf.heading,
        section_path=leaf.path,
        page_start=leaf.page_start,
        page_end=leaf.page_end,
        text=text,
        table_headers=table_headers,
        table_caption=table_caption,
        position=pos,
    )
