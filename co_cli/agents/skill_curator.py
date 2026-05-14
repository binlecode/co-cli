"""Skill curator agent — state-machine transitions + consolidation."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

from co_cli.memory.mutator import atomic_write_text

if TYPE_CHECKING:
    from co_cli.deps import CoDeps

logger = logging.getLogger(__name__)

_RUN_ID_SUFFIX_LEN = 8


class CuratorOutput(BaseModel):
    """Structured output from the curator agent."""

    summary: str = ""
    skills_merged: list[str] = []
    skills_created: list[str] = []
    skills_updated: list[str] = []


def _summarize_skill_inventory(deps: CoDeps) -> str:
    """Build a text inventory of agent-created, non-archived, non-pinned skills."""
    from co_cli.skills.usage import is_agent_created, read_records

    records = read_records(deps).get("skills", {})
    lines: list[str] = []
    for name, record in records.items():
        if record.get("state") == "archived":
            continue
        if record.get("pinned"):
            continue
        if not is_agent_created(name, deps):
            continue
        skill_path = deps.user_skills_dir / f"{name}.md"
        if not skill_path.exists():
            continue
        body = skill_path.read_text(encoding="utf-8")[:1000]
        lines.append(
            f"## {name}\n"
            f"state={record.get('state', 'active')}  "
            f"use_count={record.get('use_count', 0)}  "
            f"last_used_at={record.get('last_used_at') or 'never'}\n\n"
            f"{body}"
        )
    if not lines:
        return "(no agent-created skills eligible for curation)"
    return "\n\n---\n\n".join(lines)


def _make_run_dir(run_id: str) -> Any:
    """Return a Path for the per-run curator report directory (created)."""
    from co_cli.config.core import CURATOR_RUNS_DIR

    timestamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    suffix = run_id[-_RUN_ID_SUFFIX_LEN:] if run_id else "unknown"
    run_dir = CURATOR_RUNS_DIR / f"{timestamp}-{suffix}"
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir


def _write_curator_report(run_id: str, output: CuratorOutput, usage: Any) -> None:
    """Write run.json + run.md to ~/.co-cli/curator-runs/<timestamp>/."""
    from pydantic_ai.usage import RunUsage

    run_dir = _make_run_dir(run_id)

    report: dict = {
        "run_id": run_id,
        "summary": output.summary,
        "skills_merged": output.skills_merged,
        "skills_created": output.skills_created,
        "skills_updated": output.skills_updated,
    }
    if isinstance(usage, RunUsage):
        report["usage"] = {
            "requests": usage.requests,
            "input_tokens": usage.input_tokens,
            "output_tokens": usage.output_tokens,
        }

    atomic_write_text(run_dir / "run.json", json.dumps(report, indent=2))  # type: ignore[union-attr]

    md_lines = [
        "# Curator Run Report",
        "",
        f"**run_id:** {run_id}",
        f"**summary:** {output.summary or '(no changes)'}",
        "",
    ]
    if output.skills_merged:
        md_lines += ["**skills_merged:**"] + [f"- {s}" for s in output.skills_merged] + [""]
    if output.skills_created:
        md_lines += ["**skills_created:**"] + [f"- {s}" for s in output.skills_created] + [""]
    if output.skills_updated:
        md_lines += ["**skills_updated:**"] + [f"- {s}" for s in output.skills_updated] + [""]

    atomic_write_text(run_dir / "run.md", "\n".join(md_lines))  # type: ignore[union-attr]


async def maybe_run_curator(deps: CoDeps, *, bypass_time_gate: bool = False) -> None:
    """Run the curator if gating conditions are met.

    Phase 1: apply state transitions (stale/archive).
    Phase 2: fork consolidation agent.
    Phase 3: write reports + update curator state.

    Errors are logged and swallowed — never propagate to the caller.
    """
    await asyncio.sleep(0)

    from co_cli.skills.curator import (
        _idle_seconds,
        apply_state_transitions,
        archive_skill,
        read_curator_state,
        should_run_now,
        write_curator_state,
    )
    from co_cli.skills.usage import read_records, write_records

    try:
        now = datetime.now(UTC)
        idle_secs = _idle_seconds(deps.session, now)
        curator_state = read_curator_state(deps)

        if not should_run_now(
            curator_state,
            deps.config.skills,
            now,
            idle_secs,
            bypass_time_gate=bypass_time_gate,
        ):
            return

        # Optimistic concurrency: record the last_run_at we saw before Phase 1.
        seen_last_run_at = curator_state.get("last_run_at")

        # Phase 1 — state transitions
        sidecar_data = read_records(deps)
        transitions = apply_state_transitions(sidecar_data, deps.config.skills, now)
        for t in transitions:
            sidecar_data.setdefault("skills", {}).setdefault(t.name, {})["state"] = t.to_state
        write_records(deps, sidecar_data)

        for t in transitions:
            if t.to_state == "archived":
                try:
                    archive_skill(deps, t.name)
                except Exception as exc:
                    logger.warning("archive_skill(%s) failed: %s", t.name, exc)

        # Optimistic concurrency check — abort if another curator ran concurrently.
        current_state = read_curator_state(deps)
        if current_state.get("last_run_at") != seen_last_run_at:
            logger.info("Curator: concurrent run detected — aborting this run")
            return

        # Phase 2 — consolidation agent
        phase2_output: CuratorOutput | None = None
        phase2_usage = None
        phase2_run_id = "unknown"
        try:
            from co_cli.agents._runner import _run_agent_standalone
            from co_cli.agents.core import build_agent, discover_delegation_tools
            from co_cli.config.skills import CURATOR_MAX_ITERATIONS, CURATOR_TIMEOUT_SECONDS
            from co_cli.deps import fork_deps_for_curator
            from co_cli.skills.curator_prompts import CURATOR_INSTRUCTIONS, CURATOR_PROMPT

            child_deps = fork_deps_for_curator(deps)
            agent = build_agent(
                config=deps.config,
                model=deps.model.model,
                instructions=CURATOR_INSTRUCTIONS,
                tool_fns=discover_delegation_tools("skill_curator", deps.config),
                output_type=CuratorOutput,
            )
            inventory = _summarize_skill_inventory(deps)
            prompt = CURATOR_PROMPT.format(inventory=inventory)

            phase2_output, phase2_usage, phase2_run_id = await asyncio.wait_for(
                _run_agent_standalone(
                    agent=agent,
                    prompt=prompt,
                    deps=child_deps,
                    budget=CURATOR_MAX_ITERATIONS,
                    model_settings=deps.model.settings,
                    role="skill_curator",
                ),
                timeout=CURATOR_TIMEOUT_SECONDS,
            )
        except TimeoutError:
            logger.warning("Curator agent timed out")
            phase2_output = CuratorOutput(summary="(timed out)")
        except Exception as exc:
            logger.warning("Curator agent failed: %s", exc, exc_info=True)
            phase2_output = CuratorOutput(summary=f"(error: {exc!s:.80})")

        # Phase 3 — write report + update state
        final_output = phase2_output or CuratorOutput()
        try:
            _write_curator_report(phase2_run_id, final_output, phase2_usage)
        except Exception as exc:
            logger.warning("Curator report write failed: %s", exc)

        run_count = int(current_state.get("run_count", 0)) + 1
        updated_state = {
            "version": 1,
            "last_run_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "last_run_summary": final_output.summary,
            "run_count": run_count,
            "paused": current_state.get("paused", False),
        }
        write_curator_state(deps, updated_state)

        cb = deps.runtime.background_status_callback
        if final_output.summary and cb is not None:
            cb(f"Curator: {final_output.summary}")

    except Exception:
        logger.warning("Curator run failed", exc_info=True)
