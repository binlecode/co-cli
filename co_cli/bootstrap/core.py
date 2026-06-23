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
from co_cli.session.filename import new_session_path
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
    from co_cli.check import check_cross_encoder

    cross_result = check_cross_encoder(config)
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
      2. fts5    — SQLite FTS5 index (minimum required for memory + canon recall)
      3. grep    — explicit opt-in only (search_backend: grep in config)

    This backend serves memory + canon recall only; session recall is file-based
    ripgrep and does not use the index. FTS5 is the minimum required backend. If
    the configured backend is fts5 or hybrid and FTS5 fails to initialise,
    bootstrap raises rather than silently degrading — a grep fallback would lose
    the hybrid memory recall channel entirely.
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
            from co_cli.check import check_embedder

            embedder_check = check_embedder(config)
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


def _sync_indexes_offthread(
    config: Settings,
    on_status: Callable[[str], None],
    memory_dir: Path,
) -> None:
    """Run the blocking memory + canon index-sync on a worker thread.

    Opens its OWN short-lived IndexStore — created and used wholly within this
    worker thread — because sqlite connections are thread-affine
    (check_same_thread defaults True, store.py). The loop-thread IndexStore held
    by CoDeps is a separate connection to the same DB file; sqlite file-locking
    serialises these one-time bootstrap writes against the idle loop connection.

    The point of the offload is event-loop responsiveness: the embedding backend
    can cold-load for ~10s on the first embed, and running that on the loop thread
    makes the dream daemon deaf to SIGTERM for the whole window. With the embed in
    a worker thread, _run_foreground can race create_deps against shutdown
    (process.py) and exit promptly. RuntimeError from memory sync propagates to the
    caller, which closes the loop-thread store and re-raises.
    """
    from co_cli.index.store import IndexStore as _IS
    from co_cli.memory.store import MemoryStore as _MS

    worker_index = _IS(config=config)
    try:
        worker_memory = _MS(index=worker_index, config=config)
        _sync_memory_domain(worker_memory, config, on_status, memory_dir)
        _sync_canon_store(worker_index, config, on_status)
    finally:
        worker_index.close()


def _check_ollama_num_ctx_floor(num_ctx: int, model: str, max_context_tokens: int) -> None:
    """Raise ValueError when the model's num_ctx undercuts the configured max_context_tokens floor."""
    if num_ctx < max_context_tokens:
        raise ValueError(
            f"Ollama model {model!r} reports num_ctx={num_ctx:,} "
            f"but max_context_tokens={max_context_tokens:,} is configured. "
            f"Raise the model's num_ctx (Modelfile) or lower max_context_tokens in settings."
        )


def _probe_model_ctx(config: Settings) -> tuple[int, bool]:
    """Resolve model_max_context_tokens and agent vision-capability from config + Ollama probe.

    Returns ``(model_max_context_tokens, agent_vision_capable)``. Gemini is natively multimodal
    (vision True, no probe). Ollama reads num_ctx and the vision capability from one
    /api/show probe; probe failure degrades vision to False (honest gate) and falls
    back to the configured max_context_tokens for the context size.
    """
    if not config.llm.uses_ollama():
        logger.debug(
            "non-ollama provider %s; using configured max_context_tokens=%d",
            config.llm.provider,
            config.llm.max_context_tokens,
        )
        return config.llm.max_context_tokens, True

    from co_cli.check import probe_ollama_model, validate_ollama_num_ctx

    probe = probe_ollama_model(config.llm.host, config.llm.model)
    if probe.num_ctx is not None:
        _check_ollama_num_ctx_floor(probe.num_ctx, config.llm.model, config.llm.max_context_tokens)
    else:
        logger.warning(
            "ollama ctx probe failed; using configured max_context_tokens=%d as fallback",
            config.llm.max_context_tokens,
        )
    validate_ollama_num_ctx(config)
    return config.llm.max_context_tokens, probe.vision


@trace("tool_budget.resolved")
def _emit_tool_budget_span(
    model_max_context_tokens: int,
    spill_ratio: float,
    spill_threshold_tokens: int,
) -> None:
    from co_cli.config.tuning import MAX_TOOL_CALLS_PER_MODEL_REQUEST, SPILL_THRESHOLD_CHARS

    span = current_span()
    span.set_attribute("budget.context_window_tokens", model_max_context_tokens)
    span.set_attribute("budget.spill_ratio", spill_ratio)
    span.set_attribute("budget.tool_call_limit", MAX_TOOL_CALLS_PER_MODEL_REQUEST)
    span.set_attribute("budget.spill_threshold_chars", SPILL_THRESHOLD_CHARS)
    span.set_attribute("budget.spill_threshold_tokens", spill_threshold_tokens)


