from __future__ import annotations

import asyncio
import copy
import logging
from collections.abc import Callable
from contextlib import AsyncExitStack
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, Literal

from co_cli.observability.tracing import current_span, trace

if TYPE_CHECKING:
    from co_cli.index.store import IndexStore
    from co_cli.memory.store import MemoryStore
    from co_cli.session.store import SessionStore

import co_cli.personality
from co_cli.config.core import Settings, get_settings
from co_cli.deps import CoDeps, CoRuntimeState, resolve_workspace_paths
from co_cli.display.core import TerminalFrontend
from co_cli.session.filename import find_latest_session, new_session_path
from co_cli.tools.shell_backend import ShellBackend

logger = logging.getLogger(__name__)
MemoryBackendLiteral = Literal["grep", "fts5", "hybrid"]


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
            config.memory.tei_rerank_batch_size = tei_batch
        return True
    if cross_result.status == "skipped":
        statuses.append("  Hybrid requires TEI reranker — cross_encoder_reranker_url not set")
        logger.warning("TEI cross-encoder not configured; hybrid mode cannot start")
    else:
        statuses.append("  Hybrid requires TEI reranker — TEI cross-encoder unavailable")
        logger.warning("TEI cross-encoder configured but unavailable; hybrid mode cannot start")
        config.memory.cross_encoder_reranker_url = None
    return False


def _discover_index_backend(
    config: Settings,
    on_status: Callable[[str], None],
    degradations: dict[str, str],
) -> IndexStore | None:
    """Discover which memory backend is available and construct IndexStore.

    Two-tier resolution with fail-fast on FTS unavailability:
      1. hybrid  — sqlite-vec + embedding provider (richest search)
      2. fts5    — SQLite FTS5 index (minimum required for session recall)
      3. grep    — explicit opt-in only (search_backend: grep in config)

    FTS5 is the minimum required backend. If the configured backend is fts5 or
    hybrid and FTS5 fails to initialise, bootstrap raises rather than silently
    degrading — a grep fallback would lose the session recall channel entirely.
    """
    if config.memory.search_backend == "grep":
        return None

    configured: MemoryBackendLiteral = config.memory.search_backend
    statuses: list[str] = []

    reranker_ok = _resolve_reranker(config, statuses)

    resolved_backend: MemoryBackendLiteral = "fts5"
    if configured == "hybrid":
        if not reranker_ok:
            logger.warning("Hybrid skipped: TEI reranker configured but unavailable")
        elif config.memory.embedding_provider == "none":
            logger.info("Hybrid skipped: embedding provider is 'none'")
        else:
            from co_cli.bootstrap.check import _check_embedder

            embedder_check = _check_embedder(config)
            if embedder_check.status not in ("ok", "skipped"):
                logger.warning("Hybrid skipped: embedder unavailable — %s", embedder_check.detail)
                statuses.append(
                    f"  Memory degraded — embedder unavailable "
                    f"({embedder_check.detail}); using fts5"
                )
            else:
                resolved_backend = "hybrid"

    for status in statuses:
        on_status(status)

    if resolved_backend != configured:
        reason = "TEI reranker unavailable" if not reranker_ok else "embedder unavailable"
        degradations["memory"] = f"{configured} → {resolved_backend} ({reason})"
    config.memory.search_backend = resolved_backend

    from co_cli.index.store import IndexStore as _IS

    def _degrade_to(backend: MemoryBackendLiteral, reason: str) -> None:
        config.memory.search_backend = backend
        degradations["memory"] = f"{configured} → {backend} ({reason})"

    try:
        return _IS(config=config)
    except Exception as exc:
        if resolved_backend == "hybrid":
            logger.warning("Hybrid backend unavailable: %s", exc)
            on_status(
                f"  Memory degraded — hybrid unavailable "
                f"({_summarize_backend_error(exc)}); trying fts5"
            )
            _degrade_to("fts5", _summarize_backend_error(exc))
            try:
                return _IS(config=config)
            except Exception as exc2:
                detail = _summarize_backend_error(exc2)
                logger.error("FTS5 backend unavailable: %s", exc2)
                on_status(f"  Memory error — fts5 unavailable ({detail})")
                raise RuntimeError(
                    f"FTS5 memory backend failed to initialise ({detail}). "
                    "FTS5 is the minimum required backend for session recall. "
                    "Set search_backend: grep in config to opt out of FTS entirely."
                ) from exc2
        else:
            detail = _summarize_backend_error(exc)
            logger.error("FTS5 backend unavailable: %s", exc)
            on_status(f"  Memory error — fts5 unavailable ({detail})")
            raise RuntimeError(
                f"FTS5 memory backend failed to initialise ({detail}). "
                "FTS5 is the minimum required backend for session recall. "
                "Set search_backend: grep in config to opt out of FTS entirely."
            ) from exc


