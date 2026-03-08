"""Memory consolidator — fact extraction and contradiction resolution.

Provides LLM-driven consolidation planning for write-time memory lifecycle.
Uses two-phase mini-agent approach: extract facts, then resolve against existing.
"""

import asyncio
import logging
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ValidationError

logger = logging.getLogger(__name__)

_PROMPT_PATH = Path(__file__).parent / "prompts" / "agents" / "memory_consolidator.md"


class MemoryAction(BaseModel):
    """A single action in a consolidation plan."""

    action: Literal["ADD", "UPDATE", "DELETE", "NONE"]
    target_alias: str | None = None


class ConsolidationPlan(BaseModel):
    """Full consolidation plan produced by the resolver."""

    actions: list[MemoryAction]


class _FactList(BaseModel):
    """Structured output wrapper for fact extraction."""

    facts: list[str]


def _load_prompt_section(path: Path, section_header: str) -> str:
    """Read a named section from a markdown file.

    Returns content between `section_header` and the next `##` header (or EOF).

    Args:
        path: Path to the markdown file.
        section_header: Exact header line to find (e.g. "## Phase 1: Fact Extraction").

    Returns:
        Section body text, stripped. Empty string if header not found.
    """
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    in_section = False
    body_lines: list[str] = []

    for line in lines:
        if line.strip() == section_header.strip():
            in_section = True
            continue
        if in_section:
            if line.startswith("## ") and line.strip() != section_header.strip():
                break
            body_lines.append(line)

    return "\n".join(body_lines).strip()


async def extract_facts(
    candidate: str,
    model: Any,
    timeout_seconds: float | None = None,
) -> list[str]:
    """Extract normalized facts from a candidate memory string.

    Calls an LLM mini-agent (Phase 1 prompt) to decompose the candidate into
    discrete facts. Falls back to single-fact passthrough on timeout or error.

    Args:
        candidate: Raw memory content to extract facts from.
        model: LLM model instance (reuses main agent model).
        timeout_seconds: Per-call timeout budget. 0 = immediate timeout (test mode).
                         None = no timeout.

    Returns:
        List of normalized fact strings. Falls back to [candidate] on failure.
    """
    from pydantic_ai import Agent

    try:
        system_prompt = _load_prompt_section(_PROMPT_PATH, "## Phase 1: Fact Extraction")

        fact_agent: Agent[None, _FactList] = Agent(
            model=model,
            output_type=_FactList,
            system_prompt=system_prompt,
        )

        coro = fact_agent.run(candidate)
        if timeout_seconds is not None:
            result = await asyncio.wait_for(coro, timeout=timeout_seconds)
        else:
            result = await coro

        facts = result.output.facts
        return facts if facts else [candidate]

    except asyncio.TimeoutError:
        raise
    except (ValidationError, Exception) as e:
        logger.debug(f"extract_facts fallback (validation/error): {e}")
        return [candidate]


async def resolve(
    facts: list[str],
    existing: list[Any],
    model: Any,
    timeout_seconds: float | None = None,
) -> ConsolidationPlan:
    """Resolve facts against existing memories to produce an action plan.

    Calls an LLM mini-agent (Phase 2 prompt) to determine ADD/UPDATE/DELETE/NONE
    for each candidate fact. Falls back to ADD plan on timeout or validation error.

    Args:
        facts: Extracted fact strings from the candidate.
        existing: Existing MemoryEntry objects to compare against (aliased as M1, M2...).
        model: LLM model instance (reuses main agent model).
        timeout_seconds: Per-call timeout budget. 0 = immediate timeout (test mode).
                         None = no timeout.

    Returns:
        ConsolidationPlan with action decisions. Falls back to ADD plan on failure.
    """
    from pydantic_ai import Agent

    _add_fallback = ConsolidationPlan(actions=[MemoryAction(action="ADD")])

    try:
        system_prompt = _load_prompt_section(_PROMPT_PATH, "## Phase 2: Contradiction Resolution")

        # Build alias map for the prompt
        alias_entries = []
        for i, entry in enumerate(existing):
            alias = f"M{i + 1}"
            alias_entries.append(
                f'{{"alias": "{alias}", "content": {entry.content!r}, "tags": {entry.tags!r}}}'
            )

        aliases_text = "\n".join(alias_entries) if alias_entries else "(none)"
        facts_text = "\n".join(f"- {f}" for f in facts)

        user_prompt = (
            f"Candidate facts:\n{facts_text}\n\n"
            f"Existing memories:\n{aliases_text}"
        )

        resolve_agent: Agent[None, ConsolidationPlan] = Agent(
            model=model,
            output_type=ConsolidationPlan,
            system_prompt=system_prompt,
        )

        coro = resolve_agent.run(user_prompt)
        if timeout_seconds is not None:
            result = await asyncio.wait_for(coro, timeout=timeout_seconds)
        else:
            result = await coro

        plan = result.output
        action_counts: dict[str, int] = {}
        for a in plan.actions:
            action_counts[a.action] = action_counts.get(a.action, 0) + 1
        logger.info(
            "consolidation resolved",
            extra={
                "facts_count": len(facts),
                "candidate_set_size": len(existing),
                "actions": action_counts,
                "fallback": False,
            },
        )
        return plan

    except asyncio.TimeoutError:
        raise
    except (ValidationError, Exception) as e:
        logger.info(
            "consolidation fallback",
            extra={
                "facts_count": len(facts),
                "candidate_set_size": len(existing),
                "actions": {"ADD": 1},
                "fallback": True,
            },
        )
        logger.debug(f"resolve fallback reason: {e}")
        return _add_fallback


def build_alias_map(existing: list[Any]) -> dict[str, Any]:
    """Build alias → MemoryEntry mapping for use in apply_plan_atomically.

    Args:
        existing: List of MemoryEntry objects in the same order as passed to resolve().

    Returns:
        Dict mapping alias strings ("M1", "M2"...) to MemoryEntry objects.
    """
    return {f"M{i + 1}": entry for i, entry in enumerate(existing)}
