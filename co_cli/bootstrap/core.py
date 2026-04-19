from __future__ import annotations

import logging
from contextlib import AsyncExitStack
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from opentelemetry import trace

if TYPE_CHECKING:
    from co_cli.knowledge._store import KnowledgeStore

from co_cli.config._core import Settings, settings
from co_cli.context.session import find_latest_session, new_session_path
from co_cli.context.types import SafetyState
from co_cli.deps import CoDeps, CoRuntimeState, resolve_workspace_paths
from co_cli.display._core import TerminalFrontend
from co_cli.tools.shell_backend import ShellBackend

logger = logging.getLogger(__name__)
_TRACER = trace.get_tracer("co-cli.bootstrap")
KnowledgeBackendLiteral = Literal["grep", "fts5", "hybrid"]


def _summarize_backend_error(exc: Exception) -> str:
    detail = str(exc).strip() or exc.__class__.__name__
    return detail.splitlines()[0]


def _resolve_reranker(
    config: Settings,
    statuses: list[str],
) -> None:
    """Resolve reranker availability, mutating config and appending degradation messages.

    Called inside _discover_knowledge_backend only when an index is active (hybrid/fts5).
    Skipped on grep — no index means no reranking.
    """
    from co_cli.bootstrap.check import check_cross_encoder, check_reranker_llm

    cross_result = check_cross_encoder(config)
    if cross_result.status not in ("ok", "skipped"):
        statuses.append(
            "  Reranker degraded — TEI cross-encoder unavailable; search results will be unranked"
        )
        logger.warning("TEI cross-encoder unavailable; degrading to none")
        config.knowledge.cross_encoder_reranker_url = None
    elif cross_result.extra:
        tei_batch = cross_result.extra.get("max_client_batch_size")
        if isinstance(tei_batch, int) and tei_batch > 0:
            config.knowledge.tei_rerank_batch_size = tei_batch

    reranker_result = check_reranker_llm(config)
    if reranker_result.status not in ("ok", "skipped"):
        statuses.append(
            "  Reranker degraded — LLM reranker unavailable; search results will be unranked"
        )
        logger.warning("LLM reranker unavailable; degrading to none")
        config.knowledge.llm_reranker = None


def _discover_knowledge_backend(
    config: Settings,
    frontend: TerminalFrontend,
    degradations: dict[str, str],
) -> KnowledgeStore | None:
    """Discover which knowledge backend is available and construct the store.

    Three-tier fallback with graceful degradation:
      1. hybrid  — sqlite-vec + embedding provider (richest search)
      2. fts5    — SQLite FTS5 index (keyword search, no vectors)
      3. grep    — pure file search, no store required

    Probes embedder/reranker availability, mutates config fields directly, constructs
    the store, and reports degradation to frontend.
    Config reflects the runtime backend; degradations dict records what changed and why.
    """
    if config.knowledge.search_backend == "grep":
        return None

    configured: KnowledgeBackendLiteral = config.knowledge.search_backend
    statuses: list[str] = []

    # Resolve reranker — config fields must reflect actual availability
    _resolve_reranker(config, statuses)

    # --- Level 1: hybrid (sqlite-vec + embedding) ---
    # Only attempt hybrid if that's what was configured; respect explicit fts5 choice.
    resolved_backend: KnowledgeBackendLiteral = "fts5"
    if configured == "hybrid":
        if config.knowledge.embedding_provider == "none":
            logger.info("Hybrid skipped: embedding provider is 'none'")
        else:
            from co_cli.bootstrap.check import check_embedder

            embedder_check = check_embedder(config)
            if embedder_check.status not in ("ok", "skipped"):
                logger.warning("Hybrid skipped: embedder unavailable — %s", embedder_check.detail)
                statuses.append(
                    f"  Knowledge degraded — embedder unavailable "
                    f"({embedder_check.detail}); using fts5"
                )
            else:
                resolved_backend = "hybrid"

    for status in statuses:
        frontend.on_status(status)

    if resolved_backend != configured:
        degradations["knowledge"] = f"{configured} → {resolved_backend} (embedder unavailable)"
    config.knowledge.search_backend = resolved_backend

    # --- Construct store with resolved config ---
    from co_cli.knowledge._store import KnowledgeStore as _KS

    def _degrade_to(backend: KnowledgeBackendLiteral, reason: str) -> None:
        config.knowledge.search_backend = backend
        degradations["knowledge"] = f"{configured} → {backend} ({reason})"

    try:
        return _KS(config=config)
    except Exception as exc:
        if resolved_backend == "hybrid":
            logger.warning("Hybrid backend unavailable: %s", exc)
            frontend.on_status(
                f"  Knowledge degraded — hybrid unavailable "
                f"({_summarize_backend_error(exc)}); trying fts5"
            )
            _degrade_to("fts5", _summarize_backend_error(exc))
            try:
                return _KS(config=config)
            except Exception as exc2:
                logger.warning("FTS5 backend unavailable: %s", exc2)
                frontend.on_status(
                    f"  Knowledge degraded — fts5 unavailable "
                    f"({_summarize_backend_error(exc2)}); using grep"
                )
                _degrade_to("grep", _summarize_backend_error(exc2))
                return None
        else:
            logger.warning("FTS5 backend unavailable: %s", exc)
            frontend.on_status(
                f"  Knowledge degraded — fts5 unavailable "
                f"({_summarize_backend_error(exc)}); using grep"
            )
            _degrade_to("grep", _summarize_backend_error(exc))
            return None


