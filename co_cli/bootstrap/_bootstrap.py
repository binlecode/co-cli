from __future__ import annotations

import logging
from contextlib import AsyncExitStack
from pathlib import Path
from typing import TYPE_CHECKING

from opentelemetry import trace

if TYPE_CHECKING:
    from co_cli.knowledge._store import KnowledgeStore

from co_cli.config import settings
from co_cli.context.types import SafetyState
from co_cli.context.session import find_latest_session, new_session, save_session
from co_cli.deps import CoDeps, CoConfig, CoRuntimeState
from co_cli.display._core import TerminalFrontend
from co_cli.tools.shell_backend import ShellBackend

logger = logging.getLogger(__name__)
_TRACER = trace.get_tracer("co-cli.bootstrap")


def _summarize_backend_error(exc: Exception) -> str:
    detail = str(exc).strip() or exc.__class__.__name__
    return detail.splitlines()[0]


def _resolve_reranker(config: CoConfig, statuses: list[str]) -> None:
    """Resolve reranker availability, mutating config and appending degradation messages.

    Called inside _discover_knowledge_backend only when an index is active (hybrid/fts5).
    Skipped on grep — no index means no reranking.
    """
    from co_cli.bootstrap._check import check_cross_encoder, check_reranker_llm
    cross_result = check_cross_encoder(config)
    if cross_result.status not in ("ok", "skipped"):
        statuses.append(
            "  Reranker degraded — TEI cross-encoder unavailable; search results will be unranked"
        )
        logger.warning("TEI cross-encoder unavailable; degrading to none")
        config.knowledge_cross_encoder_reranker_url = None

    reranker_result = check_reranker_llm(config)
    if reranker_result.status not in ("ok", "skipped"):
        statuses.append(
            "  Reranker degraded — LLM reranker unavailable; search results will be unranked"
        )
        logger.warning("LLM reranker unavailable; degrading to none")
        config.knowledge_llm_reranker = None


def _discover_knowledge_backend(
    config: CoConfig, frontend: TerminalFrontend,
) -> tuple[CoConfig, KnowledgeStore | None]:
    """Discover which knowledge backend is available and construct the store.

    Three-tier fallback with graceful degradation:
      1. hybrid  — sqlite-vec + embedding provider (richest search)
      2. fts5    — SQLite FTS5 index (keyword search, no vectors)
      3. grep    — pure file search, no store required

    Probes embedder/reranker availability, mutates config fields directly, constructs
    the store, and reports degradation to frontend. Returns (config, store).
    Config reflects the runtime backend; degradations dict records what changed and why.
    """
    if config.knowledge_search_backend == "grep":
        return config, None

    configured = config.knowledge_search_backend
    statuses: list[str] = []

    # Resolve reranker — config fields must reflect actual availability
    # so the store and query pipeline see correct values.
    _resolve_reranker(config, statuses)

    # --- Level 1: hybrid (sqlite-vec + embedding) ---
    resolved_backend = "fts5"  # default fallback
    if config.knowledge_embedding_provider == "none":
        logger.info("Hybrid skipped: embedding provider is 'none'")
    else:
        from co_cli.bootstrap._check import check_embedder
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
        config.degradations = {**config.degradations, "knowledge": f"{configured} → {resolved_backend} (embedder unavailable)"}
    config.knowledge_search_backend = resolved_backend

    # --- Construct store with resolved config ---
    from co_cli.knowledge._store import KnowledgeStore as _KS

    def _degrade_to(backend: str, reason: str) -> None:
        """Mutate config to reflect degraded backend."""
        config.knowledge_search_backend = backend
        config.degradations = {**config.degradations, "knowledge": f"{configured} → {backend} ({reason})"}

    try:
        return config, _KS(config=config)
    except Exception as exc:
        if resolved_backend == "hybrid":
            logger.warning("Hybrid backend unavailable: %s", exc)
            frontend.on_status(
                f"  Knowledge degraded — hybrid unavailable "
                f"({_summarize_backend_error(exc)}); trying fts5"
            )
            _degrade_to("fts5", _summarize_backend_error(exc))
            try:
                return config, _KS(config=config)
            except Exception as exc2:
                logger.warning("FTS5 backend unavailable: %s", exc2)
                frontend.on_status(
                    f"  Knowledge degraded — fts5 unavailable "
                    f"({_summarize_backend_error(exc2)}); using grep"
                )
                _degrade_to("grep", _summarize_backend_error(exc2))
                return config, None
        else:
            logger.warning("FTS5 backend unavailable: %s", exc)
            frontend.on_status(
                f"  Knowledge degraded — fts5 unavailable "
                f"({_summarize_backend_error(exc)}); using grep"
            )
            _degrade_to("grep", _summarize_backend_error(exc))
            return config, None


