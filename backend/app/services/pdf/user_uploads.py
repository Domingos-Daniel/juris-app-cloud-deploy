from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path

from fastapi import UploadFile

from app.core.config import get_settings
from app.core.logger import get_logger
from app.db.models import UserDocumentItem
from app.db.postgres import postgres_manager
from app.services.pdf.chunker import semantic_chunk_text
from app.services.pdf.extractor import (
    extract_pages_from_pdf,
    extract_pages_from_pdf_text_only,
)
from app.services.pdf.ingestion import (
    _primary_article_number,
    _is_normative_chunk,
    _normative_density,
)
from app.services.rag.vector_store import legislation_vector_store

logger = get_logger(__name__)

MAX_UPLOAD_MB = 15
MAX_UPLOAD_BYTES = MAX_UPLOAD_MB * 1024 * 1024


def _user_document_branch(filename: str, text: str) -> str:
    haystack = f"{filename} {text}".lower()
    if any(
        token in haystack
        for token in ["trabalho", "trabalhador", "empregador", "despedimento"]
    ):
        return "laboral"
    if any(token in haystack for token in ["crime", "penal", "arguido", "queixa"]):
        return "penal"
    if any(
        token in haystack
        for token in ["mútuo", "mutuo", "empréstimo", "emprestimo", "contrato"]
    ):
        return "civil"
    if any(token in haystack for token in ["constituição", "constituicao"]):
        return "constitucional"
    return "indeterminado"


def _user_document_metadata(
    display_name: str,
    page_number: int,
    chunk: str,
    page_used_ocr: bool,
    chunk_index: int,
) -> dict:
    return {
        "source": display_name,
        "title": display_name,
        "link_original": None,
        "page": page_number,
        "article_number": _primary_article_number(chunk),
        "law_status": "Documento do utilizador",
        "used_ocr": page_used_ocr,
        "chunk_index": chunk_index,
        "source_scope": "user_upload",
        "legal_branch": _user_document_branch(display_name, chunk),
        "diploma_slug": None,
        "is_front_matter": False,
        "is_structural": False,
        "normative_density": _normative_density(chunk),
        "is_normative": _is_normative_chunk(chunk),
        "source_priority": 0.9,
        "document_kind": "user_document",
    }


def _guess_category(filename: str, text: str) -> str:
    haystack = f"{filename} {text}".lower()
    if any(
        token in haystack
        for token in [
            "dossier de apoio",
            "apoio legislativo",
            "legislação",
            "legislacao",
            "lei geral",
            "código",
            "codigo",
            "artigo",
            "art.",
        ]
    ):
        return "Legislação de apoio"
    if any(token in haystack for token in ["trabalho", "empregador", "trabalhador", "despedimento"]):
        return "Trabalho"
    if any(
        token in haystack for token in ["contrato", "acordo", "aceitacao", "aceitação"]
    ):
        return "Contrato"
    if any(
        token in haystack
        for token in ["procura", "mandato", "procuracao", "procuração"]
    ):
        return "Mandato"
    if any(token in haystack for token in ["peticao", "petição", "requerimento"]):
        return "Peca Processual"
    return "Geral"


