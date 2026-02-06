import asyncio
import os
import subprocess
import time
from uuid import uuid4

import httpx
import typer
from rich.markdown import Markdown
from rich.table import Table
from prompt_toolkit import PromptSession
from prompt_toolkit.history import FileHistory
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

from pydantic_ai import Agent
from pydantic_ai.messages import ModelRequest, ToolCallPart, ToolReturnPart
from pydantic_ai.models.instrumented import InstrumentationSettings

from co_cli.agent import get_agent
from co_cli.deps import CoDeps
from co_cli.sandbox import Sandbox
from co_cli.telemetry import SQLiteSpanExporter
from co_cli.config import settings, DATA_DIR
from co_cli.display import console, PROMPT_CHAR
from co_cli.banner import display_welcome_banner

# Setup Telemetry - must be done before Agent.instrument_all()
from opentelemetry.sdk.resources import Resource

exporter = SQLiteSpanExporter()
resource = Resource.create({
    "service.name": "co-cli",
    "service.version": "0.2.4",
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


def create_deps() -> CoDeps:
    """Create deps from settings."""
    from pathlib import Path
    from co_cli.google_auth import ensure_google_credentials, build_google_service, ALL_GOOGLE_SCOPES

    # Resolve obsidian vault path
    vault_path = None
    if settings.obsidian_vault_path:
        vault_path = Path(settings.obsidian_vault_path)

    # Single auth call for all Google services (auto-runs gcloud if needed)
    google_creds = ensure_google_credentials(
        settings.google_credentials_path,
        ALL_GOOGLE_SCOPES,
    )
    google_drive = build_google_service("drive", "v3", google_creds)
    google_gmail = build_google_service("gmail", "v1", google_creds)
    google_calendar = build_google_service("calendar", "v3", google_creds)

    # Build Slack client
    slack_client = None
    if settings.slack_bot_token:
        from slack_sdk import WebClient
        slack_client = WebClient(token=settings.slack_bot_token)

    return CoDeps(
        sandbox=Sandbox(image=settings.docker_image),
        auto_confirm=settings.auto_confirm,
        session_id=uuid4().hex,
        obsidian_vault_path=vault_path,
        google_drive=google_drive,
        google_gmail=google_gmail,
        google_calendar=google_calendar,
        slack_client=slack_client,
    )


def _patch_dangling_tool_calls(
    messages: list, error_message: str = "Interrupted by user."
) -> list:
    """Patch message history if last response has unanswered tool calls.

    LLM models expect both a tool call and its corresponding return in
    history. Without this patch, the next agent.run() would fail.
    """
    if not messages:
        return messages

    last_msg = messages[-1]
    if not (hasattr(last_msg, "kind") and last_msg.kind == "response"):
        return messages

    tool_calls = [p for p in last_msg.parts if isinstance(p, ToolCallPart)]
    if not tool_calls:
        return messages

    return_parts = [
        ToolReturnPart(
            tool_name=tc.tool_name,
            tool_call_id=tc.tool_call_id,
            content=error_message,
        )
        for tc in tool_calls
    ]
    return messages + [ModelRequest(parts=return_parts)]


async def chat_loop():
    agent, model_settings = get_agent()
    deps = create_deps()
    session = PromptSession(history=FileHistory(str(DATA_DIR / "history.txt")))

    # Show model banner
    provider = settings.llm_provider.lower()
    if provider == "gemini":
        model_info = f"Gemini ({settings.gemini_model})"
    else:
        model_info = f"Ollama ({settings.ollama_model})"

    display_welcome_banner(model_info)

    message_history = []
    last_interrupt_time = 0.0
    try:
        while True:
            try:
                user_input = await session.prompt_async(f"Co {PROMPT_CHAR} ")
                last_interrupt_time = 0.0  # Reset on successful input
                if user_input.lower() in ["exit", "quit"]:
                    break
                if not user_input.strip():
                    continue

                console.print("[dim]Co is thinking...[/dim]")
                try:
                    result = await agent.run(
                        user_input, deps=deps, message_history=message_history,
                        model_settings=model_settings,
                    )
                    message_history = result.all_messages()
                    console.print(Markdown(result.output))
                except KeyboardInterrupt:
                    # Cancel current operation, don't count toward exit
                    # Patch any dangling tool calls so next turn doesn't fail
                    message_history = _patch_dangling_tool_calls(message_history)
                    console.print("\n[dim]Interrupted.[/dim]")

            except EOFError:
                break
            except KeyboardInterrupt:
                now = time.monotonic()
                if now - last_interrupt_time <= 2.0:
                    break
                last_interrupt_time = now
                console.print("\n[dim]Press Ctrl+C again to exit[/dim]")
            except Exception as e:
                console.print(f"[bold red]Error:[/bold red] {e}")
    finally:
        deps.sandbox.cleanup()


@app.command()
def chat(
    theme: str = typer.Option(None, "--theme", "-t", help="Color theme: dark or light"),
):
    """Start an interactive chat session with Co."""
    if theme:
        settings.theme = theme
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

    # Google credentials
    from co_cli.google_auth import GOOGLE_TOKEN_PATH, ADC_PATH
    if settings.google_credentials_path and os.path.exists(os.path.expanduser(settings.google_credentials_path)):
        google_status = "Configured"
        google_detail = settings.google_credentials_path
    elif GOOGLE_TOKEN_PATH.exists():
        google_status = "Configured"
        google_detail = str(GOOGLE_TOKEN_PATH)
    elif ADC_PATH.exists():
        google_status = "ADC Available"
        google_detail = str(ADC_PATH)
    else:
        google_status = "Not Found"
        google_detail = "Run 'co chat' to auto-setup or install gcloud"
    table.add_row("Google", google_status, google_detail)

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


@app.command()
def tail(
    trace_id: str = typer.Option(None, "--trace", "-t", help="Filter to a specific trace ID"),
    tools_only: bool = typer.Option(False, "--tools-only", help="Only show tool spans"),
    models_only: bool = typer.Option(False, "--models-only", help="Only show model/chat spans"),
    poll: float = typer.Option(1.0, "--poll", "-p", help="Poll interval in seconds"),
    no_follow: bool = typer.Option(False, "--no-follow", "-n", help="Print recent spans and exit"),
    last: int = typer.Option(20, "--last", "-l", help="Number of recent spans to show on startup"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show LLM input/output content for model spans"),
):
    """Tail agent spans in real time (like tail -f for OTel traces)."""
    from co_cli.tail import run_tail

    run_tail(
        trace_id=trace_id,
        tools_only=tools_only,
        models_only=models_only,
        poll_interval=poll,
        no_follow=no_follow,
        last=last,
        verbose=verbose,
    )


if __name__ == "__main__":
    app()
