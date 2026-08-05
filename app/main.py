"""
OmniBridge – FastAPI Application Factory
Wires everything together: DB init, provider registration, routes, middleware.
"""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from app.config import settings
from app.database import get_all_provider_states, init_db
from app.providers.gemini.cookie_refresh import cookie_refresh_loop
from app.providers.registry import register_provider
from app.routes.admin import router as admin_router
from app.routes.anthropic import router as anthropic_router
from app.routes.openai import router as openai_router
from app.routes.google_proxy import router as google_proxy_router

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
)
logger = logging.getLogger("omnibridge")

_bg_tasks: list[asyncio.Task] = []


@asynccontextmanager
async def lifespan(app: FastAPI):
    # ── Startup ────────────────────────────────────────────────────────────
    logger.info("OmniBridge starting up...")
    await init_db()

    provider_states = await get_all_provider_states()

    # Register Gemini engine
    if provider_states.get("gemini", settings.gemini_enabled):
        from app.providers.gemini.engine import GeminiEngine
        gemini_cfg = settings.get_gemini_config()
        register_provider("gemini", GeminiEngine(config=gemini_cfg))
        logger.info("Gemini provider registered.")

        # Start cookie refresh background task
        refresh_interval = gemini_cfg.get("cookie_refresh_interval", 3300)
        task = asyncio.create_task(cookie_refresh_loop(interval=refresh_interval))
        _bg_tasks.append(task)

    logger.info("OmniBridge ready. Admin: http://localhost:%d/admin", settings.port)
    yield

    # ── Shutdown ───────────────────────────────────────────────────────────
    logger.info("OmniBridge shutting down...")
    for task in _bg_tasks:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    logger.info("OmniBridge stopped.")


def create_app() -> FastAPI:
    app = FastAPI(
        title="OmniBridge",
        description=(
            "Modular multi-provider AI proxy server translating web session providers "
            "into OpenAI-compatible and Anthropic-compatible API endpoints."
        ),
        version="1.0.0",
        docs_url="/docs",
        redoc_url="/redoc",
        lifespan=lifespan,
    )

    # ── Middleware ─────────────────────────────────────────────────────────
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.admin_secret_key,
        session_cookie="omnibridge_session",
        max_age=86400,           # 24 hours
        https_only=False,        # set True in production with TLS
        same_site="lax",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ── Static files (admin UI assets) ────────────────────────────────────
    import os
    static_dir = os.path.join(os.path.dirname(__file__), "admin", "static")
    if os.path.isdir(static_dir):
        app.mount("/admin/static", StaticFiles(directory=static_dir), name="admin_static")

    # ── Routers ────────────────────────────────────────────────────────────
    app.include_router(openai_router)
    app.include_router(anthropic_router)
    app.include_router(google_proxy_router)
    app.include_router(admin_router)

    @app.get("/", include_in_schema=False)
    async def root():
        return {
            "name": "OmniBridge",
            "version": "1.0.0",
            "docs": "/docs",
            "admin": "/admin",
        }

    @app.get("/health", include_in_schema=False)
    async def health():
        states = await get_all_provider_states()
        return {"status": "ok", "providers": states}

    return app


app = create_app()

if __name__ == "__main__":
    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level,
        reload=False,
    )
