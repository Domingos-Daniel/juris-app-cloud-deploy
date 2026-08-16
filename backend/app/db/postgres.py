from __future__ import annotations

import asyncio
import hashlib
import json
import re
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterator
from zoneinfo import ZoneInfo

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from cachetools import TTLCache

from app.core.config import get_settings
from app.core.logger import get_logger
from app.db.models import RetrievedChunk


logger = get_logger(__name__)

# Cache de embeddings por query string para evitar geração redundante.
# Cada chamada a retriever_service.retrieve() gera um embedding mesmo para
# variantes da mesma query. Com ~20 queries por pedido, poupa ~2s.
_embedding_cache: TTLCache = TTLCache(maxsize=64, ttl=60)

DEFAULT_USER_ID = "default-user"
DEFAULT_USER_NAME = "Utilizador Local"
DEFAULT_DAILY_MESSAGE_LIMIT = 10
USAGE_TIMEZONE = "Africa/Luanda"

# Portuguese stopwords for FTS filter building
_FTS_STOPWORDS = frozenset(
    {
        "de",
        "da",
        "do",
        "das",
        "dos",
        "e",
        "ou",
        "a",
        "o",
        "as",
        "os",
        "um",
        "uma",
        "no",
        "na",
        "nos",
        "nas",
        "com",
        "sem",
        "por",
        "para",
        "que",
        "quando",
        "como",
        "entre",
        "sobre",
        "agora",
        "mesmo",
        "caso",
        "se",
        "ao",
        "aos",
        "em",
        "pelo",
        "pela",
        "pelos",
        "pelas",
        "mais",
        "menos",
        "muito",
        "pouco",
        "todo",
        "toda",
    }
)
_FTS_WORD_RE = __import__("re").compile(r"\w+", __import__("re").UNICODE)


def _build_fts_or_query(query: str) -> str | None:
    """Build an OR-based tsquery from individual query terms.

    Uses ANY-term match (OR logic) so the GIN index can narrow the scan
    without being overly restrictive. Caps at 12 terms to avoid bloat.
    """
    matches = _FTS_WORD_RE.findall(query.lower())
    terms = [t for t in matches if t not in _FTS_STOPWORDS and len(t) >= 2][:12]
    if not terms:
        return None
    return " | ".join(terms)


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _vector_literal(values: list[float] | None) -> str | None:
    if not values:
        return None
    return "{" + ",".join(f"{float(value):.12g}" for value in values) + "}"


def _pgvector_literal(values: list[float] | None) -> str | None:
    if not values:
        return None
    return "[" + ",".join(f"{float(value):.12g}" for value in values) + "]"


