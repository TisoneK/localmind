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
from core.models import StreamChunk

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
    # Gemma 3 family — Ollama default context is 2048 for :1b, 8192 for larger
    "gemma3:1b": 2048,
    "gemma3:4b": 8192,
    "gemma3:12b": 8192,
    "gemma3:27b": 8192,
}


class OllamaAdapter(BaseAdapter):
    def __init__(self, model_override: str = ""):
        self._base_url = settings.ollama_base_url.rstrip("/")
        self._model = model_override if model_override else settings.ollama_model
        self._timeout = settings.ollama_timeout
        self._keep_alive = settings.ollama_keep_alive
        self._client = httpx.AsyncClient(timeout=self._timeout)

    @property
    def context_window(self) -> int:
        base_model = self._model.split(":")[0] + ":" + self._model.split(":")[1] if ":" in self._model else self._model
        return _MODEL_CONTEXT_WINDOWS.get(self._model, _MODEL_CONTEXT_WINDOWS.get(base_model, 8192))

    async def chat(
        self,
        messages: list[dict],
        temperature: float = 0.7,
        **kwargs,
    ) -> AsyncIterator[StreamChunk]:
        url = f"{self._base_url}/v1/chat/completions"
        payload = {
            "model": self._model,
            "messages": messages,
            "temperature": temperature,
            "stream": True,
            "keep_alive": self._keep_alive,
            # Tell Ollama the context window to use — prevents it silently
            # truncating to its own default (e.g. 2048 for gemma3:1b) when the
            # model supports more.
            "options": {"num_ctx": self.context_window},
        }

        try:
            async with self._client.stream("POST", url, json=payload) as response:
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
                f"Ollama timed out after {self._timeout}s. "
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