@trace("sync_memory")
def _sync_memory_domain(
    memory_store: MemoryStore | None,
    config: Settings,
    on_status: Callable[[str], None],
    memory_dir: Path,
) -> None:
    """Reconcile memory artifacts with current files on disk."""
    if memory_store is None:
        on_status("  Memory store not available — skipped")
        return

    span = current_span()
    try:
        if memory_dir.exists():
            count = memory_store.sync_dir(memory_dir)
            backend = config.memory.search_backend
            span.set_attribute("count", count)
            span.set_attribute("backend", backend)
            span.set_attribute("status", "ok")
            on_status(f"  Memory synced — {count} item(s) ({backend})")
        else:
            span.set_attribute("status", "skipped")
            on_status("  Memory store empty — no memory dir")
    except Exception as e:
        span.set_attribute("status", "error")
        span.set_attribute("error", str(e))
        raise RuntimeError(f"Memory store sync failed: {e}") from e


def _sync_canon_store(
    index_store: IndexStore | None,
    config: Settings,
    on_status: Callable[[str], None],
) -> None:
    """Index canon scene files into the shared FTS pipeline under source='canon'."""
    if index_store is None or not config.personality:
        return
    souls_dir = (Path(co_cli.personality.__file__).parent / "prompts" / "souls").resolve()
    canon_dir = souls_dir / config.personality / "canon"
    if not canon_dir.exists():
        return
    try:
        count = _sync_canon_dir(index_store, canon_dir)
        logger.debug("Canon synced — %d file(s) for role=%s", count, config.personality)
    except Exception as exc:
        logger.warning("Canon store sync failed: %s", exc)
        on_status(f"  Canon sync failed — {exc}")


def _sync_canon_dir(index_store: IndexStore, canon_dir: Path) -> int:
    """Index canon files under source='canon' with no chunking (one chunk per file)."""
    import hashlib

    from co_cli.index.chunk import Chunk
    from co_cli.memory.frontmatter import parse_frontmatter

    current_paths: set[str] = set()
    indexed = 0
    for file_path in canon_dir.glob("*.md"):
        path_str = str(file_path)
        current_paths.add(path_str)
        try:
            raw = file_path.read_text(encoding="utf-8")
            file_hash = hashlib.sha256(raw.encode("utf-8")).hexdigest()
            if not index_store.needs_reindex("canon", path_str, file_hash):
                continue

            frontmatter, body = parse_frontmatter(raw)
            title = frontmatter.get("title") or file_path.stem
            body_stripped = body.strip()
            chunk = Chunk(
                index=0,
                content=body_stripped,
                start_line=0,
                end_line=max(0, len(body_stripped.splitlines()) - 1),
            )
            with index_store.transaction() as tx:
                tx.upsert(
                    source="canon",
                    kind="canon",
                    path=path_str,
                    title=title,
                    mtime=file_path.stat().st_mtime,
                    hash=file_hash,
                    created_at=frontmatter.get("created_at"),
                    updated_at=frontmatter.get("updated_at"),
                    description=frontmatter.get("description"),
                )
                tx.index_chunks("canon", path_str, [chunk])
            indexed += 1
        except Exception as e:
            logger.warning(f"Failed to index canon file {file_path}: {e}")

    index_store.remove_stale("canon", current_paths, directory=canon_dir)
    return indexed


def _check_ollama_num_ctx_floor(num_ctx: int, model: str, max_ctx: int) -> None:
    """Raise ValueError when the model's num_ctx undercuts the configured max_ctx floor."""
    if num_ctx < max_ctx:
        raise ValueError(
            f"Ollama model {model!r} reports num_ctx={num_ctx:,} "
            f"but max_ctx={max_ctx:,} is configured. "
            f"Raise the model's num_ctx (Modelfile) or lower max_ctx in settings."
        )


