"""Dream-cycle state and orchestration for the knowledge lifecycle.

The dream cycle is a batch lifecycle pass that runs on session end (when
``consolidation_enabled`` is set). It performs three phases — transcript
mining, knowledge merge, and automated decay — each bounded and each
recoverable via ``knowledge_dir/_archive/``. Cross-cycle state (which
sessions have already been mined, cumulative counters, last-run timestamp)
persists to ``knowledge_dir/_dream_state.json``. See ``docs/specs/cognition.md``
for the lifecycle model.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

from opentelemetry import trace as otel_trace
from pydantic import BaseModel, Field
from pydantic_ai import Agent

from co_cli.knowledge._archive import archive_artifacts
from co_cli.knowledge._artifact import (
    IndexSourceEnum,
    KnowledgeArtifact,
    SourceTypeEnum,
    load_knowledge_artifacts,
)
from co_cli.knowledge._chunker import chunk_text
from co_cli.knowledge._decay import find_decay_candidates
from co_cli.knowledge._distiller import build_transcript_window
from co_cli.knowledge._frontmatter import render_knowledge_file
from co_cli.knowledge._similarity import token_jaccard
from co_cli.knowledge.mutator import _atomic_write
from co_cli.llm._call import llm_call
from co_cli.tools.knowledge.helpers import _slugify
from co_cli.tools.knowledge.write import knowledge_save

if TYPE_CHECKING:
    from co_cli.deps import CoDeps

logger = logging.getLogger(__name__)
_TRACER = otel_trace.get_tracer("co.dream")

_DREAM_STATE_FILENAME = "_dream_state.json"
_DREAM_PROMPT_PATH = Path(__file__).parent / "prompts" / "dream_miner.md"
_DREAM_MERGE_PROMPT_PATH = Path(__file__).parent / "prompts" / "dream_merge.md"
_DREAM_WINDOW_MAX_TEXT = 50
_DREAM_WINDOW_MAX_TOOL = 50
_DREAM_WINDOW_SOFT_CHAR_LIMIT = 16_000
_DREAM_WINDOW_CHUNK_SIZE = 12_000
_DREAM_WINDOW_CHUNK_OVERLAP = 2_000
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
    """Cross-cycle dream state persisted at ``knowledge_dir/_dream_state.json``.

    ``processed_sessions`` tracks session filenames that have been mined so
    the retrospective extractor skips them on subsequent cycles.
    """

    last_dream_at: str | None = None
    processed_sessions: list[str] = Field(default_factory=list)
    stats: DreamStats = Field(default_factory=DreamStats)


def dream_state_path(knowledge_dir: Path) -> Path:
    """Canonical path for the dream-state JSON file."""
    return knowledge_dir / _DREAM_STATE_FILENAME


def load_dream_state(knowledge_dir: Path) -> DreamState:
    """Load dream state from disk; return a fresh instance if missing or corrupt."""
    path = dream_state_path(knowledge_dir)
    if not path.exists():
        return DreamState()
    try:
        raw = path.read_text(encoding="utf-8")
        return DreamState.model_validate_json(raw)
    except (OSError, ValueError) as exc:
        logger.warning("load_dream_state: ignoring corrupt state at %s: %s", path, exc)
        return DreamState()


def save_dream_state(knowledge_dir: Path, state: DreamState) -> None:
    """Persist dream state as JSON to ``knowledge_dir/_dream_state.json``."""
    knowledge_dir.mkdir(parents=True, exist_ok=True)
    path = dream_state_path(knowledge_dir)
    payload = state.model_dump()
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# Transcript mining — retrospective sub-agent over past session transcripts
# ---------------------------------------------------------------------------


def build_dream_miner_agent() -> Agent[CoDeps, str]:
    """Build a dream miner agent. Hoist outside the chunk loop; call .run() per chunk."""
    return Agent(
        instructions=_DREAM_PROMPT_PATH.read_text(encoding="utf-8").strip(),
        tools=[knowledge_save],
    )


def _chunk_dream_window(
    window: str,
    *,
    soft_limit: int = _DREAM_WINDOW_SOFT_CHAR_LIMIT,
    chunk_size: int = _DREAM_WINDOW_CHUNK_SIZE,
    overlap: int = _DREAM_WINDOW_CHUNK_OVERLAP,
) -> list[str]:
    """Split an oversized window into overlapping chunks; return a single-element
    list when the window fits under ``soft_limit``.
    """
    if len(window) <= soft_limit:
        return [window]
    chunks: list[str] = []
    pos = 0
    step = max(1, chunk_size - overlap)
    while pos < len(window):
        chunk = window[pos : pos + chunk_size]
        if chunk:
            chunks.append(chunk)
        if pos + chunk_size >= len(window):
            break
        pos += step
    return chunks


async def _mine_transcripts(deps: CoDeps, state: DreamState) -> int:
    """Mine recent unprocessed session transcripts for durable knowledge.

    Returns the number of new knowledge artifacts written to
    ``deps.knowledge_dir`` during this cycle. Sessions already listed in
    ``state.processed_sessions`` are skipped. Malformed or empty transcripts
    are marked processed so they are not retried. Sub-agent failures are
    logged and the session is left unmarked so a future cycle can retry.
    """
    from co_cli.context.transcript import load_transcript

    sessions_dir = deps.sessions_dir
    if not sessions_dir.exists():
        return 0

    lookback = deps.config.knowledge.consolidation_lookback_sessions
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

        before_count = _count_active_artifacts(deps.knowledge_dir)
        # initialize before loop — covers zero-chunk case where saves_so_far is never assigned
        saves_so_far = 0
        miner_agent = build_dream_miner_agent()
        try:
            for chunk in _chunk_dream_window(window):
                with _TRACER.start_as_current_span(
                    "invoke_agent _dream_miner_agent"
                ) as agent_span:
                    agent_span.set_attribute("agent.role", "dream_miner")
                    await miner_agent.run(
                        chunk,
                        deps=deps,
                        model=model_obj,
                    )
                saves_so_far = _count_active_artifacts(deps.knowledge_dir) - before_count
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


def _count_active_artifacts(knowledge_dir: Path) -> int:
    """Count top-level ``*.md`` artifacts, excluding the ``_archive/`` subdir."""
    if not knowledge_dir.exists():
        return 0
    return sum(1 for path in knowledge_dir.glob("*.md") if path.is_file())


# ---------------------------------------------------------------------------
# Knowledge merge — consolidate similar artifacts of the same kind
# ---------------------------------------------------------------------------


_DREAM_MERGE_PROMPT: str = _DREAM_MERGE_PROMPT_PATH.read_text(encoding="utf-8").strip()


def _is_merge_immune(artifact: KnowledgeArtifact) -> bool:
    """decay_protected artifacts are never merged."""
    return artifact.decay_protected


def _cluster_by_similarity(
    members: list[KnowledgeArtifact], threshold: float
) -> list[list[KnowledgeArtifact]]:
    """Union-find clustering by pairwise token-Jaccard similarity.

    Returns clusters of size ≥ 2 in arbitrary order. Same-kind grouping is
    handled by the caller.
    """
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

    grouped: dict[int, list[KnowledgeArtifact]] = defaultdict(list)
    for idx, member in enumerate(members):
        grouped[find(idx)].append(member)
    return [cluster for cluster in grouped.values() if len(cluster) >= 2]


def _render_merge_prompt(cluster: list[KnowledgeArtifact]) -> str:
    """Format cluster entries for the merge sub-agent."""
    parts: list[str] = []
    for index, artifact in enumerate(cluster, start=1):
        label = artifact.title or "untitled"
        parts.append(
            f"[Entry {index}] kind={artifact.artifact_kind} title={label}\n{artifact.content.strip()}"
        )
    return "\n\n---\n\n".join(parts)


def _write_consolidated_artifact(
    deps: CoDeps,
    cluster: list[KnowledgeArtifact],
    merged_body: str,
) -> KnowledgeArtifact:
    """Write a new consolidated artifact and index it. Source URL-less."""
    artifact_id = str(uuid4())
    union_tags: set[str] = set()
    for artifact in cluster:
        union_tags.update(artifact.tags)

    kind = cluster[0].artifact_kind
    title = cluster[0].title or f"consolidated {kind}"
    slug = _slugify(title)
    filename = f"{slug}-{artifact_id[:8]}.md"
    file_path = deps.knowledge_dir / filename

    merged_artifact = KnowledgeArtifact(
        id=artifact_id,
        path=file_path,
        artifact_kind=kind,
        title=title,
        content=merged_body.strip(),
        created=datetime.now(UTC).isoformat(),
        tags=sorted(union_tags),
        source_type=SourceTypeEnum.CONSOLIDATED.value,
        source_ref=None,
    )

    file_content = render_knowledge_file(merged_artifact)
    _atomic_write(file_path, file_content)

    store = deps.knowledge_store
    if store is not None:
        content_hash = hashlib.sha256(file_content.encode()).hexdigest()
        store.index(
            source=IndexSourceEnum.KNOWLEDGE,
            kind=kind,
            path=str(file_path),
            title=title,
            content=merged_body.strip(),
            mtime=file_path.stat().st_mtime,
            hash=content_hash,
            tags=" ".join(sorted(union_tags)) if union_tags else None,
            created=merged_artifact.created,
            type=kind,
            description=None,
            artifact_id=str(merged_artifact.id),
            source_ref=None,
        )
        chunks = chunk_text(
            merged_body.strip(),
            chunk_size=deps.config.knowledge.chunk_size,
            overlap=deps.config.knowledge.chunk_overlap,
        )
        store.index_chunks(IndexSourceEnum.KNOWLEDGE, str(file_path), chunks)

    return merged_artifact


async def _merge_cluster(
    deps: CoDeps, cluster: list[KnowledgeArtifact]
) -> KnowledgeArtifact | None:
    """Invoke the consolidation sub-agent and write one merged artifact.

    Returns the new artifact on success; returns ``None`` when the sub-agent
    output is too short or empty to trust (caller will skip archive).
    """
    prompt = _render_merge_prompt(cluster)
    merged_body = (await llm_call(deps, prompt, instructions=_DREAM_MERGE_PROMPT) or "").strip()
    if len(merged_body) < _MERGED_BODY_MIN_CHARS:
        logger.warning(
            "dream.merge: merged body too short (%d chars); skipping cluster",
            len(merged_body),
        )
        return None
    return _write_consolidated_artifact(deps, cluster, merged_body)


def _identify_mergeable_clusters(deps: CoDeps) -> list[list[KnowledgeArtifact]]:
    """Identify same-kind, non-immune clusters whose pairwise similarity clears
    the configured threshold. Applied caps: per-cluster size and per-cycle count.
    """
    threshold = deps.config.knowledge.consolidation_similarity_threshold
    artifacts = load_knowledge_artifacts(deps.knowledge_dir)
    if not artifacts:
        return []

    groups: dict[str, list[KnowledgeArtifact]] = defaultdict(list)
    for artifact in artifacts:
        if _is_merge_immune(artifact):
            continue
        groups[artifact.artifact_kind].append(artifact)

    clusters: list[list[KnowledgeArtifact]] = []
    for members in groups.values():
        clusters.extend(_cluster_by_similarity(members, threshold))

    clusters = [cluster[:_MAX_CLUSTER_SIZE] for cluster in clusters]
    return clusters[:_MAX_MERGES_PER_CYCLE]


async def _merge_similar_artifacts(deps: CoDeps) -> int:
    """Run the merge phase of the dream cycle.

    Returns the number of clusters merged. Respects per-cycle and per-cluster
    caps. Skips clusters containing any decay-protected artifact. Archives
    originals only after the merged artifact is durably written.
    """
    clusters = _identify_mergeable_clusters(deps)
    if not clusters:
        return 0

    knowledge_dir = deps.knowledge_dir
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
            archive_artifacts(cluster, knowledge_dir, deps.knowledge_store)
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


def _decay_sweep(deps: CoDeps) -> int:
    """Archive decay-eligible artifacts; return the number archived.

    Uses :func:`find_decay_candidates` (TASK-5.2) then moves the oldest
    ``_MAX_DECAY_PER_CYCLE`` entries to ``knowledge_dir/_archive/`` via
    :func:`archive_artifacts` (TASK-5.1). Pinned and decay-protected entries
    are already excluded by the candidate selector.
    """
    candidates = find_decay_candidates(deps.knowledge_dir, deps.config.knowledge)
    if not candidates:
        return 0
    batch = candidates[:_MAX_DECAY_PER_CYCLE]
    return archive_artifacts(batch, deps.knowledge_dir, deps.knowledge_store)


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


async def run_dream_cycle(
    deps: CoDeps,
    dry_run: bool = False,
    *,
    timeout_secs: float = _DREAM_CYCLE_TIMEOUT_SECS,
) -> DreamResult:
    """Execute a full dream cycle — mine transcripts, merge similar artifacts, decay stale.

    Each phase is independently try/except'd so one failure does not block the
    others. The whole cycle runs under an ``asyncio.timeout(timeout_secs)``
    bound (default 60s); on timeout the partial result is returned with
    ``timed_out=True`` and a marker appended to ``errors``. When ``dry_run``
    is True, no files are written, no originals are archived, and no state is
    persisted; ``result.merged`` reports the number of clusters that would be
    merged and ``result.decayed`` reports the number of artifacts that would
    be archived. Mining is skipped in dry-run mode (requires an LLM call to
    predict).
    """
    result = DreamResult()
    state = load_dream_state(deps.knowledge_dir)

    with _TRACER.start_as_current_span("co.dream.cycle") as cycle_span:
        cycle_span.set_attribute("dream.dry_run", dry_run)
        cycle_span.set_attribute("dream.timeout_secs", timeout_secs)

        try:
            async with asyncio.timeout(timeout_secs):
                if dry_run:
                    with _TRACER.start_as_current_span("co.dream.merge") as merge_span:
                        clusters = _identify_mergeable_clusters(deps)
                        result.merged = len(clusters)
                        merge_span.set_attribute("dream.merged", result.merged)
                    with _TRACER.start_as_current_span("co.dream.decay") as decay_span:
                        candidates = find_decay_candidates(
                            deps.knowledge_dir, deps.config.knowledge
                        )
                        result.decayed = min(len(candidates), _MAX_DECAY_PER_CYCLE)
                        decay_span.set_attribute("dream.decayed", result.decayed)
                else:
                    try:
                        with _TRACER.start_as_current_span("co.dream.mine") as mine_span:
                            result.extracted = await _mine_transcripts(deps, state)
                            mine_span.set_attribute("dream.extracted", result.extracted)
                    except Exception as exc:
                        logger.warning("dream.cycle: mine failed", exc_info=True)
                        result.errors.append(f"mine: {exc}")

                    try:
                        with _TRACER.start_as_current_span("co.dream.merge") as merge_span:
                            result.merged = await _merge_similar_artifacts(deps)
                            merge_span.set_attribute("dream.merged", result.merged)
                    except Exception as exc:
                        logger.warning("dream.cycle: merge failed", exc_info=True)
                        result.errors.append(f"merge: {exc}")

                    try:
                        with _TRACER.start_as_current_span("co.dream.decay") as decay_span:
                            result.decayed = _decay_sweep(deps)
                            decay_span.set_attribute("dream.decayed", result.decayed)
                    except Exception as exc:
                        logger.warning("dream.cycle: decay failed", exc_info=True)
                        result.errors.append(f"decay: {exc}")

                    state.last_dream_at = datetime.now(UTC).isoformat()
                    state.stats.total_cycles += 1
                    state.stats.total_extracted += result.extracted
                    state.stats.total_merged += result.merged
                    state.stats.total_decayed += result.decayed
                    save_dream_state(deps.knowledge_dir, state)
        except TimeoutError:
            logger.warning(
                "dream.cycle: timeout after %ss — returning partial result", timeout_secs
            )
            result.timed_out = True
            result.errors.append(f"timeout after {timeout_secs}s")

        cycle_span.set_attribute("dream.extracted", result.extracted)
        cycle_span.set_attribute("dream.merged", result.merged)
        cycle_span.set_attribute("dream.decayed", result.decayed)
        cycle_span.set_attribute("dream.errors", len(result.errors))
        cycle_span.set_attribute("dream.timed_out", result.timed_out)

    return result
