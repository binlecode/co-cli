"""Bootstrap initialization — runs startup steps and reports status.

Each step does real init work, reports status via frontend.on_status(),
and emits an OTel span.
"""

from pathlib import Path

from opentelemetry import trace

from co_cli.deps import CoDeps
from co_cli.display import TerminalFrontend
from co_cli._session import load_session, is_fresh, new_session


async def run_bootstrap(
    deps: CoDeps,
    frontend: TerminalFrontend,
    *,
    knowledge_dir: Path,
    session_path: Path,
    session_ttl_minutes: int,
    n_skills: int,
) -> dict:
    """Run startup steps and report status.

    Returns session_data dict for use in subsequent touch_session/save_session calls.
    """
    tracer = trace.get_tracer("co-cli.bootstrap")

    # Step 1: sync_knowledge
    with tracer.start_as_current_span("sync_knowledge") as span:
        try:
            if deps.knowledge_index is not None and knowledge_dir.exists():
                count = deps.knowledge_index.sync_dir("memory", knowledge_dir)
                backend = deps.knowledge_search_backend
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

    # Step 2: restore_session
    with tracer.start_as_current_span("restore_session") as span:
        session_data = load_session(session_path)
        if is_fresh(session_data, session_ttl_minutes):
            deps.session_id = session_data["session_id"]
            short_id = deps.session_id[:8]
            span.set_attribute("status", "restored")
            span.set_attribute("session_id", short_id)
            frontend.on_status(f"  Session restored — {short_id}...")
        else:
            session_data = new_session()
            deps.session_id = session_data["session_id"]
            short_id = deps.session_id[:8]
            span.set_attribute("status", "new")
            span.set_attribute("session_id", short_id)
            frontend.on_status(f"  Session new — {short_id}...")

    # Step 3: skills loaded status
    frontend.on_status(f"  {n_skills} skill(s) loaded")

    return session_data
