from __future__ import annotations

import asyncio
import copy
import logging
from contextlib import AsyncExitStack
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from opentelemetry import trace

if TYPE_CHECKING:
    from co_cli.memory.memory_store import MemoryStore

from co_cli.config.core import Settings, get_settings
from co_cli.deps import CoDeps, CoRuntimeState, resolve_workspace_paths
from co_cli.display.core import TerminalFrontend
from co_cli.memory.session import find_latest_session, new_session_path
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
) -> bool:
    """Check TEI reranker availability. Returns False if TEI is absent or unreachable.

    Hybrid mode requires TEI — False causes the caller to degrade to fts5.
    Skipped on grep — no index means no reranking.
    """
    from co_cli.bootstrap.check import _check_cross_encoder

    cross_result = _check_cross_encoder(config)
    if cross_result.status == "ok":
        tei_batch = cross_result.extra.get("max_client_batch_size")
        if isinstance(tei_batch, int) and tei_batch > 0:
            config.knowledge.tei_rerank_batch_size = tei_batch
        return True
    # TEI not configured or unreachable — hybrid requires TEI reranker
    if cross_result.status == "skipped":
        statuses.append("  Hybrid requires TEI reranker — cross_encoder_reranker_url not set")
        logger.warning("TEI cross-encoder not configured; hybrid mode cannot start")
    else:
        statuses.append("  Hybrid requires TEI reranker — TEI cross-encoder unavailable")
        logger.warning("TEI cross-encoder configured but unavailable; hybrid mode cannot start")
        config.knowledge.cross_encoder_reranker_url = None
    return False


def _discover_memory_backend(
    config: Settings,
    frontend: TerminalFrontend,
    degradations: dict[str, str],
) -> MemoryStore | None:
    """Discover which knowledge backend is available and construct the store.

    Two-tier resolution with fail-fast on FTS unavailability:
      1. hybrid  — sqlite-vec + embedding provider (richest search)
      2. fts5    — SQLite FTS5 index (minimum required for session recall)
      3. grep    — explicit opt-in only (search_backend: grep in config)

    FTS5 is the minimum required backend. If the configured backend is fts5 or
    hybrid and FTS5 fails to initialise, bootstrap raises rather than silently
    degrading — a grep fallback would lose the sessions recall channel entirely.
    Raises RuntimeError on FTS5 init failure.
    """
    if config.knowledge.search_backend == "grep":
        return None

    configured: KnowledgeBackendLiteral = config.knowledge.search_backend
    statuses: list[str] = []

    # Resolve reranker — if TEI is configured but unreachable, hybrid cannot start
    reranker_ok = _resolve_reranker(config, statuses)

    # --- Level 1: hybrid (sqlite-vec + embedding) ---
    # Only attempt hybrid if that's what was configured; respect explicit fts5 choice.
    resolved_backend: KnowledgeBackendLiteral = "fts5"
    if configured == "hybrid":
        if not reranker_ok:
            logger.warning("Hybrid skipped: TEI reranker configured but unavailable")
        elif config.knowledge.embedding_provider == "none":
            logger.info("Hybrid skipped: embedding provider is 'none'")
        else:
            from co_cli.bootstrap.check import _check_embedder

            embedder_check = _check_embedder(config)
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
        reason = "TEI reranker unavailable" if not reranker_ok else "embedder unavailable"
        degradations["knowledge"] = f"{configured} → {resolved_backend} ({reason})"
    config.knowledge.search_backend = resolved_backend

    # --- Construct store with resolved config ---
    from co_cli.memory.memory_store import MemoryStore as _MS

    def _degrade_to(backend: KnowledgeBackendLiteral, reason: str) -> None:
        config.knowledge.search_backend = backend
        degradations["knowledge"] = f"{configured} → {backend} ({reason})"

    try:
        return _MS(config=config)
    except Exception as exc:
        if resolved_backend == "hybrid":
            logger.warning("Hybrid backend unavailable: %s", exc)
            frontend.on_status(
                f"  Knowledge degraded — hybrid unavailable "
                f"({_summarize_backend_error(exc)}); trying fts5"
            )
            _degrade_to("fts5", _summarize_backend_error(exc))
            try:
                return _MS(config=config)
            except Exception as exc2:
                detail = _summarize_backend_error(exc2)
                logger.error("FTS5 backend unavailable: %s", exc2)
                frontend.on_status(f"  Knowledge error — fts5 unavailable ({detail})")
                raise RuntimeError(
                    f"FTS5 knowledge backend failed to initialise ({detail}). "
                    "FTS5 is the minimum required backend for session recall. "
                    "Set search_backend: grep in config to opt out of FTS entirely."
                ) from exc2
        else:
            detail = _summarize_backend_error(exc)
            logger.error("FTS5 backend unavailable: %s", exc)
            frontend.on_status(f"  Knowledge error — fts5 unavailable ({detail})")
            raise RuntimeError(
                f"FTS5 knowledge backend failed to initialise ({detail}). "
                "FTS5 is the minimum required backend for session recall. "
                "Set search_backend: grep in config to opt out of FTS entirely."
            ) from exc


