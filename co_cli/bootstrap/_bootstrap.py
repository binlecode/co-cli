import dataclasses
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from opentelemetry import trace
from pydantic_ai import Agent

from co_cli.agent import discover_mcp_tools
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

    Called inside resolve_knowledge_backend only when an index is active (hybrid/fts5).
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


def resolve_knowledge_backend(config: CoConfig) -> tuple[CoConfig, Any | None, list[str]]:
    """Resolve knowledge backend and reranker with graceful degradation.

    Bootstrap always prefers max capability and falls back with warnings:
      1. hybrid  — sqlite-vec + embedding provider (richest search)
      2. fts5    — SQLite FTS5 index (keyword search, no vectors)
      3. grep    — pure file search, no index required

    "grep" in config is the only hard stop — it skips indexing entirely.
    Otherwise bootstrap always attempts hybrid first regardless of config value,
    falling back through fts5 to grep as each level fails.

    Reranker resolution runs only when an index is active (hybrid or fts5).
    On grep there is no index to rerank against, so the probe is skipped.

    Embedding provider "none" explicitly disables hybrid (no point attempting).
    """
    statuses: list[str] = []

    if config.knowledge_search_backend == "grep":
        return config, None, statuses

    from co_cli.knowledge._index_store import KnowledgeIndex

    # Resolve reranker before index construction — config fields must reflect
    # actual availability so the index and query pipeline see correct values.
    config = _resolve_reranker(config, statuses)

    # --- Level 1: hybrid (sqlite-vec + embedding) ---
    if config.knowledge_embedding_provider == "none":
        # Embedding explicitly disabled — skip hybrid silently.
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
            hybrid_config = dataclasses.replace(config, knowledge_search_backend="hybrid")
            try:
                return hybrid_config, KnowledgeIndex(config=hybrid_config), statuses
            except Exception as exc:
                logger.warning("Hybrid backend unavailable: %s", exc)
                statuses.append(
                    f"  Knowledge degraded — hybrid unavailable "
                    f"({_summarize_backend_error(exc)}); using fts5"
                )

    # --- Level 2: fts5 (index DB required) ---
    fts5_config = dataclasses.replace(config, knowledge_search_backend="fts5")
    try:
        return fts5_config, KnowledgeIndex(config=fts5_config), statuses
    except Exception as exc:
        logger.warning("FTS5 backend unavailable: %s", exc)
        statuses.append(
            f"  Knowledge degraded — fts5 unavailable "
            f"({_summarize_backend_error(exc)}); using grep"
        )

    # --- Level 3: grep (no index) ---
    return dataclasses.replace(config, knowledge_search_backend="grep"), None, statuses


def create_deps() -> CoDeps:
    """Assemble CoDeps from settings — pure config, zero IO.

    Raises ValueError on provider/model hard errors.
    """
    from co_cli._model_factory import ModelRegistry
    from co_cli.agent import build_tool_registry

    # Step 1: build config (single call, fully resolved)
    config = CoConfig.from_settings(settings, cwd=Path.cwd())

    # Step 2: fail-fast gate (config shape only — no IO)
    error = config.validate()
    if error:
        raise ValueError(error)

    # Step 3: build registries (pure config — no IO)
    model_registry = ModelRegistry.from_config(config)
    tool_registry = build_tool_registry(config)

    # Step 4: assemble deps
    runtime = CoRuntimeState(safety_state=SafetyState())
    return CoDeps(
        shell=ShellBackend(),
        config=config,
        model_registry=model_registry,
        tool_index=tool_registry.tool_index,
        runtime=runtime,
    )


def initialize_knowledge(deps: CoDeps, frontend: TerminalFrontend) -> None:
    """Resolve knowledge backend (IO probes) and update deps in-place.

    Called in _chat_loop after initialize_session_capabilities(), before sync_knowledge().
    Reports degradation statuses directly to frontend.
    """
    config, knowledge_index, statuses = resolve_knowledge_backend(deps.config)
    # CoConfig is frozen; CoDeps is not — whole-object replacement
    deps.config = config
    deps.knowledge_index = knowledge_index
    for status in statuses:
        frontend.on_status(status)


def sync_knowledge(deps: CoDeps, frontend: TerminalFrontend) -> None:
    with _TRACER.start_as_current_span("sync_knowledge") as span:
        try:
            if deps.knowledge_index is not None and (
                deps.config.memory_dir.exists() or deps.config.library_dir.exists()
            ):
                mem_count = deps.knowledge_index.sync_dir("memory", deps.config.memory_dir, kind_filter="memory")
                art_count = deps.knowledge_index.sync_dir("library", deps.config.library_dir, kind_filter="article")
                count = mem_count + art_count
                backend = deps.config.knowledge_search_backend
                span.set_attribute("count", count)
                span.set_attribute("backend", backend)
                span.set_attribute("status", "ok")
                frontend.on_status(f"  Knowledge synced — {count} item(s) ({backend})")
            else:
                span.set_attribute("status", "skipped")
                frontend.on_status("  Knowledge index not available — skipped")
        except Exception as e:
            span.set_attribute("status", "error")
            span.set_attribute("error", str(e))
            if deps.knowledge_index is not None:
                try:
                    deps.knowledge_index.close()
                except Exception:
                    pass
                deps.knowledge_index = None
            frontend.on_status(f"  Knowledge sync failed — {e}")


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


async def initialize_session_capabilities(
    agent: Agent,
    deps: CoDeps,
    frontend: TerminalFrontend,
    mcp_init_ok: bool,
) -> int:
    """Complete session capability assembly after agent context entry.

    Owns MCP discovery (conditional on mcp_servers configured and mcp_init_ok)
    and skill loading. Updates deps.tools in-place; callers read final state
    from deps after this returns.
    """
    # deferred imports to avoid bootstrap→commands module-level cycle
    from co_cli.commands._commands import _load_skills, set_skill_commands

    # 1. MCP discovery (conditional)
    if deps.config.mcp_servers and mcp_init_ok:
        _, discovery_errors, mcp_index = await discover_mcp_tools(
            agent, exclude=set(deps.tool_index.keys())
        )
        for prefix, err in discovery_errors.items():
            frontend.on_status(f"MCP server {prefix!r} failed to list tools: {err} ...")
        deps.tool_index.update(mcp_index)

    # 2. Skill loading
    skill_commands = _load_skills(deps.config.skills_dir, settings=settings, user_skills_dir=deps.config.user_skills_dir)
    set_skill_commands(skill_commands, deps)

    from co_cli.commands._commands import get_skill_registry
    return len(get_skill_registry(deps.skill_commands))

