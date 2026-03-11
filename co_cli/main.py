import asyncio
import dataclasses
import logging
import os
import subprocess
import time
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Any
from uuid import uuid4

import typer
from prompt_toolkit import PromptSession
from prompt_toolkit.completion import WordCompleter
from prompt_toolkit.history import FileHistory
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

from pydantic_ai import Agent
from pydantic_ai.models.instrumented import InstrumentationSettings

from co_cli.agents._factory import ModelRegistry
from co_cli._orchestrate import run_turn, _patch_dangling_tool_calls
from co_cli._history import OpeningContextState, SafetyState, precompute_compaction
from co_cli._signal_analyzer import analyze_for_signals, SignalResult
from co_cli._memory_lifecycle import persist_memory as _persist_memory
from co_cli.agent import get_agent
from co_cli.deps import CoDeps, CoServices, CoConfig, CoSessionState, CoRuntimeState
from co_cli._shell_backend import ShellBackend
from co_cli._telemetry import SQLiteSpanExporter
from co_cli.config import settings, DATA_DIR, SEARCH_DB, LOGS_DB
from co_cli.display import console, set_theme, PROMPT_CHAR, TerminalFrontend
from co_cli._banner import display_welcome_banner
from co_cli._status import get_status, render_status_table, check_security, render_security_findings
from co_cli._commands import (
    dispatch as dispatch_command, CommandContext, COMMANDS, SKILL_COMMANDS,
    _load_skills, _swap_model_inplace, _skills_snapshot, _build_completer_words,
)
from co_cli._exec_approvals import prune_stale as _prune_stale_approvals
from co_cli._background import TaskRunner, TaskStorage
from co_cli._session import (
    save_session, touch_session, increment_compaction,
)
from co_cli._bootstrap import run_bootstrap
from co_cli._model_check import run_model_check
from co_cli.prompts.personalities._composer import load_soul_critique

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

logger = logging.getLogger(__name__)


@app.callback()
def _default(ctx: typer.Context):
    """Start an interactive chat session (default when no subcommand is given)."""
    if ctx.invoked_subcommand is None:
        chat()


def create_deps(task_runner: TaskRunner | None = None) -> CoDeps:
    """Create deps from settings."""
    session_id = uuid4().hex

    # Initialize knowledge index with adaptive fallback:
    # hybrid -> fts5 -> grep (no index).
    knowledge_index = None
    resolved_knowledge_backend = settings.knowledge_search_backend
    if settings.knowledge_search_backend in ("fts5", "hybrid"):
        from co_cli._knowledge_index import KnowledgeIndex

        def _build_index(backend: str):
            return KnowledgeIndex(
                SEARCH_DB,
                backend=backend,
                embedding_provider=settings.knowledge_embedding_provider,
                embedding_model=settings.knowledge_embedding_model,
                embedding_dims=settings.knowledge_embedding_dims,
                ollama_host=settings.ollama_host,
                gemini_api_key=settings.gemini_api_key,
                embed_api_url=settings.knowledge_embed_api_url,
                rerank_api_url=settings.knowledge_rerank_api_url,
                hybrid_vector_weight=settings.knowledge_hybrid_vector_weight,
                hybrid_text_weight=settings.knowledge_hybrid_text_weight,
                reranker_provider=settings.knowledge_reranker_provider,
                reranker_model=settings.knowledge_reranker_model,
                chunk_size=settings.knowledge_chunk_size,
                chunk_overlap=settings.knowledge_chunk_overlap,
            )

        if settings.knowledge_search_backend == "hybrid":
            try:
                knowledge_index = _build_index("hybrid")
                resolved_knowledge_backend = "hybrid"
            except Exception as e_hybrid:
                logger.warning(
                    "Knowledge backend 'hybrid' unavailable; falling back to 'fts5': %s",
                    e_hybrid,
                )
                try:
                    knowledge_index = _build_index("fts5")
                    resolved_knowledge_backend = "fts5"
                except Exception as e_fts:
                    logger.warning(
                        "Knowledge backend 'fts5' unavailable after hybrid fallback; "
                        "falling back to 'grep': %s",
                        e_fts,
                    )
                    knowledge_index = None
                    resolved_knowledge_backend = "grep"
        else:
            try:
                knowledge_index = _build_index("fts5")
                resolved_knowledge_backend = "fts5"
            except Exception as e_fts:
                logger.warning(
                    "Knowledge backend 'fts5' unavailable; falling back to 'grep': %s",
                    e_fts,
                )
                knowledge_index = None
                resolved_knowledge_backend = "grep"

    _personality_critique = (
        load_soul_critique(settings.personality) if settings.personality else ""
    )

    exec_approvals_path = Path.cwd() / ".co-cli" / "exec-approvals.json"

    # Prune stale exec approvals at session start (removes entries older than 90 days)
    _prune_stale_approvals(exec_approvals_path, max_age_days=90)

    memory_dir = Path.cwd() / ".co-cli" / "memory"
    library_dir = Path(settings.library_path) if settings.library_path else DATA_DIR / "library"

    services = CoServices(
        shell=ShellBackend(),
        knowledge_index=knowledge_index,
        task_runner=task_runner,
    )
    config = dataclasses.replace(
        CoConfig.from_settings(settings),
        session_id=session_id,
        exec_approvals_path=exec_approvals_path,
        memory_dir=memory_dir,
        library_dir=library_dir,
        skills_dir=Path.cwd() / ".co-cli" / "skills",
        personality_critique=_personality_critique,
        knowledge_search_backend=resolved_knowledge_backend,
        mcp_count=len(settings.mcp_servers),
    )
    services.model_registry = ModelRegistry.from_config(config)
    runtime = CoRuntimeState(
        opening_ctx_state=OpeningContextState(),
        safety_state=SafetyState(),
    )
    return CoDeps(services=services, config=config, runtime=runtime)


