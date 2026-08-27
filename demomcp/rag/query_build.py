"""检索计划 → 查询：dense 用 rewritten_query；BM25 用 rewritten_query + 术语键词扩展；抽取 concepts。"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from demomcp.rag.schemas import RagFilters, RetrievalPlan

# 已知语料公司（名称/代码/别名 → 标准公司名），用于从查询推断 filters（跨公司隔离）。
_COMPANY_ALIASES: list[tuple[tuple[str, ...], str]] = [
    (("比亚迪", "002594", "byd", "002594.sz"), "比亚迪"),
    (("宁德时代", "宁德", "300750", "catl", "300750.sz"), "宁德时代"),
]


def infer_filters(query: str) -> RagFilters:
    """从查询匹配公司/年份 → RagFilters（命中才填；供 RAG_STRICT_SCOPE 做跨公司/财年隔离）。"""
    q = (query or "").lower()
    company = None
    for keys, company_name in _COMPANY_ALIASES:
        if any(k in q for k in keys):
            company = company_name
            break
    year = None
    m = re.search(r"(?<!\d)(?:19|20)\d{2}(?!\d)", query or "")
    if m:
        year = int(m.group(0))
    return RagFilters(company=company, year=year)

FIN_TERMS: list[str] = [
    "净利率", "毛利率", "研发费用率", "研发投入", "扣非", "归母净利润", "营业收入",
    "主营业务", "分行业", "分产品", "分地区", "同比", "环比", "经营情况", "风险因素",
    "经营现金流", "资产负债率", "存货", "应收账款", "营业成本",
    # 经营/战略类（口语常问、年报措辞）——让「海外/乘用车/出口/全球化」等进入 BM25 关键词扩展
    "海外", "乘用车", "出口", "全球化", "出海", "产销量", "国际市场", "新能源汽车",
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
