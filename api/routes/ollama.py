"""
Ollama lifecycle routes — F1

POST /api/ollama/start         — start `ollama serve` as a subprocess
GET  /api/ollama/status        — is Ollama reachable + loaded models
POST /api/ollama/pull          — stream `ollama pull <model>` progress as SSE
GET  /api/models               — list installed models with metadata
POST /api/models/select        — switch active model for the session
"""
from __future__ import annotations
import asyncio
import json
import shutil
import subprocess
import logging
from typing import Optional

import httpx
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from core.config import settings

router = APIRouter()
logger = logging.getLogger(__name__)

OLLAMA_BASE = "http://localhost:11434"


# ── helpers ───────────────────────────────────────────────────────────────

async def _ollama_get(path: str) -> Optional[dict]:
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(f"{OLLAMA_BASE}{path}")
            r.raise_for_status()
            return r.json()
    except Exception:
        return None


# ── /api/ollama/start ─────────────────────────────────────────────────────

class StartResponse(BaseModel):
    started: bool
    already_running: bool
    message: str


@router.post("/ollama/start", response_model=StartResponse)
async def ollama_start():
    """Start `ollama serve` as a background subprocess."""
    ollama_bin = shutil.which("ollama")
    if not ollama_bin:
        raise HTTPException(
            status_code=503,
            detail="Ollama binary not found. Install from https://ollama.ai",
        )

    # Already running?
    status = await _ollama_get("/api/tags")
    if status is not None:
        return StartResponse(started=False, already_running=True, message="Ollama is already running.")

    # Spawn
    subprocess.Popen(
        [ollama_bin, "serve"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    # Wait up to 8 s
    for _ in range(8):
        await asyncio.sleep(1)
        if await _ollama_get("/api/tags") is not None:
            return StartResponse(started=True, already_running=False, message="Ollama started successfully.")

    return StartResponse(
        started=False, already_running=False,
        message="Ollama is starting but taking longer than expected. Retry in a moment.",
    )


# ── /api/ollama/status ────────────────────────────────────────────────────

class OllamaStatusResponse(BaseModel):
    reachable: bool
    active_model: Optional[str]
    loaded_models: list[str]


@router.get("/ollama/status", response_model=OllamaStatusResponse)
async def ollama_status():
    tags = await _ollama_get("/api/tags")
    if tags is None:
        return OllamaStatusResponse(reachable=False, active_model=None, loaded_models=[])

    loaded = [m["name"] for m in tags.get("models", [])]
    return OllamaStatusResponse(
        reachable=True,
        active_model=settings.ollama_model,
        loaded_models=loaded,
    )


# ── /api/ollama/pull ──────────────────────────────────────────────────────

class PullRequest(BaseModel):
    model: str


@router.post("/ollama/pull")
async def ollama_pull(req: PullRequest):
    """Stream `ollama pull <model>` progress as SSE."""
    ollama_bin = shutil.which("ollama")
    if not ollama_bin:
        raise HTTPException(status_code=503, detail="Ollama binary not found.")

    async def _stream():
        proc = await asyncio.create_subprocess_exec(
            ollama_bin, "pull", req.model,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        try:
            async for line in proc.stdout:
                text = line.decode("utf-8", errors="replace").strip()
                if text:
                    yield f"data: {json.dumps({'text': text})}\n\n"
            await proc.wait()
            rc = proc.returncode
            if rc == 0:
                yield f"data: {json.dumps({'done': True, 'success': True})}\n\n"
            else:
                yield f"data: {json.dumps({'done': True, 'success': False, 'error': f'pull exited {rc}'})}\n\n"
        except asyncio.CancelledError:
            proc.kill()
            raise

    return StreamingResponse(_stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ── /api/models ───────────────────────────────────────────────────────────

class ModelInfo(BaseModel):
    name: str
    size_gb: Optional[float]
    context_window: Optional[int]
    active: bool


@router.get("/models", response_model=list[ModelInfo])
async def list_models():
    tags = await _ollama_get("/api/tags")
    if tags is None:
        raise HTTPException(status_code=503, detail="Ollama is not reachable.")

    result = []
    for m in tags.get("models", []):
        name = m.get("name", "")
        size_bytes = m.get("size", 0)
        size_gb = round(size_bytes / 1e9, 2) if size_bytes else None
        # Context window lives in model details — fetch lazily
        ctx = None
        try:
            details = await _ollama_get(f"/api/show")  # would need POST with name; skip for now
        except Exception:
            pass
        result.append(ModelInfo(
            name=name,
            size_gb=size_gb,
            context_window=ctx,
            active=(name == settings.ollama_model or name.split(":")[0] == settings.ollama_model),
        ))
    return result


# ── /api/models/select ────────────────────────────────────────────────────

class SelectModelRequest(BaseModel):
    model: str


class SelectModelResponse(BaseModel):
    selected: str
    message: str


@router.post("/models/select", response_model=SelectModelResponse)
async def select_model(req: SelectModelRequest):
    """Switch active model for the current server process (runtime override)."""
    # Verify the model exists in Ollama
    tags = await _ollama_get("/api/tags")
    if tags is None:
        raise HTTPException(status_code=503, detail="Ollama is not reachable.")

    available = [m["name"] for m in tags.get("models", [])]
    # Accept bare name match (e.g. "llama3" matches "llama3:latest")
    matched = next(
        (n for n in available if n == req.model or n.startswith(req.model + ":")),
        None,
    )
    if not matched:
        raise HTTPException(status_code=404, detail=f"Model '{req.model}' not found in Ollama.")

    # Mutate settings at runtime (affects new requests; existing streams unaffected)
    settings.ollama_model = matched
    logger.info(f"Active model switched to {matched}")
    return SelectModelResponse(selected=matched, message=f"Active model is now {matched}.")
