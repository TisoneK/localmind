"""
LocalMind configuration — loaded from environment / .env file.
All settings validated by Pydantic at startup.
"""
import os
import sys
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field


def _default_home() -> str:
    """Return platform-appropriate LocalMind home directory."""
    if sys.platform == "win32":
        base = Path(os.environ.get("USERPROFILE", Path.home()))
    else:
        base = Path.home()
    return str(base / "LocalMind")


def _default_uploads() -> str:
    return str(Path(_default_home()) / "uploads")


def _allowed_user_folders() -> list[str]:
    """Folders the model is allowed to READ from (never OS/system folders)."""
    if sys.platform == "win32":
        base = Path(os.environ.get("USERPROFILE", Path.home()))
        return [
            str(base / "Downloads"),
            str(base / "Documents"),
            str(base / "Pictures"),
            str(base / "Videos"),
            str(base / "Music"),
            str(base / "Desktop"),
            str(Path(_default_home())),
        ]
    else:
        home = Path.home()
        return [
            str(home / "Downloads"),
            str(home / "Documents"),
            str(home / "Pictures"),
            str(home / "Videos"),
            str(home / "Music"),
            str(home / "Desktop"),
            str(Path(_default_home())),
        ]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        frozen=False,   # allow hot-swap of ollama_model at runtime
    )

    # Ollama
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "llama3.1:8b"
    ollama_embed_model: str = "nomic-embed-text"  # pin via OLLAMA_EMBED_MODEL in .env
    ollama_timeout: int = 120
    ollama_keep_alive: str = "-1"   # -1 = keep model loaded forever; e.g. "5m", "1h"

    # Adapter
    localmind_adapter: str = "ollama"

    # Server
    localmind_host: str = "127.0.0.1"
    localmind_port: int = 8000
    localmind_log_level: str = "INFO"

    # Storage — default to ~/LocalMind/
    localmind_home: str = Field(default_factory=_default_home)
    localmind_db_path: str = ""          # resolved at startup if empty
    localmind_uploads_path: str = Field(default_factory=_default_uploads)

    # File reader
    localmind_max_file_size_mb: int = 50
    localmind_chunk_size_tokens: int = 1500
    localmind_chunk_overlap_tokens: int = 200

    # Web search
    localmind_search_provider: str = "tiered"  # tiered, duckduckgo, searxng, brave
    brave_search_api_key: str = ""
    searxng_url: str = "https://searx.be"

    # Code executor
    localmind_code_exec_timeout: int = 30
    localmind_code_exec_enabled: bool = True

    # Shell tool
    localmind_shell_enabled: bool = False

    # Context window management
    localmind_response_reserve_tokens: int = 2048
    localmind_history_max_tokens: int = 4096

    # Model routing
    ollama_model_fast: str = ""
    ollama_model_code: str = ""

    # Intent-specific timeouts (seconds) — override per model/environment in .env
    ollama_timeout_chat: int = 90
    ollama_timeout_web_search: int = 180
    ollama_timeout_file_task: int = 180
    ollama_timeout_file_write: int = 180
    ollama_timeout_shell: int = 180
    ollama_timeout_code_exec: int = 240
    ollama_timeout_sysinfo: int = 60
    ollama_timeout_memory_op: int = 120
    ollama_timeout_default: int = 180

    # Agent loop
    localmind_agent_enabled: bool = True
    localmind_agent_max_iterations: int = 6

    # Permission gates — require user confirmation before write/delete
    localmind_require_write_permission: bool = True

    def resolve_paths(self) -> None:
        """Resolve dynamic paths after init. Call once at startup."""
        home = Path(self.localmind_home).expanduser()
        home.mkdir(parents=True, exist_ok=True)
        Path(self.localmind_uploads_path).expanduser().mkdir(parents=True, exist_ok=True)
        if not self.localmind_db_path:
            self.localmind_db_path = str(home / "localmind.db")
        else:
            # Expand ~ and convert relative paths to absolute so VectorStore
            # always opens the same file regardless of working directory.
            p = Path(self.localmind_db_path).expanduser()
            if not p.is_absolute():
                p = (home / p).resolve()
            self.localmind_db_path = str(p)

    def allowed_read_paths(self) -> list[Path]:
        return [Path(p) for p in _allowed_user_folders()]

    def is_path_allowed(self, path: Path) -> bool:
        """Return True if path is within an allowed user folder."""
        try:
            resolved = path.resolve()
        except Exception:
            return False
        for allowed in self.allowed_read_paths():
            try:
                resolved.relative_to(allowed.resolve())
                return True
            except ValueError:
                continue
        return False


# Singleton
settings = Settings()
settings.resolve_paths()
