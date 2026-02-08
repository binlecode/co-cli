import asyncio
import json
import signal
import subprocess
import time
from pathlib import Path
from uuid import uuid4

import typer
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel
from prompt_toolkit import PromptSession
from prompt_toolkit.completion import WordCompleter
from prompt_toolkit.history import FileHistory
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

from pydantic_ai import Agent, AgentRunResultEvent, DeferredToolRequests, DeferredToolResults, ToolDenied
from pydantic_ai.messages import (
    FunctionToolCallEvent, FunctionToolResultEvent,
    ModelRequest, PartDeltaEvent, TextPartDelta, ToolCallPart, ToolReturnPart,
)
from pydantic_ai.models.instrumented import InstrumentationSettings
from pydantic_ai.usage import UsageLimits
from rich.prompt import Prompt

from co_cli._approval import _is_safe_command
from co_cli.agent import get_agent
from co_cli.deps import CoDeps
from co_cli.sandbox import SandboxProtocol, DockerSandbox, SubprocessBackend
from co_cli.telemetry import SQLiteSpanExporter
from co_cli.config import settings, DATA_DIR
from co_cli.display import console, set_theme, PROMPT_CHAR
from co_cli.banner import display_welcome_banner
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
    help="Co - The Production-Grade Personal Assistant CLI",
    context_settings={"help_option_names": ["--help", "-h"]},
)


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


_RENDER_INTERVAL = 0.05  # 20 FPS — matches aider's baseline


async def _stream_agent_run(agent, *, user_input=None, deps, message_history,
                            model_settings, usage_limits,
                            deferred_tool_results=None):
    """Run agent with streaming — display tool events and Markdown text inline."""
    pending_cmds: dict[str, str] = {}
    result = None
    streamed_text = False
    text_buffer = ""
    live: Live | None = None
    last_render = 0.0

    try:
        async for event in agent.run_stream_events(
            user_input, deps=deps, message_history=message_history,
            model_settings=model_settings,
            usage_limits=usage_limits,
            deferred_tool_results=deferred_tool_results,
        ):
            if isinstance(event, PartDeltaEvent) and isinstance(event.delta, TextPartDelta):
                text_buffer += event.delta.content_delta
                streamed_text = True
                now = time.monotonic()
                if now - last_render >= _RENDER_INTERVAL:
                    if live is None:
                        live = Live(
                            Markdown(text_buffer), console=console,
                            auto_refresh=False,
                        )
                        live.start()
                    else:
                        live.update(Markdown(text_buffer))
                        live.refresh()
                    last_render = now
                continue

            # Commit accumulated Markdown before tool/result output
            if live:
                live.update(Markdown(text_buffer))
                live.refresh()
                live.stop()
                live = None
                text_buffer = ""
                last_render = 0.0

            if isinstance(event, FunctionToolCallEvent):
                tool = event.part.tool_name
                if tool == "run_shell_command":
                    cmd = event.part.args_as_dict().get("cmd", "")
                    pending_cmds[event.tool_call_id] = cmd
                    console.print(f"[dim]  {tool}({cmd})[/dim]")
                else:
                    console.print(f"[dim]  {tool}()[/dim]")

            elif isinstance(event, FunctionToolResultEvent):
                if not isinstance(event.result, ToolReturnPart):
                    continue
                content = event.result.content
                if isinstance(content, str) and content.strip():
                    title = pending_cmds.get(event.tool_call_id, event.result.tool_name)
                    console.print(Panel(
                        content.rstrip(), title=f"$ {title}", border_style="shell",
                    ))
                elif isinstance(content, dict) and "display" in content:
                    console.print(content["display"])

            elif isinstance(event, AgentRunResultEvent):
                result = event.result

        # Normal completion — final render
        if live:
            live.update(Markdown(text_buffer))
            live.refresh()
            live.stop()
            live = None
    finally:
        # Cancellation cleanup — just stop Live to restore terminal
        if live:
            try:
                live.stop()
            except Exception:
                pass

    return result, streamed_text


_CHOICES_HINT = " [[green]y[/green]/[red]n[/red]/[bold orange3]a[/bold orange3](yolo)]"


async def _handle_approvals(agent, deps, result, model_settings, usage_limits):
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

            # Auto-approve safe shell commands only when sandbox provides isolation.
            # Without a sandbox, approval is the security layer — all commands prompt.
            if call.tool_name == "run_shell_command":
                cmd = args.get("cmd", "")
                if (
                    deps.sandbox.isolation_level != "none"
                    and _is_safe_command(cmd, deps.shell_safe_commands)
                ):
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

    return await _stream_agent_run(
        agent, deps=deps, message_history=result.all_messages(),
        model_settings=model_settings, usage_limits=usage_limits,
        deferred_tool_results=approvals,
    )


async def chat_loop():
    agent, model_settings, tool_names = get_agent()
    deps = create_deps()
    completer = WordCompleter(
        [f"/{name}" for name in COMMANDS],
        sentence=True,
    )
    session = PromptSession(
        history=FileHistory(str(DATA_DIR / "history.txt")),
        completer=completer,
        complete_while_typing=False,
    )

    info = get_status(tool_count=len(tool_names))
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
                        continue

                console.print("[dim]Co is thinking...[/dim]")
                result = None
                streamed_text = False
                try:
                    result, streamed_text = await _stream_agent_run(
                        agent, user_input=user_input, deps=deps,
                        message_history=message_history, model_settings=model_settings,
                        usage_limits=UsageLimits(request_limit=settings.max_request_limit),
                    )

                    # Handle deferred tool approvals (loop: resumed run may trigger more)
                    while isinstance(result.output, DeferredToolRequests):
                        result, streamed_text = await _handle_approvals(
                            agent, deps, result, model_settings,
                            UsageLimits(request_limit=settings.max_request_limit),
                        )

                    message_history = result.all_messages()
                    if not streamed_text and isinstance(result.output, str):
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
        set_theme(theme)
    try:
        asyncio.run(chat_loop())
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
