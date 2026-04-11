"""Health check endpoint."""
from __future__ import annotations
from fastapi import APIRouter
from pydantic import BaseModel
from adapters import get_adapter
from core.config import settings
from tools import available_tools

router = APIRouter()


class HealthResponse(BaseModel):
    status: str
    ollama_reachable: bool
    engine_ready: bool
    model: str
    adapter: str
    tools: list[dict]
    version: str = "0.4.0-dev"


# Set to True by app.py once engine.startup() completes.
_engine_ready: bool = False

def set_engine_ready() -> None:
    global _engine_ready
    _engine_ready = True


@router.get("/health", response_model=HealthResponse)
async def health():
    adapter = get_adapter(settings.localmind_adapter)
    reachable = await adapter.health_check()
    return HealthResponse(
        status="ok" if reachable and _engine_ready else "degraded",
        ollama_reachable=reachable,
        engine_ready=_engine_ready,
        model=settings.ollama_model,
        adapter=settings.localmind_adapter,
        tools=available_tools(),
    )
