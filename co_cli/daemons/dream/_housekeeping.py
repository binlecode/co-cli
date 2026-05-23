"""Clock-driven memory and skill housekeeping for the dream daemon.

Memory and skill merge + decay phases. Reviewer (Plan 1) is the sole transcript
reader; housekeeping here operates on the durable memory item store, the user
skill library, and per-skill recall sidecars.
"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from co_cli.config.dream import DreamSettings
from co_cli.daemons.dream._state import (
    HousekeepingState,
    save_housekeeping_state,
)
from co_cli.llm.call import llm_call
from co_cli.memory.archive import archive_artifacts
from co_cli.memory.decay import find_decay_candidates
from co_cli.memory.frontmatter import parse_frontmatter
from co_cli.memory.item import (
    MemoryItem,
    MemoryKindEnum,
    SourceTypeEnum,
    load_memory_items,
)
from co_cli.memory.service import save_memory_item
from co_cli.memory.similarity import token_jaccard
from co_cli.observability.tracing import trace
from co_cli.skills.usage import read_record

if TYPE_CHECKING:
    from co_cli.deps import CoDeps

logger = logging.getLogger(__name__)

_MEMORY_MERGE_PROMPT_PATH = Path(__file__).parent / "prompts" / "memory_merge.md"
_MEMORY_MERGE_PROMPT: str = _MEMORY_MERGE_PROMPT_PATH.read_text(encoding="utf-8").strip()
_SKILL_MERGE_PROMPT_PATH = Path(__file__).parent / "prompts" / "skill_merge.md"
_SKILL_MERGE_PROMPT: str = _SKILL_MERGE_PROMPT_PATH.read_text(encoding="utf-8").strip()

_MAX_CLUSTER_SIZE = 5
_MAX_MERGES_PER_CYCLE = 10
_MERGED_BODY_MIN_CHARS = 20
_MAX_DECAY_PER_CYCLE = 20
_SKILL_ARCHIVE_SUBDIR = ".archive"


def _cluster_by_similarity(members: list[MemoryItem], threshold: float) -> list[list[MemoryItem]]:
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

    grouped: dict[int, list[MemoryItem]] = defaultdict(list)
    for idx, member in enumerate(members):
        grouped[find(idx)].append(member)
    return [cluster for cluster in grouped.values() if len(cluster) >= 2]


def _identify_mergeable_clusters(deps: CoDeps) -> list[list[MemoryItem]]:
    """Same-kind, non-immune, non-article clusters above the similarity threshold.

    Articles (kind=article) are external source content — LLM-merging them
    violates RAG integrity. Article redundancy is handled via decay only.
    """
    threshold = deps.config.memory.consolidation_similarity_threshold
    items = load_memory_items(deps.memory_dir)
    if not items:
        return []

    groups: dict[str, list[MemoryItem]] = defaultdict(list)
    for item in items:
        if item.decay_protected:
            continue
        if item.memory_kind == MemoryKindEnum.ARTICLE.value:
            continue
        groups[item.memory_kind].append(item)

    clusters: list[list[MemoryItem]] = []
    for members in groups.values():
        clusters.extend(_cluster_by_similarity(members, threshold))

    clusters = [cluster[:_MAX_CLUSTER_SIZE] for cluster in clusters]
    return clusters[:_MAX_MERGES_PER_CYCLE]


def _select_canonical(cluster: list[MemoryItem]) -> MemoryItem:
    """Recall-aware canonical pick — highest recall_count wins; recency tiebreaker."""
    return max(cluster, key=lambda x: (x.recall_count, x.created_at or ""))


def _render_merge_prompt(cluster: list[MemoryItem], anchor: MemoryItem) -> str:
    """Render the prompt with the anchor first so the LLM treats it as the base."""
    ordered = [anchor] + [m for m in cluster if m.id != anchor.id]
    parts: list[str] = []
    for index, item in enumerate(ordered, start=1):
        label = item.title or "untitled"
        parts.append(
            f"[Entry {index}] kind={item.memory_kind} title={label}\n{item.content.strip()}"
        )
    return "\n\n---\n\n".join(parts)


def _write_consolidated_item(
    deps: CoDeps,
    cluster: list[MemoryItem],
    anchor: MemoryItem,
    merged_body: str,
) -> Path:
    """Write a new consolidated memory item using the anchor's kind/title."""
    result = save_memory_item(
        deps.memory_dir,
        content=merged_body.strip(),
        memory_kind=anchor.memory_kind,
        title=anchor.title or f"consolidated {anchor.memory_kind}",
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


async def _merge_cluster(deps: CoDeps, cluster: list[MemoryItem]) -> Path | None:
    anchor = _select_canonical(cluster)
    prompt = _render_merge_prompt(cluster, anchor)
    merged_body = (await llm_call(deps, prompt, instructions=_MEMORY_MERGE_PROMPT) or "").strip()
    if len(merged_body) < _MERGED_BODY_MIN_CHARS:
        logger.warning(
            "housekeeping.merge: merged body too short (%d chars); skipping cluster",
            len(merged_body),
        )
        return None
    return _write_consolidated_item(deps, cluster, anchor, merged_body)


@trace("co.housekeeping.merge")
async def merge_memory(deps: CoDeps, state: HousekeepingState) -> int:
    """Consolidate similar same-kind memory items; archive originals.

    Returns the count of clusters merged this pass. Articles are excluded;
    they decay or stay (see _identify_mergeable_clusters).
    """
    clusters = _identify_mergeable_clusters(deps)
    if not clusters:
        return 0

    merged_count = 0
    for cluster in clusters:
        try:
            merged = await _merge_cluster(deps, cluster)
        except Exception:
            logger.warning("housekeeping.merge: sub-agent failed on cluster", exc_info=True)
            continue
        if merged is None:
            continue
        try:
            archive_artifacts(cluster, deps.memory_dir, deps.memory_store)
        except Exception:
            logger.warning(
                "housekeeping.merge: archive failed after merge; merged item kept",
                exc_info=True,
            )
        merged_count += 1

    state.stats.memory_merged += merged_count
    return merged_count


@trace("co.housekeeping.decay")
def decay_memory(deps: CoDeps, state: HousekeepingState) -> int:
    """Archive decay-eligible memory items; return the count archived."""
    candidates = find_decay_candidates(deps.memory_dir, deps.config.memory)
    if not candidates:
        return 0
    batch = candidates[:_MAX_DECAY_PER_CYCLE]
    archived = archive_artifacts(batch, deps.memory_dir, deps.memory_store)
    state.stats.memory_decayed += archived
    return archived


@dataclass(frozen=True)
class _SkillCandidate:
    """A user skill considered for housekeeping (merge or decay)."""

    name: str
    body: str
    path: Path


def _load_user_skill_candidates(user_skills_dir: Path) -> list[_SkillCandidate]:
    """Load user-skill bodies (frontmatter stripped) for clustering and decay.

    Bundled skills (under co_cli/skills/) are upstream-managed and excluded.
    """
    if not user_skills_dir.exists():
        return []
    result: list[_SkillCandidate] = []
    for path in sorted(user_skills_dir.glob("*.md")):
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            logger.debug("housekeeping.skill: unreadable %s: %s", path, exc)
            continue
        _, body = parse_frontmatter(text)
        result.append(_SkillCandidate(name=path.stem, body=body.strip(), path=path))
    return result


def _skill_recall_key(deps: CoDeps, name: str) -> tuple[int, int]:
    """Canonical-pick key — (distinct recall days, raw use_count). (0, 0) on missing sidecar."""
    record = read_record(deps, name) or {}
    recall_days = record.get("recall_days") or []
    return (len(recall_days), int(record.get("use_count") or 0))


def _cluster_skills_by_similarity(
    members: list[_SkillCandidate], threshold: float
) -> list[list[_SkillCandidate]]:
    """Union-find clustering of skill candidates by token-Jaccard on body text."""
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
            if token_jaccard(members[i].body, members[j].body) >= threshold:
                union(i, j)

    grouped: dict[int, list[_SkillCandidate]] = defaultdict(list)
    for idx, member in enumerate(members):
        grouped[find(idx)].append(member)
    return [cluster for cluster in grouped.values() if len(cluster) >= 2]


def _select_canonical_skill(deps: CoDeps, cluster: list[_SkillCandidate]) -> _SkillCandidate:
    """Recall-aware canonical pick — highest (recall_days, use_count) wins."""
    return max(cluster, key=lambda s: _skill_recall_key(deps, s.name))


def _render_skill_merge_prompt(
    deps: CoDeps, cluster: list[_SkillCandidate], anchor: _SkillCandidate
) -> str:
    """Render the prompt with the anchor first; LLM treats it as the umbrella base."""
    ordered = [anchor] + [s for s in cluster if s.name != anchor.name]
    parts: list[str] = []
    for index, skill in enumerate(ordered, start=1):
        recall_days, use_count = _skill_recall_key(deps, skill.name)
        parts.append(
            f"[Skill {index}] name={skill.name} recall_days={recall_days} "
            f"use_count={use_count}\n{skill.body}"
        )
    return "\n\n---\n\n".join(parts)


def _archive_user_skill(deps: CoDeps, path: Path) -> bool:
    """Move a user-skill .md to user_skills_dir/.archive/. Returns True on success."""
    archive_dir = deps.user_skills_dir / _SKILL_ARCHIVE_SUBDIR
    archive_dir.mkdir(parents=True, exist_ok=True)
    dest = archive_dir / path.name
    if dest.exists():
        stem = path.stem
        suffix = path.suffix
        for counter in range(1, 1000):
            candidate = archive_dir / f"{stem}-{counter}{suffix}"
            if not candidate.exists():
                dest = candidate
                break
        else:
            logger.warning("housekeeping.skill: too many archive collisions for %s", path.name)
            return False
    try:
        path.rename(dest)
        return True
    except OSError as exc:
        logger.warning("housekeeping.skill: archive rename failed %s: %s", path, exc)
        return False


def _write_consolidated_skill(deps: CoDeps, anchor: _SkillCandidate, merged_body: str) -> Path:
    """Overwrite the anchor's skill file with the merged body (frontmatter preserved)."""
    text = anchor.path.read_text(encoding="utf-8")
    meta_raw, _ = _split_frontmatter_raw(text)
    new_text = f"{meta_raw}{merged_body.strip()}\n" if meta_raw else f"{merged_body.strip()}\n"
    anchor.path.write_text(new_text, encoding="utf-8")
    return anchor.path


def _split_frontmatter_raw(text: str) -> tuple[str, str]:
    """Return (raw_frontmatter_block_with_delimiters_and_trailing_newline, body).

    If no frontmatter, returns ("", text).
    """
    if not text.startswith("---\n"):
        return "", text
    end = text.find("\n---\n", 4)
    if end == -1:
        return "", text
    raw = text[: end + len("\n---\n")]
    body = text[end + len("\n---\n") :]
    return raw, body


async def _merge_skill_cluster(deps: CoDeps, cluster: list[_SkillCandidate]) -> Path | None:
    anchor = _select_canonical_skill(deps, cluster)
    prompt = _render_skill_merge_prompt(deps, cluster, anchor)
    merged_body = (await llm_call(deps, prompt, instructions=_SKILL_MERGE_PROMPT) or "").strip()
    if len(merged_body) < _MERGED_BODY_MIN_CHARS:
        logger.warning(
            "housekeeping.skill_merge: merged body too short (%d chars); skipping cluster",
            len(merged_body),
        )
        return None
    return _write_consolidated_skill(deps, anchor, merged_body)


def _identify_skill_clusters(deps: CoDeps) -> list[list[_SkillCandidate]]:
    """User skills clustered by body similarity, excluding pinned skills."""
    candidates = _load_user_skill_candidates(deps.user_skills_dir)
    if not candidates:
        return []
    eligible = [c for c in candidates if not (read_record(deps, c.name) or {}).get("pinned")]
    if len(eligible) < 2:
        return []
    threshold = deps.config.skills.consolidation_similarity_threshold
    clusters = _cluster_skills_by_similarity(eligible, threshold)
    clusters = [cluster[:_MAX_CLUSTER_SIZE] for cluster in clusters]
    return clusters[:_MAX_MERGES_PER_CYCLE]


@trace("co.housekeeping.skill_merge")
async def merge_skills(deps: CoDeps, state: HousekeepingState) -> int:
    """Consolidate similar user skills into class-level umbrellas; archive originals.

    Returns the count of clusters merged this pass. Pinned skills are excluded;
    bundled skills are never considered (upstream-managed).
    """
    clusters = _identify_skill_clusters(deps)
    if not clusters:
        return 0

    merged_count = 0
    for cluster in clusters:
        anchor = _select_canonical_skill(deps, cluster)
        try:
            merged_path = await _merge_skill_cluster(deps, cluster)
        except Exception:
            logger.warning("housekeeping.skill_merge: LLM merge failed on cluster", exc_info=True)
            continue
        if merged_path is None:
            continue
        for skill in cluster:
            if skill.name == anchor.name:
                continue
            _archive_user_skill(deps, skill.path)
        merged_count += 1

    from co_cli.skills.lifecycle import refresh_skills

    if merged_count:
        try:
            refresh_skills(deps)
        except Exception:
            logger.warning("housekeeping.skill_merge: refresh_skills failed", exc_info=True)

    state.stats.skill_merged += merged_count
    return merged_count


def _parse_sidecar_iso(ts: str) -> datetime:
    """Parse an ISO 8601 timestamp string (with optional trailing Z) into a UTC datetime."""
    return datetime.fromisoformat(ts.rstrip("Z")).replace(tzinfo=UTC)


def _parse_sidecar_iso_date(ds: str) -> date:
    """Parse a YYYY-MM-DD recall_days entry into a date."""
    return date.fromisoformat(ds)


def _find_decay_candidate_skills(deps: CoDeps) -> list[_SkillCandidate]:
    """User skills past decay_after_days from sidecar created_at AND no recent recall."""
    candidates = _load_user_skill_candidates(deps.user_skills_dir)
    if not candidates:
        return []
    decay_after = deps.config.skills.decay_after_days
    recall_window = deps.config.skills.recall_protection_days
    now = datetime.now(UTC)
    today = now.date()
    eligible: list[_SkillCandidate] = []

    for candidate in candidates:
        record = read_record(deps, candidate.name)
        if record is None:
            continue
        if record.get("pinned"):
            continue
        created_at = record.get("created_at")
        if not created_at:
            continue
        try:
            age_days = (now - _parse_sidecar_iso(created_at)).days
        except ValueError:
            continue
        if age_days < decay_after:
            continue
        recall_days = record.get("recall_days") or []
        if recall_days:
            try:
                last_recall = _parse_sidecar_iso_date(recall_days[-1])
            except ValueError:
                last_recall = None
            if last_recall is not None and (today - last_recall).days < recall_window:
                continue
        eligible.append(candidate)
    return eligible


@trace("co.housekeeping.skill_decay")
def decay_skills(deps: CoDeps, state: HousekeepingState) -> int:
    """Archive aged user skills with no recent recall; return the count archived."""
    candidates = _find_decay_candidate_skills(deps)
    if not candidates:
        return 0
    batch = candidates[:_MAX_DECAY_PER_CYCLE]
    archived = 0
    for skill in batch:
        if _archive_user_skill(deps, skill.path):
            archived += 1

    if archived:
        from co_cli.skills.lifecycle import refresh_skills

        try:
            refresh_skills(deps)
        except Exception:
            logger.warning("housekeeping.skill_decay: refresh_skills failed", exc_info=True)

    state.stats.skill_decayed += archived
    return archived


@trace("co.housekeeping.pass")
async def run_housekeeping(
    deps: CoDeps, cfg: DreamSettings, state: HousekeepingState
) -> HousekeepingState:
    """Run one full housekeeping pass — memory + skill merge then decay.

    ``cfg.max_pass_seconds`` bounds the merge phase only (LLM-driven, async).
    Decay is synchronous, bounded by ``_MAX_DECAY_PER_CYCLE`` filesystem moves,
    and always runs — wrapping it in the timeout would let a slow merge starve
    decay of its chance to archive aged candidates. On merge timeout, partial
    merge counters are persisted, decay still runs, and ``last_housekeeping_at``
    is set to now so the next tick fires on schedule.
    """
    from co_cli.config.core import DREAM_DAEMON_DIR

    try:
        async with asyncio.timeout(cfg.max_pass_seconds):
            await merge_memory(deps, state)
            await merge_skills(deps, state)
    except TimeoutError:
        logger.warning(
            "housekeeping.merge: wall-clock cap (%ss) exceeded; partial counters persisted",
            cfg.max_pass_seconds,
        )

    decay_memory(deps, state)
    decay_skills(deps, state)

    state.last_housekeeping_at = datetime.now(UTC).isoformat()
    save_housekeeping_state(DREAM_DAEMON_DIR, state)
    return state