async def _discover_mcp_tools(agent: Agent, native_tool_names: list[str]) -> list[str]:
    """Discover MCP tool names from connected servers (after async with agent).

    Falls back to ``{prefix}_*`` placeholders if list_tools() is unavailable.
    """
    from pydantic_ai.mcp import MCPServer

    mcp_tool_names: list[str] = []
    native_set = set(native_tool_names)

    for toolset in agent.toolsets:
        # Unwrap approval wrappers to reach the MCPServer base instance
        inner = getattr(toolset, "wrapped", toolset)
        if not isinstance(inner, MCPServer):
            continue
        try:
            tools = await inner.list_tools()
            prefix = inner.tool_prefix or ""
            for t in tools:
                name = f"{prefix}_{t.name}" if prefix else t.name
                if name not in native_set:
                    mcp_tool_names.append(name)
        except Exception as e:
            import logging as _logging
            _logging.getLogger(__name__).warning(
                f"MCP tool list failed for {inner.tool_prefix!r}: {e}"
            )
            # Server not yet connected or list failed — use placeholder
            prefix = inner.tool_prefix or "mcp"
            mcp_tool_names.append(f"{prefix}_*")

    return native_tool_names + sorted(mcp_tool_names)


async def _handle_signal(
    signal: SignalResult,
    deps: CoDeps,
    frontend: Any,
    model: Any,
) -> None:
    """Apply admission policy then persist or prompt for a detected signal."""
    if not signal.found or not signal.candidate or not signal.tag:
        return
    if signal.tag not in deps.config.memory_auto_save_tags:
        logger.debug(
            "Memory signal suppressed by policy: tag=%s not in memory_auto_save_tags",
            signal.tag,
        )
        return
    tags = [signal.tag] + (["personality-context"] if signal.inject else [])
    if signal.confidence == "high":
        await _persist_memory(
            deps, signal.candidate, tags, None,
            on_failure="skip", model=model,
        )
        frontend.on_status(f"Learned: {signal.candidate[:80]}")
    else:
        choice = frontend.prompt_approval(f"Worth remembering: {signal.candidate}")
        if choice in ("y", "a"):
            await _persist_memory(
                deps, signal.candidate, tags, None,
                on_failure="add", model=model,
            )


