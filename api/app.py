"""
FastAPI application factory.

Registers all routers, CORS, static file serving, and startup/shutdown hooks.
Use create_app() to get the ASGI app instance.
"""
from __future__ import annotations
import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from core.config import settings
from api.routes import chat, sessions, health, models

logger = logging.getLogger(__name__)

UI_DIST = Path(__file__).parent.parent / "ui" / "dist"


def create_app() -> FastAPI:
    app = FastAPI(
        title="LocalMind",
        description="Local AI with tool use — file reading, web search, memory, code execution",
        version="0.1.0-dev",
        docs_url="/api/docs",
        redoc_url=None,
    )

    # ── CORS ──────────────────────────────────────────────────────────────────
    # Only allow localhost origins — LocalMind is a local-only service
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://localhost:5173",   # Vite dev server
            "http://localhost:8000",   # Production
            f"http://{settings.localmind_host}:{settings.localmind_port}",
        ],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ── API routes ────────────────────────────────────────────────────────────
    app.include_router(health.router, prefix="/api", tags=["health"])
    app.include_router(models.router, prefix="/api", tags=["models"])
    app.include_router(sessions.router, prefix="/api", tags=["sessions"])
    app.include_router(chat.router, prefix="/api", tags=["chat"])

    # ── Static UI ─────────────────────────────────────────────────────────────
    if UI_DIST.exists():
        app.mount("/assets", StaticFiles(directory=str(UI_DIST / "assets")), name="assets")

        @app.get("/{full_path:path}", include_in_schema=False)
        async def serve_spa(full_path: str):
            """Serve the React SPA for all non-API routes."""
            index = UI_DIST / "index.html"
            if index.exists():
                return FileResponse(str(index))
            return {"error": "UI not built. Run: cd ui && npm run build"}
    else:
        @app.get("/", include_in_schema=False)
        async def no_ui():
            return {
                "status": "LocalMind API running",
                "ui": "Not built. Run: cd ui && npm install && npm run build",
                "docs": "/api/docs",
            }

    @app.on_event("startup")
    async def startup():
        logger.info(f"LocalMind starting — model: {settings.ollama_model}")
        logger.info(f"Ollama: {settings.ollama_base_url}")

    return app
