"""
localmind start — launch the web UI and API server.
Opens the browser automatically when ready.
"""
from __future__ import annotations
import asyncio
import time
import webbrowser
from typing import Optional

import typer
from rich.console import Console
from rich.panel import Panel

console = Console()


def command(
    host: str = typer.Option("127.0.0.1", help="Host to bind to"),
    port: int = typer.Option(8000, help="Port to listen on"),
    no_browser: bool = typer.Option(False, "--no-browser", help="Don't open browser automatically"),
    reload: bool = typer.Option(False, "--reload", help="Auto-reload on code changes (dev mode)"),
):
    """Launch the LocalMind web UI and API server."""
    import uvicorn
    from api.app import create_app

    url = f"http://{host}:{port}"

    console.print(Panel(
        f"[bold cyan]LocalMind[/bold cyan] is starting...\n\n"
        f"  API:  [link={url}/api/docs]{url}/api/docs[/link]\n"
        f"  UI:   [link={url}]{url}[/link]\n\n"
        f"  Press [bold]Ctrl+C[/bold] to stop",
        title="[bold]LocalMind[/bold]",
        border_style="cyan",
    ))

    if not no_browser:
        # Open browser after a short delay to let the server start
        def open_browser():
            time.sleep(1.5)
            webbrowser.open(url)
        import threading
        threading.Thread(target=open_browser, daemon=True).start()

    uvicorn.run(
        "api.app:create_app",
        factory=True,
        host=host,
        port=port,
        reload=reload,
        log_level="warning",
    )