def _quality_status(used_ocr: bool, chunk_count: int, pages_count: int) -> str:
    if chunk_count <= 0:
        return "empty"
    if used_ocr:
        return "ocr"
    if pages_count >= 3 and chunk_count <= max(1, pages_count // 3):
        return "partial"
    return "good"


def _is_pdf_upload(upload: UploadFile) -> bool:
    content_type = (upload.content_type or "").split(";")[0].strip().lower()
    filename = (upload.filename or "").casefold()
    return content_type == "application/pdf" or filename.endswith(".pdf")


class UserDocumentService:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.upload_dir = self.settings.processed_dir / "user_uploads_tmp"
        self.upload_dir.mkdir(parents=True, exist_ok=True)
        self.storage_dir = self.settings.processed_dir / "user_documents"
        self.storage_dir.mkdir(parents=True, exist_ok=True)

    async def save_uploaded_pdf(
        self,
        upload: UploadFile,
        user_id: str | int | None = None,
        *,
        allow_background_ocr: bool = False,
    ) -> UserDocumentItem:
        if not _is_pdf_upload(upload):
            raise ValueError("Apenas ficheiros PDF sao suportados neste momento.")
        declared_size = getattr(upload, "size", None)
        if declared_size and int(declared_size) > MAX_UPLOAD_BYTES:
            raise ValueError(f"O PDF excede o limite maximo de {MAX_UPLOAD_MB} MB.")
        payload = await upload.read()
        if not payload:
            raise ValueError("O ficheiro PDF esta vazio.")
        if len(payload) > MAX_UPLOAD_BYTES:
            raise ValueError(f"O PDF excede o limite maximo de {MAX_UPLOAD_MB} MB.")
        safe_name = Path(upload.filename or "documento.pdf").name
        file_hash = hashlib.sha1(
            (safe_name + str(len(payload))).encode("utf-8")
        ).hexdigest()

        self.upload_dir.mkdir(parents=True, exist_ok=True)
        self.storage_dir.mkdir(parents=True, exist_ok=True)

        tmp_path = self.upload_dir / f"{file_hash}.pdf"
        tmp_path.write_bytes(payload)
        persistent_path = self.storage_dir / f"{file_hash}-{safe_name}"
        persistent_path.write_bytes(payload)
        try:
            return await self._process_pdf_path(
                tmp_path,
                safe_name,
                len(payload),
                user_id=user_id,
                storage_path=str(persistent_path),
                allow_background_ocr=allow_background_ocr,
            )
        finally:
            try:
                tmp_path.unlink(missing_ok=True)
            except Exception:
                pass

    async def reprocess_document(
        self, document_id: str, user_id: str | int | None = None
    ) -> UserDocumentItem:
        document = postgres_manager.get_document(document_id, user_id=user_id)
        if not document:
            raise ValueError("Documento nao encontrado.")
        storage_path = document.get("storage_path")
        if not storage_path:
            raise ValueError(
                "Este documento foi criado antes da preservacao do PDF original e nao pode ser reprocessado automaticamente."
            )
        pdf_path = Path(storage_path)
        if not pdf_path.exists():
            raise ValueError(
                "O PDF original deste documento ja nao esta disponivel no armazenamento local."
            )
        legislation_vector_store.delete_document_chunks(document_id)
        postgres_manager.delete_document(document_id, user_id=user_id)
        return await self._process_pdf_path(
            pdf_path,
            document.get("display_name") or document.get("filename") or pdf_path.name,
            int(document.get("size_bytes") or pdf_path.stat().st_size),
            user_id=user_id,
            storage_path=str(pdf_path),
            allow_background_ocr=False,
        )

    async def _process_pdf_path(
        self,
        pdf_path: Path,
        display_name: str,
        size_bytes: int,
        user_id: str | int | None = None,
        storage_path: str | None = None,
        allow_background_ocr: bool = False,
    ) -> UserDocumentItem:
        pages, needs_ocr = extract_pages_from_pdf_text_only(pdf_path)
        joined_parts: list[str] = []
        for page_info in pages:
            page_text = page_info["text"].strip()
            if not page_text:
                continue
            joined_parts.append(page_text)
        searchable_text = "\n\n".join(joined_parts).strip()
        has_useful_text = len(searchable_text) >= 300

        if not has_useful_text and needs_ocr:
            if not allow_background_ocr:
                raise ValueError(
                    "Este PDF parece ser digitalizado/imagem e precisa de OCR. "
                    "Para uma experiência rápida, envie um PDF pesquisável com texto selecionável."
                )
            document_id = postgres_manager.save_document(
                filename=display_name,
                display_name=display_name,
                storage_path=storage_path,
                mime_type="application/pdf",
                size_bytes=size_bytes,
                status="ocr_pending",
                page_count=len(pages),
                chunks_created=0,
                extraction_mode="ocr",
                quality_status="ocr_pending",
                summary="PDF digitalizado recebido. O OCR será processado em segundo plano.",
                preview_text=None,
                category=_guess_category(display_name, ""),
                user_id=user_id,
            )
            self.schedule_pending_ocr(document_id, user_id)
            payload = postgres_manager.get_document(document_id, user_id=user_id)
            if not payload:
                raise ValueError("Documento guardado, mas nao foi possivel reler o registo.")
            return UserDocumentItem(**payload)

        if not has_useful_text:
            raise ValueError("Nao foi possivel extrair texto util deste PDF.")

        full_text = "\n\n".join(joined_parts).strip()
        summary = full_text[:320] if full_text else None
        preview_text = full_text[:2000] if full_text else None
        category = _guess_category(display_name, full_text[:1200])
        text_pages = [page for page in pages if (page.get("text") or "").strip()]
        quality_status = "partial" if needs_ocr else "good"
        document_id = postgres_manager.save_document(
            filename=display_name,
            display_name=display_name,
            storage_path=storage_path,
            mime_type="application/pdf",
            size_bytes=size_bytes,
            status="ready",
            page_count=len(pages),
            chunks_created=len(text_pages),
            extraction_mode="direct",
            quality_status=quality_status,
            summary=summary,
            preview_text=preview_text,
            category=category,
            user_id=user_id,
        )
        postgres_manager.replace_document_pages(document_id, text_pages)
        logger.info(
            "Documento do utilizador %s preparado sem indexacao vetorial, %s paginas com texto",
            display_name,
            len(text_pages),
        )
        payload = postgres_manager.get_document(document_id, user_id=user_id)
        if not payload:
            raise ValueError(
                "Documento processado mas nao foi possivel reler o registo persistido."
            )
        return UserDocumentItem(**payload)

    def schedule_pending_ocr(
        self, document_id: str, user_id: str | int | None = None
    ) -> None:
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self.process_pending_ocr(document_id, user_id))
        except RuntimeError:
            logger.warning("Sem event loop ativo para agendar OCR do documento %s", document_id)

    async def process_pending_ocr(
        self, document_id: str, user_id: str | int | None = None
    ) -> None:
        await asyncio.to_thread(self._process_pending_ocr_sync, document_id, user_id)

    def _process_pending_ocr_sync(
        self, document_id: str, user_id: str | int | None = None
    ) -> None:
        document = postgres_manager.get_document(document_id, user_id=user_id)
        if not document:
            return
        storage_path = document.get("storage_path")
        if not storage_path:
            postgres_manager.update_document_processing_state(
                document_id,
                status="failed",
                quality_status="ocr_failed",
                summary="OCR não iniciado porque o PDF original não está disponível.",
                user_id=user_id,
            )
            return
        pdf_path = Path(storage_path)
        try:
            postgres_manager.update_document_processing_state(
                document_id,
                status="ocr_processing",
                quality_status="ocr_processing",
                user_id=user_id,
            )
            pages, used_ocr = extract_pages_from_pdf(pdf_path)
            text_pages = [page for page in pages if (page.get("text") or "").strip()]
            full_text = "\n\n".join(page["text"].strip() for page in text_pages).strip()
            if not full_text:
                raise ValueError("OCR nao extraiu texto util.")
            postgres_manager.replace_document_pages(document_id, text_pages)
            postgres_manager.update_document_processing_state(
                document_id,
                status="ocr_ready",
                page_count=len(pages),
                chunks_created=len(text_pages),
                extraction_mode="ocr" if used_ocr else "direct",
                quality_status="ocr" if used_ocr else "good",
                summary=full_text[:320],
                preview_text=full_text[:2000],
                category=_guess_category(document.get("display_name") or "", full_text[:1200]),
                user_id=user_id,
            )
            logger.info("OCR em background concluido para documento %s", document_id)
        except Exception as exc:
            logger.warning("OCR em background falhou para %s: %s", document_id, exc)
            postgres_manager.update_document_processing_state(
                document_id,
                status="failed",
                quality_status="ocr_failed",
                summary=f"OCR falhou: {exc}",
                user_id=user_id,
            )

    def list_documents(
        self, user_id: str | int | None = None
    ) -> list[UserDocumentItem]:
        return [
            UserDocumentItem(**row)
            for row in postgres_manager.list_documents(user_id=user_id)
        ]

    def get_document(
        self, document_id: str, user_id: str | int | None = None
    ) -> UserDocumentItem:
        payload = postgres_manager.get_document(document_id, user_id=user_id)
        if not payload:
            raise ValueError("Documento nao encontrado.")
        return UserDocumentItem(**payload)

    def rename_document(
        self, document_id: str, display_name: str, user_id: str | int | None = None
    ) -> UserDocumentItem:
        postgres_manager.rename_document(document_id, display_name, user_id=user_id)
        payload = postgres_manager.get_document(document_id, user_id=user_id)
        if not payload:
            raise ValueError("Documento nao encontrado apos renomeacao.")
        return UserDocumentItem(**payload)

    def delete_document(
        self, document_id: str, user_id: str | int | None = None
    ) -> None:
        payload = postgres_manager.get_document(document_id, user_id=user_id)
        legislation_vector_store.delete_document_chunks(document_id)
        postgres_manager.replace_document_pages(document_id, [])
        postgres_manager.delete_document(document_id, user_id=user_id)
        storage_path = (payload or {}).get("storage_path")
        if storage_path:
            try:
                Path(storage_path).unlink(missing_ok=True)
            except Exception:
                pass

    def get_document_preview(
        self, document_id: str, user_id: str | int | None = None
    ) -> dict:
        payload = postgres_manager.get_document(document_id, user_id=user_id)
        if not payload:
            raise ValueError("Documento nao encontrado.")
        pages = postgres_manager.get_document_pages(document_id, user_id=user_id)
        chunks = legislation_vector_store.get_document_chunks(document_id, limit=6)
        return {
            "document": payload,
            "chunks": [
                {
                    "page": page["page"],
                    "text": page["text"][:1200],
                    "article_number": _primary_article_number(page["text"]),
                }
                for page in pages[:6]
            ]
            or [
                {
                    "page": chunk.page,
                    "text": chunk.text,
                    "article_number": chunk.article_number,
                }
                for chunk in chunks
            ],
        }

    def attach_document_to_chat(
        self, document_id: str, chat_id: str | None, user_id: str | int | None = None
    ) -> dict:
        if chat_id:
            postgres_manager.set_chat_active_document(
                chat_id, document_id, user_id=user_id
            )
        postgres_manager.mark_document_used(document_id, user_id=user_id)
        payload = postgres_manager.get_document(document_id, user_id=user_id)
        if not payload:
            raise ValueError("Documento nao encontrado.")
        return payload


user_document_service = UserDocumentService()
