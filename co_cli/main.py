import asyncio
import logging
import os
import subprocess
import time
from contextlib import AsyncExitStack
from pathlib import Path
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

from co_cli._orchestrate import run_turn, _patch_dangling_tool_calls
from co_cli._history import OpeningContextState, SafetyState, precompute_compaction
from co_cli._signal_analyzer import analyze_for_signals
from co_cli.memory_lifecycle import persist_memory as _persist_memory
from co_cli.agent import get_agent
from co_cli.deps import CoDeps
from co_cli.shell_backend import ShellBackend
from co_cli._telemetry import SQLiteSpanExporter
from co_cli.config import settings, DATA_DIR, get_role_head
from co_cli.display import console, set_theme, PROMPT_CHAR, TerminalFrontend
from co_cli._banner import display_welcome_banner
from co_cli.status import get_status, render_status_table, check_security, render_security_findings
from co_cli._commands import (
    dispatch as dispatch_command, CommandContext, COMMANDS, SKILL_COMMANDS,
    _load_skills, _swap_model_inplace, _skills_snapshot, _build_completer_words,
)
from co_cli._exec_approvals import prune_stale as _prune_stale_approvals
from co_cli.background import TaskRunner, TaskStorage
from co_cli._session import (
    save_session, touch_session, increment_compaction,
)
from co_cli._bootstrap import run_bootstrap
from co_cli._preflight import run_preflight
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

    # Resolve obsidian vault path
    vault_path = None
    if settings.obsidian_vault_path:
        vault_path = Path(settings.obsidian_vault_path)

    # Initialize knowledge index with adaptive fallback:
    # hybrid -> fts5 -> grep (no index).
    knowledge_index = None
    resolved_knowledge_backend = settings.knowledge_search_backend
    if settings.knowledge_search_backend in ("fts5", "hybrid"):
        from co_cli.knowledge_index import KnowledgeIndex

        def _build_index(backend: str):
            return KnowledgeIndex(
                DATA_DIR / "search.db",
                backend=backend,
                embedding_provider=settings.knowledge_embedding_provider,
                embedding_model=settings.knowledge_embedding_model,
                embedding_dims=settings.knowledge_embedding_dims,
                ollama_host=settings.ollama_host,
                gemini_api_key=settings.gemini_api_key,
                hybrid_vector_weight=settings.knowledge_hybrid_vector_weight,
                hybrid_text_weight=settings.knowledge_hybrid_text_weight,
                reranker_provider=settings.knowledge_reranker_provider,
                reranker_model=settings.knowledge_reranker_model,
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

    deps = CoDeps(
        shell=ShellBackend(),
        session_id=session_id,
        obsidian_vault_path=vault_path,
        google_credentials_path=settings.google_credentials_path,
        exec_approvals_path=exec_approvals_path,
        shell_max_timeout=settings.shell_max_timeout,
        shell_safe_commands=settings.shell_safe_commands,
        gemini_api_key=settings.gemini_api_key,
        brave_search_api_key=settings.brave_search_api_key,
        web_fetch_allowed_domains=settings.web_fetch_allowed_domains,
        web_fetch_blocked_domains=settings.web_fetch_blocked_domains,
        web_policy=settings.web_policy,
        web_http_max_retries=settings.web_http_max_retries,
        web_http_backoff_base_seconds=settings.web_http_backoff_base_seconds,
        web_http_backoff_max_seconds=settings.web_http_backoff_max_seconds,
        web_http_jitter_ratio=settings.web_http_jitter_ratio,
        personality=settings.personality,
        personality_critique=_personality_critique,
        memory_max_count=settings.memory_max_count,
        memory_dedup_window_days=settings.memory_dedup_window_days,
        memory_dedup_threshold=settings.memory_dedup_threshold,
        memory_recall_half_life_days=settings.memory_recall_half_life_days,
        memory_consolidation_top_k=settings.memory_consolidation_top_k,
        memory_consolidation_timeout_seconds=settings.memory_consolidation_timeout_seconds,
        max_history_messages=settings.max_history_messages,
        tool_output_trim_chars=settings.tool_output_trim_chars,
        summarization_model=get_role_head(settings.model_roles, "summarization"),
        doom_loop_threshold=settings.doom_loop_threshold,
        max_reflections=settings.max_reflections,
        knowledge_index=knowledge_index,
        knowledge_search_backend=resolved_knowledge_backend,
        knowledge_reranker_provider=settings.knowledge_reranker_provider,
        memory_dir=memory_dir,
        library_dir=library_dir,
        mcp_count=len(settings.mcp_servers),
        approval_risk_enabled=settings.approval_risk_enabled,
        approval_auto_low_risk=settings.approval_auto_low_risk,
        model_roles={k: list(v) for k, v in settings.model_roles.items()},
        ollama_host=settings.ollama_host,
        llm_provider=settings.llm_provider,
        ollama_num_ctx=settings.ollama_num_ctx,
        ctx_warn_threshold=settings.ctx_warn_threshold,
        ctx_overflow_threshold=settings.ctx_overflow_threshold,
        task_runner=task_runner,
    )
    # Initialize session-scoped processor state
    deps._opening_ctx_state = OpeningContextState()
    deps._safety_state = SafetyState()
    return deps


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


async def chat_loop(verbose: bool = False):
    mcp_servers = settings.mcp_servers if settings.mcp_servers else None

    # Step 0: frontend first — required by run_preflight signature
    frontend = TerminalFrontend()

    # Step 1: create_deps with no task_runner yet (optional field, injected below)
    deps = create_deps()
    deps.skills_dir = Path.cwd() / ".co-cli" / "skills"

    # Step 2: run_preflight — ALL resource checks here, pre-agent
    # Raises RuntimeError on error (agent is never created).
    # Also advances deps.model_roles chains in-place if needed.
    run_preflight(deps, frontend)

    # Step 3: task runner created and injected into deps after preflight passes
    tasks_dir = Path.cwd() / ".co-cli" / "tasks"
    task_storage = TaskStorage(tasks_dir)
    task_runner = TaskRunner(
        storage=task_storage,
        max_concurrent=settings.background_max_concurrent,
        inactivity_timeout=settings.background_task_inactivity_timeout,
        auto_cleanup=settings.background_auto_cleanup,
        retention_days=settings.background_task_retention_days,
    )
    deps.task_runner = task_runner

    # Load skills at startup; package-default skills always available; project-local skills override on name collision
    skill_commands = _load_skills(deps.skills_dir, settings=settings)
    SKILL_COMMANDS.clear()
    SKILL_COMMANDS.update(skill_commands)
    _skills_watch_snapshot = _skills_snapshot(deps.skills_dir)

    # Populate skill_registry for system prompt injection (skills with descriptions,
    # excluding disable-model-invocation skills)
    deps.skill_registry = [
        {"name": s.name, "description": s.description}
        for s in skill_commands.values()
        if s.description and not s.disable_model_invocation
    ]

    # Step 4: get_agent with post-preflight chain head
    agent, model_settings, tool_names, _ = get_agent(
        web_policy=settings.web_policy,
        mcp_servers=mcp_servers,
        personality=settings.personality,
        model_name=deps.model_roles["reasoning"][0],
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
        agent, model_settings, tool_names, _ = get_agent(
            web_policy=settings.web_policy,
            personality=settings.personality,
            model_name=deps.model_roles["reasoning"][0],
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

        session_path = Path.cwd() / ".co-cli" / "session.json"
        session_data = await run_bootstrap(
            deps,
            frontend,
            memory_dir=deps.memory_dir,
            library_dir=deps.library_dir,
            session_path=session_path,
            session_ttl_minutes=settings.session_ttl_minutes,
            n_skills=len(skill_commands),
        )

        info = get_status(tool_count=len(tool_names))
        display_welcome_banner(info)

        while True:
            # File watcher: detect skill edits before each prompt
            _new_snap = _skills_snapshot(deps.skills_dir)
            if _new_snap != _skills_watch_snapshot:
                _skills_watch_snapshot = _new_snap
                _reloaded = _load_skills(deps.skills_dir, settings=settings)
                SKILL_COMMANDS.clear()
                SKILL_COMMANDS.update(_reloaded)
                deps.skill_registry = [
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
                            _saved_env = {k: os.environ.get(k) for k in deps.active_skill_env}
                            os.environ.update(deps.active_skill_env)
                        else:
                            continue

                # Join background compaction if it completed while user was typing
                if bg_compaction_task is not None:
                    try:
                        result = await bg_compaction_task
                        deps.precomputed_compaction = result
                    except Exception:
                        deps.precomputed_compaction = None
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
                    if turn_result.outcome == "error" and len(deps.model_roles["reasoning"]) > 1:
                        deps.model_roles["reasoning"].pop(0)
                        next_model = deps.model_roles["reasoning"][0]
                        try:
                            new_ms = _swap_model_inplace(
                                agent, next_model, settings.llm_provider.lower(), settings
                            )
                            model_settings = new_ms
                            frontend.on_status(f"Retrying with reasoning model: {next_model}")
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
                    deps.active_skill_env.clear()
                    deps.active_skill_allowed_tools.clear()

                # Signal detection — CC hookify pattern, auto-triggered post-turn.
                # LLM mini-agent classifies every completed turn; guardrails in the
                # prompt prevent false positives on neutral messages.
                if (
                    not turn_result.interrupted
                    and turn_result.outcome != "error"
                ):
                    signal = await analyze_for_signals(message_history, agent.model)
                    if signal.found and signal.candidate and signal.tag:
                        tags = [signal.tag] + (["personality-context"] if signal.inject else [])
                        if signal.confidence == "high":
                            await _persist_memory(
                                deps, signal.candidate, tags, None,
                                on_failure="skip", model=agent.model,
                            )
                            frontend.on_status(f"Learned: {signal.candidate[:80]}")
                        else:
                            choice = frontend.prompt_approval(
                                f"Worth remembering: {signal.candidate}"
                            )
                            if choice in ("y", "a"):
                                await _persist_memory(
                                    deps, signal.candidate, tags, None,
                                    on_failure="add", model=agent.model,
                                )

                # Clear precomputed result (consumed or stale)
                deps.precomputed_compaction = None

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
        deps.shell.cleanup()


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
