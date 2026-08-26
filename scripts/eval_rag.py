"""离线 RAG 评估 runner：小规模标注集 → RAGAS 式指标。

离线默认：纯 Python(HashingEmbedder) + InMemoryVectorStore + 自包含 BM25 + NoopReranker +
StubJudgeLLM，合成语料（比亚迪/宁德时代 2024 年报），无需真实 DeepSeek/网络，也无需 rag-full。
真实接入：`uv sync --extra rag-full` 后不设 RAG_EMBEDDING_MODEL=hashing，并 --corpus 指向 PDF 目录。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
from pathlib import Path

import yaml

from demomcp.config.settings import Settings
from demomcp.rag.fakes import StubJudgeLLM, synthetic_layout_for_annual_report
from demomcp.rag.hybrid_retriever import build_retriever
from demomcp.rag.ingest import ingest
from demomcp.rag.schemas import DocMeta, RagFilters, RetrievalPlan


def load_qa(path: str) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)["questions"]


def build_synthetic_corpus() -> list[tuple[DocMeta, object]]:
    def doc(doc_id: str, company: str, code: str, title: str, caption: str):
        meta = DocMeta(doc_id=doc_id, title=title, company=company, company_code=code, year=2024, source_pdf="synthetic", total_pages=1)
        layout = synthetic_layout_for_annual_report(
            company=company, year=2024,
            sections=[
                {"heading": "第三节 管理层讨论与分析", "text": "公司应对外部环境变化，加强成本管理。", "page": 1, "size": 18.0},
                {"heading": "3.2 研发投入", "text": "报告期内研发投入占营业收入比例约百分之五。\n其余业务稳步发展。", "page": 1, "size": 14.0},
            ],
            tables=[{"caption": caption, "headers": ["项目", "金额"], "rows": [["研发投入", "100"], ["营业收入", "2000"]], "page": 1}],
        )
        return meta, layout

    return [
        doc("fy2024_byd", "比亚迪", "002594.SZ", "比亚迪 2024 年年度报告", "表 12 比亚迪研发投入"),
        doc("fy2024_catl", "宁德时代", "300750.SZ", "宁德时代 2024 年年度报告", "表 12 宁德时代研发投入"),
    ]


async def run_eval(settings: Settings, *, corpus, qa_set: list[dict], judge) -> dict:
    retriever = build_retriever(settings)  # 自带 embedder + reranker
    for meta, layout in corpus:
        await ingest(settings, index=retriever._index, doc_meta=meta, layout=layout)

    n = len(qa_set)
    gold_found = 0
    triple = 0
    faith = 0.0
    rel = 0.0
    cross_ok = 0
    cross_total = 0

    for q in qa_set:
        plan = RetrievalPlan(
            rewritten_query=q["question"],
            filters=RagFilters(company=q.get("company"), year=q.get("year")),
            strategy=q.get("strategy", "auto"),
        )
        chunks = await retriever.retrieve(plan)
        gold = q["gold"]
        # context_recall@k：gold 的 doc + section_path 命中最优 top-k
        if any(
            gold["doc_id"] == c.metadata.get("doc_id")
            and list(gold["section_path"]) == c.metadata.get("section_path")
            for c in chunks
        ):
            gold_found += 1

        # 表格溯源三重断言：正确表格 + 正确页码 + 正确节
        tbl = next(
            (
                c for c in chunks
                if c.metadata.get("block_type") == "table"
                and c.metadata.get("table_caption") == gold.get("table_caption", "")
                and int(c.metadata.get("page_start", -1)) == gold.get("page", -1)
                and list(gold["section_path"]) == c.metadata.get("section_path")
            ),
            None,
        )
        if tbl:
            triple += 1

        # 跨公司隔离负例：query 限定公司时 top-k 不得出现其它公司块
        if q.get("company"):
            cross_total += 1
            if not any(c.metadata.get("company") != q["company"] for c in chunks):
                cross_ok += 1

        context = [c.text for c in chunks]
        faith += await judge.judge(answer="基于年报数据", context=context, question=q["question"])
        rel += await judge.judge(answer="基于年报数据", context=context, question=q["question"])

    return {
        "n": n,
        "context_recall@k": gold_found / n if n else 0.0,
        "faithfulness": faith / n if n else 0.0,
        "answer_relevance": rel / n if n else 0.0,
        "table_triple_assertion": triple / n if n else 0.0,
        "table_triple_count": triple,
        "cross_company_isolation": cross_ok / cross_total if cross_total else 1.0,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="RAG 财报知识库离线评估")
    parser.add_argument("--corpus", default="", help="PDF 目录（空=合成语料）")
    parser.add_argument("--gold", default="tests/rag_golden/qa.yaml")
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--no-real", action="store_true", help="强制纯 Python/hashing 后端")
    args = parser.parse_args()

    settings = Settings(_env_file=None) if args.no_real else Settings()
    if args.no_real or not settings.rag_use_real:
        import copy

        settings = copy.copy(settings)
        settings.rag_embedding_model = "hashing"
        settings.rag_rerank_threshold = 0.0

    if args.corpus:
        from demomcp.rag.pdf_parser import extract_doc_bits, parse_pdf

        corpus_ = Path(args.corpus)
        pdfs = [corpus_] if corpus_.is_file() else sorted(corpus_.glob("*.pdf"))
        corpus = []
        for pdf in pdfs:
            layout = parse_pdf(str(pdf))
            bits = extract_doc_bits(layout, company_map={"比亚迪": "比亚迪", "宁德时代": "宁德时代", "股份有限公司": "比亚迪"})
            # 公司名/年份从文件名兜底（如「比亚迪：2025年年度报告」）
            company = bits.get("company") or pdf.stem.split("：")[0]
            year_m = re.search(r"(20\d{2})", pdf.stem)
            year = bits.get("year") or (int(year_m.group(1)) if year_m else 2024)
            meta = DocMeta(doc_id=pdf.stem, title=pdf.stem, company=company, company_code=bits.get("company_code", ""), year=year, source_pdf=str(pdf), total_pages=layout.total_pages)
            corpus.append((meta, layout))
    else:
        corpus = build_synthetic_corpus()

    qa_set = load_qa(args.gold)
    metrics = asyncio.run(run_eval(settings, corpus=corpus, qa_set=qa_set, judge=StubJudgeLLM()))
    print(json.dumps(metrics, ensure_ascii=False, indent=2))

    # gate：gold 节召回 / faithfulness / 跨公司隔离；table_triple_assertion 仅作为报告值（玩具语料对 tokenizer 敏感）
    ok = metrics["context_recall@k"] >= 0.8 and metrics["faithfulness"] >= 0.7 and metrics["cross_company_isolation"] == 1.0
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
