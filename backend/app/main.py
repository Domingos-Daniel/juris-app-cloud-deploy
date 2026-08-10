from __future__ import annotations

from contextlib import asynccontextmanager
import re
import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pathlib import Path

from app.api.routes_auth import router as auth_router
from app.api.routes_chats import router as chats_router
from app.api.routes_chat import router as chat_router
from app.api.routes_docs import router as docs_router
from app.api.routes_catalog import router as catalog_router
from app.api.routes_voice import router as voice_router
from app.api.routes_agent import router as agent_router
from app.api.routes_tts import router as tts_router
from app.api.routes_jurisprudence import router as jurisprudence_router
from app.api.routes_admin import router as admin_router
from app.api.routes_pro import router as pro_router
from app.core.config import get_settings
from app.core.logger import configure_logging
from app.db.postgres import postgres_manager
from app.services.rag.embeddings import embedding_service
from app.services.rag.semantic_router import semantic_router
from app.services.llm.deepseek_client import deepseek_client


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    postgres_manager.initialize()
    embedding_service.initialize()
    await semantic_router.classify("direitos do trabalhador em caso de despedimento")
    # Shared httpx client with connection pooling — avoids creating a new TCP
    # connection per streaming request (major source of latency).
    http_client = httpx.AsyncClient(
        timeout=httpx.Timeout(connect=10.0, read=120.0, write=30.0, pool=10.0),
        limits=httpx.Limits(max_keepalive_connections=5, max_connections=10),
    )
    deepseek_client.set_http_client(http_client)
    yield
    await http_client.aclose()


configure_logging()
settings = get_settings()
PROJECT_ROOT = Path(__file__).resolve().parents[2]
FRONTEND_DIST = PROJECT_ROOT / "frontend" / "dist"
FRONTEND_INDEX = FRONTEND_DIST / "index.html"
SPA_ROUTE_NAMES = {"", "admin", "documents", "library", "settings", "chat", "pro"}
CHAT_ID_PATTERN = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)

app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    description="Backend RAG para assistencia juridica angolana baseada em PDFs legais.",
    docs_url="/api-docs",
    redoc_url="/api-redoc",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def healthcheck() -> dict[str, str]:
    return {"status": "ok"}


app.include_router(auth_router, prefix=settings.api_prefix)
app.include_router(chat_router, prefix=settings.api_prefix)
app.include_router(chats_router, prefix=settings.api_prefix)
app.include_router(docs_router, prefix=settings.api_prefix)
app.include_router(catalog_router, prefix=settings.api_prefix)
app.include_router(voice_router)
app.include_router(agent_router)
app.include_router(tts_router)
app.include_router(jurisprudence_router, prefix=settings.api_prefix)
app.include_router(admin_router, prefix=settings.api_prefix)
app.include_router(pro_router, prefix=settings.api_prefix)


@app.get("/{full_path:path}")
async def frontend_spa_fallback(full_path: str):
    if not FRONTEND_INDEX.exists():
        raise HTTPException(status_code=404, detail="Not Found")

    normalized = full_path.strip("/")
    if normalized in SPA_ROUTE_NAMES or CHAT_ID_PATTERN.match(normalized):
        return FileResponse(FRONTEND_INDEX)

    asset_path = FRONTEND_DIST / normalized
    if asset_path.exists() and asset_path.is_file():
        return FileResponse(asset_path)

    raise HTTPException(status_code=404, detail="Not Found")