def _sync_knowledge_store(
    store: KnowledgeStore | None,
    config: Settings,
    frontend: TerminalFrontend,
    knowledge_dir: Path,
) -> KnowledgeStore | None:
    """Reconcile the knowledge store with current knowledge files on disk.

    Hash-based — skips unchanged files. On sync failure, closes the store and
    returns None (grep fallback for the session).
    """
    if store is None:
        frontend.on_status("  Knowledge store not available — skipped")
        return None

    with _TRACER.start_as_current_span("sync_knowledge") as span:
        try:
            if knowledge_dir.exists():
                count = store.sync_dir("knowledge", knowledge_dir)
                backend = config.knowledge.search_backend
                span.set_attribute("count", count)
                span.set_attribute("backend", backend)
                span.set_attribute("status", "ok")
                frontend.on_status(f"  Knowledge synced — {count} item(s) ({backend})")
            else:
                span.set_attribute("status", "skipped")
                frontend.on_status("  Knowledge store empty — no knowledge dir")
        except Exception as e:
            span.set_attribute("status", "error")
            span.set_attribute("error", str(e))
            try:
                store.close()
            except Exception:
                pass
            frontend.on_status(f"  Knowledge sync failed — {e}")
            return None

    return store


async def create_deps(frontend: TerminalFrontend, stack: AsyncExitStack) -> CoDeps:
    """Assemble CoDeps from settings: config, registries, MCP, knowledge, skills.

    MCP servers are entered on the provided stack so they stay alive for the
    session and are cleaned up when the stack closes. Knowledge backend is
    resolved with three-tier fallback (hybrid → fts5 → grep) and files synced.

    Raises ValueError on provider/model hard errors.
    """
    from co_cli.agent._core import build_tool_registry
    from co_cli.agent._mcp import discover_mcp_tools
    from co_cli.llm._factory import build_model

    config = settings
    cwd = Path.cwd()
    paths = resolve_workspace_paths(config, cwd)

    # Step 2: fail-fast gate (config shape only — no IO)
    error = config.llm.validate_config()
    if error:
        raise ValueError(error)

    # Step 2b: Ollama context probe — fail-fast on undersized models,
    # override num_ctx with runtime Modelfile value when they differ.
    if config.llm.uses_ollama_openai():
        from co_cli.bootstrap.check import probe_ollama_context

        ctx_probe = probe_ollama_context(config.llm.host, config.llm.model)
        if ctx_probe.status == "error":
            raise ValueError(ctx_probe.detail)
        runtime_num_ctx = ctx_probe.extra.get("num_ctx", 0)
        if runtime_num_ctx > 0 and runtime_num_ctx != config.llm.num_ctx:
            logger.info(
                "Ollama runtime num_ctx=%d differs from config llm.num_ctx=%d — using runtime value",
                runtime_num_ctx,
                config.llm.num_ctx,
            )
            config.llm.num_ctx = runtime_num_ctx

    # Step 3: build registries
    llm_model = build_model(config.llm)
    tool_registry = build_tool_registry(config)

    # Step 4: MCP connect + discovery
    degradations: dict[str, str] = {}
    if tool_registry.mcp_toolsets:
        from co_cli.agent._mcp import _MCPToolsetEntry

        connected: list[_MCPToolsetEntry] = []
        for entry in tool_registry.mcp_toolsets:
            try:
                await stack.enter_async_context(entry.toolset)
                connected.append(entry)
            except Exception as e:
                frontend.on_status(f"MCP server failed to connect: {e}")
        if connected:
            _, discovery_errors, mcp_index = await discover_mcp_tools(
                connected, exclude=set(tool_registry.tool_index.keys())
            )
            for prefix, err in discovery_errors.items():
                frontend.on_status(f"MCP server {prefix!r} failed to list tools: {err}")
                degradations[f"mcp.{prefix}"] = err[:120]
            tool_registry.tool_index.update(mcp_index)

    # Step 5: load skills (filesystem reads — three-pass precedence merge)
    from co_cli.commands._commands import _load_skills

    skill_errors: list[str] = []
    skill_commands = _load_skills(
        paths["skills_dir"],
        settings=config,
        user_skills_dir=paths["user_skills_dir"],
        errors=skill_errors,
    )
    for msg in skill_errors:
        frontend.on_status(msg)

    # Step 6: discover knowledge backend + construct store (IO probes — three-tier fallback)
    knowledge_store = _discover_knowledge_backend(config, frontend, degradations)

    # Step 7: sync knowledge store with current files on disk
    knowledge_store = _sync_knowledge_store(
        knowledge_store,
        config,
        frontend,
        knowledge_dir=paths["knowledge_dir"],
    )

    # Step 8: assemble deps
    runtime = CoRuntimeState(safety_state=SafetyState())
    return CoDeps(
        shell=ShellBackend(),
        config=config,
        model=llm_model,
        knowledge_store=knowledge_store,
        tool_index=tool_registry.tool_index,
        tool_registry=tool_registry,
        skill_commands=skill_commands,
        runtime=runtime,
        degradations=degradations,
        **paths,
    )


