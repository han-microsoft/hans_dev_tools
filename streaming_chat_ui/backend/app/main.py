"""FastAPI application — entry point for the streaming chat backend.

Two-phase startup:
    Phase 1 (blocking): InMemorySessionStore + LLM service creation.
    Shutdown: Logs shutdown event.

App state singletons:
    store — InMemorySessionStore
    llm   — LLMService (echo/mock/openai/agent per .env)
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.services.llm import create_llm_service
from app.services.session_store import InMemorySessionStore

logger = logging.getLogger(__name__)

# ── Logging configuration ───────────────────────────────────────────────────
logging.basicConfig(
    level=logging.DEBUG if settings.debug else logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)


# ── Lifespan ─────────────────────────────────────────────────────────────────


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifecycle — startup and shutdown."""
    # ── Startup ──────────────────────────────────────────────────────────
    logger.info("startup.begin", extra={"provider": settings.llm_provider})

    # Phase 1: Create session store and LLM service
    app.state.store = InMemorySessionStore()
    app.state.llm = create_llm_service()

    logger.info(
        "startup.complete",
        extra={"provider": settings.llm_provider, "model": settings.llm_model},
    )

    yield

    # ── Shutdown ─────────────────────────────────────────────────────────
    logger.info("shutdown.complete")


# ── App ──────────────────────────────────────────────────────────────────────


app = FastAPI(
    title="Streaming Chat UI — API",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS for local dev (Vite runs on :5173, CRA on :3000)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Register routers ────────────────────────────────────────────────────────
from app.routers.chat import router as chat_router
from app.routers.sessions import router as sessions_router

app.include_router(chat_router, prefix="/api")
app.include_router(sessions_router, prefix="/api")


@app.get("/api/health")
async def health():
    """Health check endpoint."""
    return {"status": "ok", "provider": settings.llm_provider}
