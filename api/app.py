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

        # Single authoritative startup: warms LLM router, embed model,
        # RiskAwareRouter, and FlywheelLogger on the exact engine instance
        # that handles requests. Replaces orphan VectorStore warm-ups below.
        from api.routes.chat import _engine as _chat_engine
        await _chat_engine.startup()
        from api.routes.health import set_engine_ready
        set_engine_ready()
        logger.info("[startup] engine ready — accepting requests")

        # Background task to pre-warm VectorStore
        async def warm_vector_store():
            try:
                from storage.vector import VectorStore
                logger.info("[startup] pre-warming VectorStore...")
                vector_store = VectorStore()
                if vector_store._ready:
                    count = await vector_store.count()
                    logger.info(f"[startup] VectorStore ready — {count} facts stored")
                    # Warm up Ollama embedding model
                    await vector_store.warmup()
                else:
                    logger.info("[startup] VectorStore not ready")
            except Exception as e:
                logger.warning(f"[startup] VectorStore warm-up failed: {e}")

        async def warm_llm_models():
            """
            Pre-load every configured LLM into Ollama VRAM before the first request.

            Ollama lazy-loads models on first use — that initial load costs 30–90 s of
            dead wait on the first user message.  Sending an empty /api/generate prompt
            forces the load at server startup instead, so every subsequent request starts
            generating within 1–2 s.

            Models are warmed concurrently (asyncio.gather) so total startup cost is
            bounded by the slowest model, not the sum of all models.
            """
            from adapters.ollama import OllamaAdapter
            from core.model_router import update_pulled_models

            # Collect every distinct model name from config
            model_names: list[str] = [settings.ollama_model]
            for extra in (settings.ollama_model_fast, settings.ollama_model_code):
                if extra and extra not in model_names:
                    model_names.append(extra)

            # Refresh router first (cheap /api/tags call)
            try:
                base_adapter = OllamaAdapter()
                pulled = await base_adapter.list_models()
                update_pulled_models(pulled)
                logger.info(f"[startup] model router ready — {len(pulled)} models available")
            except Exception as e:
                logger.warning(f"[startup] model router refresh failed: {e}")
                pulled = []

            # Warm every model that is actually pulled locally
            async def _warm_one(model_name: str):
                if pulled and model_name not in pulled:
                    logger.info(f"[startup] skipping warmup for '{model_name}' — not pulled locally")
                    return
                adapter = OllamaAdapter(model_override=model_name)
                await adapter.warmup()

            import asyncio
            await asyncio.gather(*[_warm_one(m) for m in model_names], return_exceptions=True)

        import asyncio
        background_tasks = set()

        for coro in (warm_vector_store(), warm_llm_models()):
            task = asyncio.create_task(coro)
            background_tasks.add(task)
            task.add_done_callback(background_tasks.discard)

    return app
