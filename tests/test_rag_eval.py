"""离线评估 runner：合成语料 + StubJudgeLLM 跑通，跨公司隔离、表格三重断言、context_recall 达标。"""

from __future__ import annotations

from demomcp.rag.fakes import StubJudgeLLM
from scripts.eval_rag import build_synthetic_corpus, run_eval

_QA = [
    {
        "question": "比亚迪 2024 年研发投入占营业收入比例是多少？",
        "company": "比亚迪", "year": 2024, "strategy": "auto",
        "gold": {"doc_id": "fy2024_byd", "section_path": ["第三节 管理层讨论与分析", "3.2 研发投入"], "page": 1, "table_caption": "表 12 比亚迪研发投入"},
    },
    {
        "question": "宁德时代研发投入情况如何？",
        "company": "宁德时代", "year": 2024, "strategy": "auto",
        "gold": {"doc_id": "fy2024_catl", "section_path": ["第三节 管理层讨论与分析", "3.2 研发投入"], "page": 1, "table_caption": "表 12 宁德时代研发投入"},
    },
]


async def test_eval_offline_all_gates_pass(make_settings) -> None:
    s = make_settings(rag_embedding_model="hashing", rag_rerank_threshold=0.0)
    metrics = await run_eval(s, corpus=build_synthetic_corpus(), qa_set=_QA, judge=StubJudgeLLM())
    assert metrics["n"] == 2
    # 稳定 gate：gold 节召回、跨公司隔离、faithfulness；表格三重断言统计「至少一次命中」
    assert metrics["context_recall@k"] == 1.0
    assert metrics["cross_company_isolation"] == 1.0
    assert metrics["faithfulness"] >= 0.7
    assert metrics["table_triple_count"] >= 1  # 至少一条表格溯源问题检索到正确表格
