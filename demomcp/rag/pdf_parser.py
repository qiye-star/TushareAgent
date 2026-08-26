"""PDF 解析：PyMuPDF 原生 block（text/image）→ LayoutResult；另提供合成入口供测试/离线 eval。

parse_pdf 在函数体内惰性 import fitz —— import 本模块不加载 pymupdf。layout_from_synthetic
与 parse_pdf 产出同一 LayoutResult，ingest 下游只依赖它。
"""

from __future__ import annotations

import logging
import re

from demomcp.rag.schemas import LayoutBlock, LayoutResult, TableRegion
from demomcp.rag.table_split import detect_header, is_caption_line

_log = logging.getLogger(__name__)

_CODE_RE = re.compile(r"(\d{6}\.S[ZH])")
_YEAR_RE = re.compile(r"(20\d{2})")


def layout_from_synthetic(
    blocks: list[dict],
    tables: list[dict],
    total_pages: int,
) -> LayoutResult:
    """合成入口：把 block/tables dict 转成 LayoutResult（测试/离线 eval 用，不触碰 pymupdf）。"""
    blks = [
        LayoutBlock(
            page=b["page"],
            text=b.get("text", ""),
            bbox=tuple(b.get("bbox", (0, 0, 0, 0))),
            kind=b.get("kind", "text"),
            size=float(b.get("size", 0.0)),
            block_no=int(b.get("block_no", 0)),
            image=b.get("image"),
        )
        for b in blocks
    ]
    tabs = [
        TableRegion(
            page=t["page"], caption=t.get("caption"),
            headers=list(t.get("headers", [])),
            rows=[list(r) for r in t.get("rows", [])],
            top=float(t.get("top", 0.0)), left=float(t.get("left", 0.0)),
        )
        for t in tables
    ]
    return LayoutResult(blocks=blks, tables=tabs, total_pages=total_pages, doc_bits={})


def parse_pdf(pdf_path: str, *, table_engine: str = "pymupdf") -> LayoutResult:
    """真实抽取：逐页 get_text("dict") 原生 block；图片取字节；表格 find_tables。"""
    import fitz  # 惰性import：仅在调用时加载

    doc = fitz.open(pdf_path)
    blocks: list[LayoutBlock] = []
    tables: list[TableRegion] = []
    total_pages = doc.page_count

    for pno in range(total_pages):
        page = doc.load_page(pno)
        pno1 = pno + 1
        text_blocks: list[LayoutBlock] = []
        d = page.get_text("dict")
        for b in d.get("blocks", []):
            bbox = tuple(b.get("bbox", (0, 0, 0, 0)))
            btype = b.get("type", 0)
            if btype == 0:  # 文本块
                text = _block_text(b)
                size = _block_size(b)
                if text.strip():
                    blk = LayoutBlock(
                        page=pno1, text=text, bbox=bbox, kind="text",
                        size=size, block_no=int(b.get("number", 0)),
                    )
                    text_blocks.append(blk)
                    blocks.append(blk)
            elif btype == 1:  # 图片块
                blocks.append(
                    LayoutBlock(
                        page=pno1, text="", bbox=bbox, kind="image", size=0.0,
                        block_no=int(b.get("number", 0)),
                        image=_image_bytes_for_block(page, doc, bbox),
                    )
                )

        if table_engine == "pymupdf":
            try:
                for table in page.find_tables().tables:
                    rows = table.extract()
                    if not rows or not rows[0]:
                        continue
                    headers, data = detect_header(rows)
                    caption = _find_caption(text_blocks, table.bbox[1])
                    tables.append(
                        TableRegion(
                            page=pno1, caption=caption, headers=headers, rows=data,
                            top=table.bbox[1], left=table.bbox[0],
                        )
                    )
            except Exception as exc:  # noqa: BLE001 - 单页表格失败不崩整份
                _log.warning("table extraction failed on page %s: %s", pno1, exc)
                continue

    doc.close()
    return LayoutResult(blocks=blocks, tables=tables, total_pages=total_pages, doc_bits={})


def extract_doc_bits(layout: LayoutResult, *, company_map: dict[str, str] | None = None) -> dict:
    """从首页/封面/目录抽取 company/company_code/year/title（含公司名归一化）。"""
    first_text = " ".join(b.text for b in layout.blocks if b.kind == "text")[:2000]
    code_match = _CODE_RE.search(first_text)
    year_match = _YEAR_RE.search(first_text)
    company = ""
    if company_map:
        for name, norm in company_map.items():
            if name in first_text:
                company = norm
                break
    return {
        "company": company,
        "company_code": code_match.group(1) if code_match else "",
        "year": int(year_match.group(1)) if year_match else None,
        "title": layout.doc_bits.get("title", ""),
    }


def _block_text(block: dict) -> str:
    return "".join(
        span.get("text", "") for line in block.get("lines", []) for span in line.get("spans", [])
    )


def _block_size(block: dict) -> float:
    sizes = [
        span.get("size", 0.0)
        for line in block.get("lines", [])
        for span in line.get("spans", [])
        if span.get("text", "").strip()
    ]
    return max(sizes) if sizes else 0.0


def _find_caption(text_blocks: list[LayoutBlock], table_top: float) -> str | None:
    """取表格上方最近的一条题注行。"""
    above = [b for b in text_blocks if b.bbox[1] <= table_top]
    above.sort(key=lambda b: b.bbox[1], reverse=True)
    for b in above:
        if is_caption_line(b.text):
            return b.text.strip()
    return None


def _image_bytes_for_block(page, doc, bbox) -> bytes | None:
    """按 bbox 匹配页面图片，返回字节（供多模态 captioner）。"""
    for img in page.get_images(full=True):
        xref = img[0]
        try:
            rects = page.get_image_rects(xref)
        except Exception as exc:  # noqa: BLE001 - 单图定位失败跳过
            _log.warning("image rects failed on xref %s: %s", xref, exc)
            continue
        for rect in rects:
            if _overlap(bbox, (rect.x0, rect.y0, rect.x1, rect.y1)):
                try:
                    return doc.extract_image(xref)["image"]
                except Exception:  # noqa: BLE001
                    return None
    return None


def _overlap(a: tuple, b: tuple) -> bool:
    return not (a[2] <= b[0] or b[2] <= a[0] or a[3] <= b[1] or b[3] <= a[1])
