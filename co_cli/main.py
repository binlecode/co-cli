import asyncio
import json
import signal
import subprocess
import time
from pathlib import Path
from uuid import uuid4

import typer
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table
from prompt_toolkit import PromptSession
from prompt_toolkit.history import FileHistory
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

from pydantic_ai import Agent, DeferredToolRequests, DeferredToolResults, ToolDenied
from pydantic_ai.messages import ModelRequest, ToolCallPart, ToolReturnPart
from pydantic_ai.models.instrumented import InstrumentationSettings
from pydantic_ai.usage import UsageLimits
from rich.prompt import Prompt

from co_cli.agent import get_agent
from co_cli.deps import CoDeps
from co_cli.sandbox import Sandbox
from co_cli.telemetry import SQLiteSpanExporter
from co_cli.config import settings, DATA_DIR
from co_cli.display import console, PROMPT_CHAR
from co_cli.banner import display_welcome_banner
from co_cli.status import get_status

# Setup Telemetry - must be done before Agent.instrument_all()
from opentelemetry.sdk.resources import Resource

exporter = SQLiteSpanExporter()

# get_status() is lazy; just read version directly for telemetry bootstrap
import tomllib as _tomllib
_VERSION = _tomllib.loads(
    (Path(__file__).resolve().parent.parent / "pyproject.toml").read_text()
)["project"]["version"]