async def chat_loop(verbose: bool = False):
    mcp_servers = settings.mcp_servers if settings.mcp_servers else None

    # Step 0: frontend first — required by run_model_check signature
    frontend = TerminalFrontend()

    # Step 1: create_deps with no task_runner yet (optional field, injected below)
    deps = create_deps()

    # Step 2: run_model_check — ALL resource checks here, pre-agent
    # Raises RuntimeError on error (agent is never created).
    # Also advances deps.role_models pref lists in-place if needed.
    run_model_check(deps, frontend)

    # Step 3: task runner created and injected into deps after model check passes
    tasks_dir = Path.cwd() / ".co-cli" / "tasks"
    task_storage = TaskStorage(tasks_dir)
    task_runner = TaskRunner(
        storage=task_storage,
        max_concurrent=settings.background_max_concurrent,
        inactivity_timeout=settings.background_task_inactivity_timeout,
        auto_cleanup=settings.background_auto_cleanup,
        retention_days=settings.background_task_retention_days,
    )
    deps.services.task_runner = task_runner

    # Load skills at startup; package-default skills always available; project-local skills override on name collision
    skill_commands = _load_skills(deps.config.skills_dir, settings=settings)
    SKILL_COMMANDS.clear()
    SKILL_COMMANDS.update(skill_commands)
    _skills_watch_snapshot = _skills_snapshot(deps.config.skills_dir)

    # Populate skill_registry for system prompt injection (skills with descriptions,
    # excluding disable-model-invocation skills)
    deps.session.skill_registry = [
        {"name": s.name, "description": s.description}
        for s in skill_commands.values()
        if s.description and not s.disable_model_invocation
    ]

    # Step 4: get_agent with post-model-check chain head
    agent, model_settings, tool_names, tool_approvals = get_agent(
        web_policy=settings.web_policy,
        mcp_servers=mcp_servers,
        personality=settings.personality,
        model_name=deps.config.role_models["reasoning"][0].model,
    )

    # Build completer from built-in commands + user_invocable skills
    skill_completer_names = [
        f"/{name}" for name, s in skill_commands.items() if s.user_invocable
    ]
    completer = WordCompleter(
        [f"/{name}" for name in COMMANDS] + skill_completer_names,
        sentence=True,
    )
    session = PromptSession(
        history=FileHistory(str(DATA_DIR / "history.txt")),
        completer=completer,
        complete_while_typing=False,
    )

    # Step 5: start agent context (connects MCP servers).
    # AsyncExitStack guarantees __aexit__ even if fallback path is taken.
    stack = AsyncExitStack()
    try:
        await stack.enter_async_context(agent)
    except Exception as e:
        console.print(f"[yellow]MCP servers unavailable ({e}) — continuing without MCP[/yellow]")
        await stack.aclose()  # clean up partially-started first agent
        stack = AsyncExitStack()
        agent, model_settings, tool_names, tool_approvals = get_agent(
            web_policy=settings.web_policy,
            personality=settings.personality,
            model_name=deps.config.role_models["reasoning"][0].model,
        )
        await stack.enter_async_context(agent)
        mcp_servers = None

    message_history = []
    last_interrupt_time = 0.0
    bg_compaction_task: asyncio.Task | None = None
    try:
        # MCP tools discovered after context entry — update tool_names
        if mcp_servers:
            tool_names = await _discover_mcp_tools(agent, tool_names)

        # Persist final resolved tool surface into session state
        deps.session.tool_names = tool_names
        deps.session.tool_approvals = tool_approvals

        session_path = Path.cwd() / ".co-cli" / "session.json"
        session_data = await run_bootstrap(
            deps,
            frontend,
            memory_dir=deps.config.memory_dir,
            library_dir=deps.config.library_dir,
            session_path=session_path,
            session_ttl_minutes=settings.session_ttl_minutes,
            n_skills=len(skill_commands),
        )

        info = get_status(tool_count=len(tool_names))
        display_welcome_banner(info)

        while True:
            # File watcher: detect skill edits before each prompt
            _new_snap = _skills_snapshot(deps.config.skills_dir)
            if _new_snap != _skills_watch_snapshot:
                _skills_watch_snapshot = _new_snap
                _reloaded = _load_skills(deps.config.skills_dir, settings=settings)
                SKILL_COMMANDS.clear()
                SKILL_COMMANDS.update(_reloaded)
                deps.session.skill_registry = [
                    {"name": s.name, "description": s.description}
                    for s in SKILL_COMMANDS.values()
                    if s.description and not s.disable_model_invocation
                ]
                completer.words = _build_completer_words()
                console.print("[dim]Skills reloaded (files changed).[/dim]")

            _saved_env: dict[str, str | None] = {}
            try:
                user_input = await session.prompt_async(f"Co {PROMPT_CHAR} ")
                last_interrupt_time = 0.0  # Reset on successful input
                if user_input.lower() in ["exit", "quit"]:
                    break
                if not user_input.strip():
                    continue

                # /command — slash commands, no LLM
                if user_input.startswith("/"):
                    cmd_ctx = CommandContext(
                        message_history=message_history,
                        deps=deps,
                        agent=agent,
                        tool_names=tool_names,
                        completer=completer,
                    )
                    handled, new_history = await dispatch_command(user_input, cmd_ctx)
                    if handled:
                        if new_history is not None:
                            message_history = new_history
                            # Track compaction count in session (compact cmd returns new history)
                            if user_input.lstrip("/").split()[0] == "compact":
                                session_data = increment_compaction(session_data)
                                save_session(session_path, session_data)
                        if cmd_ctx.skill_body is not None:
                            # Skill dispatched — fall through to LLM turn with skill body
                            user_input = cmd_ctx.skill_body
                            # Save current env values and inject skill-env vars
                            _saved_env = {k: os.environ.get(k) for k in deps.session.active_skill_env}
                            os.environ.update(deps.session.active_skill_env)
                        else:
                            continue

                # Join background compaction if it completed while user was typing
                if bg_compaction_task is not None:
                    try:
                        result = await bg_compaction_task
                        deps.runtime.precomputed_compaction = result
                    except Exception:
                        deps.runtime.precomputed_compaction = None
                    bg_compaction_task = None

                # LLM turn — delegated to _orchestrate.run_turn()
                # try/finally guarantees skill-env rollback on all exit paths
                # (normal completion, KeyboardInterrupt, CancelledError, Exception).
                try:
                    # Capture pre-turn state for model fallback replay
                    pre_turn_history = message_history[:]
                    original_user_input = user_input
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

                    # Terminal error recovery: advance reasoning model chain (max once per turn).
                    # Context-overflow will also trigger chain advance and may still fail — acceptable MVP.
                    if turn_result.outcome == "error" and len(deps.config.role_models["reasoning"]) > 1:
                        deps.config.role_models["reasoning"].pop(0)
                        next_model_entry = deps.config.role_models["reasoning"][0]
                        try:
                            new_ms = _swap_model_inplace(
                                agent, next_model_entry.model, settings.llm_provider.lower(), settings
                            )
                            model_settings = new_ms
                            frontend.on_status(f"Retrying with reasoning model: {next_model_entry.model}")
                            turn_result = await run_turn(
                                agent=agent,
                                user_input=original_user_input,
                                deps=deps,
                                message_history=pre_turn_history,
                                model_settings=model_settings,
                                max_request_limit=settings.max_request_limit,
                                http_retries=settings.model_http_retries,
                                verbose=verbose,
                                frontend=frontend,
                            )
                            message_history = turn_result.messages
                        except Exception as _fe:
                            console.print(f"[bold red]Fallback failed:[/bold red] {_fe}")
                finally:
                    # Restore env vars saved before skill dispatch. No-op on non-skill turns.
                    for k, v in _saved_env.items():
                        if v is not None:
                            os.environ[k] = v
                        else:
                            os.environ.pop(k, None)
                    # Both clears in finally — guaranteed on all exit paths including exceptions.
                    # Prevents stale skill grants from bleeding into the next turn.
                    deps.session.active_skill_env.clear()
                    deps.session.skill_tool_grants.clear()
                    deps.session.active_skill_name = None

                # Signal detection — CC hookify pattern, auto-triggered post-turn.
                # LLM mini-agent classifies every completed turn; guardrails in the
                # prompt prevent false positives on neutral messages.
                if (
                    not turn_result.interrupted
                    and turn_result.outcome != "error"
                ):
                    signal = await analyze_for_signals(
                        message_history,
                        agent.model,
                        services=deps.services,
                    )
                    await _handle_signal(signal, deps, frontend, agent.model)

                # Clear precomputed result (consumed or stale)
                deps.runtime.precomputed_compaction = None

                # Touch session after each turn
                session_data = touch_session(session_data)
                save_session(session_path, session_data)

                # Spawn background compaction for the next turn
                bg_compaction_task = asyncio.create_task(
                    precompute_compaction(
                        message_history, deps, str(agent.model),
                    )
                )

                # Pattern-match on TurnOutcome
                if turn_result.outcome == "error":
                    console.print("[error]An error occurred during this turn.[/error]")
                elif turn_result.outcome == "stop":
                    break

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
        if bg_compaction_task is not None:
            bg_compaction_task.cancel()
        await task_runner.shutdown()
        await stack.aclose()
        deps.services.shell.cleanup()


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
    findings = check_security()
    render_security_findings(findings)


@app.command()
def logs():
    """Launch a local dashboard (Datasette) to inspect agent traces."""
    import webbrowser

    db_path = LOGS_DB
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

    db_path = LOGS_DB
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
