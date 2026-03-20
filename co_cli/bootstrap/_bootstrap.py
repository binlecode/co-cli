import dataclasses
import logging
from pathlib import Path
from typing import Any

from opentelemetry import trace

from co_cli.bootstrap._check import check_llm
from co_cli.config import settings, ROLE_REASONING
from co_cli.context._history import OpeningContextState, SafetyState
from co_cli.context._session import load_session, is_fresh, new_session, save_session
from co_cli.deps import CoDeps, CoServices, CoConfig, CoRuntimeState
from co_cli.display import TerminalFrontend
from co_cli.tools._shell_backend import ShellBackend

logger = logging.getLogger(__name__)
_TRACER = trace.get_tracer("co-cli.bootstrap")


def _summarize_backend_error(exc: Exception) -> str:
    detail = str(exc).strip() or exc.__class__.__name__
    return detail.splitlines()[0]


def resolve_knowledge_backend(config: CoConfig) -> tuple[CoConfig, Any | None, list[str]]:
    """Resolve the configured knowledge backend with graceful degradation."""
    backend = config.knowledge_search_backend
    statuses: list[str] = []
    initial_error: Exception | None = None

    if backend == "grep":
        return config, None, statuses

    from co_cli.knowledge._index_store import KnowledgeIndex

    try:
        return config, KnowledgeIndex(config=config), statuses
    except Exception as exc:
        initial_error = exc
        logger.warning("Knowledge backend bootstrap failed for %s: %s", backend, exc)

    if backend == "hybrid":
        fallback_config = dataclasses.replace(config, knowledge_search_backend="fts5")
        statuses.append(
            "  Knowledge backend degraded — hybrid unavailable "
            f"({_summarize_backend_error(initial_error or RuntimeError('hybrid init failed'))}); using fts5"
        )
        try:
            return fallback_config, KnowledgeIndex(config=fallback_config), statuses
        except Exception as fts_exc:
            logger.warning("Knowledge backend fallback failed for fts5: %s", fts_exc)
            statuses.append(
                "  Knowledge backend degraded — fts5 unavailable "
                f"({_summarize_backend_error(fts_exc)}); using grep"
            )
            return dataclasses.replace(config, knowledge_search_backend="grep"), None, statuses

    statuses.append(
        "  Knowledge backend degraded — fts5 unavailable "
        f"({_summarize_backend_error(initial_error or RuntimeError('fts5 init failed'))}); using grep"
    )
    return dataclasses.replace(config, knowledge_search_backend="grep"), None, statuses


def create_deps() -> CoDeps:
    """Assemble CoDeps from settings. Raises ValueError on provider/model hard errors."""
    # Step 1: build config (single call, fully resolved)
    config = CoConfig.from_settings(settings, cwd=Path.cwd())

    # Step 2: fail-fast gate (provider credentials + model availability)
    result = check_llm(config)
    if result.status == "error":
        raise ValueError(result.detail)

    from co_cli.agent import _build_system_prompt
    from co_cli.prompts.model_quirks._loader import normalize_model_name
    reasoning_entry = config.role_models.get(ROLE_REASONING)
    normalized_model = normalize_model_name(reasoning_entry.model) if reasoning_entry else ""
    config = dataclasses.replace(
        config,
        system_prompt=_build_system_prompt(config.llm_provider, normalized_model, config),
    )

    # Step 3: construct services
    config, knowledge_index, startup_statuses = resolve_knowledge_backend(config)

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
            save_session(deps.config.session_path, session_data)
            frontend.on_status(f"  Session new — {short_id}...")
        return session_data

