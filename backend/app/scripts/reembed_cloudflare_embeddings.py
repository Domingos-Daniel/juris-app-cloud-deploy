from __future__ import annotations

import argparse
import asyncio
import hashlib
import time

from app.core.config import get_settings
from app.db.postgres import postgres_manager
from app.services.rag.embeddings import embedding_service


def _vector_literal(values: list[float]) -> str:
    return "{" + ",".join(f"{float(value):.12g}" for value in values) + "}"


def _fetch_batch(
    limit: int, source_scope: str | None, primary_only: bool
) -> list[dict]:
    postgres_manager.initialize()
    identity = embedding_service.vector_metadata()
    clauses = ["text_content IS NOT NULL", "length(text_content) > 0"]
    params: list = []
    if source_scope:
        clauses.append("source_scope = %s")
        params.append(source_scope)
    if primary_only:
        clauses.append("coalesce((metadata->>'is_primary_source')::boolean, false)")
    clauses.append(
        "(embedding IS NULL OR embedding_provider IS DISTINCT FROM %s "
        "OR embedding_model IS DISTINCT FROM %s "
        "OR embedding_version IS DISTINCT FROM %s "
        "OR content_hash IS NULL)"
    )
    params.extend(
        [
            identity["embedding_provider"],
            identity["embedding_model"],
            identity["embedding_version"],
        ]
    )
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
    identity = embedding_service.vector_metadata(vectors[0] if vectors else None)
    has_native_vector = bool(getattr(postgres_manager, "_pgvector_available", False))
    with postgres_manager.connection() as conn, conn.cursor() as cur:
        for item, vector in zip(items, vectors, strict=False):
            content_hash = hashlib.sha256(item["text_content"].encode("utf-8")).hexdigest()
            vector_literal = _vector_literal(vector)
            if has_native_vector:
                cur.execute(
                    """
                    UPDATE legal_segments
                    SET embedding = %s,
                        embedding_vector = %s::vector,
                        embedding_provider = %s,
                        embedding_model = %s,
                        embedding_version = %s,
                        embedding_dimension = %s,
                        content_hash = %s,
                        updated_at = NOW()
                    WHERE id = %s
                    """,
                    (
                        vector_literal,
                        "[" + ",".join(f"{float(value):.12g}" for value in vector) + "]",
                        identity["embedding_provider"],
                        identity["embedding_model"],
                        identity["embedding_version"],
                        len(vector),
                        content_hash,
                        item["id"],
                    ),
                )
            else:
                cur.execute(
                    """
                    UPDATE legal_segments
                    SET embedding = %s,
                        embedding_provider = %s,
                        embedding_model = %s,
                        embedding_version = %s,
                        embedding_dimension = %s,
                        content_hash = %s,
                        updated_at = NOW()
                    WHERE id = %s
                    """,
                    (
                        vector_literal,
                        identity["embedding_provider"],
                        identity["embedding_model"],
                        identity["embedding_version"],
                        len(vector),
                        content_hash,
                        item["id"],
                    ),
                )


async def run(
    batch_size: int,
    source_scope: str | None,
    sleep_seconds: float,
    primary_only: bool,
    parallelism: int,
) -> None:
    settings = get_settings()
    if settings.embedding_model_type != "cloudflare":
        raise RuntimeError("Define EMBEDDING_MODEL_TYPE=cloudflare antes de reindexar.")
    total = 0
    started = time.time()
    while True:
        items = _fetch_batch(batch_size, source_scope, primary_only)
        if not items:
            break
        group_size = max(1, (len(items) + parallelism - 1) // parallelism)
        groups = [items[index : index + group_size] for index in range(0, len(items), group_size)]
        vector_groups = await asyncio.gather(
            *[
                embedding_service.embed_texts_for_ingestion(
                    [item["text_content"] for item in group]
                )
                for group in groups
            ]
        )
        vectors = [vector for group in vector_groups for vector in group]
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
    parser.add_argument("--primary-only", action="store_true")
    parser.add_argument("--parallelism", type=int, default=1)
    args = parser.parse_args()
    asyncio.run(
        run(
            args.batch_size,
            args.source_scope or None,
            args.sleep,
            args.primary_only,
            max(1, args.parallelism),
        )
    )


if __name__ == "__main__":
    main()
