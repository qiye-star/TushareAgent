"""测试/离线 eval 替身：FakeRetriever、StubJudgeLLM、合成年报构造器（不触碰 pymupdf）。"""

from __future__ import annotations

from demomcp.rag.pdf_parser import layout_from_synthetic
from demomcp.rag.schemas import RagChunk, RetrievalPlan


class FakeRetriever:
    """确定性检索替身：注入结果列表 + .calls 记录器（对齐 FakeToolProvider 模式）。

    可按 plan.filters 对结果做 company/year 预筛（跨公司隔离验证）。
    """

    def __init__(self, results: list[dict]) -> None:
        self.results = [RagChunk(**r) for r in results]
        self.calls: list[RetrievalPlan] = []

    async def retrieve(self, plan: RetrievalPlan) -> list[RagChunk]:
        self.calls.append(plan)
        return [r for r in self.results if _match_filters(r, plan.filters)]


class StubJudgeLLM:
    """离线确定性 judge：根据 question 返回固定分数（faithfulness/answer_relevance）。"""

    def __init__(self, *, verdicts: dict[str, float] | None = None, default: float = 0.9) -> None:
        self.verdicts = verdicts or {}
        self.default = default
        self.calls: list[dict] = []

    async def judge(self, *, answer: str, context: list[str], question: str) -> float:
        self.calls.append({"answer": answer, "context": context, "question": question})
        return self.verdicts.get(question, self.default)


def synthetic_layout_for_annual_report(
    *,
    company: str,
    year: int,
    sections: list[dict],
    tables: list[dict],
) -> object:  # LayoutResult（为免循环导入用 str 注解）
    """把 [{heading,text,page}...] 与 [{caption,headers,rows,page}...] 转成 LayoutResult。

    标题 block 用 14pt（区别于正文 10.5pt），便于 detect_headings 识别。
    """
    blocks: list[dict] = []
    block_no = 0
    page = 1
    for sec in sections:
        page = int(sec.get("page", page))
        heading = sec.get("heading")
        if heading:
            # 标题可带 size 以构造不同层级（节>款）；默认 14pt
            blocks.append(_blk(page, heading, float(sec.get("size", 14.0)), block_no))
            block_no += 1
        for para in str(sec.get("text", "")).split("\n"):
            if para.strip():
                blocks.append(_blk(page, para.strip(), 10.5, block_no))
                block_no += 1

    tabs = [
        {
            "page": int(t.get("page", page)), "caption": t.get("caption"),
            "headers": list(t.get("headers", [])), "rows": [list(r) for r in t.get("rows", [])],
            "top": 0.0, "left": 0.0,
        }
        for t in tables
    ]
    del company, year  # DocMeta 组装在 ingest 侧；此处只产出布局
    return layout_from_synthetic(blocks, tabs, total_pages=max(page, 1))


def _blk(page: int, text: str, size: float, block_no: int) -> dict:
    y = block_no * 10.0
    return {"page": page, "text": text, "size": size, "kind": "text", "block_no": block_no, "bbox": (0.0, y, 100.0, y + 5.0)}


def _match_filters(chunk: RagChunk, filters) -> bool:
    return (filters.company is None or chunk.metadata.get("company") == filters.company) and (
        filters.year is None or chunk.metadata.get("year") == filters.year
    )