def _probe_model_ctx(config: Settings) -> int:
    """Resolve model_max_ctx from config + Ollama probe."""
    if not config.llm.uses_ollama():
        logger.debug(
            "non-ollama provider %s; using configured max_ctx=%d",
            config.llm.provider,
            config.llm.max_ctx,
        )
        return config.llm.max_ctx

    from co_cli.bootstrap.check import probe_ollama_model, validate_ollama_num_ctx

    num_ctx = probe_ollama_model(config.llm.host, config.llm.model)
    if num_ctx is not None:
        _check_ollama_num_ctx_floor(num_ctx, config.llm.model, config.llm.max_ctx)
    else:
        logger.warning(
            "ollama ctx probe failed; using configured max_ctx=%d as fallback",
            config.llm.max_ctx,
        )
    validate_ollama_num_ctx(config)
    return config.llm.max_ctx


@trace("tool_budget.resolved")
def _emit_tool_budget_span(
    model_max_ctx: int,
    spill_ratio: float,
    spill_threshold_tokens: int,
) -> None:
    from co_cli.tools.tool_call_limit import MAX_TOOL_CALLS_PER_MODEL_TURN
    from co_cli.tools.tool_io import SPILL_THRESHOLD_CHARS

    span = current_span()
    span.set_attribute("budget.context_window_tokens", model_max_ctx)
    span.set_attribute("budget.spill_ratio", spill_ratio)
    span.set_attribute("budget.tool_call_limit", MAX_TOOL_CALLS_PER_MODEL_TURN)
    span.set_attribute("budget.spill_threshold_chars", SPILL_THRESHOLD_CHARS)
    span.set_attribute("budget.spill_threshold_tokens", spill_threshold_tokens)


async def create_deps(
    *,
    on_status: Callable[[str], None],
    stack: AsyncExitStack | None = None,
    theme_override: str | None = None,
) -> CoDeps:
    """Assemble CoDeps from settings: config, registries, MCP, memory, skills.

    on_status is the status-message sink (REPL: frontend.on_status; daemon: logger.info).
    stack is the MCP lifecycle stack — when None, MCP servers are not connected
    (headless callers like the dream daemon). Memory backend is resolved with
    three-tier fallback (hybrid → fts5 → grep) and files synced.

    Raises ValueError on provider/model hard errors.
    """
    from co_cli.agent.core import (
        assemble_routing_toolset,
        build_mcp_entries,
        build_native_toolset,
    )
    from co_cli.agent.mcp import MCPToolsetEntry, discover_mcp_tools
    from co_cli.llm.factory import build_judge_model, build_model

    config = copy.deepcopy(get_settings())
    if theme_override:
        config.theme = theme_override
    paths = resolve_workspace_paths(config)

    error = config.llm.validate_config()
    if error:
        raise ValueError(error)

    model_max_ctx = _probe_model_ctx(config)

    from co_cli.tools.tool_call_limit import MAX_TOOL_CALLS_PER_MODEL_TURN
    from co_cli.tools.tool_io import SPILL_THRESHOLD_CHARS

    spill_ratio = config.compaction.spill_ratio
    spill_threshold_tokens = int(spill_ratio * model_max_ctx)

    _emit_tool_budget_span(
        model_max_ctx=model_max_ctx,
        spill_ratio=spill_ratio,
        spill_threshold_tokens=spill_threshold_tokens,
    )

    logger.info(
        "tool-budget bounds: context_window=%d tool_call_limit=%d spill=%dc spill_threshold=%d tokens",
        model_max_ctx,
        MAX_TOOL_CALLS_PER_MODEL_TURN,
        SPILL_THRESHOLD_CHARS,
        spill_threshold_tokens,
    )

    llm_model = build_model(config.llm)
    judge_llm_model = build_judge_model(config.llm)
    native_toolset, tool_index = build_native_toolset(config)

    degradations: dict[str, str] = {}
    connected: list[MCPToolsetEntry] = []
    if stack is not None:
        mcp_entries = build_mcp_entries(config, tool_index)
        for entry in mcp_entries:
            try:
                async with asyncio.timeout(entry.timeout):
                    await stack.enter_async_context(entry.toolset)
                connected.append(entry)
            except Exception as e:
                on_status(f"MCP server failed to connect: {e}")
        if connected:
            _, discovery_errors, mcp_index = await discover_mcp_tools(
                connected, exclude=set(tool_index.keys())
            )
            for prefix, err in discovery_errors.items():
                on_status(f"MCP server {prefix!r} failed to list tools: {err}")
                degradations[f"mcp.{prefix}"] = err[:120]
            tool_index.update(mcp_index)

    toolset = assemble_routing_toolset(native_toolset, [entry.toolset for entry in connected])

    from co_cli.commands.registry import BUILTIN_COMMANDS, filter_namespace_conflicts
    from co_cli.skills.loader import load_skills

    skill_errors: list[str] = []
    loaded_skills = load_skills(
        paths["skills_dir"],
        user_skills_dir=paths["user_skills_dir"],
        errors=skill_errors,
    )
    skill_index = filter_namespace_conflicts(
        loaded_skills, set(BUILTIN_COMMANDS.keys()), skill_errors
    )
    for msg in skill_errors:
        on_status(msg)

    index_store = _discover_index_backend(config, on_status, degradations)

    memory_store: MemoryStore | None = None
    session_store: SessionStore | None = None
    if index_store is not None:
        from co_cli.memory.store import MemoryStore as _MS
        from co_cli.session.store import SessionStore as _SS

        memory_store = _MS(index=index_store, config=config)
        session_store = _SS(index=index_store, config=config)
        try:
            _sync_memory_domain(memory_store, config, on_status, paths["memory_dir"])
        except RuntimeError:
            index_store.close()
            raise

    _sync_canon_store(index_store, config, on_status)

    runtime = CoRuntimeState()
    deps = CoDeps(
        shell=ShellBackend(),
        config=config,
        model=llm_model,
        judge_model=judge_llm_model,
        index_store=index_store,
        memory_store=memory_store,
        session_store=session_store,
        tool_index=tool_index,
        toolset=toolset,
        skill_index=skill_index,
        runtime=runtime,
        model_max_ctx=model_max_ctx,
        spill_threshold_tokens=spill_threshold_tokens,
        degradations=MappingProxyType(degradations),
        **paths,
    )
    deps.runtime.background_status_callback = on_status
    return deps


