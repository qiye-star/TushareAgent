"""检索计划 → 查询：dense 用 rewritten_query；BM25 用 rewritten_query + 术语键词扩展；抽取 concepts。"""

from __future__ import annotations

from dataclasses import dataclass, field

from demomcp.rag.schemas import RetrievalPlan

FIN_TERMS: list[str] = [
    "净利率", "毛利率", "研发费用率", "研发投入", "扣非", "归母净利润", "营业收入",
    "主营业务", "分行业", "分产品", "分地区", "同比", "环比", "经营情况", "风险因素",
    "经营现金流", "资产负债率", "存货", "应收账款", "营业成本",
]


@dataclass(frozen=True)
class QuerySet:
    dense_query: str
    bm25_query: str
    concepts: list[str] = field(default_factory=list)


def extract_concepts(query: str) -> list[str]:
    """命中的财报术语（子串匹配，无 jieba 也确定）。"""
    seen: list[str] = []
    for term in FIN_TERMS:
        if term in query and term not in seen:
            seen.append(term)
    return seen


def expand_keywords(query: str, *, top_n: int = 8) -> list[str]:
    """术语键词扩展：抽 FIN_TERMS 命中，喂给 BM25。"""
    return extract_concepts(query)[:top_n]


def build_queries(plan: RetrievalPlan) -> QuerySet:
    """dense=rewritten_query；bm25=rewritten_query + 扩展键词；concepts 抽取。hyDE 默认关闭。"""
    concepts = plan.concepts or extract_concepts(plan.rewritten_query)
    kw = expand_keywords(plan.rewritten_query)
    bm25_q = " ".join([plan.rewritten_query, *kw]).strip()
    return QuerySet(dense_query=plan.rewritten_query, bm25_query=bm25_q, concepts=concepts)
