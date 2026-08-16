from __future__ import annotations

import argparse
import asyncio
import json
import math
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from app.db.postgres import postgres_manager
from app.services.rag.retriever import retriever_service


@dataclass(slots=True)
class BenchmarkMetrics:
    cases: int
    article_recall_at_10: float
    diploma_top_3_accuracy: float
    mean_reciprocal_rank: float
    ndcg_at_10: float
    average_latency_seconds: float


def _gold_cases(limit: int) -> list[dict]:
    postgres_manager.initialize()
    with postgres_manager.connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, title, diploma_slug, legal_branch, topic_route, article_main,
                   text_content, metadata
            FROM legal_segments
            WHERE source_scope = 'official'
              AND article_main IS NOT NULL
              AND coalesce(metadata->>'segmentation', '') = 'article_block'
              AND coalesce((metadata->>'is_primary_source')::boolean, false)
            ORDER BY md5(id::text)
            LIMIT %s
            """,
            (limit,),
        )
        rows = list(cur.fetchall())
    cases: list[dict] = []
    for index, row in enumerate(rows):
        article = str(row["article_main"])
        heading = (row["text_content"] or "").splitlines()[0][:180]
        question = (
            f"O que estabelece o Art. {article} do diploma {row['title']}?"
            if index % 2 == 0
            else f"Explique em linguagem simples esta matéria jurídica: {heading}"
        )
        cases.append({**row, "question": question})
    return cases


async def run(limit: int, concurrency: int) -> dict:
    cases = _gold_cases(limit)
    reciprocal_ranks: list[float] = []
    ndcgs: list[float] = []
    article_hits = 0
    diploma_hits = 0
    latencies: list[float] = []
    failures: list[dict] = []
    semaphore = asyncio.Semaphore(max(1, concurrency))

    async def retrieve_case(case: dict) -> tuple[dict, list, float]:
        async with semaphore:
            started = time.perf_counter()
            chunks = await retriever_service.retrieve(
                case["question"],
                k=10,
                where={"source_scope": "official"},
            )
            return case, chunks, time.perf_counter() - started

    results = await asyncio.gather(*(retrieve_case(case) for case in cases))
    for case, retrieved, latency in results:
        ranked = retrieved[:10]
        latencies.append(latency)
        article = str(case["article_main"]).replace(".", "")
        article_rank = 0
        for rank, chunk in enumerate(ranked, start=1):
            metadata = chunk.metadata or {}
            recovered = str(
                metadata.get("article_main") or chunk.article_number or ""
            ).replace(".", "")
            if recovered == article and not article_rank:
                article_rank = rank
        if article_rank:
            article_hits += 1
            reciprocal_ranks.append(1.0 / article_rank)
            ndcgs.append(1.0 / math.log2(article_rank + 1))
        else:
            reciprocal_ranks.append(0.0)
            ndcgs.append(0.0)
            failures.append(
                {
                    "question": case["question"],
                    "expected_article": article,
                    "expected_diploma": case["title"],
                    "retrieved": [
                        {
                            "article": chunk.article_number,
                            "title": chunk.title,
                        }
                        for chunk in ranked
                    ],
                }
            )
        if any(chunk.title == case["title"] for chunk in ranked[:3]):
            diploma_hits += 1

    count = max(1, len(cases))
    metrics = BenchmarkMetrics(
        cases=len(cases),
        article_recall_at_10=article_hits / count,
        diploma_top_3_accuracy=diploma_hits / count,
        mean_reciprocal_rank=sum(reciprocal_ranks) / count,
        ndcg_at_10=sum(ndcgs) / count,
        average_latency_seconds=sum(latencies) / count,
    )
    return {
        "benchmark_layer": "hybrid_retriever",
        "metrics": asdict(metrics),
        "failures": failures,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=200)
    parser.add_argument("--concurrency", type=int, default=8)
    parser.add_argument("--output", default="data/benchmarks/rag_v2_report.json")
    args = parser.parse_args()
    report = asyncio.run(run(max(1, args.limit), max(1, args.concurrency)))
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report["metrics"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
