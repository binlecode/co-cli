"""Session-end combined skill+knowledge review agent."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from pydantic import BaseModel
from pydantic_ai.messages import ModelMessage

from co_cli.memory.mutator import atomic_write_text

if TYPE_CHECKING:
    from co_cli.deps import CoDeps

logger = logging.getLogger(__name__)

_RUN_ID_SUFFIX_LEN = 8


class SessionReviewOutput(BaseModel):
    """Structured output from the session review agent."""

    summary: str = ""
    skills_patched: list[str] = []
    skills_created: list[str] = []
    knowledge_created: list[str] = []
    knowledge_updated: list[str] = []


@dataclass(frozen=True)
class SessionReviewResult:
    """Minimal result returned to the caller."""

    summary: str
    run_id: str


def _make_run_dir(deps: CoDeps, run_id: str) -> object:
    """Return a Path for the per-run report directory (created)."""
    from co_cli.config.core import SESSION_REVIEWS_DIR

    base = SESSION_REVIEWS_DIR
    timestamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    suffix = run_id[-_RUN_ID_SUFFIX_LEN:] if run_id else "unknown"
    run_dir = base / f"{timestamp}-{suffix}"
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir


def _write_review_report(
    deps: CoDeps,
    run_id: str,
    output: SessionReviewOutput,
    usage: object,
    transcript_length: int = 0,
) -> None:
    """Write run.json + run.md to ~/.co-cli/session-reviews/<timestamp>/."""
    from pydantic_ai.usage import RunUsage

    run_dir = _make_run_dir(deps, run_id)

    report: dict = {
        "run_id": run_id,
        "summary": output.summary,
        "skills_patched": output.skills_patched,
        "skills_created": output.skills_created,
        "knowledge_created": output.knowledge_created,
        "knowledge_updated": output.knowledge_updated,
        "transcript_length": transcript_length,
    }
    if isinstance(usage, RunUsage):
        report["usage"] = {
            "requests": usage.requests,
            "input_tokens": usage.input_tokens,
            "output_tokens": usage.output_tokens,
        }

    atomic_write_text(run_dir / "run.json", json.dumps(report, indent=2))  # type: ignore[union-attr]

    md_lines = [
        "# Session Review Report",
        "",
        f"**run_id:** {run_id}",
        f"**summary:** {output.summary or '(no changes)'}",
        "",
    ]
    if output.skills_patched:
        md_lines += ["**skills_patched:**"] + [f"- {s}" for s in output.skills_patched] + [""]
    if output.skills_created:
        md_lines += ["**skills_created:**"] + [f"- {s}" for s in output.skills_created] + [""]
    if output.knowledge_created:
        md_lines += (
            ["**knowledge_created:**"] + [f"- {s}" for s in output.knowledge_created] + [""]
        )
    if output.knowledge_updated:
        md_lines += (
            ["**knowledge_updated:**"] + [f"- {s}" for s in output.knowledge_updated] + [""]
        )

    atomic_write_text(run_dir / "run.md", "\n".join(md_lines))  # type: ignore[union-attr]


async def run_session_review(
    deps: CoDeps, message_history: list[ModelMessage]
) -> SessionReviewResult:
    """Fork a session_reviewer agent and run the combined skill+knowledge review."""
    from co_cli.agents._runner import _run_agent_standalone
    from co_cli.agents.core import build_agent, discover_delegation_tools
    from co_cli.config.skills import REVIEW_MAX_ITERATIONS
    from co_cli.context.summarization import serialize_messages
    from co_cli.deps import fork_deps_for_reviewer
    from co_cli.skills.curator_prompts import SESSION_REVIEW_INSTRUCTIONS, SESSION_REVIEW_PROMPT

    child_deps = fork_deps_for_reviewer(deps)
    agent = build_agent(
        config=deps.config,
        model=deps.model.model,
        instructions=SESSION_REVIEW_INSTRUCTIONS,
        tool_fns=discover_delegation_tools("session_reviewer", deps.config),
        output_type=SessionReviewOutput,
    )
    transcript = serialize_messages(
        message_history,
        deps.config.observability.redact_patterns,
        include_tool_results=False,
    )
    prompt = SESSION_REVIEW_PROMPT.format(transcript=transcript)
    output, usage, run_id = await _run_agent_standalone(
        agent=agent,
        prompt=prompt,
        deps=child_deps,
        budget=REVIEW_MAX_ITERATIONS,
        model_settings=deps.model.settings,
        role="session_review",
    )
    _write_review_report(deps, run_id, output, usage, transcript_length=len(transcript))
    return SessionReviewResult(summary=output.summary, run_id=run_id)
