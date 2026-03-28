import dataclasses
import logging
from pathlib import Path
from typing import Any

from opentelemetry import trace

from co_cli.bootstrap._check import check_agent_llm
from co_cli.config import settings, ROLE_REASONING
from co_cli.context._history import OpeningContextState, SafetyState
from co_cli.context._session import load_session, is_fresh, new_session, save_session
from co_cli.deps import CoDeps, CoServices, CoConfig, CoRuntimeState
from co_cli.display._core import TerminalFrontend
from co_cli.tools._shell_backend import ShellBackend

logger = logging.getLogger(__name__)
_TRACER = trace.get_tracer("co-cli.bootstrap")


def _summarize_backend_error(exc: Exception) -> str:
    detail = str(exc).strip() or exc.__class__.__name__
    return detail.splitlines()[0]


def resolve_reranker(config: CoConfig) -> tuple[CoConfig, list[str]]:
    """Resolve reranker with graceful degradation to none.

    Each reranker degrades independently — cross-encoder and LLM listwise
    are checked separately. If unavailable, the field is set to None
    (results returned in BM25/vector order).
    """
    statuses: list[str] = []
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
    return config, statuses


def resolve_knowledge_backend(config: CoConfig) -> tuple[CoConfig, Any | None, list[str]]:
    """Resolve knowledge backend with graceful degradation.

    Bootstrap always prefers max capability and falls back with warnings:
      1. hybrid  — sqlite-vec + embedding provider (richest search)
      2. fts5    — SQLite FTS5 index (keyword search, no vectors)
      3. grep    — pure file search, no index required

    "grep" in config is the only hard stop — it skips indexing entirely.
    Otherwise bootstrap always attempts hybrid first regardless of config value,
    falling back through fts5 to grep as each level fails.

    Embedding provider "none" explicitly disables hybrid (no point attempting).
    """
    statuses: list[str] = []

    if config.knowledge_search_backend == "grep":
        return config, None, statuses

    from co_cli.knowledge._index_store import KnowledgeIndex

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
    """Assemble CoDeps from settings. Raises ValueError on provider/model hard errors."""
    # Step 1: build config (single call, fully resolved)
    config = CoConfig.from_settings(settings, cwd=Path.cwd())

    # Step 2: fail-fast gate (provider credentials + model availability)
    result = check_agent_llm(config)
    if result.status == "error":
        raise ValueError(result.detail)

    from co_cli.prompts._assembly import _build_system_prompt
    from co_cli.prompts.model_quirks._loader import normalize_model_name
    reasoning_entry = config.role_models.get(ROLE_REASONING)
    normalized_model = normalize_model_name(reasoning_entry.model) if reasoning_entry else ""
    config = dataclasses.replace(
        config,
        system_prompt=_build_system_prompt(config.llm_provider, normalized_model, config),
    )

    # Step 3: construct services
    config, reranker_statuses = resolve_reranker(config)
    config, knowledge_index, knowledge_statuses = resolve_knowledge_backend(config)
    startup_statuses = reranker_statuses + knowledge_statuses

    from co_cli.tools._background import TaskStorage, TaskRunner
    task_runner = TaskRunner(
        storage=TaskStorage(config.tasks_dir),
        max_concurrent=config.background_max_concurrent,
        inactivity_timeout=config.background_task_inactivity_timeout,
        auto_cleanup=config.background_auto_cleanup,
        retention_days=config.background_task_retention_days,
    )

    from co_cli._model_factory import ModelRegistry
    services = CoServices(
        shell=ShellBackend(),
        knowledge_index=knowledge_index,
        task_runner=task_runner,
        model_registry=ModelRegistry.from_config(config),
    )
    runtime = CoRuntimeState(
        startup_statuses=startup_statuses,
        opening_ctx_state=OpeningContextState(),
        safety_state=SafetyState(),
    )
    return CoDeps(services=services, config=config, runtime=runtime)


def sync_knowledge(deps: CoDeps, frontend: TerminalFrontend) -> None:
    with _TRACER.start_as_current_span("sync_knowledge") as span:
        try:
            if deps.services.knowledge_index is not None and (
                deps.config.memory_dir.exists() or deps.config.library_dir.exists()
            ):
                mem_count = deps.services.knowledge_index.sync_dir("memory", deps.config.memory_dir, kind_filter="memory")
                art_count = deps.services.knowledge_index.sync_dir("library", deps.config.library_dir, kind_filter="article")
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
            if deps.services.knowledge_index is not None:
                try:
                    deps.services.knowledge_index.close()
                except Exception:
                    pass
                deps.services.knowledge_index = None
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