def _init_memory_index(
    deps: CoDeps,
    current_session_path: Path,
    frontend: TerminalFrontend,
) -> None:
    """Open the memory index DB and sync past sessions into it.

    Initialises deps.memory_index in-place.  On any error, logs a warning,
    sets deps.memory_index = None, and continues (graceful degradation).
    The current_session_path is excluded from the sync so the in-progress
    session is never indexed mid-session.
    """
    from co_cli.memory._store import MemoryIndex

    db_path = deps.sessions_dir.parent / "session-index.db"
    try:
        store = MemoryIndex(db_path)
        store.sync_sessions(deps.sessions_dir, exclude=current_session_path)
        deps.memory_index = store
    except Exception as exc:
        logger.warning("Session index unavailable: %s", exc)
        deps.memory_index = None
        frontend.on_status(f"  Session index unavailable — {exc}")


def restore_session(deps: CoDeps, frontend: TerminalFrontend) -> Path:
    """Restore the most recent session from sessions/ dir, or create a new session path.

    Returns the session Path (existing or newly constructed — file not created until
    first append_transcript call).
    """
    with _TRACER.start_as_current_span("restore_session") as span:
        session_path = find_latest_session(deps.sessions_dir)
        if session_path is not None:
            deps.session.session_path = session_path
            short_id = session_path.stem[-8:]
            span.set_attribute("status", "restored")
            span.set_attribute("session_id", short_id)
            frontend.on_status(f"  Session restored — {short_id}...")
        else:
            session_path = new_session_path(deps.sessions_dir)
            deps.session.session_path = session_path
            short_id = session_path.stem[-8:]
            span.set_attribute("status", "new")
            span.set_attribute("session_id", short_id)
            frontend.on_status(f"  Session new — {short_id}...")
        return session_path
