"""localmind models — list available Ollama models with human-readable profiles."""
from __future__ import annotations
import asyncio
import typer
from rich.console import Console
from rich.table import Table

console = Console()


def command():
    """List available Ollama models and recommended profiles."""
    asyncio.run(_list())


async def _list():
    from adapters.ollama import OllamaAdapter
    from api.routes.models import MODEL_PROFILES
    from core.config import settings

    adapter = OllamaAdapter()

    try:
        available = set(await adapter.list_models())
    except Exception:
        console.print("[red]Cannot reach Ollama.[/red] Run: [bold]ollama serve[/bold]")
        raise typer.Exit(1)

    table = Table(show_header=True, header_style="bold", box=None, padding=(0, 2))
    table.add_column("Model")
    table.add_column("Profile")
    table.add_column("Best for")
    table.add_column("RAM")
    table.add_column("Status")

    for name, profile in MODEL_PROFILES.items():
        is_available = name in available or any(
            a.startswith(name.split(":")[0]) for a in available
        )
        is_active = name == settings.ollama_model
        status = "[green]✓ active[/green]" if is_active else (
            "[dim]pulled[/dim]" if is_available else "[dim]not pulled[/dim]"
        )
        table.add_row(
            f"[cyan]{name}[/cyan]" if is_available else f"[dim]{name}[/dim]",
            profile["label"],
            profile["best_for"],
            f"{profile['min_ram_gb']} GB",
            status,
        )

    console.print()
    console.print(table)
    console.print()
    console.print(f"Active model: [bold cyan]{settings.ollama_model}[/bold cyan]")
    console.print("To pull a model: [bold]ollama pull <model-name>[/bold]")
    console.print("To change model: set [bold]OLLAMA_MODEL[/bold] in .env")
