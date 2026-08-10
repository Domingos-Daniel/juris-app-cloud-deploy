from __future__ import annotations

import math
import re
from collections import Counter
from typing import Any

from app.db.models import RetrievedChunk
from app.db.postgres import postgres_manager
from app.services.pdf.article_refs import primary_article_number


TOKEN_RE = re.compile(r"[A-Za-zÀ-ÿ0-9]{3,}")
ARTICLE_RE = re.compile(r"(?:art(?:igo|\.)?\s*)?(\d{1,4})\.?º?", re.IGNORECASE)
MAX_SMALL_DOCUMENT_CHARS = 12000
MAX_SNIPPET_CHARS = 1800


def _tokens(text: str) -> list[str]:
    stopwords = {
        "que",
        "para",
        "com",
        "uma",
        "dos",
        "das",
        "por",
        "não",
        "sao",
        "são",
        "como",
        "qual",
        "quais",
        "sobre",
        "este",
        "esta",
        "esse",
        "essa",
        "documento",
        "pdf",
    }
    return [
        token.casefold()
        for token in TOKEN_RE.findall(text or "")
        if token.casefold() not in stopwords
    ]


def _article_numbers(text: str) -> set[str]:
    return {match.group(1).lstrip("0") for match in ARTICLE_RE.finditer(text or "")}


def _best_snippet(text: str, query_tokens: set[str]) -> str:
    clean = re.sub(r"\s+", " ", text or "").strip()
    if len(clean) <= MAX_SNIPPET_CHARS:
        return clean
    lowered = clean.casefold()
    positions = [
        lowered.find(token)
        for token in query_tokens
        if len(token) > 3 and lowered.find(token) >= 0
    ]
    center = min(positions) if positions else 0
    start = max(0, center - 350)
    end = min(len(clean), start + MAX_SNIPPET_CHARS)
    return clean[start:end].strip()


def _page_score(page: dict[str, Any], query_tokens: set[str], query_articles: set[str]) -> float:
    text = page.get("text") or ""
    page_tokens = Counter(_tokens(text))
    if not page_tokens:
        return 0.0
    score = 0.0
    for token in query_tokens:
        score += min(page_tokens.get(token, 0), 4)
    page_articles = _article_numbers(text)
    if query_articles and page_articles.intersection(query_articles):
        score += 25.0
    score += min(math.log(max(page.get("char_count") or len(text), 1)), 8.0) * 0.05
    return score


class DocumentContextService:
    def get_relevant_chunks(
        self,
        document_id: str,
        question: str,
        *,
        user_id: str | int | None = None,
        conversation_history: list[str] | None = None,
        limit: int = 6,
    ) -> list[RetrievedChunk]:
        document = postgres_manager.get_document(document_id, user_id=user_id)
        if not document:
            return []

        pages = postgres_manager.get_document_pages(document_id, user_id=user_id)
        if not pages:
            status = document.get("status")
            if status in {"ocr_pending", "ocr_processing"}:
                text = (
                    "O PDF foi recebido, mas ainda está a ser preparado por OCR. "
                    "Aguarde a conclusão do processamento antes de pedir análise do conteúdo."
                )
                return [
                    self._chunk_from_text(
                        document,
                        text,
                        page=None,
                        chunk_id=f"doc-status:{document_id}",
                        law_status="OCR em processamento",
                    )
                ]
            return []

        total_chars = sum(int(page.get("char_count") or len(page.get("text") or "")) for page in pages)
        if total_chars <= MAX_SMALL_DOCUMENT_CHARS:
            selected = pages[:limit]
        else:
            history_text = " ".join((conversation_history or [])[-4:])
            query_tokens = set(_tokens(f"{question} {history_text}"))
            query_articles = _article_numbers(question)
            ranked = sorted(
                pages,
                key=lambda page: _page_score(page, query_tokens, query_articles),
                reverse=True,
            )
            selected = [page for page in ranked if _page_score(page, query_tokens, query_articles) > 0][:limit]
            if not selected:
                selected = pages[: min(3, limit)]

        query_tokens = set(_tokens(question))
        chunks: list[RetrievedChunk] = []
        for index, page in enumerate(selected, start=1):
            text = _best_snippet(page.get("text") or "", query_tokens)
            if not text:
                continue
            chunks.append(
                self._chunk_from_text(
                    document,
                    text,
                    page=page.get("page"),
                    chunk_id=f"docpage:{document_id}:{page.get('page')}:{index}",
                    law_status="Documento do utilizador",
                    used_ocr=bool(page.get("used_ocr", False)),
                )
            )
        return chunks

    def _chunk_from_text(
        self,
        document: dict[str, Any],
        text: str,
        *,
        page: int | None,
        chunk_id: str,
        law_status: str,
        used_ocr: bool = False,
    ) -> RetrievedChunk:
        title = document.get("display_name") or document.get("filename") or "Documento do utilizador"
        article_number = primary_article_number(text)
        return RetrievedChunk(
            chunk_id=chunk_id,
            text=text,
            source=title,
            title=title,
            link_original=None,
            page=page,
            article_number=article_number,
            law_status=law_status,
            distance=None,
            source_scope="user_upload",
            document_id=document.get("id"),
            metadata={
                "source": title,
                "title": title,
                "page": page,
                "article_number": article_number,
                "article_main": article_number,
                "source_scope": "user_upload",
                "document_id": document.get("id"),
                "document_kind": "user_document",
                "used_ocr": used_ocr,
                "legal_branch": document.get("category") or "indeterminado",
            },
        )


document_context_service = DocumentContextService()