resource = Resource.create({
    "service.name": "co-cli",
    "service.version": _VERSION,
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

app = typer.Typer(
    help="Co - The Production-Grade Personal Assistant CLI",
    context_settings={"help_option_names": ["--help", "-h"]},
)


def create_deps() -> CoDeps:
    """Create deps from settings."""
    session_id = uuid4().hex

    # Resolve obsidian vault path
    vault_path = None
    if settings.obsidian_vault_path:
        vault_path = Path(settings.obsidian_vault_path)

    # Build Slack client
    slack_client = None
    if settings.slack_bot_token:
        from slack_sdk import WebClient
        slack_client = WebClient(token=settings.slack_bot_token)

    return CoDeps(
        sandbox=Sandbox(
            image=settings.docker_image,
            container_name=f"co-runner-{session_id[:8]}",
        ),
        auto_confirm=settings.auto_confirm,
        session_id=session_id,
        obsidian_vault_path=vault_path,
        google_credentials_path=settings.google_credentials_path,
        sandbox_max_timeout=settings.sandbox_max_timeout,
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


_CHOICES_HINT = " [[green]y[/green]/[red]n[/red]/[bold orange3]a[/bold orange3](yolo)]"


async def _handle_approvals(agent, deps, result, model_settings):
    """Prompt user [y/n/a(yolo)] for each pending tool call, then resume."""
    approvals = DeferredToolResults()

    # Temporarily restore the default SIGINT handler during the synchronous
    # Prompt.ask() calls.  asyncio.run() replaces SIGINT with a handler that
    # cancels the main task, which cannot interrupt a blocking input() call.
    # Restoring default_int_handler lets Ctrl-C raise KeyboardInterrupt.
    prev_handler = signal.getsignal(signal.SIGINT)
    signal.signal(signal.SIGINT, signal.default_int_handler)
    try:
        for call in result.output.approvals:
            args = call.args
            if isinstance(args, str):
                args = json.loads(args)
            args = args or {}
            args_str = ", ".join(f"{k}={v!r}" for k, v in args.items())
            desc = f"{call.tool_name}({args_str})"

            if deps.auto_confirm:
                approvals.approvals[call.tool_call_id] = True
                continue

            console.print(f"Approve [bold]{desc}[/bold]?" + _CHOICES_HINT, end=" ")
            choice = Prompt.ask(
                "", choices=["y", "n", "a"], default="n",
                show_choices=False, show_default=False, console=console,
            )
            if choice == "a":
                deps.auto_confirm = True
                console.print("[bold orange3]YOLO mode enabled — auto-approving for this session[/bold orange3]")
                approvals.approvals[call.tool_call_id] = True
            elif choice == "y":
                approvals.approvals[call.tool_call_id] = True
            else:
                approvals.approvals[call.tool_call_id] = ToolDenied("User denied this action")
    finally:
        signal.signal(signal.SIGINT, prev_handler)

    return await agent.run(
        None,
        deps=deps,
        message_history=result.all_messages(),
        deferred_tool_results=approvals,
        model_settings=model_settings,
        usage_limits=UsageLimits(request_limit=settings.max_request_limit),
    )


def _display_tool_outputs(old_len: int, messages: list) -> None:
    """Show tool return values so the user sees raw output, not just the LLM summary."""
    # Map tool_call_id → shell command for nicer titles
    cmds: dict[str, str] = {}
    for msg in messages[old_len:]:
        if hasattr(msg, "kind") and msg.kind == "response":
            for part in msg.parts:
                if isinstance(part, ToolCallPart) and part.tool_name == "run_shell_command":
                    args = part.args_as_dict()
                    cmds[part.tool_call_id] = args.get("cmd", "")

    for msg in messages[old_len:]:
        if not (hasattr(msg, "kind") and msg.kind == "request"):
            continue
        for part in msg.parts:
            if not isinstance(part, ToolReturnPart):
                continue
            content = part.content
            if isinstance(content, str) and content.strip():
                title = cmds.get(part.tool_call_id, part.tool_name)
                console.print(Panel(content.rstrip(), title=f"$ {title}", border_style="shell"))
            elif isinstance(content, dict) and "display" in content:
                console.print(content["display"])


async def chat_loop():
    agent, model_settings = get_agent()
    deps = create_deps()
    session = PromptSession(history=FileHistory(str(DATA_DIR / "history.txt")))

    info = get_status(tool_count=len(agent._function_toolset.tools))
    display_welcome_banner(info)

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

                # !command — run directly in sandbox, no LLM
                if user_input.startswith("!"):
                    cmd = user_input[1:].strip()
                    if cmd:
                        try:
                            output = await deps.sandbox.run_command(
                                cmd, timeout=deps.sandbox_max_timeout,
                            )
                            if output.strip():
                                console.print(Panel(
                                    output.rstrip(), title=f"$ {cmd}", border_style="shell",
                                ))
                        except Exception as e:
                            console.print(f"[bold red]Error:[/bold red] {e}")
                    continue

                console.print("[dim]Co is thinking...[/dim]")
                result = None
                try:
                    result = await agent.run(
                        user_input, deps=deps, message_history=message_history,
                        model_settings=model_settings,
                        usage_limits=UsageLimits(request_limit=settings.max_request_limit),
                    )

                    # Handle deferred tool approvals (loop: resumed run may trigger more)
                    while isinstance(result.output, DeferredToolRequests):
                        result = await _handle_approvals(
                            agent, deps, result, model_settings,
                        )

                    all_msgs = result.all_messages()
                    _display_tool_outputs(len(message_history), all_msgs)
                    message_history = all_msgs
                    console.print(Markdown(result.output))
                except (KeyboardInterrupt, asyncio.CancelledError):
                    # Cancel current operation, don't count toward exit.
                    # asyncio.run() handles SIGINT by cancelling the main task
                    # (CancelledError), not by raising KeyboardInterrupt directly.
                    # Use result.all_messages() when available so dangling tool
                    # calls from the current turn are patched correctly.
                    msgs = result.all_messages() if result else message_history
                    message_history = _patch_dangling_tool_calls(msgs)
                    console.print("\n[dim]Interrupted.[/dim]")

            except EOFError:
                break
            except (KeyboardInterrupt, asyncio.CancelledError):
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
    try:
        asyncio.run(chat_loop())
    except KeyboardInterrupt:
        pass  # Safety net: asyncio.run() may re-raise after task cancellation


@app.command()
def status():
    """Show system health and tool availability."""
    info = get_status()

    table = Table(title=f"Co System Status (Provider: {info.llm_provider})")
    table.add_column("Component", style="cyan")
    table.add_column("Status", style="magenta")
    table.add_column("Details", style="green")

    table.add_row("LLM", info.llm_status.title(), info.llm_provider)
    table.add_row("Docker", info.docker.title(), "Sandbox runtime")
    table.add_row("Google", info.google.title(), info.google_detail)
    table.add_row("Obsidian", info.obsidian.title(), settings.obsidian_vault_path or "None")
    table.add_row("Slack", info.slack.title(), "Bot token" if info.slack == "configured" else "—")
    table.add_row("Database", "Active", info.db_size)

    console.print(table)


@app.command()
def logs():
    """Launch a local dashboard (Datasette) to inspect agent traces."""
    import webbrowser

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
    trace_id: str = typer.Option(None, "--trace", "-i", help="Filter to a specific trace ID"),
    tools_only: bool = typer.Option(False, "--tools-only", "-T", help="Only show tool spans"),
    models_only: bool = typer.Option(False, "--models-only", "-m", help="Only show model/chat spans"),
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
