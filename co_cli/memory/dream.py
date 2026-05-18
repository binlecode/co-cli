"""Dream-cycle state and orchestration for the memory lifecycle.

The dream cycle is a batch lifecycle pass that runs on session end (when
``consolidation_enabled`` is set). It performs three phases — transcript
mining, memory merge, and automated decay — each bounded and each
recoverable via ``memory_dir/_archive/``. Cross-cycle state (which sessions
have already been mined, cumulative counters, last-run timestamp) persists
to ``memory_dir/_dream_state.json``. See ``docs/specs/dream.md`` for the
dream lifecycle model.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field
from pydantic_ai import Agent

from co_cli.fileio.atomic import atomic_write_text
from co_cli.llm.call import llm_call
from co_cli.memory._window import build_transcript_window
from co_cli.memory.archive import archive_artifacts
from co_cli.memory.artifact import (
    MemoryArtifact,
    SourceTypeEnum,
    load_artifacts,
)
from co_cli.memory.decay import find_decay_candidates
from co_cli.memory.service import save_artifact
from co_cli.memory.similarity import token_jaccard
from co_cli.observability.tracing import current_span, trace
from co_cli.session.persistence import load_transcript
from co_cli.tools.lifecycle import CoToolLifecycle

if TYPE_CHECKING:
    from co_cli.deps import CoDeps

logger = logging.getLogger(__name__)

_DREAM_STATE_FILENAME = "_dream_state.json"
_DREAM_PROMPT_PATH = Path(__file__).parent / "prompts" / "dream_miner.md"
_DREAM_MERGE_PROMPT_PATH = Path(__file__).parent / "prompts" / "dream_merge.md"
_DREAM_WINDOW_MAX_TEXT = 50
_DREAM_WINDOW_MAX_TOOL = 50
_DREAM_WINDOW_SOFT_CHAR_LIMIT = 16_000
_DREAM_WINDOW_CHUNK_CHARS = 12_000
_DREAM_WINDOW_CHUNK_OVERLAP_CHARS = 2_000
_MAX_MERGES_PER_CYCLE = 10
_MAX_CLUSTER_SIZE = 5
_MERGED_BODY_MIN_CHARS = 20
_MAX_DECAY_PER_CYCLE = 20
_MAX_MINE_SAVES_PER_SESSION = 5
_DREAM_CYCLE_TIMEOUT_SECS = 60


class DreamStats(BaseModel):
    """Cumulative counters aggregated across every dream cycle run."""

    total_cycles: int = 0
    total_extracted: int = 0
    total_merged: int = 0
    total_decayed: int = 0


class DreamState(BaseModel):
    """Cross-cycle dream state persisted at ``memory_dir/_dream_state.json``."""

    last_dream_at: str | None = None
    processed_sessions: list[str] = Field(default_factory=list)
    stats: DreamStats = Field(default_factory=DreamStats)


def dream_state_path(memory_dir: Path) -> Path:
    """Canonical path for the dream-state JSON file."""
    return memory_dir / _DREAM_STATE_FILENAME


def load_dream_state(memory_dir: Path) -> DreamState:
    """Load dream state from disk; return a fresh instance if missing or corrupt."""
    path = dream_state_path(memory_dir)
    if not path.exists():
        return DreamState()
    try:
        raw = path.read_text(encoding="utf-8")
        return DreamState.model_validate_json(raw)
    except (OSError, ValueError) as exc:
        logger.warning("load_dream_state: ignoring corrupt state at %s: %s", path, exc)
        return DreamState()


def save_dream_state(memory_dir: Path, state: DreamState) -> None:
    """Persist dream state as JSON to ``memory_dir/_dream_state.json``."""
    path = dream_state_path(memory_dir)
    payload = state.model_dump()
    atomic_write_text(path, json.dumps(payload, indent=2))


# ---------------------------------------------------------------------------
# Transcript mining — retrospective sub-agent over past session transcripts
# ---------------------------------------------------------------------------


def build_dream_miner_agent(miner_tool: Any) -> Agent[CoDeps, str]:
    """Build a dream miner agent. Instantiated once per session."""
    return Agent(
        instructions=_DREAM_PROMPT_PATH.read_text(encoding="utf-8").strip(),
        tools=[miner_tool],
        capabilities=[CoToolLifecycle()],
    )


def _chunk_dream_window(
    window: str,
    *,
    soft_limit: int = _DREAM_WINDOW_SOFT_CHAR_LIMIT,
    chunk_chars: int = _DREAM_WINDOW_CHUNK_CHARS,
    overlap_chars: int = _DREAM_WINDOW_CHUNK_OVERLAP_CHARS,
) -> list[str]:
    if len(window) <= soft_limit:
        return [window]
    chunks: list[str] = []
    pos = 0
    step = max(1, chunk_chars - overlap_chars)
    while pos < len(window):
        chunks.append(window[pos : pos + chunk_chars])
        if pos + chunk_chars >= len(window):
            break
        pos += step
    return chunks


@trace("co.dream.mine")
async def _mine_transcripts(deps: CoDeps, state: DreamState, miner_tool: Any) -> int:
    """Mine recent unprocessed session transcripts for durable knowledge.

    Returns the number of new artifacts written to ``deps.memory_dir`` during
    this cycle. Sessions already listed in ``state.processed_sessions`` are
    skipped.
    """
    sessions_dir = deps.sessions_dir
    if not sessions_dir.exists():
        return 0

    lookback = deps.config.memory.consolidation_lookback_sessions
    all_sessions = sorted(sessions_dir.glob("*.jsonl"), reverse=True)
    recent = all_sessions[:lookback]

    already_processed = set(state.processed_sessions)
    extracted_total = 0
    model_obj = deps.model.model if deps.model else None

    for session_path in recent:
        session_name = session_path.name
        if session_name in already_processed:
            continue

        try:
            messages = load_transcript(session_path)
        except Exception:
            logger.warning("dream.mine: failed to load transcript %s", session_name, exc_info=True)
            continue

        if not messages:
            state.processed_sessions.append(session_name)
            continue

        window = build_transcript_window(
            messages,
            max_text=_DREAM_WINDOW_MAX_TEXT,
            max_tool=_DREAM_WINDOW_MAX_TOOL,
        )
        if not window.strip():
            state.processed_sessions.append(session_name)
            continue

        before_count = _count_active_artifacts(deps.memory_dir)
        saves_so_far = 0
        miner_agent = build_dream_miner_agent(miner_tool)
        try:
            for chunk in _chunk_dream_window(window):
                await miner_agent.run(
                    chunk,
                    deps=deps,
                    model=model_obj,
                    metadata={"role": "dream_miner"},
                )
                saves_so_far = _count_active_artifacts(deps.memory_dir) - before_count
                if saves_so_far >= _MAX_MINE_SAVES_PER_SESSION:
                    logger.info(
                        "dream.mine: per-session save cap reached for %s (%d saves)",
                        session_name,
                        saves_so_far,
                    )
                    break
        except Exception:
            logger.warning(
                "dream.mine: sub-agent failed on %s; will retry next cycle",
                session_name,
                exc_info=True,
            )
            continue

        extracted_total += saves_so_far
        state.processed_sessions.append(session_name)

    return extracted_total


def _count_active_artifacts(memory_dir: Path) -> int:
    """Count top-level ``*.md`` artifacts, excluding the ``_archive/`` subdir."""
    if not memory_dir.exists():
        return 0
    return sum(1 for path in memory_dir.glob("*.md") if path.is_file())


# ---------------------------------------------------------------------------
# Memory merge — consolidate similar artifacts of the same kind
# ---------------------------------------------------------------------------


_DREAM_MERGE_PROMPT: str = _DREAM_MERGE_PROMPT_PATH.read_text(encoding="utf-8").strip()


def _is_merge_immune(artifact: MemoryArtifact) -> bool:
    return artifact.decay_protected


def _cluster_by_similarity(
    members: list[MemoryArtifact], threshold: float
) -> list[list[MemoryArtifact]]:
    """Union-find clustering by pairwise token-Jaccard similarity."""
    size = len(members)
    if size < 2:
        return []

    parent = list(range(size))

    def find(node: int) -> int:
        while parent[node] != node:
            parent[node] = parent[parent[node]]
            node = parent[node]
        return node

    def union(left: int, right: int) -> None:
        root_left, root_right = find(left), find(right)
        if root_left != root_right:
            parent[root_left] = root_right

    for i in range(size):
        for j in range(i + 1, size):
            if find(i) == find(j):
                continue
            if token_jaccard(members[i].content, members[j].content) >= threshold:
                union(i, j)

    grouped: dict[int, list[MemoryArtifact]] = defaultdict(list)
    for idx, member in enumerate(members):
        grouped[find(idx)].append(member)
    return [cluster for cluster in grouped.values() if len(cluster) >= 2]


def _render_merge_prompt(cluster: list[MemoryArtifact]) -> str:
    parts: list[str] = []
    for index, artifact in enumerate(cluster, start=1):
        label = artifact.title or "untitled"
        parts.append(
            f"[Entry {index}] kind={artifact.artifact_kind} title={label}\n{artifact.content.strip()}"
        )
    return "\n\n---\n\n".join(parts)


def _write_consolidated_artifact(
    deps: CoDeps,
    cluster: list[MemoryArtifact],
    merged_body: str,
) -> Path:
    """Write a new consolidated artifact via save_artifact and index it."""
    kind = cluster[0].artifact_kind
    title = cluster[0].title or f"consolidated {kind}"
    result = save_artifact(
        deps.memory_dir,
        content=merged_body.strip(),
        artifact_kind=kind,
        title=title,
        source_type=SourceTypeEnum.CONSOLIDATED.value,
        index_store=deps.index_store,
    )

    if deps.memory_store is not None and result.action != "skipped":
        deps.memory_store.reindex_one(
            result.path,
            result.content,
            result.markdown_content,
            result.frontmatter_dict,
        )

    return result.path


async def _merge_cluster(deps: CoDeps, cluster: list[MemoryArtifact]) -> Path | None:
    """Invoke the consolidation sub-agent and write one merged artifact."""
    prompt = _render_merge_prompt(cluster)
    merged_body = (await llm_call(deps, prompt, instructions=_DREAM_MERGE_PROMPT) or "").strip()
    if len(merged_body) < _MERGED_BODY_MIN_CHARS:
        logger.warning(
            "dream.merge: merged body too short (%d chars); skipping cluster",
            len(merged_body),
        )
        return None
    return _write_consolidated_artifact(deps, cluster, merged_body)


def _identify_mergeable_clusters(deps: CoDeps) -> list[list[MemoryArtifact]]:
    """Identify same-kind, non-immune clusters above the similarity threshold."""
    threshold = deps.config.memory.consolidation_similarity_threshold
    artifacts = load_artifacts(deps.memory_dir)
    if not artifacts:
        return []

    groups: dict[str, list[MemoryArtifact]] = defaultdict(list)
    for artifact in artifacts:
        if _is_merge_immune(artifact):
            continue
        groups[artifact.artifact_kind].append(artifact)

    clusters: list[list[MemoryArtifact]] = []
    for members in groups.values():
        clusters.extend(_cluster_by_similarity(members, threshold))

    clusters = [cluster[:_MAX_CLUSTER_SIZE] for cluster in clusters]
    return clusters[:_MAX_MERGES_PER_CYCLE]


@trace("co.dream.merge.apply")
async def _merge_similar_artifacts(deps: CoDeps) -> int:
    clusters = _identify_mergeable_clusters(deps)
    if not clusters:
        return 0

    merged_count = 0
    for cluster in clusters:
        try:
            merged = await _merge_cluster(deps, cluster)
        except Exception:
            logger.warning("dream.merge: sub-agent failed on cluster", exc_info=True)
            continue
        if merged is None:
            continue
        try:
            archive_artifacts(cluster, deps.memory_dir, deps.memory_store)
        except Exception:
            logger.warning(
                "dream.merge: archive failed after merge; merged artifact kept",
                exc_info=True,
            )
        merged_count += 1

    return merged_count


# ---------------------------------------------------------------------------
# Decay sweep — archive long-unrecalled, non-immune artifacts
# ---------------------------------------------------------------------------


@trace("co.dream.decay.apply")
def _decay_sweep(deps: CoDeps) -> int:
    """Archive decay-eligible artifacts; return the number archived."""
    candidates = find_decay_candidates(deps.memory_dir, deps.config.memory)
    if not candidates:
        return 0
    batch = candidates[:_MAX_DECAY_PER_CYCLE]
    return archive_artifacts(batch, deps.memory_dir, deps.memory_store)


# ---------------------------------------------------------------------------
# Orchestrator — full dream cycle (mine → merge → decay)
# ---------------------------------------------------------------------------


@dataclass
class DreamResult:
    """Outcome of a single ``run_dream_cycle`` invocation."""

    extracted: int = 0
    merged: int = 0
    decayed: int = 0
    errors: list[str] = field(default_factory=list)
    timed_out: bool = False

    @property
    def any_changes(self) -> bool:
        return (self.extracted + self.merged + self.decayed) > 0


@trace("co.dream.merge.preview")
def _preview_merge_clusters(deps: CoDeps) -> int:
    clusters = _identify_mergeable_clusters(deps)
    n = len(clusters)
    current_span().set_attribute("dream.merged", n)
    return n


@trace("co.dream.decay.preview")
def _preview_decay_candidates(deps: CoDeps) -> int:
    candidates = find_decay_candidates(deps.memory_dir, deps.config.memory)
    n = min(len(candidates), _MAX_DECAY_PER_CYCLE)
    current_span().set_attribute("dream.decayed", n)
    return n


@trace("co.dream.cycle")
async def run_dream_cycle(
    deps: CoDeps,
    miner_tool: Any,
    dry_run: bool = False,
    *,
    timeout_secs: float = _DREAM_CYCLE_TIMEOUT_SECS,
) -> DreamResult:
    """Execute a full dream cycle — mine, merge, decay."""
    result = DreamResult()
    state = load_dream_state(deps.memory_dir)

    cycle_span = current_span()
    cycle_span.set_attribute("dream.dry_run", dry_run)
    cycle_span.set_attribute("dream.timeout_secs", timeout_secs)

    try:
        async with asyncio.timeout(timeout_secs):
            if dry_run:
                result.merged = _preview_merge_clusters(deps)
                result.decayed = _preview_decay_candidates(deps)
            else:
                try:
                    result.extracted = await _mine_transcripts(deps, state, miner_tool)
                except Exception as exc:
                    logger.warning("dream.cycle: mine failed", exc_info=True)
                    result.errors.append(f"mine: {exc}")

                try:
                    result.merged = await _merge_similar_artifacts(deps)
                except Exception as exc:
                    logger.warning("dream.cycle: merge failed", exc_info=True)
                    result.errors.append(f"merge: {exc}")

                try:
                    result.decayed = _decay_sweep(deps)
                except Exception as exc:
                    logger.warning("dream.cycle: decay failed", exc_info=True)
                    result.errors.append(f"decay: {exc}")

                state.last_dream_at = datetime.now(UTC).isoformat()
                state.stats.total_cycles += 1
                state.stats.total_extracted += result.extracted
                state.stats.total_merged += result.merged
                state.stats.total_decayed += result.decayed
                save_dream_state(deps.memory_dir, state)
    except TimeoutError:
        logger.warning("dream.cycle: timeout after %ss — returning partial result", timeout_secs)
        result.timed_out = True
        result.errors.append(f"timeout after {timeout_secs}s")

    cycle_span.set_attribute("dream.extracted", result.extracted)
    cycle_span.set_attribute("dream.merged", result.merged)
    cycle_span.set_attribute("dream.decayed", result.decayed)
    cycle_span.set_attribute("dream.errors", len(result.errors))
    cycle_span.set_attribute("dream.timed_out", result.timed_out)
    return result
