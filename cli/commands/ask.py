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

    # Stream response live — print chunks as they arrive instead of buffering
    # behind a "Thinking..." spinner. The spinner approach collected all chunks
    # silently and only rendered after done=True, causing a long blank wait on
    # slow local models.
    console.print()
    console.print("[bold]LocalMind[/bold]")
    full_response = []
    try:
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
            if chunk.text:
                # Print each token/chunk immediately so the user sees output
                # as the model generates it rather than waiting for completion.
                print(chunk.text, end="", flush=True)
                full_response.append(chunk.text)
            if chunk.done:
                break
    except typer.Exit:
        raise
    except Exception as e:
        console.print(f"\n[red]Unexpected error:[/red] {e}")
        raise typer.Exit(1)

    print()  # newline after streamed output
    response_text = "".join(full_response)

    # Re-render as markdown only if --raw is not set and output went to a terminal
    # (piped output keeps the raw streamed text already printed above)
    if not raw and sys.stdout.isatty():
        # Clear the raw-streamed text and reprint as rendered markdown
        # Only do this if the response contains markdown signals
        has_markdown = any(
            marker in response_text
            for marker in ("```", "**", "##", "- ", "| ")
        )
        if has_markdown:
            # Move cursor up to overwrite the streamed plain text with rendered version
            # This is best-effort — terminal support varies. Fall back to just printing.
            try:
                lines_printed = response_text.count("\n") + 1
                console.print(f"\033[{lines_printed}A\033[J", end="")
                console.print(Markdown(response_text))
            except Exception:
                pass  # streamed text already visible — no harm done

    console.print(f"\n[dim]Session: {session_id}[/dim]")


def _guess_mime(path: Path) -> str:
    import mimetypes
    mime, _ = mimetypes.guess_type(str(path))
    return mime or "application/octet-stream"
