from __future__ import annotations

import dataclasses
import logging
from contextlib import AsyncExitStack
from pathlib import Path
from typing import TYPE_CHECKING

from opentelemetry import trace

if TYPE_CHECKING:
    from co_cli.knowledge._store import KnowledgeStore

from co_cli.config import settings
from co_cli.context._types import SafetyState
from co_cli.context._session import load_session, is_fresh, new_session, save_session
from co_cli.deps import CoDeps, CoConfig, CoRuntimeState
from co_cli.display._core import TerminalFrontend
from co_cli.tools._shell_backend import ShellBackend

logger = logging.getLogger(__name__)
_TRACER = trace.get_tracer("co-cli.bootstrap")


def _summarize_backend_error(exc: Exception) -> str:
    detail = str(exc).strip() or exc.__class__.__name__
    return detail.splitlines()[0]


def _resolve_reranker(config: CoConfig, statuses: list[str]) -> CoConfig:
    """Resolve reranker availability, appending degradation messages to statuses.

    Called from create_deps() only when an index is active (hybrid/fts5).
    Skipped on grep — no index means no reranking.
    """
    updates: dict = {}

    from co_cli.bootstrap._check import check_cross_encoder, check_reranker_llm
    cross_result = check_cross_encoder(config)
    if cross_result.status not in ("ok", "skipped"):
        statuses.append(
            "  Reranker degraded — TEI cross-encoder unavailable; search results will be unranked"
        )
        logger.warning("TEI cross-encoder unavailable; degrading to none")
        updates["knowledge_cross_encoder_reranker_url"] = None

    reranker_result = check_reranker_llm(config)
    if reranker_result.status not in ("ok", "skipped"):
        statuses.append(
            "  Reranker degraded — LLM reranker unavailable; search results will be unranked"
        )
        logger.warning("LLM reranker unavailable; degrading to none")
        updates["knowledge_llm_reranker"] = None

    if updates:
        config = dataclasses.replace(config, **updates)
    return config


def _discover_knowledge_backend(
    config: CoConfig, frontend: TerminalFrontend,
) -> KnowledgeStore | None:
    """Discover which knowledge backend is available and construct the store.

    Three-tier fallback with graceful degradation:
      1. hybrid  — sqlite-vec + embedding provider (richest search)
      2. fts5    — SQLite FTS5 index (keyword search, no vectors)
      3. grep    — pure file search, no store required

    Probes embedder availability, constructs the store with the resolved backend,
    and reports degradation to frontend. Config is never mutated for
    knowledge_search_backend — the store holds the actual backend in store.backend.
    Reranker fields must already be resolved in config before calling this function.
    Returns the constructed store, or None on full degradation to grep.
    """
    if config.knowledge_search_backend == "grep":
        return None

    statuses: list[str] = []

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

    # --- Construct store with resolved backend ---
    store_config = dataclasses.replace(
        config, knowledge_search_backend=resolved_backend,
    )

    from co_cli.knowledge._store import KnowledgeStore as _KS

    try:
        return _KS(config=store_config)
    except Exception as exc:
        if resolved_backend == "hybrid":
            logger.warning("Hybrid backend unavailable: %s", exc)
            frontend.on_status(
                f"  Knowledge degraded — hybrid unavailable "
                f"({_summarize_backend_error(exc)}); trying fts5"
            )
            fts5_config = dataclasses.replace(store_config, knowledge_search_backend="fts5")
            try:
                return _KS(config=fts5_config)
            except Exception as exc2:
                logger.warning("FTS5 backend unavailable: %s", exc2)
                frontend.on_status(
                    f"  Knowledge degraded — fts5 unavailable "
                    f"({_summarize_backend_error(exc2)}); using grep"
                )
                return None
        else:
            logger.warning("FTS5 backend unavailable: %s", exc)
            frontend.on_status(
                f"  Knowledge degraded — fts5 unavailable "
                f"({_summarize_backend_error(exc)}); using grep"
            )
            return None


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
                backend = store.backend
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
    from co_cli._model_factory import ModelRegistry, ResolvedModel
    from co_cli.agent import build_tool_registry, build_task_agent, discover_mcp_tools
    from co_cli.config import ROLE_TASK

    # Step 1: build config (single call, fully resolved)
    config = CoConfig.from_settings(settings, cwd=Path.cwd())

    # Step 2: fail-fast gate (config shape only — no IO)
    error = config.validate()
    if error:
        raise ValueError(error)

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

    # Step 6: resolve reranker availability (updates config for reranker fields only)
    if config.knowledge_search_backend != "grep":
        reranker_statuses: list[str] = []
        config = _resolve_reranker(config, reranker_statuses)
        for status in reranker_statuses:
            frontend.on_status(status)

    # Step 7: discover knowledge backend + construct store (IO probes — three-tier fallback)
    knowledge_store = _discover_knowledge_backend(config, frontend)

    # Step 8: sync knowledge store with current files on disk
    knowledge_store = _sync_knowledge_store(knowledge_store, config, frontend)

    # Step 9: build task agent (approval resume — lightweight, no personality)
    task_agents: dict = {}
    _no_model = ResolvedModel(model=None, settings=None)
    task_model = model_registry.get(ROLE_TASK, _no_model)
    if task_model.model:
        task_agents[ROLE_TASK] = build_task_agent(
            config=config, role_model=task_model, tool_registry=tool_registry,
        )

    # Step 10: assemble deps
    runtime = CoRuntimeState(safety_state=SafetyState())
    return CoDeps(
        shell=ShellBackend(),
        config=config,
        model_registry=model_registry,
        knowledge_store=knowledge_store,
        tool_index=tool_registry.tool_index,
        skill_commands=skill_commands,
        task_agents=task_agents,
        runtime=runtime,
    )



def restore_session(deps: CoDeps, frontend: TerminalFrontend) -> dict:
    with _TRACER.start_as_current_span("restore_session") as span:
        session_data = load_session(deps.config.session_path)
        if is_fresh(session_data, deps.config.session_ttl_minutes):
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
                save_session(deps.config.session_path, session_data)
            except OSError as e:
                # Continue without persistence — session_id is still set in deps.session
                frontend.on_status(f"  Session save failed — {e}; session will not persist")
            frontend.on_status(f"  Session new — {short_id}...")
        return session_data