async def create_deps(
    *,
    on_status: Callable[[str], None],
    stack: AsyncExitStack | None = None,
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
    paths = resolve_workspace_paths(config)

    error = config.llm.validate_config()
    if error:
        raise ValueError(error)

    model_max_context_tokens, agent_vision_capable = _probe_model_ctx(config)

    from co_cli.config.tuning import MAX_TOOL_CALLS_PER_MODEL_REQUEST, SPILL_THRESHOLD_CHARS

    spill_ratio = config.compaction.spill_ratio
    spill_threshold_tokens = int(spill_ratio * model_max_context_tokens)

    _emit_tool_budget_span(
        model_max_context_tokens=model_max_context_tokens,
        spill_ratio=spill_ratio,
        spill_threshold_tokens=spill_threshold_tokens,
    )

    logger.info(
        "tool-budget bounds: context_window=%d tool_call_limit=%d spill=%dc spill_threshold=%d tokens",
        model_max_context_tokens,
        MAX_TOOL_CALLS_PER_MODEL_REQUEST,
        SPILL_THRESHOLD_CHARS,
        spill_threshold_tokens,
    )

    llm_model = build_model(config.llm)
    judge_llm_model = build_judge_model(config.llm)
    native_toolset, tool_catalog = build_native_toolset()

    degradations: dict[str, str] = {}
    connected: list[MCPToolsetEntry] = []
    if stack is not None:
        mcp_entries = build_mcp_entries(config, tool_catalog)
        for entry in mcp_entries:
            try:
                async with asyncio.timeout(entry.connect_timeout_seconds):
                    await stack.enter_async_context(entry.toolset)
                connected.append(entry)
            except Exception as e:
                on_status(f"MCP server failed to connect: {e}")
        if connected:
            _, discovery_errors, mcp_tool_catalog = await discover_mcp_tools(
                connected, exclude=set(tool_catalog.keys())
            )
            for prefix, err in discovery_errors.items():
                on_status(f"MCP server {prefix!r} failed to list tools: {err}")
                degradations[f"mcp.{prefix}"] = err[:120]
            tool_catalog.update(mcp_tool_catalog)

    toolset = assemble_routing_toolset(native_toolset, [entry.toolset for entry in connected])

    from co_cli.commands.registry import BUILTIN_COMMANDS, filter_namespace_conflicts
    from co_cli.skills.loader import load_skills

    skill_errors: list[str] = []
    loaded_skills = load_skills(
        paths["skills_dir"],
        user_skills_dir=paths["user_skills_dir"],
        errors=skill_errors,
    )
    skill_catalog = filter_namespace_conflicts(
        loaded_skills, set(BUILTIN_COMMANDS.keys()), skill_errors
    )
    for msg in skill_errors:
        on_status(msg)

    index_store = _discover_index_backend(config, on_status, degradations)

    from co_cli.session.store import SessionStore as _SS

    session_store: SessionStore = _SS(config=config, sessions_dir=paths["sessions_dir"])

    memory_store: MemoryStore | None = None
    if index_store is not None:
        from co_cli.memory.store import MemoryStore as _MS

        memory_store = _MS(index=index_store, config=config)
        try:
            await asyncio.to_thread(
                _sync_indexes_offthread, config, on_status, paths["memory_dir"]
            )
        except RuntimeError:
            index_store.close()
            raise

    runtime = CoRuntimeState()
    deps = CoDeps(
        shell=ShellBackend(),
        config=config,
        model=llm_model,
        judge_model=judge_llm_model,
        agent_vision_capable=agent_vision_capable,
        index_store=index_store,
        memory_store=memory_store,
        session_store=session_store,
        tool_catalog=tool_catalog,
        toolset=toolset,
        skill_catalog=skill_catalog,
        runtime=runtime,
        model_max_context_tokens=model_max_context_tokens,
        spill_threshold_tokens=spill_threshold_tokens,
        degradations=MappingProxyType(degradations),
        **paths,
    )

    from co_cli.bootstrap.schema_budget import measure_always_schema_budget
    from co_cli.config.tuning import ESTIMATE_CHARS_PER_TOKEN
    from co_cli.context.assembly import build_base_instructions
    from co_cli.context.guidance import build_toolset_guidance
    from co_cli.context.tokens import estimate_text_tokens
    from co_cli.personality.prompts.loader import load_soul_critique

    # Full delivered instruction floor — the three static builders that the
    # orchestrator joins into the cached prefix (base instructions + toolset
    # guidance + personality critique). Measuring only the base under-counts the
    # floor that actually rides every request.
    instruction_tokens = estimate_text_tokens(build_base_instructions(config))
    instruction_tokens += estimate_text_tokens(build_toolset_guidance(tool_catalog))
    if config.personality:
        instruction_tokens += estimate_text_tokens(load_soul_critique(config.personality))
    schema_budget = await measure_always_schema_budget(deps, native_toolset)
    deps.static_floor_tokens = (
        instruction_tokens + schema_budget.total_chars // ESTIMATE_CHARS_PER_TOKEN
    )

    return deps


def maybe_autospawn_dream(deps: CoDeps, frontend: TerminalFrontend) -> None:
    """Spawn the dream daemon in the background if not already running.

    No-op when dream.autostart is False or CO_DREAM_NO_AUTOSPAWN is set.
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

    if not deps.config.dream.autostart:
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


@trace("start_session")
def start_session(deps: CoDeps, frontend: TerminalFrontend) -> Path:
    """Start a fresh session. Resuming a prior session is explicit via /resume."""
    span = current_span()
    session_path = new_session_path(deps.sessions_dir)
    deps.session.session_path = session_path
    short_id = session_path.stem[-8:]
    span.set_attribute("status", "new")
    span.set_attribute("session_id", short_id)
    frontend.on_status(f"  Session new — {short_id}...")
    return session_path
