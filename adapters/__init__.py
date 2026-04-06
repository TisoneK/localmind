"""
Adapter registry. Maps adapter names to classes.
Set LOCALMIND_ADAPTER in .env to switch runtimes.
"""
from adapters.base import BaseAdapter


def get_adapter(name: str) -> BaseAdapter:
    if name == "ollama":
        from adapters.ollama import OllamaAdapter
        return OllamaAdapter()
    # Future:
    # if name == "lmstudio":
    #     from adapters.lmstudio import LMStudioAdapter
    #     return LMStudioAdapter()
    raise ValueError(f"Unknown adapter: '{name}'. Available: ollama")
