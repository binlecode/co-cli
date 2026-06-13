"""Dream daemon domain reviewers — memory and skill review agents.

MEMORY_REVIEW_SPEC and SKILL_REVIEW_SPEC declare the tool surfaces and
prompts for the two domain reviewers. process_review loads the transcript
and dispatches to the appropriate reviewer.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import BaseModel

from co_cli.agent.spec import TaskAgentSpec
from co_cli.config.skills import REVIEW_MAX_ITERATIONS
from co_cli.session.persistence import load_transcript


class SessionReviewOutput(BaseModel):
    """Structured output from a domain review agent."""

    summary: str = ""
    skills_patched: list[str] = []
    skills_created: list[str] = []
    knowledge_created: list[str] = []
    knowledge_updated: list[str] = []


if TYPE_CHECKING:
    from pydantic_ai.messages import ModelMessage

    from co_cli.deps import CoDeps

logger = logging.getLogger(__name__)

_PROMPTS_DIR = Path(__file__).parent / "prompts"


def _with_curation_lens(base: str, deps: CoDeps) -> str:
    """Append the active soul's curation lens to a review prompt.

    The lens scopes the character's retention judgment (what counts as durable
    signal, how aggressively to merge) into curation — without importing voice
    or the full personality prompt. Gated on deps.config.personality, mirroring
    the orchestrator's critique gate; degrades to the bare base prompt when
    personality is disabled or the role ships no curation.md.
    """
    role = deps.config.personality
    if not role:
        return base
    from co_cli.personality.prompts.loader import load_soul_curation

    lens = load_soul_curation(role)
    if not lens:
        return base
    return f"{base}\n\n{lens}"


def _memory_review_instructions(deps: CoDeps) -> str:
    base = (_PROMPTS_DIR / "memory_review.md").read_text(encoding="utf-8")
    return _with_curation_lens(base, deps)


def _skill_review_instructions(deps: CoDeps) -> str:
    base = (_PROMPTS_DIR / "skill_review.md").read_text(encoding="utf-8")
    return _with_curation_lens(base, deps)


MEMORY_REVIEW_SPEC = TaskAgentSpec(
    name="memory_reviewer",
    instructions=_memory_review_instructions,
    tool_names=(
        "memory_search",
        "memory_create",
        "memory_append",
        "memory_replace",
    ),
    output_type=SessionReviewOutput,
    default_budget=REVIEW_MAX_ITERATIONS,
    error_message="",
    include_skill_manifest=False,
)

SKILL_REVIEW_SPEC = TaskAgentSpec(
    name="skill_reviewer",
    instructions=_skill_review_instructions,
    tool_names=(
        "skill_view",
        "skill_create",
        "skill_edit",
        "skill_patch",
        "memory_search",
    ),
    output_type=SessionReviewOutput,
    default_budget=REVIEW_MAX_ITERATIONS,
    error_message="",
    include_skill_manifest=True,
)

_REVIEW_PROMPT_TEMPLATE: str = """\
Review the session transcript below.

{transcript}\
"""


async def _run_memory_review(deps: CoDeps, messages: list[ModelMessage]) -> None:
    """Run the memory review agent on the given transcript messages."""
    from co_cli.agent.run import run_standalone
    from co_cli.context.summarization import serialize_messages
    from co_cli.deps import fork_deps_for_reviewer

    transcript = serialize_messages(
        messages,
        deps.config.observability.redact_patterns,
        include_tool_results=False,
    )
    prompt = _REVIEW_PROMPT_TEMPLATE.format(transcript=transcript)
    child_deps = fork_deps_for_reviewer(deps)
    await run_standalone(MEMORY_REVIEW_SPEC, child_deps, prompt)


async def _run_skill_review(deps: CoDeps, messages: list[ModelMessage]) -> None:
    """Run the skill review agent on the given transcript messages."""
    from co_cli.agent.run import run_standalone
    from co_cli.context.summarization import serialize_messages
    from co_cli.deps import fork_deps_for_reviewer
    from co_cli.skills.lifecycle import refresh_skills

    transcript = serialize_messages(
        messages,
        deps.config.observability.redact_patterns,
        include_tool_results=False,
    )
    prompt = _REVIEW_PROMPT_TEMPLATE.format(transcript=transcript)
    child_deps = fork_deps_for_reviewer(deps)
    # Reload skills from disk so the reviewer sees up-to-date skill state.
    refresh_skills(child_deps)
    await run_standalone(SKILL_REVIEW_SPEC, child_deps, prompt)


async def process_review(
    deps: CoDeps,
    domain: str,
    session_id: str,
    persisted_message_count: int,
) -> None:
    """Load transcript and dispatch to the appropriate domain reviewer.

    Raises ValueError on unknown domain — corruption in the queue payload that
    should land the kick in failed/, not done/. Missing transcript is treated
    as a benign no-op (graceful degrade for the race where REPL deletes the
    session before the daemon processes its kick).
    """
    transcript_path = deps.sessions_dir / f"{session_id}.jsonl"
    if not transcript_path.exists():
        logger.warning("review: session file missing %s", session_id)
        return
    messages = load_transcript(transcript_path, max_message_count=persisted_message_count)
    if domain == "memory":
        await _run_memory_review(deps, messages)
    elif domain == "skill":
        await _run_skill_review(deps, messages)
    else:
        raise ValueError(f"unknown review domain: {domain!r}")
