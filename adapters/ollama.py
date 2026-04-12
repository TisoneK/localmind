"""
Ollama adapter -- wraps the Ollama OpenAI-compatible API.

Ollama exposes a REST API at http://localhost:11434 that is compatible with
the OpenAI chat completions format. This adapter handles:
- Streaming responses token by token
- Connection errors with clear user-facing messages
- Model context window detection
- Health checking (is Ollama running?)
"""
from __future__ import annotations
import json
import logging
from typing import AsyncIterator

import httpx

from adapters.base import BaseAdapter
from core.config import settings
from core.models import StreamChunk, Intent

# Per-intent timeout overrides (seconds).
# CHAT is fast; tool-heavy intents need more time.
def _build_intent_timeouts() -> dict[str, int]:
    """Read per-intent timeouts from settings so .env changes take effect on restart."""
    return {
        "chat":       settings.ollama_timeout_chat,
        "web_search": settings.ollama_timeout_web_search,
        "file_task":  settings.ollama_timeout_file_task,
        "file_write": settings.ollama_timeout_file_write,
        "shell":      settings.ollama_timeout_shell,
        "code_exec":  settings.ollama_timeout_code_exec,
        "sysinfo":    settings.ollama_timeout_sysinfo,
        "memory_op":  settings.ollama_timeout_memory_op,
    }

_INTENT_TIMEOUTS: dict[str, int] = _build_intent_timeouts()
_DEFAULT_TIMEOUT: int = settings.ollama_timeout_default

logger = logging.getLogger(__name__)

# Known context windows for common models
_MODEL_CONTEXT_WINDOWS: dict[str, int] = {
    "llama3.1:8b": 128000,
    "llama3.2:3b": 128000,
    "llama3.1:70b": 128000,
    "qwen2.5-coder:7b": 32768,
    "qwen2.5-coder:14b": 32768,
    "deepseek-coder-v2:16b": 65536,
    "mistral:7b": 32768,
    "mixtral:8x7b": 32768,
    "phi3:mini": 4096,
    "gemma2:9b": 8192,
    # Gemma 3 family — Ollama supports up to 131072 context for the full family.
    # The :1b was previously listed as 2048 (a Gemma 2 holdover) which caused
    # response_reserve (2048) to consume the entire context window, leaving 64
    # tokens for all content and making every non-trivial request time out.
    "gemma3:1b":  131072,
    "gemma3:4b":  131072,
    "gemma3:12b": 131072,
    "gemma3:27b": 131072,
}


class OllamaAdapter(BaseAdapter):
    def __init__(self, model_override: str = ""):
        self._base_url = settings.ollama_base_url.rstrip("/")
        # Strip inline .env comments — pydantic-settings doesn't do this by default
        raw = model_override if model_override else settings.ollama_model
        self._model = raw.split("#")[0].strip()
        self._timeout = settings.ollama_timeout
        self._keep_alive = settings.ollama_keep_alive
        self._client = httpx.AsyncClient(timeout=self._timeout)

    @property
    def context_window(self) -> int:
        # Strip any inline comment that pydantic-settings may have included
        # e.g. 'gemma3:1b   # comment' → 'gemma3:1b'
        model = self._model.split("#")[0].strip()
        base_model = model.split(":")[0] if ":" not in model else model
        return _MODEL_CONTEXT_WINDOWS.get(model, _MODEL_CONTEXT_WINDOWS.get(base_model, 8192))

    async def chat(
        self,
        messages: list[dict],
        temperature: float = 0.7,
        intent: str = "chat",
        **kwargs,
    ) -> AsyncIterator[StreamChunk]:
        url = f"{self._base_url}/v1/chat/completions"
        timeout = _INTENT_TIMEOUTS.get(intent, _DEFAULT_TIMEOUT)
        # Cap num_ctx for file_task and chat: a 1b model with 131k context
        # allocates a huge KV cache and stalls. Use a practical ceiling.
        _ctx_caps = {
            "file_task": 8192,
            "chat":      8192,
            "memory_op": 4096,
        }
        num_ctx = min(self.context_window, _ctx_caps.get(intent, self.context_window))

        payload = {
            "model": self._model,
            "messages": messages,
            "temperature": temperature,
            "stream": True,
            "keep_alive": self._keep_alive,
            "options": {"num_ctx": num_ctx},
        }

        try:
            async with self._client.stream("POST", url, json=payload, timeout=timeout) as response:
                if response.status_code != 200:
                    body = await response.aread()
                    error_msg = f"Ollama returned {response.status_code}: {body.decode()[:200]}"
                    logger.error(error_msg)
                    yield StreamChunk(text=error_msg, done=True, error=error_msg)
                    return

                async for line in response.aiter_lines():
                    if not line or not line.startswith("data: "):
                        continue
                    data = line[6:]  # strip "data: "
                    if data == "[DONE]":
                        yield StreamChunk(text="", done=True)
                        return
                    try:
                        obj = json.loads(data)
                        delta = obj["choices"][0]["delta"]
                        text = delta.get("content", "")
                        if text:
                            yield StreamChunk(text=text, done=False)
                    except (json.JSONDecodeError, KeyError, IndexError):
                        continue

        except httpx.ConnectError:
            msg = (
                f"Cannot connect to Ollama at {self._base_url}. "
                "Is Ollama running? Try: ollama serve"
            )
            logger.error(msg)
            yield StreamChunk(text=msg, done=True, error=msg)
        except httpx.TimeoutException:
            msg = (
                f"Ollama timed out after {timeout}s. "
                "The model may still be loading -- please try again in a moment."
            )
            logger.error(msg)
            yield StreamChunk(text=msg, done=True, error=msg)

    async def warmup(self) -> bool:
        """
        Pre-load the model into VRAM using Ollama's native /api/generate endpoint.

        Ollama's documented "load model without generating" form: send model +
        keep_alive with NO prompt key at all. An empty-string prompt triggers a
        400; omitting the key entirely is what tells Ollama to load and hold the
        model without producing any tokens.

        Returns True if the model loaded successfully, False otherwise.
        """
        url = f"{self._base_url}/api/generate"
        payload = {
            "model": self._model,
            "keep_alive": self._keep_alive,
        }
        try:
            response = await self._client.post(url, json=payload, timeout=60)
            ok = response.status_code == 200
            if ok:
                logger.info("[adapter] model '%s' pre-loaded (keep_alive=%s)", self._model, self._keep_alive)
            else:
                logger.warning("[adapter] warmup returned %s for model '%s'", response.status_code, self._model)
            return ok
        except Exception as exc:
            logger.warning("[adapter] warmup failed for model '%s': %s", self._model, exc)
            return False

    async def health_check(self) -> bool:
        try:
            response = await self._client.get(f"{self._base_url}/api/tags", timeout=5)
            return response.status_code == 200
        except Exception:
            return False

    async def list_models(self) -> list[str]:
        """Return names of locally available Ollama models."""
        try:
            response = await self._client.get(f"{self._base_url}/api/tags", timeout=5)
            data = response.json()
            return [m["name"] for m in data.get("models", [])]
        except Exception:
            return []