def _sync_knowledge_store(
    store: KnowledgeStore | None,
    config: CoConfig,
    frontend: TerminalFrontend,
) -> KnowledgeStore | None:
    """Reconcile the knowledge store with current memory + library files on disk.

    Hash-based — skips unchanged files. On sync failure, closes the store and
    returns None (grep fallback for the session).
    """
    if store is None:
        frontend.on_status("  Knowledge store not available — skipped")
        return None

    with _TRACER.start_as_current_span("sync_knowledge") as span:
        try:
            if config.memory_dir.exists() or config.library_dir.exists():
                mem_count = store.sync_dir("memory", config.memory_dir, kind_filter="memory")
                art_count = store.sync_dir("library", config.library_dir, kind_filter="article")
                count = mem_count + art_count
                backend = config.knowledge_search_backend
                span.set_attribute("count", count)
                span.set_attribute("backend", backend)
                span.set_attribute("status", "ok")
                frontend.on_status(f"  Knowledge synced — {count} item(s) ({backend})")
            else:
                span.set_attribute("status", "skipped")
                frontend.on_status("  Knowledge store empty — no memory/library dirs")
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
    from co_cli._model_factory import ModelRegistry
    from co_cli.agent import build_tool_registry, discover_mcp_tools

    # Step 1: build config (single call, fully resolved)
    config = CoConfig.from_settings(settings, cwd=Path.cwd())

    # Step 2: fail-fast gate (config shape only — no IO)
    error = config.validate()
    if error:
        raise ValueError(error)

    # Step 2b: Ollama context probe — fail-fast on undersized models,
    # override llm_num_ctx with runtime Modelfile value when they differ.
    if config.uses_ollama_openai():
        reasoning_entry = config.role_models.get("reasoning")
        if reasoning_entry:
            from co_cli.bootstrap._check import probe_ollama_context
            ctx_probe = probe_ollama_context(config.llm_host, reasoning_entry.model)
            if ctx_probe.status == "error":
                raise ValueError(ctx_probe.detail)
            runtime_num_ctx = ctx_probe.extra.get("num_ctx", 0)
            if runtime_num_ctx > 0 and runtime_num_ctx != config.llm_num_ctx:
                logger.info(
                    "Ollama runtime num_ctx=%d differs from config llm_num_ctx=%d — using runtime value",
                    runtime_num_ctx, config.llm_num_ctx,
                )
                config.llm_num_ctx = runtime_num_ctx

    # Step 3: build registries (pure config — no IO)
    model_registry = ModelRegistry.from_config(config)
    tool_registry = build_tool_registry(config)

    # Step 4: MCP connect + discovery
    # Enter each server on the caller's stack so connections stay alive for the session.
    # Only connected servers are passed to discovery (failed ones are skipped).
    if tool_registry.mcp_toolsets:
        connected: list = []
        for ts in tool_registry.mcp_toolsets:
            try:
                await stack.enter_async_context(ts)
                connected.append(ts)
            except Exception as e:
                frontend.on_status(f"MCP server failed to connect: {e}")
        if connected:
            _, discovery_errors, mcp_index = await discover_mcp_tools(
                connected, exclude=set(tool_registry.tool_index.keys())
            )
            for prefix, err in discovery_errors.items():
                frontend.on_status(f"MCP server {prefix!r} failed to list tools: {err}")
            tool_registry.tool_index.update(mcp_index)

    # Step 5: load skills (filesystem reads — three-pass precedence merge)
    from co_cli.commands._commands import _load_skills
    skill_commands = _load_skills(config.skills_dir, settings=settings, user_skills_dir=config.user_skills_dir)

    # Step 6: discover knowledge backend + construct store (IO probes — three-tier fallback)
    config, knowledge_store = _discover_knowledge_backend(config, frontend)

    # Step 7: sync knowledge store with current files on disk
    knowledge_store = _sync_knowledge_store(knowledge_store, config, frontend)

    # Step 8: assemble deps
    runtime = CoRuntimeState(safety_state=SafetyState())
    return CoDeps(
        shell=ShellBackend(),
        config=config,
        model_registry=model_registry,
        knowledge_store=knowledge_store,
        tool_index=tool_registry.tool_index,
        skill_commands=skill_commands,
        runtime=runtime,
    )


def restore_session(deps: CoDeps, frontend: TerminalFrontend) -> dict:
    """Restore the most recent session from sessions/ dir, or create a new one."""
    with _TRACER.start_as_current_span("restore_session") as span:
        session_data = find_latest_session(deps.config.sessions_dir)
        if session_data is not None:
            deps.session.session_id = session_data["session_id"]
            short_id = deps.session.session_id[:8]
            span.set_attribute("status", "restored")
            span.set_attribute("session_id", short_id)
            frontend.on_status(f"  Session restored — {short_id}...")
        else:
            session_data = new_session()
            deps.session.session_id = session_data["session_id"]
            short_id = deps.session.session_id[:8]
            span.set_attribute("status", "new")
            span.set_attribute("session_id", short_id)
            try:
                save_session(deps.config.sessions_dir, session_data)
            except OSError as e:
                frontend.on_status(f"  Session save failed — {e}; session will not persist")
            frontend.on_status(f"  Session new — {short_id}...")
        return session_data



