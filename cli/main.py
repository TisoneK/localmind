"""
LocalMind CLI — entry point.

Commands are defined in cli/commands/ and registered here.
Each command is a separate module for clean separation.

Usage:
    localmind start              # Launch web UI
    localmind ask "..."          # One-shot question
    localmind ask "..." --file x # Ask about a file
    localmind chat               # Interactive REPL
    localmind models             # List available models
    localmind health             # Check Ollama connectivity
    localmind sessions           # List conversation sessions
"""
import typer
from cli.commands import ask, chat, start, models, health, sessions

app = typer.Typer(
    name="localmind",
    help="Local AI with tool use — make Ollama work like Claude.",
    no_args_is_help=True,
    rich_markup_mode="rich",
)

# Register sub-commands
app.command("ask")(ask.command)
app.command("chat")(chat.command)
app.command("start")(start.command)
app.command("models")(models.command)
app.command("health")(health.command)
app.command("sessions")(sessions.command)


if __name__ == "__main__":
    app()
