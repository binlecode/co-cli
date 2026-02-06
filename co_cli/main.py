import asyncio
import os
import subprocess
from uuid import uuid4

import httpx
import typer
from rich.console import Console
from rich.markdown import Markdown
from rich.table import Table
from prompt_toolkit import PromptSession
from prompt_toolkit.history import FileHistory
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

from pydantic_ai import Agent
from pydantic_ai.models.instrumented import InstrumentationSettings

from co_cli.agent import get_agent
from co_cli.deps import CoDeps
from co_cli.sandbox import Sandbox
from co_cli.telemetry import SQLiteSpanExporter
from co_cli.config import settings, DATA_DIR

# Setup Telemetry - must be done before Agent.instrument_all()
from opentelemetry.sdk.resources import Resource

exporter = SQLiteSpanExporter()
resource = Resource.create({
    "service.name": "co-cli",
    "service.version": "0.1.0",
})
tracer_provider = TracerProvider(resource=resource)
tracer_provider.add_span_processor(BatchSpanProcessor(exporter))
trace.set_tracer_provider(tracer_provider)

# Enable pydantic-ai instrumentation for all agents
# Using version=3 for latest OTel GenAI semantic conventions (spec compliant)
Agent.instrument_all(InstrumentationSettings(
    tracer_provider=tracer_provider,
    version=3,
))

app = typer.Typer(help="Co - The Production-Grade Personal Assistant CLI")
console = Console()


def create_deps() -> CoDeps:
    """Create deps from settings."""
    from pathlib import Path

    # Resolve obsidian vault path
    vault_path = None
    if settings.obsidian_vault_path:
        vault_path = Path(settings.obsidian_vault_path)

    return CoDeps(
        sandbox=Sandbox(image=settings.docker_image),
        auto_confirm=settings.auto_confirm,
        session_id=uuid4().hex,
        obsidian_vault_path=vault_path,
    )


async def chat_loop():
    agent = get_agent()
    deps = create_deps()
    session = PromptSession(history=FileHistory(str(DATA_DIR / "history.txt")))

    # Show model banner
    provider = settings.llm_provider.lower()
    if provider == "gemini":
        model_info = f"Gemini ({settings.gemini_model})"
    else:
        model_info = f"Ollama ({settings.ollama_model})"

    console.print(f"[bold blue]Co is active.[/bold blue] Model: [cyan]{model_info}[/cyan]")
    console.print("[dim]Type 'exit' or 'quit' to stop[/dim]")

    try:
        while True:
            try:
                user_input = await session.prompt_async("Co > ")
                if user_input.lower() in ["exit", "quit"]:
                    break
                if not user_input.strip():
                    continue

                console.print("[dim]Co is thinking...[/dim]")
                result = await agent.run(user_input, deps=deps)
                console.print(Markdown(result.output))

            except (EOFError, KeyboardInterrupt):
                break
            except Exception as e:
                console.print(f"[bold red]Error:[/bold red] {e}")
    finally:
        deps.sandbox.cleanup()


@app.command()
def chat():
    """Start an interactive chat session with Co."""
    asyncio.run(chat_loop())


@app.command()
def status():
    """Show system health and tool availability."""
    provider = settings.llm_provider.lower()

    table = Table(title=f"Co System Status (Provider: {provider.title()})")
    table.add_column("Component", style="cyan")
    table.add_column("Status", style="magenta")
    table.add_column("Details", style="green")

    if provider == "gemini":
        api_key = settings.gemini_api_key
        model_name = settings.gemini_model
        status = "Configured" if api_key else "Missing API Key"
        table.add_row("Gemini", status, model_name)
    else:
        # Check Ollama
        ollama_host = settings.ollama_host
        model_name = settings.ollama_model
        try:
            response = httpx.get(ollama_host)
            if response.status_code == 200:
                ollama_status = "Online"
            else:
                ollama_status = f"Offline ({response.status_code})"
        except Exception:
            ollama_status = "Offline"

        table.add_row("Ollama", ollama_status, f"Host: {ollama_host}")
        table.add_row("Model", "Ready" if ollama_status == "Online" else "N/A", model_name)

    obsidian_path = settings.obsidian_vault_path
    obsidian_status = "Configured" if obsidian_path and os.path.exists(obsidian_path) else "Not Found"
    table.add_row("Obsidian", obsidian_status, obsidian_path or "None")

    # DB Size
    db_path = DATA_DIR / "co-cli.db"
    db_size = f"{os.path.getsize(db_path) / 1024:.1f} KB" if db_path.exists() else "0 KB"
    table.add_row("Database", "Active", db_size)

    console.print(table)


@app.command()
def logs():
    """Launch a local dashboard (Datasette) to inspect agent traces."""
    import webbrowser
    from pathlib import Path

    db_path = DATA_DIR / "co-cli.db"
    if not db_path.exists():
        console.print("[yellow]No logs found yet.[/yellow]")
        return

    # Metadata file for better display
    metadata_path = Path(__file__).parent / "datasette_metadata.json"

    url = "http://127.0.0.1:8001"
    console.print(f"[bold green]Opening Datasette dashboard...[/bold green]")
    console.print(f"[cyan]URL: {url}[/cyan]")
    console.print("[dim]Press Ctrl+C to stop[/dim]")

    # Auto-open browser after a short delay
    import threading
    threading.Timer(1.0, lambda: webbrowser.open(url)).start()

    cmd = ["datasette", str(db_path), "--port", "8001"]
    if metadata_path.exists():
        cmd.extend(["--metadata", str(metadata_path)])

    try:
        subprocess.run(cmd)
    except KeyboardInterrupt:
        pass


@app.command()
def traces():
    """Open a visual trace viewer with nested spans (like Logfire)."""
    import webbrowser
    from co_cli.trace_viewer import write_trace_html

    db_path = DATA_DIR / "co-cli.db"
    if not db_path.exists():
        console.print("[yellow]No traces found yet. Run 'co chat' first.[/yellow]")
        return

    html_path = write_trace_html()
    console.print(f"[bold green]Generated trace viewer:[/bold green] {html_path}")
    webbrowser.open(f"file://{html_path}")


if __name__ == "__main__":
    app()
