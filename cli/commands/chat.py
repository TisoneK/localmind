"""
localmind chat — interactive REPL session.
Maintains session history across turns.
Commands: /exit /clear /file <path> /session /help
"""
from __future__ import annotations
import asyncio
import uuid
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.prompt import Prompt

console = Console()


def command(
    session: Optional[str] = typer.Option(None, "--session", "-s", help="Resume an existing session"),
    model: Optional[str] = typer.Option(None, "--model", "-m", help="Model to use"),
):
    """Start an interactive chat session."""
    asyncio.run(_repl(session, model))


async def _repl(session: Optional[str], model: Optional[str]):
    from core.engine import Engine
    from core.config import settings

    if model:
        settings.ollama_model = model

    session_id = session or str(uuid.uuid4())
    engine = Engine()

    console.print(Panel(
        f"[bold cyan]LocalMind[/bold cyan] interactive chat\n"
        f"Session: [dim]{session_id}[/dim]\n"
        f"Model:   [dim]{settings.ollama_model}[/dim]\n\n"
        f"Commands: [bold]/file <path>[/bold]  [bold]/clear[/bold]  [bold]/session[/bold]  [bold]/exit[/bold]",
        border_style="cyan",
    ))

    pending_file: Optional[Path] = None

    while True:
        try:
            prefix = "[file attached] " if pending_file else ""
            user_input = Prompt.ask(f"\n[bold cyan]{prefix}You[/bold cyan]")
        except (KeyboardInterrupt, EOFError):
            console.print("\n[dim]Goodbye.[/dim]")
            break

        stripped = user_input.strip()
        if not stripped:
            continue

        # ── Built-in commands ───────────────────────────────────────────
        if stripped == "/exit" or stripped == "/quit":
            console.print("[dim]Goodbye.[/dim]")
            break

        if stripped == "/clear":
            console.clear()
            pending_file = None
            continue

        if stripped == "/session":
            console.print(f"[dim]Session ID: {session_id}[/dim]")
            continue

        if stripped == "/help":
            console.print(
                "[bold]/file <path>[/bold]  Attach a file to your next message\n"
                "[bold]/clear[/bold]        Clear the screen\n"
                "[bold]/session[/bold]      Show current session ID\n"
                "[bold]/exit[/bold]         Exit"
            )
            continue

        if stripped.startswith("/file "):
            file_path = Path(stripped[6:].strip())
            if not file_path.exists():
                console.print(f"[red]File not found:[/red] {file_path}")
            else:
                pending_file = file_path
                console.print(f"[dim]File attached: {file_path.name} ({file_path.stat().st_size:,} bytes)[/dim]")
            continue

        # ── Process message ─────────────────────────────────────────────
        file_bytes = None
        filename = None
        content_type = None

        if pending_file:
            file_bytes = pending_file.read_bytes()
            filename = pending_file.name
            import mimetypes
            content_type, _ = mimetypes.guess_type(str(pending_file))
            content_type = content_type or "application/octet-stream"
            pending_file = None

        console.print("\n[bold]LocalMind[/bold]")
        full_response = []

        try:
            async for chunk in engine.process(
                message=stripped,
                session_id=session_id,
                file=file_bytes,
                filename=filename,
                content_type=content_type,
            ):
                if chunk.error:
                    console.print(f"[red]Error:[/red] {chunk.error}")
                    break
                full_response.append(chunk.text)
                if chunk.done:
                    break

            response_text = "".join(full_response)
            console.print(Markdown(response_text))

        except Exception as e:
            console.print(f"[red]Unexpected error:[/red] {e}")
