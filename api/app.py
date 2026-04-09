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
from api.routes import chat, sessions, health, models, ollama, memory, filesystem

logger = logging.getLogger(__name__)

UI_HTML = Path(__file__).parent.parent / "ui" / "index.html"


def create_app() -> FastAPI:
    app = FastAPI(
        title="LocalMind",
        description="Local AI runtime — file reading, web search, memory, tool use",
        version="0.4.0-dev",
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
    app.include_router(ollama.router, prefix="/api", tags=["ollama"])
    app.include_router(memory.router, prefix="/api", tags=["memory"])
    app.include_router(models.router, prefix="/api", tags=["models"])
    app.include_router(sessions.router, prefix="/api", tags=["sessions"])
    app.include_router(filesystem.router, prefix="/api", tags=["filesystem"])
    app.include_router(chat.router, prefix="/api", tags=["chat"])

    @app.get("/api/routing", tags=["models"])
    async def model_routing():
        """Show which model handles each intent — useful for debugging and UI display."""
        from core.model_router import routing_report
        return {
            "main_model": settings.ollama_model,
            "routing": routing_report(settings.ollama_model),
            "note": "First pulled model from preference list wins per intent.",
        }

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
        logger.info(f"LocalMind — model: {settings.ollama_model} | ui: {UI_HTML.exists()}")
        
        # Background task to pre-warm VectorStore
        async def warm_vector_store():
            try:
                from storage.vector import VectorStore
                logger.info("[startup] pre-warming VectorStore...")
                vector_store = VectorStore()
                if vector_store._ready:
                    count = await vector_store.count()
                    logger.info(f"[startup] VectorStore ready — {count} facts stored")
                else:
                    logger.info("[startup] VectorStore not ready")
            except Exception as e:
                logger.warning(f"[startup] VectorStore warm-up failed: {e}")
        
        # Start VectorStore warm-up in background
        import asyncio
        background_tasks = set()
        task = asyncio.create_task(warm_vector_store())
        background_tasks.add(task)
        task.add_done_callback(background_tasks.discard)
        
        # Warm up model router with currently pulled models
        try:
            from adapters.ollama import OllamaAdapter
            from core.model_router import update_pulled_models
            adapter = OllamaAdapter()
            pulled = await adapter.list_models()
            update_pulled_models(pulled)
            logger.info(f"[startup] model router ready — {len(pulled)} models available")
        except Exception as e:
            logger.warning(f"[startup] model router warm-up failed: {e}")

    return app
