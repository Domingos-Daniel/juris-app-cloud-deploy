from __future__ import annotations

import argparse
import asyncio
import time

from app.core.config import get_settings
from app.db.postgres import postgres_manager
from app.services.rag.embeddings import embedding_service


def _vector_literal(values: list[float]) -> str:
    return "{" + ",".join(f"{float(value):.12g}" for value in values) + "}"


def _fetch_batch(limit: int, source_scope: str | None, target_dim: int | None) -> list[dict]:
    postgres_manager.initialize()
    clauses = ["text_content IS NOT NULL", "length(text_content) > 0"]
    params: list = []
    if source_scope:
        clauses.append("source_scope = %s")
        params.append(source_scope)
    if target_dim:
        clauses.append("(embedding IS NULL OR array_length(embedding, 1) IS DISTINCT FROM %s)")
        params.append(target_dim)
    sql = f"""
        SELECT id, text_content
        FROM legal_segments
        WHERE {" AND ".join(clauses)}
        ORDER BY id
        LIMIT %s
    """
    params.append(limit)
    with postgres_manager.connection() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        return list(cur.fetchall())


def _update_embeddings(items: list[dict], vectors: list[list[float]]) -> None:
    with postgres_manager.connection() as conn, conn.cursor() as cur:
        for item, vector in zip(items, vectors, strict=False):
            cur.execute(
                "UPDATE legal_segments SET embedding = %s, updated_at = NOW() WHERE id = %s",
                (_vector_literal(vector), item["id"]),
            )


async def run(
    batch_size: int,
    source_scope: str | None,
    sleep_seconds: float,
    target_dim: int | None,
) -> None:
    settings = get_settings()
    if settings.embedding_model_type != "cloudflare":
        raise RuntimeError("Define EMBEDDING_MODEL_TYPE=cloudflare antes de reindexar.")
    total = 0
    started = time.time()
    while True:
        items = _fetch_batch(batch_size, source_scope, target_dim)
        if not items:
            break
        texts = [item["text_content"] for item in items]
        vectors = await embedding_service.embed_texts_for_ingestion(texts)
        _update_embeddings(items, vectors)
        total += len(items)
        elapsed = max(0.1, time.time() - started)
        print(f"reembedded={total} rate={total / elapsed:.2f}/s")
        if sleep_seconds > 0:
            await asyncio.sleep(sleep_seconds)
    print(f"done total={total}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--batch-size", type=int, default=24)
    parser.add_argument("--source-scope", default="official")
    parser.add_argument("--sleep", type=float, default=0.25)
    parser.add_argument("--target-dim", type=int, default=1024)
    args = parser.parse_args()
    asyncio.run(run(args.batch_size, args.source_scope or None, args.sleep, args.target_dim))


if __name__ == "__main__":
    main()
