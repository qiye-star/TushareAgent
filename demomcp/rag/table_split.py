"""表格切块：题注/表头识别、按行分块（每块带同表头+题注）、跨页表头指纹去重。

输入 PyMuPDF find_tables 抽取的 TableRegion（rows 为已剥表头的数据行）。
"""

from __future__ import annotations

import hashlib
import re

from demomcp.rag.schemas import Block, TableRegion

_CAPTION_RE = re.compile(r"^(表\s*[一二三四五六七八九十百\d]+|单位：|数据来源：)")


def is_caption_line(text: str) -> bool:
    """题注识别：表[...] / 单位： / 数据来源： 开头。"""
    return bool(_CAPTION_RE.match(text.strip()))


def _cell(value) -> str:
    """单元格兜底：None → ""；合并单元格（嵌套 list）→ 空格连接；其余 → str。"""
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        return " ".join(_cell(v) for v in value)
    return str(value)


def detect_header(rows: list[list[str]], *, max_rows: int = 2) -> tuple[list[str], list[list[str]]]:
    """取首行（必要时前 max_rows 行）为表头；返回 (headers, 剩余数据行)。"""
    if not rows:
        return [], []
    headers = [_cell(v) for v in rows[0]]
    return headers, rows[1:]


def textify_table(headers: list[str], row: list[str]) -> str:
    """把一行连同表头拼成可读文本（表头前置便于检索表头语义）。"""
    return " | ".join(_cell(v) for v in (headers + row))


def split_table_rows(
    rows: list[list[str]],
    *,
    caption: str | None,
    headers: list[str],
    section_path: list[str],
    page_start: int,
    page_end: int,
    rows_per_chunk: int = 8,
    chunk_size: int = 400,
) -> list[Block]:
    """数据行 ≤ rows_per_chunk 且总字符 < chunk_size → 整表一块；否则按行切块，每块带同表头+题注。"""
    full_text = _table_text(caption, headers, rows)
    if len(rows) <= rows_per_chunk and len(full_text) < chunk_size:
        return [_make_table_block(caption, headers, rows, section_path, page_start, page_end)]
    blocks: list[Block] = []
    for i in range(0, len(rows), rows_per_chunk):
        part = rows[i : i + rows_per_chunk]
        blocks.append(_make_table_block(caption, headers, part, section_path, page_start, page_end))
    return blocks


def header_fingerprint(headers: list[str]) -> str:
    """表头去重指纹：归一化（去空格/首末空白）后 join + hash。跨页续表同表头指纹一致。"""
    norm = "|".join(h.strip() for h in headers)
    return hashlib.sha1(norm.encode("utf-8")).hexdigest()


def merge_cross_page(pieces: list[TableRegion], *, rows_per_chunk: int = 8) -> list[TableRegion]:
    """用 header_fingerprint 合并跨页同表头分段：删除后续分段首行若等于表头（重复表头），行合并。"""
    merged: dict[str, TableRegion] = {}
    order: list[str] = []
    for piece in pieces:
        key = header_fingerprint(piece.headers)
        if key not in merged:
            merged[key] = piece
            order.append(key)
            continue
        target = merged[key]
        data_rows = list(target.rows)
        if piece.rows and piece.rows[0] == target.headers:
            piece_rows = piece.rows[1:]
        else:
            piece_rows = list(piece.rows)
        merged[key] = TableRegion(
            page=target.page, caption=target.caption or piece.caption, headers=target.headers,
            rows=data_rows + piece_rows, top=target.top, left=target.left,
        )
    return [merged[key] for key in order]


def _table_text(caption: str | None, headers: list[str], rows: list[list[str]]) -> str:
    lines: list[str] = []
    if caption:
        lines.append(caption)
    lines.append(" | ".join(_cell(v) for v in headers))
    lines.extend(textify_table(headers, row) for row in rows)
    return "\n".join(lines)


def _make_table_block(
    caption: str | None,
    headers: list[str],
    rows: list[list[str]],
    section_path: list[str],
    page_start: int,
    page_end: int,
) -> Block:
    return Block(
        block_type="table",
        heading=section_path[-1] if section_path else "",
        section_path=section_path,
        page_start=page_start,
        page_end=page_end,
        text=_table_text(caption, headers, rows),
        table_headers=headers,
        table_caption=caption,
    )