class PostgresManager:
    def __init__(self) -> None:
        self.settings = get_settings()
        self._initialized = False
        self._pool: ConnectionPool | None = None
        self._pgvector_available = False

    def _require_dsn(self) -> str:
        dsn = (self.settings.postgres_dsn or "").strip()
        if not dsn:
            raise RuntimeError(
                "POSTGRES_DSN não está configurado. Este backend já não suporta SQLite."
            )
        return dsn

    def _get_pool(self) -> ConnectionPool:
        if self._pool is None:
            self._pool = ConnectionPool(
                self._require_dsn(),
                min_size=0,
                max_size=10,
                kwargs={"row_factory": dict_row, "connect_timeout": 5},
                check=ConnectionPool.check_connection,
                max_idle=60,
                max_lifetime=900,
            )
        return self._pool

    @contextmanager
    def connection(self) -> Iterator[psycopg.Connection]:
        pool = self._get_pool()
        conn = pool.getconn()
        try:
            yield conn
            conn.commit()
        except Exception:
            try:
                conn.rollback()
            except Exception as rollback_exc:
                logger.warning("Postgres rollback skipped after connection loss: %s", rollback_exc)
            raise
        finally:
            pool.putconn(conn)

    def initialize(self) -> None:
        if self._initialized:
            return
        schema = self.settings.postgres_schema
        with self.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")
                cur.execute(f"SET search_path TO {schema}, public")
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS users (
                        id TEXT PRIMARY KEY,
                        name TEXT NOT NULL,
                        email TEXT,
                        phone TEXT,
                        password_hash TEXT NOT NULL DEFAULT '',
                        is_seeded BOOLEAN NOT NULL DEFAULT FALSE,
                        ai_preferences JSONB NOT NULL DEFAULT '{}'::jsonb,
                        role TEXT NOT NULL DEFAULT 'user',
                        created_at TIMESTAMPTZ NOT NULL,
                        updated_at TIMESTAMPTZ
                    )
                    """
                )
                cur.execute(
                    "ALTER TABLE users ADD COLUMN IF NOT EXISTS phone TEXT"
                )
                cur.execute(
                    "ALTER TABLE users ADD COLUMN IF NOT EXISTS password_hash TEXT NOT NULL DEFAULT ''"
                )
                cur.execute(
                    "ALTER TABLE users ADD COLUMN IF NOT EXISTS ai_preferences JSONB NOT NULL DEFAULT '{}'::jsonb"
                )
                cur.execute(
                    "ALTER TABLE users ADD COLUMN IF NOT EXISTS role TEXT NOT NULL DEFAULT 'user'"
                )
                cur.execute(
                    "ALTER TABLE users ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ"
                )
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS app_settings (
                        key TEXT PRIMARY KEY,
                        value_json JSONB NOT NULL DEFAULT '{}'::jsonb,
                        updated_at TIMESTAMPTZ NOT NULL
                    )
                    """
                )
                cur.execute(
                    """
                    INSERT INTO app_settings (key, value_json, updated_at)
                    VALUES (%s, %s::jsonb, %s)
                    ON CONFLICT (key) DO NOTHING
                    """,
                    (
                        "usage_limits",
                        json.dumps({"daily_message_limit": DEFAULT_DAILY_MESSAGE_LIMIT}),
                        utc_now_iso(),
                    ),
                )
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS auth_tokens (
                        token TEXT PRIMARY KEY,
                        user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                        username TEXT NOT NULL,
                        created_at TIMESTAMPTZ NOT NULL
                    )
                    """
                )
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS chats (
                        id TEXT PRIMARY KEY,
                        user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                        title TEXT NOT NULL,
                        active_document_id TEXT,
                        created_at TIMESTAMPTZ NOT NULL,
                        updated_at TIMESTAMPTZ NOT NULL
                    )
                    """
                )
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS chat_messages (
                        id TEXT PRIMARY KEY,
                        chat_id TEXT NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
                        role TEXT NOT NULL,
                        content TEXT NOT NULL,
                        provider_used TEXT,
                        created_at TIMESTAMPTZ NOT NULL,
                        sources_json JSONB NOT NULL DEFAULT '[]'::jsonb,
                        metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb
                    )
                    """
                )
                cur.execute(
                    "ALTER TABLE chat_messages ADD COLUMN IF NOT EXISTS metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb"
                )
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS documents (
                        id TEXT PRIMARY KEY,
                        user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                        filename TEXT NOT NULL,
                        display_name TEXT NOT NULL,
                        storage_path TEXT,
                        mime_type TEXT NOT NULL,
                        size_bytes BIGINT NOT NULL,
                        status TEXT NOT NULL,
                        created_at TIMESTAMPTZ NOT NULL,
                        page_count INTEGER NOT NULL DEFAULT 0,
                        chunks_created INTEGER NOT NULL DEFAULT 0,
                        extraction_mode TEXT NOT NULL DEFAULT 'direct',
                        quality_status TEXT NOT NULL DEFAULT 'good',
                        summary TEXT,
                        preview_text TEXT,
                        category TEXT,
                        last_used_at TIMESTAMPTZ
                    )
                    """
                )
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS document_pages (
                        id BIGSERIAL PRIMARY KEY,
                        document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
                        page_number INTEGER NOT NULL,
                        text_content TEXT NOT NULL,
                        used_ocr BOOLEAN NOT NULL DEFAULT FALSE,
                        char_count INTEGER NOT NULL DEFAULT 0,
                        created_at TIMESTAMPTZ NOT NULL,
                        UNIQUE(document_id, page_number)
                    )
                    """
                )
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS queries (
                        id BIGSERIAL PRIMARY KEY,
                        question TEXT NOT NULL,
                        answer TEXT NOT NULL,
                        timestamp TIMESTAMPTZ NOT NULL
                    )
                    """
                )
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS legal_documents (
                        id TEXT PRIMARY KEY,
                        entity_slug TEXT,
                        entity_name TEXT,
                        year TEXT,
                        document_slug TEXT,
                        title TEXT NOT NULL,
                        page_url TEXT,
                        download_pdf_url TEXT,
                        matched_internal_slug TEXT,
                        legal_branch_guess TEXT,
                        topic_route_guess TEXT,
                        status TEXT NOT NULL DEFAULT 'discovered',
                        source_invalid BOOLEAN NOT NULL DEFAULT FALSE,
                        local_pdf_path TEXT,
                        metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
                        created_at TIMESTAMPTZ NOT NULL,
                        updated_at TIMESTAMPTZ NOT NULL
                    )
                    """
                )
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS legal_document_versions (
                        id TEXT PRIMARY KEY,
                        legal_document_id TEXT NOT NULL REFERENCES legal_documents(id) ON DELETE CASCADE,
                        source_hash TEXT,
                        version_label TEXT,
                        created_at TIMESTAMPTZ NOT NULL
                    )
                    """
                )
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS legal_segments (
                        id TEXT PRIMARY KEY,
                        legal_document_id TEXT REFERENCES legal_documents(id) ON DELETE CASCADE,
                        source TEXT NOT NULL,
                        title TEXT NOT NULL,
                        link_original TEXT,
                        page INTEGER,
                        article_number TEXT,
                        article_main TEXT,
                        article_references TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[],
                        law_status TEXT NOT NULL DEFAULT 'Nao verificado',
                        source_scope TEXT NOT NULL DEFAULT 'official',
                        document_id TEXT,
                        diploma_slug TEXT,
                        legal_branch TEXT,
                        topic_route TEXT,
                        text_content TEXT NOT NULL,
                        metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
                        text_search TSVECTOR,
                        embedding double precision[],
                        embedding_provider TEXT,
                        embedding_model TEXT,
                        embedding_version TEXT,
                        embedding_dimension INTEGER,
                        content_hash TEXT,
                        created_at TIMESTAMPTZ NOT NULL,
                        updated_at TIMESTAMPTZ NOT NULL
                    )
                    """
                )
                if self.settings.pgvector_enabled:
                    cur.execute("SAVEPOINT pgvector_setup")
                    try:
                        cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
                        cur.execute(
                            "ALTER TABLE legal_segments ADD COLUMN IF NOT EXISTS embedding_vector vector"
                        )
                        cur.execute("RELEASE SAVEPOINT pgvector_setup")
                        self._pgvector_available = True
                    except Exception as exc:
                        cur.execute("ROLLBACK TO SAVEPOINT pgvector_setup")
                        cur.execute("RELEASE SAVEPOINT pgvector_setup")
                        logger.warning("pgvector indisponível; usando fallback array: %s", exc)
                for statement in (
                    "ALTER TABLE legal_segments ADD COLUMN IF NOT EXISTS embedding_provider TEXT",
                    "ALTER TABLE legal_segments ADD COLUMN IF NOT EXISTS embedding_model TEXT",
                    "ALTER TABLE legal_segments ADD COLUMN IF NOT EXISTS embedding_version TEXT",
                    "ALTER TABLE legal_segments ADD COLUMN IF NOT EXISTS embedding_dimension INTEGER",
                    "ALTER TABLE legal_segments ADD COLUMN IF NOT EXISTS content_hash TEXT",
                ):
                    cur.execute(statement)
                cur.execute(
                    "CREATE INDEX IF NOT EXISTS idx_document_pages_doc ON document_pages(document_id, page_number)"
                )
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS jurisprudence_cases (
                        id TEXT PRIMARY KEY,
                        court TEXT NOT NULL,
                        chamber TEXT,
                        case_number TEXT,
                        title TEXT NOT NULL,
                        decision_date DATE,
                        publication_date DATE,
                        url TEXT NOT NULL,
                        pdf_url TEXT,
                        legal_branch TEXT,
                        topic_route TEXT,
                        summary TEXT,
                        metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
                        created_at TIMESTAMPTZ NOT NULL,
                        updated_at TIMESTAMPTZ NOT NULL
                    )
                    """
                )
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS jurisprudence_holdings (
                        id TEXT PRIMARY KEY,
                        jurisprudence_case_id TEXT NOT NULL REFERENCES jurisprudence_cases(id) ON DELETE CASCADE,
                        holding_text TEXT NOT NULL,
                        legal_branch TEXT,
                        topic_route TEXT,
                        metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
                        created_at TIMESTAMPTZ NOT NULL
                    )
                    """
                )
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS legal_citations (
                        id TEXT PRIMARY KEY,
                        legal_segment_id TEXT NOT NULL REFERENCES legal_segments(id) ON DELETE CASCADE,
                        citation_text TEXT NOT NULL,
                        created_at TIMESTAMPTZ NOT NULL
                    )
                    """
                )
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS legal_relations (
                        id TEXT PRIMARY KEY,
                        source_document_id TEXT NOT NULL REFERENCES legal_documents(id) ON DELETE CASCADE,
                        target_document_id TEXT REFERENCES legal_documents(id) ON DELETE CASCADE,
                        relation_type TEXT NOT NULL,
                        metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
                        created_at TIMESTAMPTZ NOT NULL
                    )
                    """
                )
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS ingestion_jobs (
                        id TEXT PRIMARY KEY,
                        job_type TEXT NOT NULL,
                        status TEXT NOT NULL,
                        metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
                        created_at TIMESTAMPTZ NOT NULL,
                        updated_at TIMESTAMPTZ NOT NULL
                    )
                    """
                )
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS ingestion_job_items (
                        id TEXT PRIMARY KEY,
                        job_id TEXT NOT NULL REFERENCES ingestion_jobs(id) ON DELETE CASCADE,
                        legal_document_id TEXT REFERENCES legal_documents(id) ON DELETE CASCADE,
                        status TEXT NOT NULL,
                        error_message TEXT,
                        metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
                        created_at TIMESTAMPTZ NOT NULL,
                        updated_at TIMESTAMPTZ NOT NULL
                    )
                    """
                )
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS conversation_legal_state (
                        chat_id TEXT PRIMARY KEY REFERENCES chats(id) ON DELETE CASCADE,
                        user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                        topic_route TEXT,
                        legal_branch TEXT,
                        diploma_slug TEXT,
                        active_article TEXT,
                        metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
                        updated_at TIMESTAMPTZ NOT NULL
                    )
                    """
                )
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS chat_edit_versions (
                        id TEXT PRIMARY KEY,
                        chat_id TEXT NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
                        edit_index INTEGER NOT NULL,
                        version_index INTEGER NOT NULL,
                        version_data JSONB NOT NULL DEFAULT '[]'::jsonb,
                        created_at TIMESTAMPTZ NOT NULL,
                        UNIQUE (chat_id, edit_index, version_index)
                    )
                    """
                )
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS professional_profiles (
                        user_id TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
                        status TEXT NOT NULL DEFAULT 'inactive',
                        display_name TEXT,
                        license_number TEXT,
                        professional_title TEXT,
                        organization_name TEXT,
                        created_at TIMESTAMPTZ NOT NULL,
                        updated_at TIMESTAMPTZ NOT NULL
                    )
                    """
                )
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS pro_workspaces (
                        id TEXT PRIMARY KEY,
                        owner_user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                        name TEXT NOT NULL,
                        workspace_type TEXT NOT NULL DEFAULT 'individual',
                        created_at TIMESTAMPTZ NOT NULL,
                        updated_at TIMESTAMPTZ NOT NULL
                    )
                    """
                )
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS pro_workspace_members (
                        id TEXT PRIMARY KEY,
                        workspace_id TEXT NOT NULL REFERENCES pro_workspaces(id) ON DELETE CASCADE,
                        user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                        member_role TEXT NOT NULL DEFAULT 'owner',
                        status TEXT NOT NULL DEFAULT 'active',
                        created_at TIMESTAMPTZ NOT NULL,
                        updated_at TIMESTAMPTZ NOT NULL,
                        UNIQUE (workspace_id, user_id)
                    )
                    """
                )
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS pro_clients (
                        id TEXT PRIMARY KEY,
                        workspace_id TEXT NOT NULL REFERENCES pro_workspaces(id) ON DELETE CASCADE,
                        owner_user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                        client_type TEXT NOT NULL DEFAULT 'individual',
                        name TEXT NOT NULL,
                        email TEXT,
                        phone TEXT,
                        identification_number TEXT,
                        address TEXT,
                        notes TEXT,
                        conflict_terms TEXT,
                        status TEXT NOT NULL DEFAULT 'active',
                        created_at TIMESTAMPTZ NOT NULL,
                        updated_at TIMESTAMPTZ NOT NULL
                    )
                    """
                )
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS pro_cases (
                        id TEXT PRIMARY KEY,
                        workspace_id TEXT NOT NULL REFERENCES pro_workspaces(id) ON DELETE CASCADE,
                        client_id TEXT REFERENCES pro_clients(id) ON DELETE SET NULL,
                        owner_user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                        title TEXT NOT NULL,
                        case_number TEXT,
                        court TEXT,
                        opposing_party TEXT,
                        legal_branch TEXT,
                        status TEXT NOT NULL DEFAULT 'open',
                        priority TEXT NOT NULL DEFAULT 'normal',
                        opened_at DATE,
                        next_deadline_at TIMESTAMPTZ,
                        summary TEXT,
                        metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
                        created_at TIMESTAMPTZ NOT NULL,
                        updated_at TIMESTAMPTZ NOT NULL
                    )
                    """
                )
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS pro_case_chats (
                        case_id TEXT NOT NULL REFERENCES pro_cases(id) ON DELETE CASCADE,
                        chat_id TEXT NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
                        linked_by TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                        created_at TIMESTAMPTZ NOT NULL,
                        PRIMARY KEY (case_id, chat_id)
                    )
                    """
                )
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS pro_case_documents (
                        case_id TEXT NOT NULL REFERENCES pro_cases(id) ON DELETE CASCADE,
                        document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
                        label TEXT,
                        added_by TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                        created_at TIMESTAMPTZ NOT NULL,
                        PRIMARY KEY (case_id, document_id)
                    )
                    """
                )
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS pro_tasks (
                        id TEXT PRIMARY KEY,
                        workspace_id TEXT NOT NULL REFERENCES pro_workspaces(id) ON DELETE CASCADE,
                        case_id TEXT NOT NULL REFERENCES pro_cases(id) ON DELETE CASCADE,
                        title TEXT NOT NULL,
                        description TEXT,
                        status TEXT NOT NULL DEFAULT 'pending',
                        priority TEXT NOT NULL DEFAULT 'normal',
                        due_at TIMESTAMPTZ,
                        assigned_to TEXT REFERENCES users(id) ON DELETE SET NULL,
                        created_by TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                        created_at TIMESTAMPTZ NOT NULL,
                        updated_at TIMESTAMPTZ NOT NULL
                    )
                    """
                )
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS pro_deadlines (
                        id TEXT PRIMARY KEY,
                        workspace_id TEXT NOT NULL REFERENCES pro_workspaces(id) ON DELETE CASCADE,
                        case_id TEXT NOT NULL REFERENCES pro_cases(id) ON DELETE CASCADE,
                        title TEXT NOT NULL,
                        due_at TIMESTAMPTZ NOT NULL,
                        source TEXT,
                        status TEXT NOT NULL DEFAULT 'open',
                        reminder_days INTEGER NOT NULL DEFAULT 3,
                        created_by TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                        created_at TIMESTAMPTZ NOT NULL,
                        updated_at TIMESTAMPTZ NOT NULL
                    )
                    """
                )
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS pro_notes (
                        id TEXT PRIMARY KEY,
                        workspace_id TEXT NOT NULL REFERENCES pro_workspaces(id) ON DELETE CASCADE,
                        case_id TEXT NOT NULL REFERENCES pro_cases(id) ON DELETE CASCADE,
                        author_user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                        body TEXT NOT NULL,
                        visibility TEXT NOT NULL DEFAULT 'internal',
                        created_at TIMESTAMPTZ NOT NULL,
                        updated_at TIMESTAMPTZ NOT NULL
                    )
                    """
                )
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS pro_activity_events (
                        id TEXT PRIMARY KEY,
                        workspace_id TEXT NOT NULL REFERENCES pro_workspaces(id) ON DELETE CASCADE,
                        case_id TEXT REFERENCES pro_cases(id) ON DELETE CASCADE,
                        actor_user_id TEXT REFERENCES users(id) ON DELETE SET NULL,
                        event_type TEXT NOT NULL,
                        payload_json JSONB NOT NULL DEFAULT '{}'::jsonb,
                        created_at TIMESTAMPTZ NOT NULL
                    )
                    """
                )
                cur.execute(
                    "CREATE INDEX IF NOT EXISTS idx_chat_edit_versions_chat ON chat_edit_versions(chat_id, edit_index)"
                )
                cur.execute(
                    "CREATE INDEX IF NOT EXISTS idx_chats_user_updated ON chats(user_id, updated_at DESC)"
                )
                cur.execute(
                    "CREATE INDEX IF NOT EXISTS idx_chat_messages_chat_created ON chat_messages(chat_id, created_at ASC)"
                )
                cur.execute(
                    "CREATE INDEX IF NOT EXISTS idx_documents_user_created ON documents(user_id, created_at DESC)"
                )
                cur.execute(
                    "CREATE INDEX IF NOT EXISTS idx_legal_documents_slug ON legal_documents(matched_internal_slug, document_slug)"
                )
                cur.execute(
                    "CREATE INDEX IF NOT EXISTS idx_legal_segments_lookup ON legal_segments(diploma_slug, legal_branch, page)"
                )
                cur.execute(
                    "CREATE INDEX IF NOT EXISTS idx_legal_segments_doc ON legal_segments(document_id, source_scope)"
                )
                cur.execute(
                    "CREATE INDEX IF NOT EXISTS idx_legal_segments_embedding_identity ON legal_segments(embedding_provider, embedding_model, embedding_dimension)"
                )
                cur.execute(
                    "CREATE INDEX IF NOT EXISTS idx_jurisprudence_cases_branch ON jurisprudence_cases(court, legal_branch, publication_date DESC)"
                )
                cur.execute(
                    "CREATE INDEX IF NOT EXISTS idx_legal_segments_fts ON legal_segments USING GIN(text_search)"
                )
                cur.execute(
                    "CREATE INDEX IF NOT EXISTS idx_professional_profiles_status ON professional_profiles(status)"
                )
                cur.execute(
                    "CREATE INDEX IF NOT EXISTS idx_pro_workspace_members_user ON pro_workspace_members(user_id, status)"
                )
                cur.execute(
                    "CREATE INDEX IF NOT EXISTS idx_pro_clients_workspace ON pro_clients(workspace_id, status, updated_at DESC)"
                )
                cur.execute(
                    "CREATE INDEX IF NOT EXISTS idx_pro_cases_workspace ON pro_cases(workspace_id, status, updated_at DESC)"
                )
                cur.execute(
                    "CREATE INDEX IF NOT EXISTS idx_pro_tasks_case ON pro_tasks(case_id, status, due_at ASC)"
                )
                cur.execute(
                    "CREATE INDEX IF NOT EXISTS idx_pro_deadlines_case ON pro_deadlines(case_id, status, due_at ASC)"
                )
                cur.execute(
                    "CREATE INDEX IF NOT EXISTS idx_pro_activity_case ON pro_activity_events(case_id, created_at DESC)"
                )
            self._seed_default_user(conn)
        self._initialized = True

    def _seed_default_user(self, conn: psycopg.Connection) -> None:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO users (id, name, email, is_seeded, created_at)
                VALUES (%s, %s, %s, TRUE, %s)
                ON CONFLICT (id) DO UPDATE
                SET name = EXCLUDED.name,
                    is_seeded = TRUE
                """,
                (DEFAULT_USER_ID, DEFAULT_USER_NAME, None, utc_now_iso()),
            )

    # ── User management ───────────────────────────────────────────

    def register_user(
        self, name: str, email: str, phone: str, password_hash: str
    ) -> str | None:
        self.initialize()
        user_id = str(uuid.uuid4())
        try:
            with self.connection() as conn, conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO users (id, name, email, phone, password_hash, created_at)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    (
                        user_id,
                        name,
                        email or None,
                        phone or None,
                        password_hash,
                        utc_now_iso(),
                    ),
                )
            return user_id
        except Exception:
            return None

    def get_user_by_email(self, email: str) -> dict | None:
        self.initialize()
        with self.connection() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT id, name, email, phone, password_hash, ai_preferences, is_seeded, role, created_at FROM users WHERE email = %s",
                (email,),
            )
            row = cur.fetchone()
            return dict(row) if row else None

    def get_user_by_id(self, user_id: str) -> dict | None:
        self.initialize()
        with self.connection() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT id, name, email, phone, password_hash, ai_preferences, is_seeded, role, created_at FROM users WHERE id = %s",
                (user_id,),
            )
            row = cur.fetchone()
            return dict(row) if row else None

    def update_user_profile(
        self, user_id: str, name: str, email: str, phone: str
    ) -> bool:
        self.initialize()
        try:
            with self.connection() as conn, conn.cursor() as cur:
                cur.execute(
                    """UPDATE users SET name = %s, email = %s, phone = %s, updated_at = %s
                       WHERE id = %s""",
                    (name, email or None, phone or None, utc_now_iso(), user_id),
                )
            return True
        except Exception:
            return False

    def update_user_preferences(self, user_id: str, prefs: dict) -> bool:
        self.initialize()
        try:
            with self.connection() as conn, conn.cursor() as cur:
                cur.execute(
                    """UPDATE users SET ai_preferences = %s, updated_at = %s WHERE id = %s""",
                    (json.dumps(prefs), utc_now_iso(), user_id),
                )
            return True
        except Exception:
            return False

    def get_user_preferences(self, user_id: str) -> dict:
        self.initialize()
        with self.connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT ai_preferences FROM users WHERE id = %s", (user_id,))
            row = cur.fetchone()
            return row["ai_preferences"] if row and row.get("ai_preferences") else {}

    def get_usage_limits(self) -> dict:
        self.initialize()
        with self.connection() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT value_json FROM app_settings WHERE key = %s",
                ("usage_limits",),
            )
            row = cur.fetchone()
            payload = row.get("value_json") if row else {}
            if not isinstance(payload, dict):
                payload = {}
            limit = payload.get("daily_message_limit", DEFAULT_DAILY_MESSAGE_LIMIT)
            try:
                limit_value = max(0, int(limit))
            except (TypeError, ValueError):
                limit_value = DEFAULT_DAILY_MESSAGE_LIMIT
            return {"daily_message_limit": limit_value}

    def update_usage_limits(self, daily_message_limit: int) -> dict:
        self.initialize()
        limit_value = max(0, int(daily_message_limit))
        payload = {"daily_message_limit": limit_value}
        with self.connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO app_settings (key, value_json, updated_at)
                VALUES (%s, %s::jsonb, %s)
                ON CONFLICT (key) DO UPDATE
                SET value_json = EXCLUDED.value_json,
                    updated_at = EXCLUDED.updated_at
                """,
                ("usage_limits", json.dumps(payload), utc_now_iso()),
            )
        return payload

    def get_user_daily_message_usage(self, user_id: str, role: str = "user") -> dict:
        self.initialize()
        limits = self.get_usage_limits()
        limit = max(0, int(limits.get("daily_message_limit", DEFAULT_DAILY_MESSAGE_LIMIT)))
        unlimited = role == "admin" or limit == 0
        used = 0
        with self.connection() as conn, conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT COUNT(*) AS total
                FROM chat_messages messages
                INNER JOIN chats chats ON chats.id = messages.chat_id
                WHERE chats.user_id = %s
                  AND messages.role = 'user'
                  AND (messages.created_at AT TIME ZONE %s)::date = (NOW() AT TIME ZONE %s)::date
                """,
                (user_id, USAGE_TIMEZONE, USAGE_TIMEZONE),
            )
            used = int((cur.fetchone() or {}).get("total", 0) or 0)

        now_local = datetime.now(ZoneInfo(USAGE_TIMEZONE))
        next_midnight = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
        if next_midnight <= now_local:
            from datetime import timedelta

            next_midnight = next_midnight + timedelta(days=1)
        remaining = None if unlimited else max(0, limit - used)
        return {
            "daily_message_limit": limit,
            "messages_used_today": used,
            "messages_remaining_today": remaining,
            "daily_limit_exempt": unlimited,
            "resets_at": next_midnight.isoformat(),
            "timezone": USAGE_TIMEZONE,
        }

    def has_any_user(self) -> bool:
        self.initialize()
        with self.connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT 1 FROM users WHERE is_seeded = FALSE LIMIT 1")
            return cur.fetchone() is not None

    def issue_auth_token(self, user_id: str, username: str, token: str) -> None:
        self.initialize()
        with self.connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO auth_tokens (token, user_id, username, created_at)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (token) DO UPDATE
                SET username = EXCLUDED.username,
                    created_at = EXCLUDED.created_at
                """,
                (token, user_id, username, utc_now_iso()),
            )

    def has_auth_token(self, token: str) -> bool:
        self.initialize()
        with self.connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT 1 FROM auth_tokens WHERE token = %s", (token,))
            return cur.fetchone() is not None

    def get_user_id_for_token(self, token: str) -> str | None:
        self.initialize()
        with self.connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT user_id FROM auth_tokens WHERE token = %s", (token,))
            row = cur.fetchone()
            return row["user_id"] if row else None

    def get_default_user_id(self) -> str:
        self.initialize()
        return DEFAULT_USER_ID

    def get_default_user_name(self) -> str:
        self.initialize()
        with self.connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT name FROM users WHERE id = %s", (DEFAULT_USER_ID,))
            row = cur.fetchone()
            return row["name"] if row and row.get("name") else DEFAULT_USER_NAME

    def save_query(self, question: str, answer: str) -> int:
        self.initialize()
        with self.connection() as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO queries (question, answer, timestamp) VALUES (%s, %s, %s) RETURNING id",
                (question, answer, utc_now_iso()),
            )
            row = cur.fetchone()
            return int(row["id"])

    def create_chat(
        self,
        title: str,
        active_document_id: str | None = None,
        user_id: str | None = None,
    ) -> str:
        self.initialize()
        chat_id = str(uuid.uuid4())
        now = utc_now_iso()
        with self.connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO chats (id, user_id, title, active_document_id, created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (
                    chat_id,
                    user_id or DEFAULT_USER_ID,
                    title,
                    active_document_id,
                    now,
                    now,
                ),
            )
        return chat_id

    def append_chat_exchange(
        self,
        *,
        chat_id: str,
        question: str,
        answer: str,
        provider_used: str,
        sources: list[dict],
        active_document_id: str | None = None,
        assistant_metadata: dict[str, Any] | None = None,
    ) -> None:
        self.initialize()
        now_user = utc_now_iso()
        now_assistant = utc_now_iso()
        with self.connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                UPDATE chats
                SET updated_at = %s,
                    active_document_id = COALESCE(%s, active_document_id)
                WHERE id = %s
                """,
                (now_assistant, active_document_id, chat_id),
            )
            cur.execute(
                """
                INSERT INTO chat_messages (id, chat_id, role, content, provider_used, created_at, sources_json, metadata_json)
                VALUES (%s, %s, 'user', %s, NULL, %s, '[]'::jsonb, '{}'::jsonb)
                """,
                (str(uuid.uuid4()), chat_id, question, now_user),
            )
            cur.execute(
                """
                INSERT INTO chat_messages (id, chat_id, role, content, provider_used, created_at, sources_json, metadata_json)
                VALUES (%s, %s, 'assistant', %s, %s, %s, %s::jsonb, %s::jsonb)
                """,
                (
                    str(uuid.uuid4()),
                    chat_id,
                    answer,
                    provider_used,
                    now_assistant,
                    json.dumps(sources, ensure_ascii=False),
                    json.dumps(assistant_metadata or {}, ensure_ascii=False),
                ),
            )

    def chat_belongs_to_user(self, chat_id: str, user_id: str | int | None = None) -> bool:
        self.initialize()
        with self.connection() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM chats WHERE id = %s AND user_id = %s",
                (chat_id, user_id or DEFAULT_USER_ID),
            )
            return cur.fetchone() is not None

    def get_conversation_state(
        self, chat_id: str, user_id: str | int | None = None
    ) -> dict[str, Any] | None:
        self.initialize()
        with self.connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT chat_id, user_id, topic_route, legal_branch, diploma_slug, active_article, metadata, updated_at
                FROM conversation_legal_state
                WHERE chat_id = %s AND user_id = %s
                """,
                (chat_id, user_id or DEFAULT_USER_ID),
            )
            row = cur.fetchone()
            if not row:
                return None
            return {
                "chat_id": row["chat_id"],
                "user_id": row["user_id"],
                "topic_route": row["topic_route"],
                "legal_branch": row["legal_branch"],
                "diploma_slug": row["diploma_slug"],
                "active_article": row["active_article"],
                "metadata": row["metadata"] or {},
                "updated_at": row["updated_at"].isoformat(),
            }

    def upsert_conversation_state(
        self,
        *,
        chat_id: str,
        user_id: str | int | None = None,
        topic_route: str | None = None,
        legal_branch: str | None = None,
        diploma_slug: str | None = None,
        active_article: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self.initialize()
        with self.connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO conversation_legal_state (
                    chat_id, user_id, topic_route, legal_branch, diploma_slug, active_article, metadata, updated_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s)
                ON CONFLICT (chat_id) DO UPDATE
                SET user_id = EXCLUDED.user_id,
                    topic_route = EXCLUDED.topic_route,
                    legal_branch = EXCLUDED.legal_branch,
                    diploma_slug = EXCLUDED.diploma_slug,
                    active_article = EXCLUDED.active_article,
                    metadata = EXCLUDED.metadata,
                    updated_at = EXCLUDED.updated_at
                """,
                (
                    chat_id,
                    user_id or DEFAULT_USER_ID,
                    topic_route,
                    legal_branch,
                    diploma_slug,
                    active_article,
                    json.dumps(metadata or {}, ensure_ascii=False),
                    utc_now_iso(),
                ),
            )

    def list_chats(self, user_id: str | None = None) -> list[dict]:
        self.initialize()
        with self.connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, title, created_at, updated_at, active_document_id
                FROM chats
                WHERE user_id = %s
                ORDER BY updated_at DESC
                """,
                (user_id or DEFAULT_USER_ID,),
            )
            chats = cur.fetchall()
            items: list[dict] = []
            for chat in chats:
                cur.execute(
                    """
                    SELECT id, role, content, provider_used, created_at, sources_json, metadata_json
                    FROM chat_messages
                    WHERE chat_id = %s
                    ORDER BY created_at ASC, CASE WHEN role = 'user' THEN 0 ELSE 1 END, id ASC
                    """,
                    (chat["id"],),
                )
                messages = cur.fetchall()
                items.append(
                    {
                        "id": chat["id"],
                        "title": chat["title"],
                        "created_at": chat["created_at"].isoformat(),
                        "updated_at": chat["updated_at"].isoformat(),
                        "active_document_id": chat["active_document_id"],
                        "messages": [
                            {
                                "id": message["id"],
                                "role": message["role"],
                                "content": message["content"],
                                "provider_used": message["provider_used"],
                                "created_at": message["created_at"].isoformat(),
                                "sources": message["sources_json"] or [],
                                "answer_mode": (message["metadata_json"] or {}).get("answer_mode"),
                                "clarifying_questions": (message["metadata_json"] or {}).get("clarifying_questions", []),
                                "clarification_request": (message["metadata_json"] or {}).get("clarification_request"),
                            }
                            for message in messages
                        ],
                    }
                )
            return items

    def delete_chat(self, chat_id: str, user_id: str | None = None) -> bool:
        self.initialize()
        with self.connection() as conn, conn.cursor() as cur:
            params = [chat_id]
            where = "id = %s"
            if user_id:
                where += " AND user_id = %s"
                params.append(user_id)
            cur.execute(f"DELETE FROM chats WHERE {where}", params)
            return cur.rowcount > 0

    def delete_all_chats(self, user_id: str | None = None) -> int:
        self.initialize()
        with self.connection() as conn, conn.cursor() as cur:
            if user_id:
                cur.execute("DELETE FROM chats WHERE user_id = %s", (user_id,))
            else:
                cur.execute("DELETE FROM chats")
            return cur.rowcount

    def save_edit_version(
        self,
        *,
        chat_id: str,
        edit_index: int,
        version_index: int,
        version_data: list[dict],
    ) -> None:
        self.initialize()
        with self.connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO chat_edit_versions (id, chat_id, edit_index, version_index, version_data, created_at)
                VALUES (%s, %s, %s, %s, %s::jsonb, %s)
                ON CONFLICT (chat_id, edit_index, version_index) DO UPDATE
                SET version_data = EXCLUDED.version_data
                """,
                (
                    str(uuid.uuid4()),
                    chat_id,
                    edit_index,
                    version_index,
                    json.dumps(version_data, ensure_ascii=False),
                    utc_now_iso(),
                ),
            )

    def get_edit_versions(self, chat_id: str) -> list[dict]:
        self.initialize()
        with self.connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, edit_index, version_index, version_data, created_at
                FROM chat_edit_versions
                WHERE chat_id = %s
                ORDER BY edit_index ASC, version_index ASC
                """,
                (chat_id,),
            )
            rows = cur.fetchall()
            result: list[dict] = []
            current_edit: dict | None = None
            for row in rows:
                edit_idx = row["edit_index"]
                if not current_edit or current_edit["editIndex"] != edit_idx:
                    current_edit = {"editIndex": edit_idx, "versions": []}
                    result.append(current_edit)
                current_edit["versions"].append({
                    "tail": row["version_data"],
                })
            return result

    def save_document(
        self,
        *,
        filename: str,
        mime_type: str,
        size_bytes: int,
        status: str,
        page_count: int,
        chunks_created: int,
        extraction_mode: str,
        display_name: str | None = None,
        storage_path: str | None = None,
        quality_status: str = "good",
        summary: str | None = None,
        preview_text: str | None = None,
        category: str | None = None,
        user_id: str | None = None,
    ) -> str:
        self.initialize()
        document_id = str(uuid.uuid4())
        with self.connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO documents (
                    id, user_id, filename, display_name, storage_path, mime_type,
                    size_bytes, status, created_at, page_count, chunks_created,
                    extraction_mode, quality_status, summary, preview_text, category
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    document_id,
                    user_id or DEFAULT_USER_ID,
                    filename,
                    display_name or filename,
                    storage_path,
                    mime_type,
                    size_bytes,
                    status,
                    utc_now_iso(),
                    page_count,
                    chunks_created,
                    extraction_mode,
                    quality_status,
                    summary,
                    preview_text,
                    category,
                ),
            )
        return document_id

    def update_document_processing_state(
        self,
        document_id: str,
        *,
        status: str,
        page_count: int | None = None,
        chunks_created: int | None = None,
        extraction_mode: str | None = None,
        quality_status: str | None = None,
        summary: str | None = None,
        preview_text: str | None = None,
        category: str | None = None,
        user_id: str | int | None = None,
    ) -> bool:
        self.initialize()
        assignments = ["status = %s"]
        params: list[Any] = [status]
        optional = {
            "page_count": page_count,
            "chunks_created": chunks_created,
            "extraction_mode": extraction_mode,
            "quality_status": quality_status,
            "summary": summary,
            "preview_text": preview_text,
            "category": category,
        }
        for column, value in optional.items():
            if value is not None:
                assignments.append(f"{column} = %s")
                params.append(value)
        params.extend([document_id, user_id or DEFAULT_USER_ID])
        with self.connection() as conn, conn.cursor() as cur:
            cur.execute(
                f"""
                UPDATE documents
                SET {", ".join(assignments)}
                WHERE id = %s AND user_id = %s
                """,
                tuple(params),
            )
            return cur.rowcount > 0

    def replace_document_pages(
        self, document_id: str, pages: list[dict[str, Any]]
    ) -> int:
        self.initialize()
        now = utc_now_iso()
        with self.connection() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM document_pages WHERE document_id = %s", (document_id,))
            inserted = 0
            for page in pages:
                text = (page.get("text") or "").strip()
                if not text:
                    continue
                cur.execute(
                    """
                    INSERT INTO document_pages (
                        document_id, page_number, text_content, used_ocr,
                        char_count, created_at
                    )
                    VALUES (%s, %s, %s, %s, %s, %s)
                    ON CONFLICT (document_id, page_number) DO UPDATE
                    SET text_content = EXCLUDED.text_content,
                        used_ocr = EXCLUDED.used_ocr,
                        char_count = EXCLUDED.char_count
                    """,
                    (
                        document_id,
                        int(page.get("page") or page.get("page_number") or 0),
                        text,
                        bool(page.get("used_ocr", False)),
                        len(text),
                        now,
                    ),
                )
                inserted += 1
            return inserted

    def get_document_pages(
        self, document_id: str, user_id: str | int | None = None
    ) -> list[dict[str, Any]]:
        self.initialize()
        with self.connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT pages.page_number, pages.text_content, pages.used_ocr, pages.char_count
                FROM document_pages pages
                INNER JOIN documents docs ON docs.id = pages.document_id
                WHERE pages.document_id = %s AND docs.user_id = %s
                ORDER BY pages.page_number ASC
                """,
                (document_id, user_id or DEFAULT_USER_ID),
            )
            return [
                {
                    "page": int(row["page_number"]),
                    "text": row["text_content"],
                    "used_ocr": bool(row["used_ocr"]),
                    "char_count": int(row["char_count"] or 0),
                }
                for row in cur.fetchall()
            ]

    def rename_document(
        self, document_id: str, display_name: str, user_id: str | int | None = None
    ) -> None:
        self.initialize()
        with self.connection() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE documents SET display_name = %s WHERE id = %s AND user_id = %s",
                (display_name, document_id, user_id or DEFAULT_USER_ID),
            )

    def mark_document_used(
        self, document_id: str, user_id: str | int | None = None
    ) -> None:
        self.initialize()
        with self.connection() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE documents SET last_used_at = %s WHERE id = %s AND user_id = %s",
                (utc_now_iso(), document_id, user_id or DEFAULT_USER_ID),
            )

    def delete_document(
        self, document_id: str, user_id: str | int | None = None
    ) -> None:
        self.initialize()
        with self.connection() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE chats SET active_document_id = NULL WHERE active_document_id = %s AND user_id = %s",
                (document_id, user_id or DEFAULT_USER_ID),
            )
            cur.execute(
                "DELETE FROM documents WHERE id = %s AND user_id = %s",
                (document_id, user_id or DEFAULT_USER_ID),
            )

    def get_document(
        self, document_id: str, user_id: str | int | None = None
    ) -> dict | None:
        self.initialize()
        with self.connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, filename, display_name, storage_path, mime_type, size_bytes, status,
                       created_at, page_count, chunks_created, extraction_mode, quality_status,
                       summary, preview_text, category, last_used_at
                FROM documents
                WHERE id = %s AND user_id = %s
                """,
                (document_id, user_id or DEFAULT_USER_ID),
            )
            row = cur.fetchone()
            if not row:
                return None
            cur.execute(
                """
                SELECT COUNT(*) AS usage_count, MAX(updated_at) AS last_chat_use
                FROM chats
                WHERE active_document_id = %s AND user_id = %s
                """,
                (document_id, user_id or DEFAULT_USER_ID),
            )
            usage = cur.fetchone() or {"usage_count": 0, "last_chat_use": None}
            return {
                "id": row["id"],
                "filename": row["filename"],
                "display_name": row["display_name"],
                "storage_path": row["storage_path"],
                "mime_type": row["mime_type"],
                "size_bytes": int(row["size_bytes"]),
                "status": row["status"],
                "created_at": row["created_at"].isoformat(),
                "page_count": int(row["page_count"] or 0),
                "chunks_created": int(row["chunks_created"] or 0),
                "extraction_mode": row["extraction_mode"],
                "quality_status": row["quality_status"],
                "summary": row["summary"],
                "preview_text": row["preview_text"],
                "category": row["category"],
                "usage_count": int(usage["usage_count"] or 0),
                "last_used_at": (
                    row["last_used_at"].isoformat()
                    if row["last_used_at"]
                    else (
                        usage["last_chat_use"].isoformat()
                        if usage["last_chat_use"]
                        else None
                    )
                ),
            }

    def set_chat_active_document(
        self, chat_id: str, document_id: str | None, user_id: str | int | None = None
    ) -> None:
        self.initialize()
        with self.connection() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE chats SET active_document_id = %s, updated_at = %s WHERE id = %s AND user_id = %s",
                (document_id, utc_now_iso(), chat_id, user_id or DEFAULT_USER_ID),
            )

    def list_documents(self, user_id: str | None = None) -> list[dict]:
        self.initialize()
        with self.connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, filename, display_name, storage_path, mime_type, size_bytes, status,
                       created_at, page_count, chunks_created, extraction_mode, quality_status,
                       summary, preview_text, category, last_used_at
                FROM documents
                WHERE user_id = %s
                ORDER BY created_at DESC
                """,
                (user_id or DEFAULT_USER_ID,),
            )
            rows = cur.fetchall()
            items: list[dict] = []
            for row in rows:
                cur.execute(
                    "SELECT COUNT(*) AS usage_count, MAX(updated_at) AS last_chat_use FROM chats WHERE active_document_id = %s AND user_id = %s",
                    (row["id"], user_id or DEFAULT_USER_ID),
                )
                usage = cur.fetchone() or {"usage_count": 0, "last_chat_use": None}
                items.append(
                    {
                        "id": row["id"],
                        "filename": row["filename"],
                        "display_name": row["display_name"],
                        "storage_path": row["storage_path"],
                        "mime_type": row["mime_type"],
                        "size_bytes": int(row["size_bytes"]),
                        "status": row["status"],
                        "created_at": row["created_at"].isoformat(),
                        "page_count": int(row["page_count"] or 0),
                        "chunks_created": int(row["chunks_created"] or 0),
                        "extraction_mode": row["extraction_mode"],
                        "quality_status": row["quality_status"],
                        "summary": row["summary"],
                        "preview_text": row["preview_text"],
                        "category": row["category"],
                        "usage_count": int(usage["usage_count"] or 0),
                        "last_used_at": (
                            row["last_used_at"].isoformat()
                            if row["last_used_at"]
                            else (
                                usage["last_chat_use"].isoformat()
                                if usage["last_chat_use"]
                                else None
                            )
                        ),
                    }
                )
            return items

    def import_legal_documents(
        self,
        documents: list[dict[str, Any]],
        local_path_resolver: callable | None = None,
    ) -> int:
        self.initialize()
        imported = 0
        with self.connection() as conn, conn.cursor() as cur:
            for doc in documents:
                local_path = local_path_resolver(doc) if local_path_resolver else None
                source_invalid = bool(doc.get("source_invalid", False))
                if local_path and not Path(local_path).exists():
                    local_path = None
                now = utc_now_iso()
                doc_id = f"lexao:{doc.get('entity_slug', 'unknown')}:{doc.get('year', 'unknown')}:{doc.get('document_slug', 'unknown')}"
                cur.execute(
                    """
                    INSERT INTO legal_documents (
                        id, entity_slug, entity_name, year, document_slug, title, page_url,
                        download_pdf_url, matched_internal_slug, legal_branch_guess,
                        topic_route_guess, status, source_invalid, local_pdf_path,
                        metadata, created_at, updated_at
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s)
                    ON CONFLICT (id) DO UPDATE
                    SET title = EXCLUDED.title,
                        download_pdf_url = EXCLUDED.download_pdf_url,
                        matched_internal_slug = EXCLUDED.matched_internal_slug,
                        legal_branch_guess = EXCLUDED.legal_branch_guess,
                        topic_route_guess = EXCLUDED.topic_route_guess,
                        status = EXCLUDED.status,
                        source_invalid = EXCLUDED.source_invalid,
                        local_pdf_path = EXCLUDED.local_pdf_path,
                        metadata = EXCLUDED.metadata,
                        updated_at = EXCLUDED.updated_at
                    """,
                    (
                        doc_id,
                        doc.get("entity_slug"),
                        doc.get("entity_name"),
                        doc.get("year"),
                        doc.get("document_slug"),
                        doc.get("title") or "Documento",
                        doc.get("page_url"),
                        doc.get("download_pdf_url"),
                        doc.get("matched_internal_slug"),
                        doc.get("legal_branch_guess"),
                        doc.get("topic_route_guess"),
                        "source_invalid"
                        if source_invalid
                        else ("available" if local_path else "discovered"),
                        source_invalid,
                        local_path,
                        json.dumps(doc, ensure_ascii=False),
                        now,
                        now,
                    ),
                )
                imported += 1
        return imported

    def _segment_to_chunk(
        self, row: dict[str, Any], distance: float | None = None
    ) -> RetrievedChunk:
        return RetrievedChunk(
            chunk_id=row["id"],
            text=row["text_content"],
            source=row["source"],
            title=row["title"],
            link_original=row["link_original"],
            page=row["page"],
            article_number=row["article_number"],
            law_status=row["law_status"],
            distance=distance,
            source_scope=row["source_scope"],
            document_id=row["document_id"],
            metadata=row["metadata"] or {},
        )

    def upsert_legal_segments(self, items: list[dict[str, Any]]) -> int:
        self.initialize()
        if not items:
            return 0
        now = utc_now_iso()
        with self.connection() as conn, conn.cursor() as cur:
            for item in items:
                metadata = item["metadata"]
                refs = metadata.get("article_references") or []
                vector = _vector_literal(item.get("embedding"))
                pgvector = _pgvector_literal(item.get("embedding"))
                content_hash = hashlib.sha256(item["text"].encode("utf-8")).hexdigest()
                cur.execute(
                    """
                    INSERT INTO legal_segments (
                        id, legal_document_id, source, title, link_original, page, article_number,
                        article_main, article_references, law_status, source_scope, document_id,
                        diploma_slug, legal_branch, topic_route, text_content, metadata,
                        text_search, embedding, embedding_provider, embedding_model,
                        embedding_version, embedding_dimension, content_hash, created_at, updated_at
                    )
                    VALUES (
                        %s, NULL, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                        %s, %s::jsonb, to_tsvector('portuguese', %s),
                        %s::double precision[], %s, %s, %s, %s, %s, %s, %s
                    )
                    ON CONFLICT (id) DO UPDATE
                    SET source = EXCLUDED.source,
                        title = EXCLUDED.title,
                        link_original = EXCLUDED.link_original,
                        page = EXCLUDED.page,
                        article_number = EXCLUDED.article_number,
                        article_main = EXCLUDED.article_main,
                        article_references = EXCLUDED.article_references,
                        law_status = EXCLUDED.law_status,
                        source_scope = EXCLUDED.source_scope,
                        document_id = EXCLUDED.document_id,
                        diploma_slug = EXCLUDED.diploma_slug,
                        legal_branch = EXCLUDED.legal_branch,
                        topic_route = EXCLUDED.topic_route,
                        text_content = EXCLUDED.text_content,
                        metadata = EXCLUDED.metadata,
                        text_search = EXCLUDED.text_search,
                        embedding = EXCLUDED.embedding,
                        embedding_provider = EXCLUDED.embedding_provider,
                        embedding_model = EXCLUDED.embedding_model,
                        embedding_version = EXCLUDED.embedding_version,
                        embedding_dimension = EXCLUDED.embedding_dimension,
                        content_hash = EXCLUDED.content_hash,
                        updated_at = EXCLUDED.updated_at
                    """,
                    (
                        item["id"],
                        metadata.get("source", "Desconhecido"),
                        metadata.get("title", metadata.get("source", "Documento")),
                        metadata.get("link_original"),
                        metadata.get("page"),
                        metadata.get("article_number"),
                        metadata.get("article_main"),
                        refs,
                        metadata.get("law_status", "Nao verificado"),
                        metadata.get("source_scope", "official"),
                        metadata.get("document_id"),
                        metadata.get("diploma_slug"),
                        metadata.get("legal_branch"),
                        metadata.get("topic_route"),
                        item["text"],
                        json.dumps(metadata, ensure_ascii=False),
                        item["text"],
                        vector,
                        metadata.get("embedding_provider"),
                        metadata.get("embedding_model"),
                        metadata.get("embedding_version"),
                        metadata.get("embedding_dimension") or len(item.get("embedding") or []),
                        content_hash,
                        now,
                        now,
                    ),
                )
                if self._pgvector_available and pgvector:
                    cur.execute(
                        "UPDATE legal_segments SET embedding_vector = %s::vector WHERE id = %s",
                        (pgvector, item["id"]),
                    )
        return len(items)

    def upsert_jurisprudence_cases(self, items: list[dict[str, Any]]) -> int:
        self.initialize()
        if not items:
            return 0
        now = utc_now_iso()
        with self.connection() as conn, conn.cursor() as cur:
            for item in items:
                cur.execute(
                    """
                    INSERT INTO jurisprudence_cases (
                        id, court, chamber, case_number, title, decision_date,
                        publication_date, url, pdf_url, legal_branch, topic_route,
                        summary, metadata, created_at, updated_at
                    )
                    VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                        %s::jsonb, %s, %s
                    )
                    ON CONFLICT (id) DO UPDATE
                    SET chamber = EXCLUDED.chamber,
                        case_number = EXCLUDED.case_number,
                        title = EXCLUDED.title,
                        decision_date = EXCLUDED.decision_date,
                        publication_date = EXCLUDED.publication_date,
                        url = EXCLUDED.url,
                        pdf_url = EXCLUDED.pdf_url,
                        legal_branch = EXCLUDED.legal_branch,
                        topic_route = EXCLUDED.topic_route,
                        summary = EXCLUDED.summary,
                        metadata = EXCLUDED.metadata,
                        updated_at = EXCLUDED.updated_at
                    """,
                    (
                        item["id"],
                        item["court"],
                        item.get("chamber"),
                        item.get("case_number"),
                        item["title"],
                        item.get("decision_date"),
                        item.get("publication_date"),
                        item["url"],
                        item.get("pdf_url"),
                        item.get("legal_branch"),
                        item.get("topic_route"),
                        item.get("summary"),
                        json.dumps(item.get("metadata") or {}, ensure_ascii=False),
                        now,
                        now,
                    ),
                )
        return len(items)

    def available_diploma_slugs(self) -> set[str]:
        self.initialize()
        with self.connection() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT DISTINCT diploma_slug FROM legal_segments WHERE diploma_slug IS NOT NULL"
            )
            return {
                row["diploma_slug"] for row in cur.fetchall() if row.get("diploma_slug")
            }

    async def query_legal_segments(
        self, query: str, k: int, where: dict[str, Any] | None = None
    ) -> list[RetrievedChunk]:
        self.initialize()
        from app.services.rag.embeddings import embedding_service

        cache_key = f"{embedding_service.model_version}:{query.strip().casefold()}"
        cached_emb = _embedding_cache.get(cache_key)
        if cached_emb is not None:
            query_vec = cached_emb
        else:
            try:
                query_embedding = await embedding_service.embed_query(query)
                query_vec = list(query_embedding) if query_embedding else None
                if query_vec:
                    _embedding_cache[cache_key] = query_vec
            except Exception as exc:
                logger.warning("Embedding query failed; falling back to lexical retrieval: %s", exc)
                query_vec = None
        lexical_rows, dense_rows = await asyncio.gather(
            asyncio.to_thread(self._query_legal_segments_lexical, query, k, where),
            asyncio.to_thread(
                self._query_legal_segments_dense,
                query_vec,
                k,
                where,
                embedding_service.provider,
                embedding_service.model_name,
            ),
        )
        exact_article_query = bool(
            re.search(r"\b(?:art(?:igo)?\.?\s*)\d+", query, re.IGNORECASE)
        )
        fused = self._reciprocal_rank_fusion(
            lexical_rows,
            dense_rows,
            limit=max(1, k),
            lexical_weight=1.2 if exact_article_query else 1.0,
            dense_weight=1.0 if exact_article_query else 1.35,
        )
        return [
            self._segment_to_chunk(row, distance=row.get("distance")) for row in fused
        ]

    @staticmethod
    def _where_sql(where: dict[str, Any] | None) -> tuple[list[str], list[Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        for key, value in (where or {}).items():
            if key.startswith("metadata__"):
                clauses.append("metadata ->> %s = %s")
                params.extend([key.split("__", 1)[1], str(value)])
            else:
                clauses.append(f"{key} = %s")
                params.append(value)
        return clauses, params

    def _query_legal_segments_lexical(
        self, query: str, k: int, where: dict[str, Any] | None
    ) -> list[dict[str, Any]]:
        clauses, where_params = self._where_sql(where)
        fts_query = _build_fts_or_query(query)
        if fts_query:
            clauses.append("text_search @@ to_tsquery('portuguese', %s)")
            where_params.append(fts_query)
        sql = """
            SELECT id, source, title, link_original, page, article_number, law_status,
                   source_scope, document_id, metadata, text_content,
                   ts_rank_cd(text_search, websearch_to_tsquery('portuguese', %s), 32) AS lexical_rank,
                   NULL::double precision AS distance
            FROM legal_segments
        """
        sql += " WHERE " + (" AND ".join(clauses) if clauses else "TRUE")
        sql += " ORDER BY lexical_rank DESC, page ASC NULLS LAST LIMIT %s"
        params = [query, *where_params, max(20, k * 8)]
        with self.connection() as conn, conn.cursor() as cur:
            cur.execute(sql, params)
            return list(cur.fetchall())

    def _query_legal_segments_dense(
        self,
        query_vec: list[float] | None,
        k: int,
        where: dict[str, Any] | None,
        provider: str,
        model: str,
    ) -> list[dict[str, Any]]:
        if not query_vec:
            return []
        clauses, params = self._where_sql(where)
        clauses.extend(
            [
                "embedding_provider = %s",
                "embedding_model = %s",
                "embedding_dimension = %s",
            ]
        )
        params.extend([provider, model, len(query_vec)])
        if not self._pgvector_available:
            return self._query_legal_segments_dense_array(
                query_vec, k, clauses, params
            )
        clauses.append("embedding_vector IS NOT NULL")
        vector = _pgvector_literal(query_vec)
        sql = """
            SELECT id, source, title, link_original, page, article_number, law_status,
                   source_scope, document_id, metadata, text_content,
                   0::double precision AS lexical_rank,
                   embedding_vector <=> %s::vector AS distance
            FROM legal_segments
        """
        sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY embedding_vector <=> %s::vector LIMIT %s"
        query_params = [vector, *params, vector, max(20, k * 8)]
        with self.connection() as conn, conn.cursor() as cur:
            cur.execute(sql, query_params)
            return list(cur.fetchall())

    def _query_legal_segments_dense_array(
        self,
        query_vec: list[float],
        k: int,
        clauses: list[str],
        params: list[Any],
    ) -> list[dict[str, Any]]:
        import numpy as np

        sql = """
            SELECT id, source, title, link_original, page, article_number, law_status,
                   source_scope, document_id, metadata, text_content, embedding,
                   0::double precision AS lexical_rank
            FROM legal_segments
        """
        clauses = [*clauses, "embedding IS NOT NULL"]
        sql += " WHERE " + " AND ".join(clauses)
        with self.connection() as conn, conn.cursor() as cur:
            cur.execute(sql, params)
            rows = list(cur.fetchall())
        query_array = np.asarray(query_vec, dtype=np.float32)
        query_norm = float(np.linalg.norm(query_array))
        for row in rows:
            vector = np.asarray(row.pop("embedding"), dtype=np.float32)
            denominator = query_norm * float(np.linalg.norm(vector))
            similarity = float(np.dot(query_array, vector)) / denominator if denominator else 0.0
            row["distance"] = 1.0 - similarity
        rows.sort(key=lambda row: float(row.get("distance", 1.0)))
        return rows[: max(20, k * 8)]

    @staticmethod
    def _reciprocal_rank_fusion(
        lexical_rows: list[dict[str, Any]],
        dense_rows: list[dict[str, Any]],
        limit: int,
        rank_constant: int = 60,
        lexical_weight: float = 1.15,
        dense_weight: float = 1.0,
    ) -> list[dict[str, Any]]:
        scores: dict[str, float] = {}
        rows: dict[str, dict[str, Any]] = {}
        for weight, ranked in (
            (lexical_weight, lexical_rows),
            (dense_weight, dense_rows),
        ):
            for rank, row in enumerate(ranked, start=1):
                chunk_id = str(row["id"])
                rows.setdefault(chunk_id, row)
                scores[chunk_id] = scores.get(chunk_id, 0.0) + weight / (
                    rank_constant + rank
                )
                if row.get("distance") is not None:
                    rows[chunk_id]["distance"] = row["distance"]
        ordered_ids = sorted(scores, key=scores.get, reverse=True)[:limit]
        for chunk_id in ordered_ids:
            rows[chunk_id]["rrf_score"] = scores[chunk_id]
        return [rows[chunk_id] for chunk_id in ordered_ids]

    def _query_legal_segments_sync(
        self,
        query_vec: list[float] | None,
        k: int,
        sql: str,
        params: list[Any],
    ) -> list[RetrievedChunk]:
        import numpy as np

        with self.connection() as conn, conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()

        scored: list[tuple[float, float, dict[str, Any]]] = []
        query_np = (
            np.array(query_vec, dtype=np.float32) if query_vec is not None else None
        )
        query_norm = float(np.linalg.norm(query_np)) if query_np is not None else 0.0

        for row in rows:
            emb = row.get("embedding")
            if query_np is not None and emb is not None and query_norm > 0:
                emb_list = list(emb) if not isinstance(emb, list) else emb
                emb_arr = np.array(emb_list, dtype=np.float32)
                emb_norm = float(np.linalg.norm(emb_arr))
                if emb_norm > 0:
                    sim = float(np.dot(query_np, emb_arr)) / (query_norm * emb_norm)
                    distance = 1.0 - sim
                else:
                    distance = 1.0
            else:
                distance = 1.0
            lexical = float(row["lexical_rank"] or 0)
            scored.append((distance, -lexical, row))

        scored.sort(key=lambda x: (x[0], x[1]))
        chunks: list[RetrievedChunk] = []
        for distance, _lexical_neg, row in scored[: max(1, k)]:
            chunks.append(self._segment_to_chunk(row, distance=float(distance)))
        return chunks

    def get_branch_prototypes(self) -> dict[str, Any]:
        self.initialize()
        with self.connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT legal_branch, embedding
                FROM legal_segments
                WHERE legal_branch IS NOT NULL AND embedding IS NOT NULL
                    """
                )
            rows = cur.fetchall()
        branch_embeddings: dict[str, list[list[float]]] = {}
        for row in rows:
            branch = row["legal_branch"] or "indeterminado"
            emb = row["embedding"]
            if emb is None:
                continue
            branch_embeddings.setdefault(branch, []).append(list(emb))
        import numpy as np

        prototypes: dict[str, Any] = {}
        for branch, embs in branch_embeddings.items():
            if embs:
                prototypes[branch] = np.mean(np.array(embs, dtype=np.float32), axis=0)
        return prototypes

    def get_document_chunks(
        self, document_id: str, limit: int = 8
    ) -> list[RetrievedChunk]:
        self.initialize()
        with self.connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, source, title, link_original, page, article_number, law_status,
                       source_scope, document_id, metadata, text_content
                FROM legal_segments
                WHERE document_id = %s
                ORDER BY page ASC NULLS LAST, id ASC
                LIMIT %s
                """,
                (document_id, limit),
            )
            return [self._segment_to_chunk(row) for row in cur.fetchall()]

    def delete_document_chunks(self, document_id: str) -> None:
        self.initialize()
        with self.connection() as conn, conn.cursor() as cur:
            cur.execute(
                "DELETE FROM legal_segments WHERE document_id = %s", (document_id,)
            )

    def delete_segments_by_metadata(self, **metadata_filters: Any) -> int:
        self.initialize()
        clauses: list[str] = []
        params: list[Any] = []
        for key, value in metadata_filters.items():
            clauses.append(f"{key} = %s")
            params.append(value)
        sql = "DELETE FROM legal_segments"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        with self.connection() as conn, conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.rowcount

    def count_segments_by_metadata(self, **metadata_filters: Any) -> int:
        self.initialize()
        clauses: list[str] = []
        params: list[Any] = []
        for key, value in metadata_filters.items():
            clauses.append(f"{key} = %s")
            params.append(value)
        sql = "SELECT COUNT(*) AS total FROM legal_segments"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        with self.connection() as conn, conn.cursor() as cur:
            cur.execute(sql, params)
            row = cur.fetchone()
            return int(row["total"] or 0)

    def find_article_chunks(
        self,
        diploma_slug: str,
        article_number: str,
        expected_page: int | None = None,
        limit: int = 8,
    ) -> list[RetrievedChunk]:
        self.initialize()
        normalized_target = str(article_number).replace(".", "").strip()
        if not diploma_slug or not normalized_target:
            return []
        sql = """
            SELECT id, source, title, link_original, page, article_number, law_status,
                   source_scope, document_id, metadata, text_content
            FROM legal_segments
            WHERE diploma_slug = %s
              AND (
                  article_main = %s
                  OR (
                      string_to_array(
                          regexp_replace(coalesce(article_number, ''), '\\s+', '', 'g'),
                          ','
                      ) @> ARRAY[%s]
                      AND coalesce(metadata->>'segmentation', '') = 'article_block'
                  )
              )
            ORDER BY page ASC NULLS LAST, id ASC
            LIMIT %s
        """
        with self.connection() as conn, conn.cursor() as cur:
            cur.execute(
                sql,
                (
                    diploma_slug,
                    normalized_target,
                    normalized_target,
                    limit,
                ),
            )
            return [self._segment_to_chunk(row) for row in cur.fetchall()]

    def get_legal_segment_stats(self) -> dict[str, Any]:
        self.initialize()
        with self.connection() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) AS total, MAX(updated_at) AS last_update FROM legal_segments"
            )
            row = cur.fetchone() or {"total": 0, "last_update": None}
            return {"total": int(row["total"] or 0), "last_update": row["last_update"]}

    def list_jurisprudence(
        self,
        court: str | None = None,
        legal_branch: str | None = None,
        search: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[dict[str, Any]], int]:
        self.initialize()
        conditions: list[str] = []
        params: list[Any] = []
        if court:
            conditions.append("court = %s")
            params.append(court)
        if legal_branch:
            conditions.append("legal_branch = %s")
            params.append(legal_branch)
        if search:
            conditions.append("(title ILIKE %s OR summary ILIKE %s OR case_number ILIKE %s)")
            like = f"%{search}%"
            params.extend([like, like, like])
        where_clause = " AND ".join(conditions) if conditions else "TRUE"
        with self.connection() as conn, conn.cursor() as cur:
            cur.execute(f"SELECT COUNT(*) FROM jurisprudence_cases WHERE {where_clause}", params)
            total = int(cur.fetchone()["count"] or 0)
            cur.execute(
                f"""
                SELECT id, court, chamber, case_number, title, decision_date,
                       publication_date, url, pdf_url, legal_branch, topic_route,
                       summary, metadata, created_at, updated_at
                FROM jurisprudence_cases
                WHERE {where_clause}
                ORDER BY publication_date DESC NULLS LAST, decision_date DESC NULLS LAST
                LIMIT %s OFFSET %s
                """,
                params + [limit, offset],
            )
            rows = [dict(row) for row in cur.fetchall()]
            return rows, total

    def get_jurisprudence_case(self, case_id: str) -> dict[str, Any] | None:
        self.initialize()
        with self.connection() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM jurisprudence_cases WHERE id = %s", (case_id,)
            )
            row = cur.fetchone()
            if not row:
                return None
            case = dict(row)
            cur.execute(
                "SELECT id, holding_text, legal_branch, topic_route, metadata, created_at "
                "FROM jurisprudence_holdings WHERE jurisprudence_case_id = %s "
                "ORDER BY created_at",
                (case_id,),
            )
            case["holdings"] = [dict(r) for r in cur.fetchall()]
            return case

    def list_jurisprudence_courts(self) -> list[str]:
        self.initialize()
        with self.connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT DISTINCT court FROM jurisprudence_cases ORDER BY court")
            return [row["court"] for row in cur.fetchall() if row.get("court")]

    def list_jurisprudence_branches(self) -> list[str]:
        self.initialize()
        with self.connection() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT DISTINCT legal_branch FROM jurisprudence_cases "
                "WHERE legal_branch IS NOT NULL ORDER BY legal_branch"
            )
            return [row["legal_branch"] for row in cur.fetchall() if row.get("legal_branch")]

    def insert_jurisprudence_case(self, cid: str, court: str, case_number: str, title: str,
                                   decision_date: str, legal_branch: str, summary: str, url: str) -> bool:
        self.initialize()
        try:
            with self.connection() as conn, conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO jurisprudence_cases (id, court, case_number, title, decision_date, legal_branch, summary, url) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                    (cid, court, case_number or None, title, decision_date or None, legal_branch or None, summary or None, url or None),
                )
                return True
        except Exception:
            return False

    def delete_jurisprudence_case(self, case_id: str) -> bool:
        self.initialize()
        with self.connection() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM jurisprudence_cases WHERE id = %s", (case_id,))
            return cur.rowcount > 0

    # ── Professional mode ─────────────────────────────────────────────

    def _format_professional_profile(self, row: dict | None, workspace: dict | None = None) -> dict | None:
        if not row:
            return None
        return {
            "user_id": row["user_id"],
            "status": row.get("status") or "inactive",
            "display_name": row.get("display_name") or "",
            "license_number": row.get("license_number") or "",
            "professional_title": row.get("professional_title") or "",
            "organization_name": row.get("organization_name") or "",
            "created_at": _iso(row.get("created_at")),
            "updated_at": _iso(row.get("updated_at")),
            "workspace": workspace,
        }

    def get_professional_profile(self, user_id: str) -> dict | None:
        self.initialize()
        with self.connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT user_id, status, display_name, license_number, professional_title,
                       organization_name, created_at, updated_at
                FROM professional_profiles
                WHERE user_id = %s
                """,
                (user_id,),
            )
            profile = cur.fetchone()
            if not profile:
                return None
            cur.execute(
                """
                SELECT workspaces.id, workspaces.name, workspaces.workspace_type,
                       members.member_role, workspaces.created_at, workspaces.updated_at
                FROM pro_workspace_members members
                INNER JOIN pro_workspaces workspaces ON workspaces.id = members.workspace_id
                WHERE members.user_id = %s AND members.status = 'active'
                ORDER BY workspaces.created_at ASC
                LIMIT 1
                """,
                (user_id,),
            )
            workspace = cur.fetchone()
            workspace_payload = None
            if workspace:
                workspace_payload = {
                    "id": workspace["id"],
                    "name": workspace["name"],
                    "workspace_type": workspace["workspace_type"],
                    "member_role": workspace["member_role"],
                    "created_at": _iso(workspace["created_at"]),
                    "updated_at": _iso(workspace["updated_at"]),
                }
            return self._format_professional_profile(dict(profile), workspace_payload)

    def _ensure_professional_workspace(
        self, cur: psycopg.Cursor, user_id: str, name: str | None = None
    ) -> str:
        cur.execute(
            """
            SELECT workspaces.id
            FROM pro_workspace_members members
            INNER JOIN pro_workspaces workspaces ON workspaces.id = members.workspace_id
            WHERE members.user_id = %s AND members.status = 'active'
            ORDER BY workspaces.created_at ASC
            LIMIT 1
            """,
            (user_id,),
        )
        existing = cur.fetchone()
        if existing:
            return existing["id"]
        workspace_id = str(uuid.uuid4())
        now = utc_now_iso()
        workspace_name = (name or "Escritório individual").strip() or "Escritório individual"
        cur.execute(
            """
            INSERT INTO pro_workspaces (id, owner_user_id, name, workspace_type, created_at, updated_at)
            VALUES (%s, %s, %s, 'individual', %s, %s)
            """,
            (workspace_id, user_id, workspace_name, now, now),
        )
        cur.execute(
            """
            INSERT INTO pro_workspace_members (id, workspace_id, user_id, member_role, status, created_at, updated_at)
            VALUES (%s, %s, %s, 'owner', 'active', %s, %s)
            ON CONFLICT (workspace_id, user_id) DO UPDATE
            SET status = 'active', updated_at = EXCLUDED.updated_at
            """,
            (str(uuid.uuid4()), workspace_id, user_id, now, now),
        )
        return workspace_id

    def set_professional_profile_admin(
        self,
        user_id: str,
        *,
        status: str = "active",
        display_name: str | None = None,
        license_number: str | None = None,
        professional_title: str | None = None,
        organization_name: str | None = None,
        actor_user_id: str | None = None,
    ) -> dict | None:
        self.initialize()
        if status not in {"active", "inactive", "suspended"}:
            return None
        with self.connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT id, name FROM users WHERE id = %s", (user_id,))
            user = cur.fetchone()
            if not user:
                return None
            now = utc_now_iso()
            cur.execute(
                """
                INSERT INTO professional_profiles (
                    user_id, status, display_name, license_number, professional_title,
                    organization_name, created_at, updated_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (user_id) DO UPDATE
                SET status = EXCLUDED.status,
                    display_name = COALESCE(EXCLUDED.display_name, professional_profiles.display_name),
                    license_number = COALESCE(EXCLUDED.license_number, professional_profiles.license_number),
                    professional_title = COALESCE(EXCLUDED.professional_title, professional_profiles.professional_title),
                    organization_name = COALESCE(EXCLUDED.organization_name, professional_profiles.organization_name),
                    updated_at = EXCLUDED.updated_at
                """,
                (
                    user_id,
                    status,
                    (display_name or user["name"] or "").strip() or None,
                    (license_number or "").strip() or None,
                    (professional_title or "").strip() or None,
                    (organization_name or "").strip() or None,
                    now,
                    now,
                ),
            )
            if status == "active":
                workspace_id = self._ensure_professional_workspace(
                    cur,
                    user_id,
                    organization_name or display_name or user["name"],
                )
                cur.execute(
                    """
                    INSERT INTO pro_activity_events (id, workspace_id, actor_user_id, event_type, payload_json, created_at)
                    VALUES (%s, %s, %s, 'professional_profile_activated', %s::jsonb, %s)
                    """,
                    (
                        str(uuid.uuid4()),
                        workspace_id,
                        actor_user_id,
                        json.dumps({"target_user_id": user_id, "status": status}, ensure_ascii=False),
                        now,
                    ),
                )
        return self.get_professional_profile(user_id)

    def get_professional_workspace_for_user(self, user_id: str) -> dict | None:
        self.initialize()
        profile = self.get_professional_profile(user_id)
        if not profile or profile.get("status") != "active" or not profile.get("workspace"):
            return None
        return profile["workspace"]

    def get_pro_admin_overview(self) -> dict:
        self.initialize()
        with self.connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT COUNT(*)::int AS total FROM professional_profiles WHERE status = 'active'")
            active_profiles = int((cur.fetchone() or {}).get("total", 0) or 0)
            cur.execute("SELECT COUNT(*)::int AS total FROM pro_clients WHERE status <> 'archived'")
            clients = int((cur.fetchone() or {}).get("total", 0) or 0)
            cur.execute("SELECT COUNT(*)::int AS total FROM pro_cases WHERE status <> 'archived'")
            cases = int((cur.fetchone() or {}).get("total", 0) or 0)
            cur.execute("SELECT COUNT(*)::int AS total FROM pro_deadlines WHERE status = 'open'")
            deadlines = int((cur.fetchone() or {}).get("total", 0) or 0)
        return {
            "active_profiles": active_profiles,
            "clients": clients,
            "cases": cases,
            "open_deadlines": deadlines,
        }

    def get_pro_dashboard(self, workspace_id: str) -> dict:
        self.initialize()
        with self.connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT COUNT(*)::int AS total FROM pro_clients WHERE workspace_id = %s AND status <> 'archived'", (workspace_id,))
            clients = int((cur.fetchone() or {}).get("total", 0) or 0)
            cur.execute("SELECT COUNT(*)::int AS total FROM pro_cases WHERE workspace_id = %s AND status <> 'archived'", (workspace_id,))
            cases = int((cur.fetchone() or {}).get("total", 0) or 0)
            cur.execute("SELECT COUNT(*)::int AS total FROM pro_tasks WHERE workspace_id = %s AND status <> 'done'", (workspace_id,))
            tasks = int((cur.fetchone() or {}).get("total", 0) or 0)
            cur.execute("SELECT COUNT(*)::int AS total FROM pro_deadlines WHERE workspace_id = %s AND status = 'open'", (workspace_id,))
            deadlines = int((cur.fetchone() or {}).get("total", 0) or 0)
            cur.execute(
                """
                SELECT cases.id, cases.title, cases.status, cases.priority, cases.next_deadline_at,
                       clients.name AS client_name, cases.updated_at
                FROM pro_cases cases
                LEFT JOIN pro_clients clients ON clients.id = cases.client_id
                WHERE cases.workspace_id = %s AND cases.status <> 'archived'
                ORDER BY cases.updated_at DESC
                LIMIT 5
                """,
                (workspace_id,),
            )
            recent_cases = [
                {
                    **dict(row),
                    "next_deadline_at": _iso(row.get("next_deadline_at")),
                    "updated_at": _iso(row.get("updated_at")),
                }
                for row in cur.fetchall()
            ]
        return {
            "totals": {
                "clients": clients,
                "cases": cases,
                "open_tasks": tasks,
                "open_deadlines": deadlines,
            },
            "recent_cases": recent_cases,
        }

    def _add_pro_activity(
        self,
        cur: psycopg.Cursor,
        workspace_id: str,
        actor_user_id: str,
        event_type: str,
        payload: dict[str, Any] | None = None,
        case_id: str | None = None,
    ) -> None:
        cur.execute(
            """
            INSERT INTO pro_activity_events (id, workspace_id, case_id, actor_user_id, event_type, payload_json, created_at)
            VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s)
            """,
            (
                str(uuid.uuid4()),
                workspace_id,
                case_id,
                actor_user_id,
                event_type,
                json.dumps(payload or {}, ensure_ascii=False),
                utc_now_iso(),
            ),
        )

    def list_pro_clients(self, workspace_id: str, search: str | None = None) -> list[dict]:
        self.initialize()
        params: list[Any] = [workspace_id]
        search_clause = ""
        if search and search.strip():
            search_clause = "AND (name ILIKE %s OR email ILIKE %s OR phone ILIKE %s OR conflict_terms ILIKE %s)"
            term = f"%{search.strip()}%"
            params.extend([term, term, term, term])
        with self.connection() as conn, conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT id, client_type, name, email, phone, identification_number, address,
                       notes, conflict_terms, status, created_at, updated_at
                FROM pro_clients
                WHERE workspace_id = %s {search_clause}
                ORDER BY updated_at DESC
                """,
                params,
            )
            return [
                {
                    **dict(row),
                    "created_at": _iso(row.get("created_at")),
                    "updated_at": _iso(row.get("updated_at")),
                }
                for row in cur.fetchall()
            ]

    def upsert_pro_client(self, workspace_id: str, user_id: str, payload: dict[str, Any], client_id: str | None = None) -> dict:
        self.initialize()
        now = utc_now_iso()
        client_id = client_id or str(uuid.uuid4())
        with self.connection() as conn, conn.cursor() as cur:
            if payload.get("id"):
                client_id = str(payload["id"])
            cur.execute(
                """
                INSERT INTO pro_clients (
                    id, workspace_id, owner_user_id, client_type, name, email, phone,
                    identification_number, address, notes, conflict_terms, status, created_at, updated_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (id) DO UPDATE
                SET client_type = EXCLUDED.client_type,
                    name = EXCLUDED.name,
                    email = EXCLUDED.email,
                    phone = EXCLUDED.phone,
                    identification_number = EXCLUDED.identification_number,
                    address = EXCLUDED.address,
                    notes = EXCLUDED.notes,
                    conflict_terms = EXCLUDED.conflict_terms,
                    status = EXCLUDED.status,
                    updated_at = EXCLUDED.updated_at
                WHERE pro_clients.workspace_id = EXCLUDED.workspace_id
                RETURNING id
                """,
                (
                    client_id,
                    workspace_id,
                    user_id,
                    payload.get("client_type") or "individual",
                    (payload.get("name") or "").strip(),
                    (payload.get("email") or "").strip() or None,
                    (payload.get("phone") or "").strip() or None,
                    (payload.get("identification_number") or "").strip() or None,
                    (payload.get("address") or "").strip() or None,
                    (payload.get("notes") or "").strip() or None,
                    (payload.get("conflict_terms") or "").strip() or None,
                    payload.get("status") or "active",
                    now,
                    now,
                ),
            )
            if not cur.fetchone():
                raise ValueError("Cliente nao encontrado")
            self._add_pro_activity(cur, workspace_id, user_id, "client_saved", {"client_id": client_id, "name": payload.get("name")})
        return self.get_pro_client(workspace_id, client_id) or {}

    def get_pro_client(self, workspace_id: str, client_id: str) -> dict | None:
        rows = self.list_pro_clients(workspace_id)
        return next((row for row in rows if row["id"] == client_id), None)

    def archive_pro_client(self, workspace_id: str, user_id: str, client_id: str) -> bool:
        self.initialize()
        with self.connection() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE pro_clients SET status = 'archived', updated_at = %s WHERE id = %s AND workspace_id = %s",
                (utc_now_iso(), client_id, workspace_id),
            )
            ok = cur.rowcount > 0
            if ok:
                self._add_pro_activity(cur, workspace_id, user_id, "client_archived", {"client_id": client_id})
            return ok

    def list_pro_cases(self, workspace_id: str, search: str | None = None, status: str | None = None) -> list[dict]:
        self.initialize()
        params: list[Any] = [workspace_id]
        clauses = ["cases.workspace_id = %s"]
        if status:
            clauses.append("cases.status = %s")
            params.append(status)
        if search and search.strip():
            clauses.append("(cases.title ILIKE %s OR cases.case_number ILIKE %s OR clients.name ILIKE %s OR cases.opposing_party ILIKE %s)")
            term = f"%{search.strip()}%"
            params.extend([term, term, term, term])
        with self.connection() as conn, conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT cases.id, cases.client_id, cases.title, cases.case_number, cases.court,
                       cases.opposing_party, cases.legal_branch, cases.status, cases.priority,
                       cases.opened_at, cases.next_deadline_at, cases.summary, cases.metadata,
                       cases.created_at, cases.updated_at, clients.name AS client_name
                FROM pro_cases cases
                LEFT JOIN pro_clients clients ON clients.id = cases.client_id
                WHERE {" AND ".join(clauses)}
                ORDER BY cases.updated_at DESC
                """,
                params,
            )
            return [
                {
                    **dict(row),
                    "opened_at": _iso(row.get("opened_at")),
                    "next_deadline_at": _iso(row.get("next_deadline_at")),
                    "created_at": _iso(row.get("created_at")),
                    "updated_at": _iso(row.get("updated_at")),
                    "metadata": row.get("metadata") or {},
                }
                for row in cur.fetchall()
            ]

    def get_pro_case(self, workspace_id: str, case_id: str) -> dict | None:
        cases = self.list_pro_cases(workspace_id)
        return next((case for case in cases if case["id"] == case_id), None)

    def upsert_pro_case(self, workspace_id: str, user_id: str, payload: dict[str, Any], case_id: str | None = None) -> dict:
        self.initialize()
        now = utc_now_iso()
        case_id = case_id or str(uuid.uuid4())
        with self.connection() as conn, conn.cursor() as cur:
            client_id = payload.get("client_id") or None
            if client_id:
                cur.execute("SELECT 1 FROM pro_clients WHERE id = %s AND workspace_id = %s", (client_id, workspace_id))
                if not cur.fetchone():
                    raise ValueError("Cliente nao encontrado")
            cur.execute(
                """
                INSERT INTO pro_cases (
                    id, workspace_id, client_id, owner_user_id, title, case_number, court,
                    opposing_party, legal_branch, status, priority, opened_at,
                    next_deadline_at, summary, metadata, created_at, updated_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s)
                ON CONFLICT (id) DO UPDATE
                SET client_id = EXCLUDED.client_id,
                    title = EXCLUDED.title,
                    case_number = EXCLUDED.case_number,
                    court = EXCLUDED.court,
                    opposing_party = EXCLUDED.opposing_party,
                    legal_branch = EXCLUDED.legal_branch,
                    status = EXCLUDED.status,
                    priority = EXCLUDED.priority,
                    opened_at = EXCLUDED.opened_at,
                    next_deadline_at = EXCLUDED.next_deadline_at,
                    summary = EXCLUDED.summary,
                    metadata = EXCLUDED.metadata,
                    updated_at = EXCLUDED.updated_at
                WHERE pro_cases.workspace_id = EXCLUDED.workspace_id
                RETURNING id
                """,
                (
                    case_id,
                    workspace_id,
                    client_id,
                    user_id,
                    (payload.get("title") or "").strip(),
                    (payload.get("case_number") or "").strip() or None,
                    (payload.get("court") or "").strip() or None,
                    (payload.get("opposing_party") or "").strip() or None,
                    (payload.get("legal_branch") or "").strip() or None,
                    payload.get("status") or "open",
                    payload.get("priority") or "normal",
                    payload.get("opened_at") or None,
                    payload.get("next_deadline_at") or None,
                    (payload.get("summary") or "").strip() or None,
                    json.dumps(payload.get("metadata") or {}, ensure_ascii=False),
                    now,
                    now,
                ),
            )
            if not cur.fetchone():
                raise ValueError("Caso nao encontrado")
            self._add_pro_activity(cur, workspace_id, user_id, "case_saved", {"case_id": case_id, "title": payload.get("title")}, case_id)
        return self.get_pro_case(workspace_id, case_id) or {}

    def archive_pro_case(self, workspace_id: str, user_id: str, case_id: str) -> bool:
        self.initialize()
        with self.connection() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE pro_cases SET status = 'archived', updated_at = %s WHERE id = %s AND workspace_id = %s",
                (utc_now_iso(), case_id, workspace_id),
            )
            ok = cur.rowcount > 0
            if ok:
                self._add_pro_activity(cur, workspace_id, user_id, "case_archived", {"case_id": case_id}, case_id)
            return ok

    def link_pro_case_chat(self, workspace_id: str, user_id: str, case_id: str, chat_id: str) -> bool:
        self.initialize()
        with self.connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT 1 FROM pro_cases WHERE id = %s AND workspace_id = %s", (case_id, workspace_id))
            if not cur.fetchone():
                return False
            cur.execute("SELECT 1 FROM chats WHERE id = %s AND user_id = %s", (chat_id, user_id))
            if not cur.fetchone():
                return False
            cur.execute(
                """
                INSERT INTO pro_case_chats (case_id, chat_id, linked_by, created_at)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (case_id, chat_id) DO NOTHING
                """,
                (case_id, chat_id, user_id, utc_now_iso()),
            )
            self._add_pro_activity(cur, workspace_id, user_id, "chat_linked", {"chat_id": chat_id}, case_id)
            return True

    def list_pro_case_chats(self, workspace_id: str, case_id: str) -> list[dict]:
        self.initialize()
        with self.connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT 1 FROM pro_cases WHERE id = %s AND workspace_id = %s", (case_id, workspace_id))
            if not cur.fetchone():
                return []
            cur.execute(
                """
                SELECT chats.id, chats.title, chats.created_at, chats.updated_at,
                       links.created_at AS linked_at,
                       COUNT(messages.id)::int AS message_count
                FROM pro_case_chats links
                INNER JOIN chats ON chats.id = links.chat_id
                LEFT JOIN chat_messages messages ON messages.chat_id = chats.id
                WHERE links.case_id = %s
                GROUP BY chats.id, links.created_at
                ORDER BY links.created_at DESC
                """,
                (case_id,),
            )
            return [
                {
                    **dict(row),
                    "created_at": _iso(row.get("created_at")),
                    "updated_at": _iso(row.get("updated_at")),
                    "linked_at": _iso(row.get("linked_at")),
                }
                for row in cur.fetchall()
            ]

    def get_pro_case_context_for_chat(self, chat_id: str, user_id: str) -> dict | None:
        self.initialize()
        with self.connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT cases.id, cases.workspace_id, cases.title, cases.case_number, cases.court,
                       cases.opposing_party, cases.legal_branch, cases.status,
                       cases.priority, cases.summary, cases.next_deadline_at,
                       clients.name AS client_name, clients.email AS client_email,
                       clients.phone AS client_phone, clients.identification_number AS client_identification_number,
                       clients.address AS client_address, clients.notes AS client_notes,
                       clients.conflict_terms AS client_conflict_terms
                FROM pro_case_chats links
                INNER JOIN pro_cases cases ON cases.id = links.case_id
                INNER JOIN chats ON chats.id = links.chat_id
                LEFT JOIN pro_clients clients ON clients.id = cases.client_id
                WHERE links.chat_id = %s AND chats.user_id = %s
                ORDER BY links.created_at DESC
                LIMIT 1
                """,
                (chat_id, user_id),
            )
            row = cur.fetchone()
            if not row:
                return None
            payload = dict(row)
            payload["next_deadline_at"] = _iso(payload.get("next_deadline_at"))
            return payload

    def list_pro_case_documents(self, workspace_id: str, case_id: str) -> list[dict]:
        self.initialize()
        with self.connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT 1 FROM pro_cases WHERE id = %s AND workspace_id = %s", (case_id, workspace_id))
            if not cur.fetchone():
                return []
            cur.execute(
                """
                SELECT docs.id, docs.display_name, docs.filename, docs.mime_type, docs.status,
                       docs.created_at, links.label, links.created_at AS linked_at
                FROM pro_case_documents links
                INNER JOIN documents docs ON docs.id = links.document_id
                WHERE links.case_id = %s
                ORDER BY links.created_at DESC
                """,
                (case_id,),
            )
            return [
                {
                    **dict(row),
                    "created_at": _iso(row.get("created_at")),
                    "linked_at": _iso(row.get("linked_at")),
                }
                for row in cur.fetchall()
            ]

    def link_pro_case_document(self, workspace_id: str, user_id: str, case_id: str, document_id: str, label: str | None = None) -> bool:
        self.initialize()
        with self.connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT 1 FROM pro_cases WHERE id = %s AND workspace_id = %s", (case_id, workspace_id))
            if not cur.fetchone():
                return False
            cur.execute("SELECT 1 FROM documents WHERE id = %s AND user_id = %s", (document_id, user_id))
            if not cur.fetchone():
                return False
            cur.execute(
                """
                INSERT INTO pro_case_documents (case_id, document_id, label, added_by, created_at)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (case_id, document_id) DO UPDATE
                SET label = EXCLUDED.label
                """,
                (case_id, document_id, (label or "").strip() or None, user_id, utc_now_iso()),
            )
            self._add_pro_activity(cur, workspace_id, user_id, "document_linked", {"document_id": document_id}, case_id)
            return True

    def list_pro_tasks(self, workspace_id: str, case_id: str) -> list[dict]:
        return self._list_case_records("pro_tasks", workspace_id, case_id)

    def list_pro_deadlines(self, workspace_id: str, case_id: str) -> list[dict]:
        return self._list_case_records("pro_deadlines", workspace_id, case_id)

    def _list_case_records(self, table: str, workspace_id: str, case_id: str) -> list[dict]:
        self.initialize()
        with self.connection() as conn, conn.cursor() as cur:
            cur.execute(
                f"SELECT * FROM {table} WHERE workspace_id = %s AND case_id = %s ORDER BY created_at DESC",
                (workspace_id, case_id),
            )
            rows = []
            for row in cur.fetchall():
                payload = dict(row)
                for key in ("created_at", "updated_at", "due_at"):
                    if key in payload:
                        payload[key] = _iso(payload[key])
                return_payload = payload
                rows.append(return_payload)
            return rows

    def upsert_pro_task(self, workspace_id: str, user_id: str, case_id: str, payload: dict[str, Any], task_id: str | None = None) -> dict:
        self.initialize()
        task_id = task_id or str(uuid.uuid4())
        now = utc_now_iso()
        with self.connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT 1 FROM pro_cases WHERE id = %s AND workspace_id = %s", (case_id, workspace_id))
            if not cur.fetchone():
                raise ValueError("Caso nao encontrado")
            cur.execute(
                """
                INSERT INTO pro_tasks (id, workspace_id, case_id, title, description, status, priority, due_at, assigned_to, created_by, created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (id) DO UPDATE
                SET title = EXCLUDED.title, description = EXCLUDED.description, status = EXCLUDED.status,
                    priority = EXCLUDED.priority, due_at = EXCLUDED.due_at, assigned_to = EXCLUDED.assigned_to,
                    updated_at = EXCLUDED.updated_at
                WHERE pro_tasks.workspace_id = EXCLUDED.workspace_id
                RETURNING id
                """,
                (
                    task_id,
                    workspace_id,
                    case_id,
                    (payload.get("title") or "").strip(),
                    (payload.get("description") or "").strip() or None,
                    payload.get("status") or "pending",
                    payload.get("priority") or "normal",
                    payload.get("due_at") or None,
                    payload.get("assigned_to") or None,
                    user_id,
                    now,
                    now,
                ),
            )
            if not cur.fetchone():
                raise ValueError("Tarefa nao encontrada")
            self._add_pro_activity(cur, workspace_id, user_id, "task_saved", {"task_id": task_id, "title": payload.get("title")}, case_id)
        return next((row for row in self.list_pro_tasks(workspace_id, case_id) if row["id"] == task_id), {})

    def upsert_pro_deadline(self, workspace_id: str, user_id: str, case_id: str, payload: dict[str, Any], deadline_id: str | None = None) -> dict:
        self.initialize()
        deadline_id = deadline_id or str(uuid.uuid4())
        now = utc_now_iso()
        with self.connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT 1 FROM pro_cases WHERE id = %s AND workspace_id = %s", (case_id, workspace_id))
            if not cur.fetchone():
                raise ValueError("Caso nao encontrado")
            cur.execute(
                """
                INSERT INTO pro_deadlines (id, workspace_id, case_id, title, due_at, source, status, reminder_days, created_by, created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (id) DO UPDATE
                SET title = EXCLUDED.title, due_at = EXCLUDED.due_at, source = EXCLUDED.source,
                    status = EXCLUDED.status, reminder_days = EXCLUDED.reminder_days,
                    updated_at = EXCLUDED.updated_at
                WHERE pro_deadlines.workspace_id = EXCLUDED.workspace_id
                RETURNING id
                """,
                (
                    deadline_id,
                    workspace_id,
                    case_id,
                    (payload.get("title") or "").strip(),
                    payload.get("due_at"),
                    (payload.get("source") or "").strip() or None,
                    payload.get("status") or "open",
                    int(payload.get("reminder_days") or 3),
                    user_id,
                    now,
                    now,
                ),
            )
            if not cur.fetchone():
                raise ValueError("Prazo nao encontrado")
            self._add_pro_activity(cur, workspace_id, user_id, "deadline_saved", {"deadline_id": deadline_id, "title": payload.get("title")}, case_id)
        return next((row for row in self.list_pro_deadlines(workspace_id, case_id) if row["id"] == deadline_id), {})

    def list_pro_notes(self, workspace_id: str, case_id: str) -> list[dict]:
        self.initialize()
        with self.connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT notes.id, notes.body, notes.visibility, notes.created_at, notes.updated_at,
                       users.name AS author_name
                FROM pro_notes notes
                LEFT JOIN users ON users.id = notes.author_user_id
                WHERE notes.workspace_id = %s AND notes.case_id = %s
                ORDER BY notes.created_at DESC
                """,
                (workspace_id, case_id),
            )
            return [
                {
                    **dict(row),
                    "created_at": _iso(row.get("created_at")),
                    "updated_at": _iso(row.get("updated_at")),
                }
                for row in cur.fetchall()
            ]

    def create_pro_note(self, workspace_id: str, user_id: str, case_id: str, payload: dict[str, Any]) -> dict:
        self.initialize()
        note_id = str(uuid.uuid4())
        now = utc_now_iso()
        with self.connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT 1 FROM pro_cases WHERE id = %s AND workspace_id = %s", (case_id, workspace_id))
            if not cur.fetchone():
                raise ValueError("Caso nao encontrado")
            cur.execute(
                """
                INSERT INTO pro_notes (id, workspace_id, case_id, author_user_id, body, visibility, created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    note_id,
                    workspace_id,
                    case_id,
                    user_id,
                    (payload.get("body") or "").strip(),
                    payload.get("visibility") or "internal",
                    now,
                    now,
                ),
            )
            self._add_pro_activity(cur, workspace_id, user_id, "note_created", {"note_id": note_id}, case_id)
        return next((row for row in self.list_pro_notes(workspace_id, case_id) if row["id"] == note_id), {})

    def list_pro_timeline(self, workspace_id: str, case_id: str) -> list[dict]:
        self.initialize()
        with self.connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT events.id, events.event_type, events.payload_json, events.created_at,
                       users.name AS actor_name
                FROM pro_activity_events events
                LEFT JOIN users ON users.id = events.actor_user_id
                WHERE events.workspace_id = %s AND (events.case_id = %s OR events.case_id IS NULL)
                ORDER BY events.created_at DESC
                LIMIT 100
                """,
                (workspace_id, case_id),
            )
            return [
                {
                    "id": row["id"],
                    "event_type": row["event_type"],
                    "payload": row["payload_json"] or {},
                    "actor_name": row["actor_name"] or "Sistema",
                    "created_at": _iso(row["created_at"]),
                }
                for row in cur.fetchall()
            ]

    # ── Admin ──────────────────────────────────────────────────────────

    def get_admin_stats(self) -> dict:
        self.initialize()
        with self.connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) AS total FROM queries")
            total_queries = (cur.fetchone() or {}).get("total", 0) or 0
            cur.execute("SELECT COUNT(*) AS total FROM legal_segments")
            total_chunks = (cur.fetchone() or {}).get("total", 0) or 0
            cur.execute("SELECT COUNT(DISTINCT title) AS total FROM legal_segments WHERE source_scope = 'official'")
            total_docs = (cur.fetchone() or {}).get("total", 0) or 0
            cur.execute("SELECT COUNT(*) AS total FROM users WHERE is_seeded = FALSE")
            total_users = (cur.fetchone() or {}).get("total", 0) or 0
            cur.execute("SELECT COUNT(*) AS total FROM users")
            total_accounts = (cur.fetchone() or {}).get("total", 0) or 0
            cur.execute(
                "SELECT AVG(EXTRACT(EPOCH FROM (updated_at - created_at))) AS avg_time "
                "FROM chats WHERE created_at > NOW() - INTERVAL '7 days'"
            )
            avg_response = (cur.fetchone() or {}).get("avg_time", 0) or 0
            cur.execute("SELECT COUNT(*) AS total FROM chat_messages")
            total_messages = (cur.fetchone() or {}).get("total", 0) or 0
            return {
                "total_queries": int(total_queries),
                "total_chunks": int(total_chunks),
                "total_documents": int(total_docs),
                "total_users": int(total_users),
                "total_accounts": int(total_accounts),
                "total_messages": int(total_messages),
                "avg_response_time_s": round(float(avg_response), 2),
            }

    def get_seeded_user(self) -> dict | None:
        self.initialize()
        with self.connection() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT id, name, email, phone, password_hash, ai_preferences, is_seeded, role, created_at "
                "FROM users WHERE is_seeded = TRUE LIMIT 1"
            )
            row = cur.fetchone()
            return dict(row) if row else None

    def list_users(self) -> list[dict]:
        self.initialize()
        with self.connection() as conn, conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT
                    users.id,
                    users.name,
                    users.email,
                    users.phone,
                    users.role,
                    users.is_seeded,
                    users.created_at,
                    profiles.status AS professional_status,
                    profiles.display_name AS professional_display_name,
                    profiles.license_number AS professional_license_number,
                    profiles.professional_title AS professional_title,
                    profiles.organization_name AS professional_organization_name,
                    COALESCE(usage.messages_used_today, 0) AS messages_used_today
                FROM users
                LEFT JOIN professional_profiles profiles ON profiles.user_id = users.id
                LEFT JOIN (
                    SELECT
                        chats.user_id,
                        COUNT(*)::int AS messages_used_today
                    FROM chat_messages messages
                    INNER JOIN chats ON chats.id = messages.chat_id
                    WHERE messages.role = 'user'
                      AND (messages.created_at AT TIME ZONE %s)::date = (NOW() AT TIME ZONE %s)::date
                    GROUP BY chats.user_id
                ) AS usage ON usage.user_id = users.id
                ORDER BY users.created_at DESC
                """,
                (USAGE_TIMEZONE, USAGE_TIMEZONE),
            )
            users = []
            for row in cur.fetchall():
                item = dict(row)
                item["created_at"] = _iso(item.get("created_at"))
                professional_status = item.pop("professional_status", None)
                professional_display_name = item.pop("professional_display_name", None)
                professional_license_number = item.pop("professional_license_number", None)
                professional_title = item.pop("professional_title", None)
                professional_organization_name = item.pop("professional_organization_name", None)
                item["professional_profile"] = (
                    {
                        "user_id": item["id"],
                        "status": professional_status or "inactive",
                        "display_name": professional_display_name or "",
                        "license_number": professional_license_number or "",
                        "professional_title": professional_title or "",
                        "organization_name": professional_organization_name or "",
                    }
                    if professional_status
                    else None
                )
                users.append(item)
            return users

    def update_user_admin(self, user_id: str, name: str | None, email: str | None, phone: str | None, role: str | None) -> bool:
        self.initialize()
        try:
            with self.connection() as conn, conn.cursor() as cur:
                cur.execute("SELECT id FROM users WHERE id = %s", (user_id,))
                if not cur.fetchone():
                    return False
                sets = []
                params = []
                if name is not None:
                    sets.append("name = %s"); params.append(name.strip())
                if email is not None:
                    sets.append("email = %s"); params.append(email.strip().lower() or None)
                if phone is not None:
                    sets.append("phone = %s"); params.append(phone.strip() or None)
                if role is not None:
                    sets.append("role = %s"); params.append(role)
                if not sets:
                    return True
                sets.append("updated_at = %s"); params.append(utc_now_iso())
                params.append(user_id)
                cur.execute(f"UPDATE users SET {', '.join(sets)} WHERE id = %s", params)
                return True
        except Exception:
            return False

    def update_user_password_admin(self, user_id: str, password_hash: str) -> bool:
        self.initialize()
        try:
            with self.connection() as conn, conn.cursor() as cur:
                cur.execute(
                    "UPDATE users SET password_hash = %s, updated_at = %s WHERE id = %s",
                    (password_hash, utc_now_iso(), user_id),
                )
                return cur.rowcount > 0
        except Exception:
            return False

    def create_user_admin(self, user_id: str, name: str, email: str, phone: str, password_hash: str, role: str) -> bool:
        self.initialize()
        try:
            with self.connection() as conn, conn.cursor() as cur:
                cur.execute("SELECT 1 FROM users WHERE email = %s", (email.strip().lower(),))
                if cur.fetchone():
                    return False
                cur.execute(
                    "INSERT INTO users (id, name, email, phone, password_hash, role, is_seeded, created_at) "
                    "VALUES (%s, %s, %s, %s, %s, %s, FALSE, %s)",
                    (user_id, name.strip(), email.strip().lower(), phone.strip() or None, password_hash, role, utc_now_iso()),
                )
                return True
        except Exception:
            return False

    def delete_user(self, user_id: str) -> bool:
        self.initialize()
        with self.connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT id FROM users WHERE id = %s", (user_id,))
            if not cur.fetchone():
                return False
            cur.execute("DELETE FROM auth_tokens WHERE user_id = %s", (user_id,))
            cur.execute("DELETE FROM users WHERE id = %s", (user_id,))
            return True

    def get_recent_queries(self, limit: int = 20) -> list[dict]:
        self.initialize()
        with self.connection() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT id, question, answer, timestamp FROM queries ORDER BY timestamp DESC LIMIT %s",
                (limit,),
            )
            return [dict(row) for row in cur.fetchall()]

    def list_admin_conversations(self, limit: int = 60, search: str | None = None) -> list[dict]:
        self.initialize()
        limit = max(1, min(int(limit or 60), 200))
        search_term = f"%{search.strip()}%" if search and search.strip() else None
        search_clause = ""
        params: list[Any] = []
        if search_term:
            search_clause = """
                AND (
                    chats.title ILIKE %s
                    OR users.name ILIKE %s
                    OR users.email ILIKE %s
                    OR EXISTS (
                        SELECT 1
                        FROM chat_messages searchable_messages
                        WHERE searchable_messages.chat_id = chats.id
                          AND searchable_messages.content ILIKE %s
                    )
                )
            """
            params.extend([search_term, search_term, search_term, search_term])
        params.append(limit)
        with self.connection() as conn, conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT
                    chats.id,
                    chats.title,
                    chats.user_id,
                    chats.active_document_id,
                    chats.created_at,
                    chats.updated_at,
                    users.name AS user_name,
                    users.email AS user_email,
                    users.phone AS user_phone,
                    users.role AS user_role,
                    COUNT(messages.id)::int AS message_count,
                    COUNT(messages.id) FILTER (WHERE messages.role = 'user')::int AS user_message_count,
                    COUNT(messages.id) FILTER (WHERE messages.role = 'assistant')::int AS assistant_message_count,
                    MAX(messages.created_at) AS last_message_at,
                    (
                        SELECT content
                        FROM chat_messages user_messages
                        WHERE user_messages.chat_id = chats.id
                          AND user_messages.role = 'user'
                        ORDER BY user_messages.created_at DESC, user_messages.id DESC
                        LIMIT 1
                    ) AS last_question,
                    (
                        SELECT content
                        FROM chat_messages assistant_messages
                        WHERE assistant_messages.chat_id = chats.id
                          AND assistant_messages.role = 'assistant'
                        ORDER BY assistant_messages.created_at DESC, assistant_messages.id DESC
                        LIMIT 1
                    ) AS last_answer
                FROM chats
                LEFT JOIN users ON users.id = chats.user_id
                LEFT JOIN chat_messages messages ON messages.chat_id = chats.id
                WHERE 1 = 1
                {search_clause}
                GROUP BY chats.id, users.id
                ORDER BY chats.updated_at DESC
                LIMIT %s
                """,
                params,
            )
            rows = cur.fetchall()
            return [
                {
                    "id": row["id"],
                    "title": row["title"] or "Conversa sem título",
                    "user_id": row["user_id"],
                    "user": {
                        "id": row["user_id"],
                        "name": row["user_name"] or "Utilizador",
                        "email": row["user_email"] or "",
                        "phone": row["user_phone"] or "",
                        "role": row["user_role"] or "user",
                    },
                    "active_document_id": row["active_document_id"],
                    "created_at": row["created_at"].isoformat() if row["created_at"] else None,
                    "updated_at": row["updated_at"].isoformat() if row["updated_at"] else None,
                    "last_message_at": row["last_message_at"].isoformat() if row["last_message_at"] else None,
                    "message_count": int(row["message_count"] or 0),
                    "user_message_count": int(row["user_message_count"] or 0),
                    "assistant_message_count": int(row["assistant_message_count"] or 0),
                    "last_question": row["last_question"] or "",
                    "last_answer_preview": (row["last_answer"] or "")[:520],
                }
                for row in rows
            ]

    def get_admin_conversation(self, chat_id: str) -> dict | None:
        self.initialize()
        with self.connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    chats.id,
                    chats.title,
                    chats.user_id,
                    chats.active_document_id,
                    chats.created_at,
                    chats.updated_at,
                    users.name AS user_name,
                    users.email AS user_email,
                    users.phone AS user_phone,
                    users.role AS user_role
                FROM chats
                LEFT JOIN users ON users.id = chats.user_id
                WHERE chats.id = %s
                """,
                (chat_id,),
            )
            chat = cur.fetchone()
            if not chat:
                return None

            cur.execute(
                """
                SELECT id, role, content, provider_used, created_at, sources_json, metadata_json
                FROM chat_messages
                WHERE chat_id = %s
                ORDER BY created_at ASC, CASE WHEN role = 'user' THEN 0 ELSE 1 END, id ASC
                """,
                (chat_id,),
            )
            messages = cur.fetchall()

            cur.execute(
                """
                SELECT topic_route, legal_branch, diploma_slug, active_article, metadata, updated_at
                FROM conversation_legal_state
                WHERE chat_id = %s
                """,
                (chat_id,),
            )
            legal_state = cur.fetchone()

            return {
                "id": chat["id"],
                "title": chat["title"] or "Conversa sem título",
                "user_id": chat["user_id"],
                "user": {
                    "id": chat["user_id"],
                    "name": chat["user_name"] or "Utilizador",
                    "email": chat["user_email"] or "",
                    "phone": chat["user_phone"] or "",
                    "role": chat["user_role"] or "user",
                },
                "active_document_id": chat["active_document_id"],
                "created_at": chat["created_at"].isoformat() if chat["created_at"] else None,
                "updated_at": chat["updated_at"].isoformat() if chat["updated_at"] else None,
                "messages": [
                    {
                        "id": message["id"],
                        "role": message["role"],
                        "content": message["content"],
                        "provider_used": message["provider_used"],
                        "created_at": message["created_at"].isoformat() if message["created_at"] else None,
                        "sources": message["sources_json"] or [],
                        "answer_mode": (message["metadata_json"] or {}).get("answer_mode"),
                        "clarifying_questions": (message["metadata_json"] or {}).get("clarifying_questions", []),
                        "clarification_request": (message["metadata_json"] or {}).get("clarification_request"),
                    }
                    for message in messages
                ],
                "legal_state": dict(legal_state) if legal_state else None,
            }

    def get_admin_analytics(self) -> dict:
        self.initialize()
        with self.connection() as conn, conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT
                    TO_CHAR(days.day, 'YYYY-MM-DD') AS day,
                    COALESCE(COUNT(messages.id) FILTER (WHERE messages.role = 'user'), 0)::int AS user_messages,
                    COALESCE(COUNT(messages.id) FILTER (WHERE messages.role = 'assistant'), 0)::int AS assistant_messages
                FROM generate_series(
                    (NOW() AT TIME ZONE %s)::date - INTERVAL '6 days',
                    (NOW() AT TIME ZONE %s)::date,
                    INTERVAL '1 day'
                ) AS days(day)
                LEFT JOIN chat_messages messages
                  ON (messages.created_at AT TIME ZONE %s)::date = days.day::date
                GROUP BY days.day
                ORDER BY days.day ASC
                """,
                (USAGE_TIMEZONE, USAGE_TIMEZONE, USAGE_TIMEZONE),
            )
            daily_messages = [dict(row) for row in cur.fetchall()]

            cur.execute(
                """
                SELECT COALESCE(role, 'user') AS role, COUNT(*)::int AS total
                FROM users
                GROUP BY COALESCE(role, 'user')
                ORDER BY total DESC
                """
            )
            users_by_role = [dict(row) for row in cur.fetchall()]

            cur.execute(
                """
                SELECT COALESCE(status, 'unknown') AS status, COUNT(*)::int AS total
                FROM documents
                GROUP BY COALESCE(status, 'unknown')
                ORDER BY total DESC
                """
            )
            documents_by_status = [dict(row) for row in cur.fetchall()]

            cur.execute(
                """
                SELECT COALESCE(legal_branch, 'indeterminado') AS branch, COUNT(*)::int AS total
                FROM jurisprudence_cases
                GROUP BY COALESCE(legal_branch, 'indeterminado')
                ORDER BY total DESC
                LIMIT 10
                """
            )
            jurisprudence_by_branch = [dict(row) for row in cur.fetchall()]

            cur.execute(
                f"""
                SELECT
                    users.id,
                    users.name,
                    users.email,
                    users.role,
                    COUNT(messages.id)::int AS messages_used_today
                FROM users
                LEFT JOIN chats ON chats.user_id = users.id
                LEFT JOIN chat_messages messages
                  ON messages.chat_id = chats.id
                 AND messages.role = 'user'
                 AND (messages.created_at AT TIME ZONE %s)::date = (NOW() AT TIME ZONE %s)::date
                GROUP BY users.id, users.name, users.email, users.role
                ORDER BY messages_used_today DESC, users.created_at DESC
                LIMIT 5
                """,
                (USAGE_TIMEZONE, USAGE_TIMEZONE),
            )
            top_users_today = [dict(row) for row in cur.fetchall()]

        return {
            "daily_messages": daily_messages,
            "users_by_role": users_by_role,
            "documents_by_status": documents_by_status,
            "jurisprudence_by_branch": jurisprudence_by_branch,
            "top_users_today": top_users_today,
        }

    def delete_document(self, document_id: str) -> bool:
        self.initialize()
        with self.connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT id FROM documents WHERE id = %s", (document_id,))
            if not cur.fetchone():
                return False
            cur.execute("DELETE FROM legal_segments WHERE document_id = %s", (document_id,))
            cur.execute("DELETE FROM documents WHERE id = %s", (document_id,))
            return True


postgres_manager = PostgresManager()
