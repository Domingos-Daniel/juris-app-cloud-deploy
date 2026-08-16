from __future__ import annotations

import asyncio
import json
import time
import urllib.error
import urllib.request

from app.core.config import get_settings
from app.core.logger import get_logger
from app.services.llm.http_transport import read_json_response


logger = get_logger(__name__)


class CloudflareEmbeddingClient:
    def __init__(self) -> None:
        self.settings = get_settings()
        self._disabled_until = 0.0
        self._last_error = ""

    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        now = time.time()
        if now < self._disabled_until:
            raise RuntimeError(
                f"Cloudflare embeddings temporariamente indisponivel: {self._last_error}"
            )
        if not self.settings.cloudflare_account_id or not self.settings.cloudflare_api_token:
            self._trip("CLOUDFLARE_ACCOUNT_ID/CLOUDFLARE_API_TOKEN nao configurados")
            raise RuntimeError(self._last_error)
        return await asyncio.to_thread(self._request_embeddings, texts)

    def _trip(self, reason: str) -> None:
        self._last_error = reason
        self._disabled_until = time.time() + max(
            30, int(self.settings.cloudflare_embedding_circuit_breaker_seconds)
        )
        logger.warning("Cloudflare embeddings disabled temporarily: %s", reason)

    @staticmethod
    def _should_split_batch(status_code: int, batch_size: int) -> bool:
        return status_code in {400, 413} and batch_size > 1

    def _request_embeddings(self, texts: list[str]) -> list[list[float]]:
        model = self.settings.cloudflare_embedding_model.strip() or "@cf/baai/bge-m3"
        account_id = self.settings.cloudflare_account_id.strip()
        url = (
            "https://api.cloudflare.com/client/v4/accounts/"
            f"{account_id}/ai/run/{model}"
        )
        payload = {"text": texts}
        request = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.settings.cloudflare_api_token}",
                "Content-Type": "application/json",
            },
        )
        try:
            data = read_json_response(
                request,
                timeout=self.settings.cloudflare_embedding_timeout_seconds,
            )
        except urllib.error.HTTPError as exc:
            if self._should_split_batch(exc.code, len(texts)):
                midpoint = max(1, len(texts) // 2)
                return self._request_embeddings(texts[:midpoint]) + self._request_embeddings(
                    texts[midpoint:]
                )
            self._trip(f"HTTP {exc.code}")
            raise
        except Exception as exc:
            self._trip(str(exc))
            raise

        if not data.get("success", True):
            errors = data.get("errors") or []
            reason = errors[0].get("message") if errors and isinstance(errors[0], dict) else str(errors)
            self._trip(reason or "resposta Cloudflare sem sucesso")
            raise RuntimeError(self._last_error)

        result = data.get("result") or data
        vectors = result.get("data") or result.get("embeddings")
        if isinstance(vectors, dict):
            vectors = vectors.get("data") or vectors.get("embeddings")
        if not isinstance(vectors, list) or not vectors:
            raise RuntimeError("Resposta Cloudflare sem embeddings")
        if vectors and isinstance(vectors[0], dict):
            vectors = [item.get("embedding") for item in vectors]
        if not all(isinstance(vector, list) for vector in vectors):
            raise RuntimeError("Formato de embeddings Cloudflare inesperado")
        return vectors


cloudflare_embedding_client = CloudflareEmbeddingClient()
