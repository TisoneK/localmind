"""
localmind start — launch the full LocalMind stack.

Boot order (A1 / B4):
  1. Check Ollama binary exists
  2. Start `ollama serve` if not already running (wait up to 10 s)
  3. Start FastAPI / uvicorn
  4. Open browser
  5. On Ctrl+C: shut down cleanly
"""
from __future__ import annotations
import asyncio
import shutil
import subprocess
import time
import threading
import webbrowser
from typing import Optional

import typer
from rich.console import Console
from rich.panel import Panel

console = Console()


async def _ollama_reachable(timeout: float = 2.0) -> bool:
    """Return True if Ollama's API is responding."""
    try:
        import httpx
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.get("http://localhost:11434/api/tags")
            return r.status_code == 200
    except Exception:
        return False


async def _wait_for_ollama(max_wait: int = 10) -> bool:
    """Poll Ollama until it responds or we time out. Returns True on success."""
    for _ in range(max_wait):
        await asyncio.sleep(1)
        if await _ollama_reachable():
            return True
    return False


async def _start_ollama_if_needed() -> None:
    """Ensure Ollama is running. Raises typer.Exit(1) if binary not found."""
    ollama_bin = shutil.which("ollama")
    if not ollama_bin:
        console.print(
            "[bold red]Ollama not found.[/bold red]\n"
            "Install from [link=https://ollama.ai]https://ollama.ai[/link] "
            "then run [bold]localmind start[/bold] again."
        )
        raise typer.Exit(1)

    if await _ollama_reachable():
        console.print("[green]✓[/green] Ollama is already running.")
        return

    console.print("[dim]Starting Ollama (`ollama serve`)…[/dim]")
    subprocess.Popen(
        [ollama_bin, "serve"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    ok = await _wait_for_ollama(max_wait=10)
    if ok:
        console.print("[green]✓[/green] Ollama started successfully.")
    else:
        console.print(
            "[yellow]⚠ Ollama is taking longer than expected.[/yellow]\n"
            "  LocalMind will start anyway. If the UI shows 'ollama offline',\n"
            "  wait a moment then refresh, or run [bold]ollama serve[/bold] manually."
        )


def command(
    host: str = typer.Option("127.0.0.1", help="Host to bind to"),
    port: int = typer.Option(8000, help="Port to listen on"),
    no_browser: bool = typer.Option(False, "--no-browser", help="Don't open browser automatically"),
    reload: bool = typer.Option(False, "--reload", help="Auto-reload on code changes (dev mode)"),
    skip_ollama: bool = typer.Option(False, "--skip-ollama", help="Skip Ollama startup check"),
):
    """Launch the LocalMind web UI and API server."""
    import uvicorn
    from core.config import settings

    # B4: Run Ollama lifecycle check before starting server
    if not skip_ollama:
        try:
            asyncio.run(_start_ollama_if_needed())
        except typer.Exit:
            raise

    model: Optional[str] = getattr(settings, "ollama_model", None)
    url = f"http://{host}:{port}"
    model_line = f"  Model:  [bold cyan]{model}[/bold cyan]\n" if model else ""

    console.print(Panel(
        f"[bold cyan]LocalMind[/bold cyan] is ready\n\n"
        f"{model_line}"
        f"  UI:   [link={url}]{url}[/link]\n"
        f"  API:  [link={url}/api/docs]{url}/api/docs[/link]\n\n"
        f"  Press [bold]Ctrl+C[/bold] to stop",
        title="[bold]LocalMind[/bold]",
        border_style="cyan",
    ))

    if not no_browser:
        def _open():
            time.sleep(1.5)
            webbrowser.open(url)
        threading.Thread(target=_open, daemon=True).start()

    uvicorn.run(
        "api.app:create_app",
        factory=True,
        host=host,
        port=port,
        reload=reload,
        log_level="warning",
    )
