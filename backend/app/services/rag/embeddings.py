import asyncio
import hashlib
import json
import logging
from pathlib import Path
from packaging.version import Version
from app.core.config import get_settings
from app.services.llm.cloudflare_embeddings import cloudflare_embedding_client
from app.services.llm.openai_embeddings import openai_embedding_client

logger = logging.getLogger(__name__)


class EmbeddingService:
    def __init__(self) -> None:
        self.settings = get_settings()
        self._local_model = None
        self._is_e5 = False
        self._runtime_error: RuntimeError | None = None

    def _model_cache_version_ok(self, cache_dir: Path) -> bool:
        try:
            from sentence_transformers import __version__ as st_version
        except Exception:
            return False

        cfg_file = cache_dir / "config_sentence_transformers.json"
        if not cfg_file.exists():
            return True
        try:
            data = json.loads(cfg_file.read_text())
            saved_ver = data.get("__version__", {}).get("sentence_transformers", "")
            if saved_ver:
                sv = Version(saved_ver)
                cv = Version(st_version)
                if sv.major != cv.major:
                    logger.warning(
                        "Model cache was created with sentence-transformers v%s, "
                        "current is v%s — skipping ONNX to avoid incompatibility",
                        saved_ver, st_version,
                    )
                    return False
        except Exception:
            pass
        return True

    @property
    def local_model(self):
        if self._local_model is None:
            model_name = self.settings.local_embedding_model.lower()
            self._is_e5 = "e5" in model_name
            model_id = self.settings.local_embedding_model

            quantized_path, quantized_file = self._find_quantized_model()
            if quantized_path and self._model_cache_version_ok(Path(quantized_path)):
                try:
                    from sentence_transformers import SentenceTransformer

                    self._local_model = SentenceTransformer(
                        quantized_path,
                        backend="onnx",
                        device="cpu",
                        model_kwargs={
                            "provider": "CPUExecutionProvider",
                            "file_name": quantized_file,
                        },
                    )
                    logger.info("Loaded ONNX+INT8 model (%s)", quantized_file)
                    return self._local_model
                except Exception as exc:
                    logger.warning("ONNX+INT8 failed, trying ONNX float32: %s", exc)

            non_quantized_path = self._find_non_quantized_model()
            if non_quantized_path and self._model_cache_version_ok(Path(non_quantized_path)):
                try:
                    from sentence_transformers import SentenceTransformer

                    self._local_model = SentenceTransformer(
                        non_quantized_path,
                        backend="onnx",
                        device="cpu",
                        model_kwargs={"provider": "CPUExecutionProvider"},
                    )
                    logger.info("Loaded ONNX (float32) for %s", model_id)
                    return self._local_model
                except Exception as exc:
                    logger.warning("ONNX (float32) failed: %s", exc)

            logger.info("Loading PyTorch model %s (no ONNX)", model_id)
            from sentence_transformers import SentenceTransformer

            self._local_model = SentenceTransformer(model_id)
        return self._local_model

    @staticmethod
    def _find_quantized_model() -> tuple[str, str] | tuple[None, None]:
        settings = get_settings()
        safe_name = settings.local_embedding_model.replace("/", "-")
        cache_dir = Path(settings.processed_path) / "onnx_models" / safe_name
        onnx_dir = cache_dir / "onnx"

        for candidate in ["model_quint8_avx2.onnx", "model_qint8_avx512_vnni.onnx"]:
            if (onnx_dir / candidate).exists():
                return str(cache_dir), candidate
        return None, None

    @staticmethod
    def _find_non_quantized_model() -> str | None:
        settings = get_settings()
        safe_name = settings.local_embedding_model.replace("/", "-")
        cache_dir = Path(settings.processed_path) / "onnx_models" / safe_name
        onnx_dir = cache_dir / "onnx"
        if (onnx_dir / "model.onnx").exists():
            return str(cache_dir)
        return None

    def initialize(self):
        if self.settings.embedding_model_type == "local":
            _ = self.local_model

    @property
    def provider(self) -> str:
        return self.settings.embedding_model_type.strip().lower()

    @property
    def model_name(self) -> str:
        if self.provider == "local":
            return self.settings.local_embedding_model.strip()
        if self.provider == "cloudflare":
            return self.settings.cloudflare_embedding_model.strip()
        return self.settings.openai_embedding_model.strip()

    @property
    def model_version(self) -> str:
        identity = f"{self.provider}:{self.model_name}"
        return hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16]

    def vector_metadata(self, vector: list[float] | None = None) -> dict[str, str | int]:
        return {
            "embedding_provider": self.provider,
            "embedding_model": self.model_name,
            "embedding_version": self.model_version,
            "embedding_dimension": len(vector or []),
        }

    @property
    def query_prefix(self) -> str:
        return "query: " if self._is_e5 else ""

    @property
    def passage_prefix(self) -> str:
        return "passage: " if self._is_e5 else ""

    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        if self.settings.embedding_model_type == "local":
            prefixed = [self.passage_prefix + t for t in texts]
            return await asyncio.to_thread(self._embed_local, prefixed)
        if self.settings.embedding_model_type == "cloudflare":
            return await cloudflare_embedding_client.embed_texts(texts)
        if self.settings.embedding_model_type == "openai":
            return await openai_embedding_client.embed_texts(texts)
        raise RuntimeError(f"EMBEDDING_MODEL_TYPE invalido: {self.settings.embedding_model_type}")

    def _embed_local(self, texts: list[str]) -> list[list[float]]:
        if self._runtime_error is not None:
            raise self._runtime_error
        try:
            embeddings = self.local_model.encode(texts, normalize_embeddings=True)
            return embeddings.tolist()
        except Exception as exc:
            self._runtime_error = RuntimeError(
                f"Modelo local de embeddings indisponível nesta execução: {exc}"
            )
            raise self._runtime_error from exc

    async def embed_query(self, text: str) -> list[float]:
        if self.settings.embedding_model_type == "local":
            prefixed = self.query_prefix + text
            results = await asyncio.to_thread(self._embed_local, [prefixed])
        elif self.settings.embedding_model_type == "cloudflare":
            results = await cloudflare_embedding_client.embed_texts([text])
        elif self.settings.embedding_model_type == "openai":
            results = await openai_embedding_client.embed_texts([text])
        else:
            raise RuntimeError(f"EMBEDDING_MODEL_TYPE invalido: {self.settings.embedding_model_type}")
        return results[0]

    async def embed_texts_for_ingestion(self, texts: list[str]) -> list[list[float]]:
        """Alias for embed_texts, used by ingestion scripts."""
        return await self.embed_texts(texts)


embedding_service = EmbeddingService()
