import asyncio
import subprocess
import time
from contextlib import AsyncExitStack
from pathlib import Path
from uuid import uuid4

import typer
from rich.panel import Panel
from prompt_toolkit import PromptSession
from prompt_toolkit.completion import WordCompleter
from prompt_toolkit.history import FileHistory
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

from pydantic_ai import Agent
from pydantic_ai.models.instrumented import InstrumentationSettings

from co_cli._orchestrate import run_turn, _patch_dangling_tool_calls
from co_cli.agent import get_agent
from co_cli.deps import CoDeps
from co_cli.sandbox import SandboxProtocol, DockerSandbox, SubprocessBackend
from co_cli._telemetry import SQLiteSpanExporter
from co_cli.config import settings, DATA_DIR
from co_cli.display import console, set_theme, PROMPT_CHAR, TerminalFrontend
from co_cli._banner import display_welcome_banner
from co_cli.status import get_status, render_status_table
from co_cli._commands import dispatch as dispatch_command, CommandContext, COMMANDS

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
    help="Co — personal AI operator · local-first · approval-first",
    context_settings={"help_option_names": ["--help", "-h"]},
    invoke_without_command=True,
)


@app.callback()
def _default(ctx: typer.Context):
    """Start an interactive chat session (default when no subcommand is given)."""
    if ctx.invoked_subcommand is None:
        chat()


def _create_sandbox(session_id: str) -> SandboxProtocol:
    """Create sandbox backend based on settings with auto-detection fallback."""
    backend = settings.sandbox_backend

    if backend in ("docker", "auto"):
        try:
            import docker
            docker.from_env().ping()
            return DockerSandbox(
                image=settings.docker_image,
                container_name=f"co-runner-{session_id[:8]}",
                network_mode=settings.sandbox_network,
                mem_limit=settings.sandbox_mem_limit,
                cpus=settings.sandbox_cpus,
            )
        except Exception:
            if backend == "docker":
                raise  # explicit docker — don't hide the error

    console.print("[yellow]Docker unavailable — running without sandbox[/yellow]")
    return SubprocessBackend()


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
        sandbox=_create_sandbox(session_id),
        auto_confirm=settings.auto_confirm,
        session_id=session_id,
        obsidian_vault_path=vault_path,
        google_credentials_path=settings.google_credentials_path,
        sandbox_max_timeout=settings.sandbox_max_timeout,
        shell_safe_commands=settings.shell_safe_commands,
        slack_client=slack_client,
        brave_search_api_key=settings.brave_search_api_key,
        web_fetch_allowed_domains=settings.web_fetch_allowed_domains,
        web_fetch_blocked_domains=settings.web_fetch_blocked_domains,
        web_policy=settings.web_policy,
        memory_max_count=settings.memory_max_count,
        memory_dedup_window_days=settings.memory_dedup_window_days,
        memory_dedup_threshold=settings.memory_dedup_threshold,
        memory_decay_strategy=settings.memory_decay_strategy,
        memory_decay_percentage=settings.memory_decay_percentage,
        max_history_messages=settings.max_history_messages,
        tool_output_trim_chars=settings.tool_output_trim_chars,
        summarization_model=settings.summarization_model,
    )


async def _discover_mcp_tools(agent: Agent, native_tool_names: list[str]) -> list[str]:
    """Discover MCP tool names from connected servers (after async with agent).

    Falls back to ``{prefix}_*`` placeholders if list_tools() is unavailable.
    """
    from pydantic_ai.mcp import MCPServerStdio

    mcp_tool_names: list[str] = []
    native_set = set(native_tool_names)

    for toolset in agent.toolsets:
        # Unwrap approval wrappers to reach MCPServerStdio
        inner = getattr(toolset, "wrapped", toolset)
        if not isinstance(inner, MCPServerStdio):
            continue
        try:
            tools = await inner.list_tools()
            prefix = inner.tool_prefix or ""
            for t in tools:
                name = f"{prefix}_{t.name}" if prefix else t.name
                if name not in native_set:
                    mcp_tool_names.append(name)
        except Exception:
            # Server not yet connected or list failed — use placeholder
            prefix = inner.tool_prefix or "mcp"
            mcp_tool_names.append(f"{prefix}_*")

    return native_tool_names + sorted(mcp_tool_names)


async def chat_loop(verbose: bool = False):
    mcp_servers = settings.mcp_servers if settings.mcp_servers else None
    agent, model_settings, tool_names = get_agent(
        web_policy=settings.web_policy,
        mcp_servers=mcp_servers,
    )
    deps = create_deps()
    frontend = TerminalFrontend()
    completer = WordCompleter(
        [f"/{name}" for name in COMMANDS],
        sentence=True,
    )
    session = PromptSession(
        history=FileHistory(str(DATA_DIR / "history.txt")),
        completer=completer,
        complete_while_typing=False,
    )

    # Start agent context (connects MCP servers).
    # AsyncExitStack guarantees __aexit__ even if fallback path is taken.
    stack = AsyncExitStack()
    try:
        await stack.enter_async_context(agent)
    except Exception as e:
        console.print(f"[yellow]MCP servers unavailable ({e}) — continuing without MCP[/yellow]")
        await stack.aclose()  # clean up partially-started first agent
        stack = AsyncExitStack()
        agent, model_settings, tool_names = get_agent(
            web_policy=settings.web_policy,
        )
        await stack.enter_async_context(agent)
        mcp_servers = None

    message_history = []
    last_interrupt_time = 0.0
    try:
        # MCP tools discovered after context entry — update tool_names
        if mcp_servers:
            tool_names = await _discover_mcp_tools(agent, tool_names)

        info = get_status(tool_count=len(tool_names))
        display_welcome_banner(info)

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

                # /command — slash commands, no LLM
                if user_input.startswith("/"):
                    cmd_ctx = CommandContext(
                        message_history=message_history,
                        deps=deps,
                        agent=agent,
                        tool_names=tool_names,
                    )
                    handled, new_history = await dispatch_command(user_input, cmd_ctx)
                    if handled:
                        if new_history is not None:
                            message_history = new_history
                        if cmd_ctx.model_settings is not None:
                            model_settings = cmd_ctx.model_settings
                        continue

                # LLM turn — delegated to _orchestrate.run_turn()
                frontend.on_status("Co is thinking...")
                turn_result = await run_turn(
                    agent=agent,
                    user_input=user_input,
                    deps=deps,
                    message_history=message_history,
                    model_settings=model_settings,
                    max_request_limit=settings.max_request_limit,
                    http_retries=settings.model_http_retries,
                    verbose=verbose,
                    frontend=frontend,
                )
                message_history = turn_result.messages

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
        await stack.aclose()
        deps.sandbox.cleanup()


@app.command()
def chat(
    theme: str = typer.Option(None, "--theme", "-t", help="Color theme: dark or light"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Stream LLM thinking/reasoning tokens"),
):
    """Start an interactive chat session with Co."""
    if theme:
        settings.theme = theme
        set_theme(theme)
    try:
        asyncio.run(chat_loop(verbose=verbose))
    except KeyboardInterrupt:
        pass  # Safety net: asyncio.run() may re-raise after task cancellation


@app.command()
def status():
    """Show system health and tool availability."""
    info = get_status()
    console.print(render_status_table(info))


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
    from co_cli._trace_viewer import write_trace_html

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
    from co_cli._tail import run_tail

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
