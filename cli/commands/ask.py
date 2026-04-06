"""
localmind ask "..." — one-shot question from the terminal.
Supports file attachment via --file flag.
Supports stdin piping: cat file.txt | localmind ask "summarise this"
"""
from __future__ import annotations
import asyncio
import sys
import uuid
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.markdown import Markdown

console = Console()


def command(
    message: str = typer.Argument(..., help="Your question or instruction"),
    file: Optional[Path] = typer.Option(None, "--file", "-f", help="File to attach"),
    session: Optional[str] = typer.Option(None, "--session", "-s", help="Session ID (auto-generated if omitted)"),
    model: Optional[str] = typer.Option(None, "--model", "-m", help="Override model for this request"),
    raw: bool = typer.Option(False, "--raw", help="Print raw text without markdown rendering"),
):
    """Ask a one-shot question. Optionally attach a file."""
    asyncio.run(_run(message, file, session, model, raw))


async def _run(message: str, file: Optional[Path], session: Optional[str], model: Optional[str], raw: bool):
    from core.engine import Engine
    from core.config import settings

    # Handle stdin piping
    stdin_content = None
    if not sys.stdin.isatty():
        stdin_content = sys.stdin.read().strip()
        if stdin_content:
            message = f"{message}\n\n{stdin_content}"

    # Override model if requested
    if model:
        settings.ollama_model = model

    session_id = session or str(uuid.uuid4())
    engine = Engine()

    # Load file bytes if provided
    file_bytes = None
    filename = None
    content_type = None
    if file:
        if not file.exists():
            console.print(f"[red]File not found:[/red] {file}")
            raise typer.Exit(1)
        file_bytes = file.read_bytes()
        filename = file.name
        content_type = _guess_mime(file)
        console.print(f"[dim]Attached: {filename} ({len(file_bytes):,} bytes)[/dim]")

    # Stream response
    console.print()
    full_response = []
    with console.status("[dim]Thinking...[/dim]", spinner="dots"):
        chunks = []
        async for chunk in engine.process(
            message=message,
            session_id=session_id,
            file=file_bytes,
            filename=filename,
            content_type=content_type,
        ):
            if chunk.error:
                console.print(f"\n[red]Error:[/red] {chunk.error}")
                raise typer.Exit(1)
            chunks.append(chunk.text)
            if chunk.done:
                break

    response_text = "".join(chunks)

    if raw:
        print(response_text)
    else:
        console.print(Markdown(response_text))

    console.print(f"\n[dim]Session: {session_id}[/dim]")


def _guess_mime(path: Path) -> str:
    import mimetypes
    mime, _ = mimetypes.guess_type(str(path))
    return mime or "application/octet-stream"
