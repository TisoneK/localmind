"""
FastAPI application factory — v0.3

Serves the single-file UI from ui/index.html directly (no build step needed).
"""
from __future__ import annotations
import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from core.config import settings
from api.routes import chat, sessions, health, models

logger = logging.getLogger(__name__)

UI_HTML = Path(__file__).parent.parent / "ui" / "index.html"


def create_app() -> FastAPI:
    app = FastAPI(
        title="LocalMind",
        description="Local AI runtime — file reading, web search, memory, tool use",
        version="0.3.0-dev",
        docs_url="/api/docs",
        redoc_url=None,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://localhost:5173",
            "http://localhost:8000",
            f"http://{settings.localmind_host}:{settings.localmind_port}",
        ],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(health.router, prefix="/api", tags=["health"])
    app.include_router(models.router, prefix="/api", tags=["models"])
    app.include_router(sessions.router, prefix="/api", tags=["sessions"])
    app.include_router(chat.router, prefix="/api", tags=["chat"])

    # Serve single-file UI — no build step required
    if UI_HTML.exists():
        @app.get("/", include_in_schema=False)
        @app.get("/{full_path:path}", include_in_schema=False)
        async def serve_ui(full_path: str = ""):
            if full_path.startswith("api/"):
                return {"error": "not found"}
            return FileResponse(str(UI_HTML))
    else:
        @app.get("/", include_in_schema=False)
        async def no_ui():
            return {"status": "LocalMind API running", "docs": "/api/docs"}

    @app.on_event("startup")
    async def startup():
        logger.info(f"LocalMind v0.3 — model: {settings.ollama_model} | ui: {UI_HTML.exists()}")

    return app
