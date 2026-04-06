"""Model listing and selection endpoints."""
from __future__ import annotations
from fastapi import APIRouter
from pydantic import BaseModel

from adapters.ollama import OllamaAdapter
from core.config import settings

router = APIRouter()

# Human-readable profiles shown in the UI
MODEL_PROFILES = {
    "llama3.2:3b":          {"label": "Fast & Light",     "best_for": "Chat, Q&A, quick tasks",            "min_ram_gb": 4},
    "llama3.1:8b":          {"label": "Balanced",         "best_for": "General use, long documents",        "min_ram_gb": 6},
    "qwen2.5-coder:7b":     {"label": "Best for Code",    "best_for": "Code generation and review",         "min_ram_gb": 6},
    "deepseek-coder-v2:16b":{"label": "High Quality Code","best_for": "Complex code, large codebases",      "min_ram_gb": 12},
    "mistral:7b":           {"label": "Writing Focused",  "best_for": "Reports, summaries, drafting",       "min_ram_gb": 6},
    "gemma2:9b":            {"label": "Reasoning",        "best_for": "Analysis, step-by-step reasoning",   "min_ram_gb": 8},
}


class ModelInfo(BaseModel):
    name: str
    label: str
    best_for: str
    min_ram_gb: int
    available: bool
    active: bool


@router.get("/models", response_model=list[ModelInfo])
async def list_models():
    """
    List available Ollama models with human-readable profiles.
    Only returns models that are pulled locally.
    """
    adapter = OllamaAdapter()
    available = set(await adapter.list_models())

    results = []
    for model_name, profile in MODEL_PROFILES.items():
        # Also check short name matches (e.g. "llama3.1" matching "llama3.1:8b")
        is_available = model_name in available or any(
            a.startswith(model_name.split(":")[0]) for a in available
        )
        results.append(ModelInfo(
            name=model_name,
            label=profile["label"],
            best_for=profile["best_for"],
            min_ram_gb=profile["min_ram_gb"],
            available=is_available,
            active=(model_name == settings.ollama_model),
        ))

    # Also include any pulled models not in our profiles
    for name in available:
        if not any(m.name == name for m in results):
            results.append(ModelInfo(
                name=name,
                label=name,
                best_for="General use",
                min_ram_gb=4,
                available=True,
                active=(name == settings.ollama_model),
            ))

    return results
