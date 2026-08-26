"""RAG 财报知识库的数据契约：对外 pydantic 结构 + 解析/检索内部 dataclass。

设计蓝图为 docs/RAG_FINANCE.md；本模块是 demomcp/rag/ 各模块共享的源头结构。所有可变默认
一律用 Field(default_factory=...)，pydantic v2 不允许 list[...] = [] 裸默认。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# 对外可序列化结构（pydantic）
# ---------------------------------------------------------------------------


class DocMeta(BaseModel):
    """一份年报（公司 × 财年）的元信息。"""

    doc_id: str
    title: str
    company: str
    company_code: str
    year: int
    report_type: str = "annual_report"
    source_pdf: str
    source_url: str | None = None
    total_pages: int | None = None
    parse_tool: str = "pymupdf"  # pymupdf | camelot
    ingested_at: str | None = None


class SectionNode(BaseModel):
    """财报章节语义树节点：携祖先标题链、页码跨度、子节与所属块。"""

    id: str
    heading: str
    level: int = 0
    path: list[str] = Field(default_factory=list)
    page_start: int = 1
    page_end: int = 1
    children: list[SectionNode] = Field(default_factory=list)
    blocks: list[Block] = Field(default_factory=list)
    text: str = ""  # 整节正文（BM25 用）


class Block(BaseModel):
    """切块前的一个语义单元：自然段落 / 表格 / 标题 / 图 / 注释。"""

    block_type: Literal["paragraph", "table", "header", "figure", "note"]
    heading: str = ""
    section_path: list[str] = Field(default_factory=list)
    page_start: int = 1
    page_end: int = 1
    text: str = ""
    table_headers: list[str] | None = None
    table_caption: str | None = None
    position: dict[str, float] | None = None


class RagChunk(BaseModel):
    """检索返回的一个文档块：text + 冗余溯源元数据（命中即自证出处）。"""

    doc_id: str
    doc_title: str
    chunk_index: int = 0
    text: str
    score: float = 0.0
    metadata: dict[str, Any] = Field(default_factory=dict)


class CiteRef(BaseModel):
    """块 → 引用映射：确定性从 RagChunk.metadata 生成，不靠 LLM 编页码。"""

    title: str
    page: str  # "42" 或 "42-43"
    section: str  # " ".join(section_path)
    heading: str
    company: str
    year: int
    block_type: str
    inline: str  # "[比亚迪2024年报 - 第42页 3.2 研发投入]"
    ref_index: int = 0


class RagFilters(BaseModel):
    """元数据过滤：company+year 精确隔离（RAG_STRICT_SCOPE 时硬过滤）。"""

    company: str | None = None
    year: int | None = None


class RetrievalPlan(BaseModel):
    """查询改写产物：检索计划（供 rag_retrieve 节点消费）。"""

    rewritten_query: str
    concepts: list[str] = Field(default_factory=list)
    filters: RagFilters = Field(default_factory=RagFilters)
    strategy: Literal["structural", "factual", "auto"] = "auto"


class Citation(BaseModel):
    """引用溯源条目：对齐 ARCHITECTURE.md §11.1，并扩展 RAG 溯源字段。"""

    claim_id: str
    source_type: Literal["tool", "rag"]
    source_id: str
    title: str
    excerpt: str
    page: str | None = None
    link: str | None = None
    # —— 仅 RAG 新增（可选，工具引用不填）——
    section_path: str | None = None
    page_end: str | None = None
    block_type: str | None = None
    company: str | None = None
    year: int | None = None
    ref_index: int | None = None
    inline_marker: str | None = None


# ---------------------------------------------------------------------------
# 解析 / 检索内部轻量结构（@dataclass，贴合 interfaces/types.py 的中立风格）
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LayoutBlock:
    """PDF 页面的一个原生 block（PyMuPDF get_text("dict") 的 block）；一个 text block ≈ 自然段落。"""

    page: int
    text: str
    bbox: tuple[float, float, float, float]  # (x0, y0, x1, y1)
    kind: Literal["text", "image", "table"]
    size: float  # 块内 span 字号（标题检测用；image/table 为 0）
    block_no: int
    image: bytes | None = None  # 图片块字节，供多模态 captioner 用


@dataclass(frozen=True)
class TableRegion:
    """PDF 中一张表格：题注、表头、已剥表头的数据行。"""

    page: int
    caption: str | None
    headers: list[str]
    rows: list[list[str]]  # 数据行（不含表头）
    top: float
    left: float


@dataclass(frozen=True)
class LayoutResult:
    """一份文档的解析产物：原生块 + 表格 + 元信息位。"""

    blocks: list[LayoutBlock]
    tables: list[TableRegion]
    total_pages: int
    doc_bits: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ScoredChunk:
    """向量库检索返回的命中项（chunk 级）。"""

    doc_id: str
    chunk_index: int
    text: str
    score: float
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ScoredDoc:
    """BM25 / section 级检索返回的命中单元。"""

    section_id: str
    doc_id: str
    text: str
    score: float
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class IngestStats:
    """摄取统计（幂等重跑用）。"""

    doc_id: str
    n_sections: int = 0
    n_blocks: int = 0
    n_chunks: int = 0
    n_table_chunks: int = 0
    pages: int = 0
    skipped: bool = False