def _sync_memory_store(
    store: MemoryStore | None,
    config: Settings,
    frontend: TerminalFrontend,
    knowledge_dir: Path,
) -> MemoryStore | None:
    """Reconcile the knowledge store with current knowledge files on disk.

    Hash-based — skips unchanged files. On sync failure, raises RuntimeError
    rather than silently dropping the store — a None store would lose session
    recall for the session without any visible signal.
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
            raise RuntimeError(f"Knowledge store sync failed: {e}") from e

    return store


async def create_deps(
    frontend: TerminalFrontend,
    stack: AsyncExitStack,
    theme_override: str | None = None,
) -> CoDeps:
    """Assemble CoDeps from settings: config, registries, MCP, knowledge, skills.

    MCP servers are entered on the provided stack so they stay alive for the
    session and are cleaned up when the stack closes. Knowledge backend is
    resolved with three-tier fallback (hybrid → fts5 → grep) and files synced.

    Raises ValueError on provider/model hard errors.
    """
    from co_cli.agent.core import build_tool_registry
    from co_cli.agent.mcp import discover_mcp_tools
    from co_cli.llm.factory import build_model

    config = copy.deepcopy(get_settings())
    if theme_override:
        config.theme = theme_override
    cwd = Path.cwd()
    paths = resolve_workspace_paths(config, cwd)

    # Step 2: fail-fast gate (config shape only — no IO)
    error = config.llm.validate_config()
    if error:
        raise ValueError(error)

    # Step 2b: Ollama context probe — fail-fast on undersized models,
    # override num_ctx with runtime Modelfile value when they differ.
    if config.llm.uses_ollama():
        from co_cli.bootstrap.check import _probe_ollama_context

        ctx_probe = _probe_ollama_context(config.llm.host, config.llm.model)
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
        from co_cli.agent.mcp import MCPToolsetEntry

        connected: list[MCPToolsetEntry] = []
        for entry in tool_registry.mcp_toolsets:
            try:
                async with asyncio.timeout(entry.timeout):
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
    from co_cli.commands.registry import BUILTIN_COMMANDS, filter_namespace_conflicts
    from co_cli.skills.loader import load_skills

    skill_errors: list[str] = []
    loaded_skills = load_skills(
        paths["skills_dir"],
        settings=config,
        user_skills_dir=paths["user_skills_dir"],
        errors=skill_errors,
    )
    skill_commands = filter_namespace_conflicts(
        loaded_skills, set(BUILTIN_COMMANDS.keys()), skill_errors
    )
    for msg in skill_errors:
        frontend.on_status(msg)

    # Step 6: discover memory backend + construct store (IO probes — three-tier fallback)
    memory_store = _discover_memory_backend(config, frontend, degradations)

    # Step 7: sync memory store with current files on disk
    memory_store = _sync_memory_store(
        memory_store,
        config,
        frontend,
        knowledge_dir=paths["knowledge_dir"],
    )

    # Step 8: assemble deps
    runtime = CoRuntimeState()
    return CoDeps(
        shell=ShellBackend(),
        config=config,
        model=llm_model,
        memory_store=memory_store,
        tool_index=tool_registry.tool_index,
        tool_registry=tool_registry,
        skill_commands=skill_commands,
        runtime=runtime,
        degradations=degradations,
        **paths,
    )


def init_session_index(
    deps: CoDeps,
    current_session_path: Path,
    frontend: TerminalFrontend,
) -> None:
    """Sync past sessions into the unified chunks pipeline.

    The current session is excluded so the in-progress transcript is never
    indexed mid-session. On first run after migration, removes the obsolete
    session-index.db.
    """
    if deps.memory_store is None:
        frontend.on_status("  Session index unavailable — memory store missing")
        return
    try:
        legacy_db = deps.sessions_dir.parent / "session-index.db"
        if legacy_db.exists():
            try:
                legacy_db.unlink()
                logger.info("Removed legacy session-index.db (superseded by chunks pipeline)")
            except OSError as exc:
                logger.warning("Could not remove legacy session-index.db: %s", exc)
        deps.memory_store.sync_sessions(deps.sessions_dir, exclude=current_session_path)
    except Exception as exc:
        logger.warning("Session sync failed: %s", exc)
        frontend.on_status(f"  Session index sync failed — {exc}")


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
