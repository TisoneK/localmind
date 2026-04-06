"""localmind sessions — list and manage conversation sessions."""
from __future__ import annotations
import asyncio
from datetime import datetime
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

console = Console()
app = typer.Typer()


def command(
    delete: Optional[str] = typer.Option(None, "--delete", "-d", help="Delete a session by ID"),
    clear_all: bool = typer.Option(False, "--clear-all", help="Delete all sessions"),
):
    """List conversation sessions or delete them."""
    asyncio.run(_run(delete, clear_all))


async def _run(delete: Optional[str], clear_all: bool):
    from storage.db import SessionStore
    from core.config import settings

    store = SessionStore(settings.localmind_db_path)

    if clear_all:
        confirm = typer.confirm("Delete ALL sessions? This cannot be undone.")
        if confirm:
            store.clear_all()
            console.print("[green]All sessions deleted.[/green]")
        return

    if delete:
        ok = store.delete_session(delete)
        if ok:
            console.print(f"[green]Deleted session:[/green] {delete}")
        else:
            console.print(f"[red]Session not found:[/red] {delete}")
        return

    sessions = store.list_sessions()

    if not sessions:
        console.print("[dim]No sessions yet. Start chatting with:[/dim] localmind chat")
        return

    table = Table(show_header=True, header_style="bold", box=None, padding=(0, 2))
    table.add_column("Session ID")
    table.add_column("Messages")
    table.add_column("Last active")
    table.add_column("Created")

    for s in sessions:
        last_active = datetime.fromtimestamp(s["last_active"]).strftime("%Y-%m-%d %H:%M") if s["last_active"] else "—"
        created = datetime.fromtimestamp(s["created_at"]).strftime("%Y-%m-%d %H:%M")
        table.add_row(
            f"[dim]{s['id'][:16]}...[/dim]",
            str(s["message_count"]),
            last_active,
            created,
        )

    console.print()
    console.print(table)
    console.print(f"\n[dim]{len(sessions)} session(s). Delete with: localmind sessions --delete <id>[/dim]")
