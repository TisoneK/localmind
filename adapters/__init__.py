"""
Adapter registry. Maps adapter names to classes.
Set LOCALMIND_ADAPTER in .env to switch runtimes.
"""
from adapters.base import BaseAdapter


def get_adapter(name: str, model_override: str = "") -> BaseAdapter:
    if name == "ollama":
        from adapters.ollama import OllamaAdapter
        return OllamaAdapter(model_override=model_override)
    raise ValueError(f"Unknown adapter: '{name}'. Available: ollama")
