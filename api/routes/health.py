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
    model: str
    adapter: str
    tools: list[dict]
    version: str = "0.4.0-dev"


@router.get("/health", response_model=HealthResponse)
async def health():
    adapter = get_adapter(settings.localmind_adapter)
    reachable = await adapter.health_check()
    return HealthResponse(
        status="ok" if reachable else "degraded",
        ollama_reachable=reachable,
        model=settings.ollama_model,
        adapter=settings.localmind_adapter,
        tools=available_tools(),
    )
