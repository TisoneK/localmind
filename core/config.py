"""
LocalMind configuration — loaded from environment / .env file.
All settings validated by Pydantic at startup.
"""
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Ollama
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "llama3.1:8b"
    ollama_timeout: int = 120

    # Adapter
    localmind_adapter: str = "ollama"

    # Server
    localmind_host: str = "127.0.0.1"
    localmind_port: int = 8000
    localmind_log_level: str = "INFO"

    # Storage
    localmind_db_path: str = "./localmind.db"
    localmind_chroma_path: str = "./chroma_db"

    # File reader
    localmind_max_file_size_mb: int = 50
    localmind_chunk_size_tokens: int = 1500
    localmind_chunk_overlap_tokens: int = 200

    # Web search
    localmind_search_provider: str = "duckduckgo"
    brave_search_api_key: str = ""

    # Code executor
    localmind_code_exec_timeout: int = 30
    localmind_code_exec_enabled: bool = True

    # Context window management
    localmind_response_reserve_tokens: int = 2048
    localmind_history_max_tokens: int = 4096


# Singleton — import this everywhere
settings = Settings()
