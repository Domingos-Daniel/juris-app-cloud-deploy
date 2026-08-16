from __future__ import annotations

from dotenv import load_dotenv
from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


BASE_DIR = Path(__file__).resolve().parents[2]
load_dotenv(BASE_DIR / ".env", override=False)

DEFAULT_TESSERACT_CMD = Path("C:/Program Files/Tesseract-OCR/tesseract.exe")
DEFAULT_TESSDATA_DIR = BASE_DIR / "tools" / "tessdata"
DEFAULT_POPPLER_BIN = (
    BASE_DIR / "tools" / "poppler" / "poppler-24.08.0" / "Library" / "bin"
)


class Settings(BaseSettings):
    app_name: str = "Sistema Inteligente de Assistencia Juridica Angolana"
    environment: str = "development"
    debug: bool = Field(default=True, validation_alias="DEBUG")
    api_prefix: str = ""

    seeded_username: str = "admin"
    seeded_password: str = "Admin123@"

    default_llm_provider: str = "deepseek"
    openai_api_key: str = ""
    openai_base_url: str = "https://api.openai.com/v1"
    openai_model: str = "gpt-4o-mini"
    openai_embedding_model: str = "text-embedding-3-small"
    openai_embedding_dimensions: int = 768
    embedding_model_type: str = "openai"
    local_embedding_model: str = "paraphrase-multilingual-MiniLM-L12-v2"
    cloudflare_account_id: str = ""
    cloudflare_api_token: str = ""
    cloudflare_embedding_model: str = "@cf/baai/bge-m3"
    cloudflare_embedding_timeout_seconds: float = 20.0
    cloudflare_embedding_circuit_breaker_seconds: int = 300

    deepseek_api_key: str = ""
    deepseek_model: str = "deepseek-v4-flash"

    openrouter_api_key: str = ""
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    openrouter_model: str = "openai/gpt-4o-mini"

    opencode_api_key: str = ""
    opencode_base_url: str = "https://opencode.ai/zen/v1"
    opencode_model: str = "ring-2.6-1t-free"

    postgres_dsn: str = ""
    postgres_schema: str = "public"
    pgvector_enabled: bool = False
    raw_pdfs_path: str = str(BASE_DIR / "data" / "raw_pdfs")
    processed_path: str = str(BASE_DIR / "data" / "processed")

    chunk_min_words: int = 300
    chunk_max_words: int = 500
    chunk_size: int = 1800
    chunk_overlap: int = 220
    retrieval_k: int = 10
    retrieval_candidate_k: int = 50
    retrieval_rrf_rank_constant: int = 60
    retrieval_max_correction_passes: int = 2
    reranker_enabled: bool = False
    reranker_model: str = "cross-encoder/mmarco-mMiniLMv2-L12-H384-v1"
    reranker_top_n: int = 12
    max_context_chars: int = 12000
    request_timeout_seconds: float = 90.0
    pdf_text_min_chars_for_ocr_skip: int = 80
    tesseract_cmd: str | None = None
    tesseract_data_dir: str | None = None
    poppler_bin_path: str | None = None
    ocr_engine: str = "paddleocr"
    ocr_render_dpi: int = 160
    ocr_cpu_workers: int = 4
    ocr_page_batch_size: int = 8
    ocr_language: str = "pt"

    log_level: str = "INFO"

    model_config = SettingsConfigDict(
        env_file=str(BASE_DIR / ".env"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    @field_validator("debug", mode="before")
    @classmethod
    def _coerce_debug(cls, value):
        if isinstance(value, bool):
            return value
        if value is None:
            return True
        normalized = str(value).strip().lower()
        if normalized in {"1", "true", "yes", "on", "debug", "dev", "development"}:
            return True
        if normalized in {"0", "false", "no", "off", "release", "prod", "production"}:
            return False
        return bool(normalized)

    @property
    def raw_pdfs_dir(self) -> Path:
        return Path(self.raw_pdfs_path)

    @property
    def processed_dir(self) -> Path:
        return Path(self.processed_path)


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    if not settings.tesseract_cmd and DEFAULT_TESSERACT_CMD.exists():
        settings.tesseract_cmd = str(DEFAULT_TESSERACT_CMD)
    if not settings.tesseract_data_dir and DEFAULT_TESSDATA_DIR.exists():
        settings.tesseract_data_dir = str(DEFAULT_TESSDATA_DIR)
    if not settings.poppler_bin_path and DEFAULT_POPPLER_BIN.exists():
        settings.poppler_bin_path = str(DEFAULT_POPPLER_BIN)
    settings.raw_pdfs_dir.mkdir(parents=True, exist_ok=True)
    settings.processed_dir.mkdir(parents=True, exist_ok=True)
    return settings
