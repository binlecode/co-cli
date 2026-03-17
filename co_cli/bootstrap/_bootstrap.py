import dataclasses
import logging
from pathlib import Path

from opentelemetry import trace

from co_cli.bootstrap._check import check_llm, check_model_availability, check_runtime, RuntimeCheck
from co_cli.context._history import OpeningContextState, SafetyState
from co_cli.context._session import load_session, is_fresh, new_session, save_session
from co_cli.tools._shell_backend import ShellBackend
from co_cli.config import settings
from co_cli.deps import CoDeps, CoServices, CoConfig, CoRuntimeState
from co_cli.display import TerminalFrontend
from co_cli.prompts.personalities._loader import load_soul_critique

logger = logging.getLogger(__name__)
_TRACER = trace.get_tracer("co-cli.bootstrap")


def create_deps() -> CoDeps:
    """Assemble CoDeps from settings. Raises ValueError on provider/model hard errors."""
    _base_config = CoConfig.from_settings(settings)
    personality_critique = load_soul_critique(_base_config.personality) if _base_config.personality else ""

    config = dataclasses.replace(
        _base_config,
        exec_approvals_path=Path.cwd() / ".co-cli" / "exec-approvals.json",
        memory_dir=Path.cwd() / ".co-cli" / "memory",
        skills_dir=Path.cwd() / ".co-cli" / "skills",
        session_path=Path.cwd() / ".co-cli" / "session.json",
        tasks_dir=Path.cwd() / ".co-cli" / "tasks",
        personality_critique=personality_critique,
        knowledge_search_backend=settings.knowledge_search_backend,
        mcp_count=len(settings.mcp_servers),
    )

    result = check_llm(config)
    if result.status == "error":
        raise ValueError(result.detail)

    result = check_model_availability(config)
    if result.status == "error":
        raise ValueError(result.detail)

    knowledge_index = None
    if config.knowledge_search_backend in ("fts5", "hybrid"):
        from co_cli.knowledge._index_store import KnowledgeIndex
        knowledge_index = KnowledgeIndex(config=config)

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
    runtime = CoRuntimeState(opening_ctx_state=OpeningContextState(), safety_state=SafetyState())
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
            deps.config.session_id = session_data["session_id"]
            short_id = deps.config.session_id[:8]
            span.set_attribute("status", "restored")
            span.set_attribute("session_id", short_id)
            frontend.on_status(f"  Session restored — {short_id}...")
        else:
            session_data = new_session()
            deps.config.session_id = session_data["session_id"]
            short_id = deps.config.session_id[:8]
            span.set_attribute("status", "new")
            span.set_attribute("session_id", short_id)
            save_session(deps.config.session_path, session_data)
            frontend.on_status(f"  Session new — {short_id}...")
        return session_data


def check_integration_health(deps: CoDeps, frontend: TerminalFrontend) -> RuntimeCheck:
    with _TRACER.start_as_current_span("integration_health") as span:
        runtime_check = check_runtime(deps, skip_provider_checks=True)
        for line in runtime_check.summary_lines():
            frontend.on_status(line)
        span.set_attribute("status", "ok")
        span.set_attribute("has_errors", bool(runtime_check.findings))
        span.set_attribute("has_warnings", bool(runtime_check.fallbacks))
        return runtime_check


