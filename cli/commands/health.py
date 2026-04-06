"""localmind health — check Ollama connectivity and tool availability."""
from __future__ import annotations
import asyncio
import typer
from rich.console import Console
from rich.table import Table

console = Console()


def command():
    """Check that Ollama is running and all tools are available."""
    asyncio.run(_check())


async def _check():
    from adapters import get_adapter
    from core.config import settings
    from tools import list_tools

    console.print("\n[bold]LocalMind Health Check[/bold]\n")

    # Ollama connectivity
    adapter = get_adapter(settings.localmind_adapter)
    reachable = await adapter.health_check()

    if reachable:
        console.print(f"  [green]✓[/green] Ollama reachable at [dim]{settings.ollama_base_url}[/dim]")
        models = await adapter.list_models()
        if models:
            console.print(f"  [green]✓[/green] Models available: [dim]{', '.join(models[:5])}[/dim]")
        else:
            console.print("  [yellow]![/yellow] No models pulled. Run: [bold]ollama pull llama3.1:8b[/bold]")
    else:
        console.print(f"  [red]✗[/red] Ollama not reachable at [dim]{settings.ollama_base_url}[/dim]")
        console.print("    → Start Ollama: [bold]ollama serve[/bold]")

    # Active model
    model_ok = settings.ollama_model in (await adapter.list_models() if reachable else [])
    if model_ok:
        console.print(f"  [green]✓[/green] Active model: [dim]{settings.ollama_model}[/dim]")
    else:
        console.print(f"  [yellow]![/yellow] Active model [dim]{settings.ollama_model}[/dim] not pulled")
        console.print(f"    → Run: [bold]ollama pull {settings.ollama_model}[/bold]")

    # Tools
    console.print()
    table = Table(show_header=True, header_style="bold", box=None)
    table.add_column("Tool", style="cyan")
    table.add_column("Risk")
    table.add_column("Status")

    for tool in list_tools():
        risk_color = {"low": "green", "medium": "yellow", "high": "red"}.get(tool["risk"], "white")
        table.add_row(
            tool["intent"],
            f"[{risk_color}]{tool['risk']}[/{risk_color}]",
            "[green]ready[/green]",
        )
    console.print(table)

    console.print()
    if reachable and model_ok:
        console.print("[green]All systems operational.[/green]")
        raise typer.Exit(0)
    else:
        console.print("[yellow]Some issues found. See above.[/yellow]")
        raise typer.Exit(1)