def maybe_autospawn_dream(deps: CoDeps, frontend: TerminalFrontend) -> None:
    """Spawn the dream daemon in the background if not already running.

    No-op when dream.enabled is False or CO_DREAM_NO_AUTOSPAWN is set.
    Uses an advisory flock to prevent concurrent REPLs from double-spawning.
    Emits a one-shot notice to frontend on first spawn.
    """
    import os

    from co_cli.config.core import DREAM_LOCK, DREAM_PID_FILE
    from co_cli.daemons.dream.process import (
        acquire_start_lock,
        is_pid_live,
        read_pid,
        spawn_detached,
    )

    if not deps.config.dream.enabled:
        return
    if os.environ.get("CO_DREAM_NO_AUTOSPAWN"):
        return

    try:
        with acquire_start_lock(DREAM_LOCK):
            pid = read_pid(DREAM_PID_FILE)
            if pid is not None and is_pid_live(pid):
                return
            session_id = deps.session.session_path.stem
            spawn_detached(
                [
                    "co",
                    "dream",
                    "start",
                    "--foreground",
                    "--origin=repl-autospawn",
                    f"--session-id={session_id}",
                ]
            )
            frontend.on_status(
                "[dream] daemon started in background."
                " 'co dream status' to inspect; 'co dream stop' to stop."
            )
    except BlockingIOError:
        pass
    except Exception as exc:
        logger.warning("dream auto-spawn failed: %s", exc)


def init_session_index(
    deps: CoDeps,
    current_session_path: Path,
    frontend: TerminalFrontend,
) -> None:
    """Sync past sessions into the unified chunks pipeline.

    The current session is excluded so the in-progress transcript is never
    indexed mid-session.
    """
    if deps.session_store is None:
        frontend.on_status("  Session index unavailable — memory store missing")
        return
    try:
        deps.session_store.sync(deps.sessions_dir, exclude=current_session_path)
    except Exception as exc:
        logger.warning("Session sync failed: %s", exc)
        frontend.on_status(f"  Session index sync failed — {exc}")


@trace("restore_session")
def restore_session(deps: CoDeps, frontend: TerminalFrontend) -> Path:
    """Restore the most recent session or create a new session path."""
    span = current_span()
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
