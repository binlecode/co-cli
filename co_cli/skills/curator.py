"""Curator — pure state transitions, archive/restore, and the consolidation agent.

CURATOR_SPEC declares the agent's tool surface, output schema, and budget;
run_curator wraps it with daemon orchestration (state transitions, fork,
report, state update).
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

from co_cli.agent.spec import TaskAgentSpec
from co_cli.config.skills import (
    CURATOR_ARCHIVE_AFTER_DAYS,
    CURATOR_MAX_ITERATIONS,
    CURATOR_STALE_AFTER_DAYS,
    SkillsSettings,
)
from co_cli.fileio.atomic import atomic_write_text

if TYPE_CHECKING:
    from co_cli.deps import CoDeps

logger = logging.getLogger(__name__)

_RUN_ID_SUFFIX_LEN = 8

CURATOR_STATE_FILENAME = ".curator_state.json"
CURATOR_STATE_VERSION = 1


@dataclass(frozen=True)
class StateTransition:
    """A single state transition applied to one skill."""

    name: str
    from_state: str
    to_state: str


def _parse_iso(ts: str) -> datetime:
    """Parse an ISO 8601 UTC timestamp string to an aware datetime."""
    return datetime.fromisoformat(ts.rstrip("Z")).replace(tzinfo=UTC)


def _days_since(dt: datetime, now: datetime) -> float:
    """Return elapsed days between dt and now."""
    return (now - dt).total_seconds() / 86400.0


def apply_state_transition_one(
    name: str,
    record: dict[str, Any],
    settings: SkillsSettings,
    now: datetime,
) -> StateTransition | None:
    """Pure function: compute the state transition for a single skill record.

    Returns a StateTransition or None. Does not mutate the record, does not
    touch disk.

    State machine:
      active  → stale    if last_used_at > CURATOR_STALE_AFTER_DAYS ago
      stale   → archived if last activity > CURATOR_ARCHIVE_AFTER_DAYS ago
      stale   → active   if recently used (within CURATOR_STALE_AFTER_DAYS)

    Pinned skills and already-archived skills are skipped. If last_used_at
    is None, created_at is used as the proxy. If both are None, the skill
    is skipped.
    """
    del settings
    state = record["state"]
    pinned = record["pinned"]

    if pinned:
        return None
    if state == "archived":
        return None

    last_used_at_str = record["last_used_at"]
    created_at_str = record["created_at"]

    if last_used_at_str is not None:
        reference_dt = _parse_iso(last_used_at_str)
    elif created_at_str is not None:
        reference_dt = _parse_iso(created_at_str)
    else:
        return None

    days_idle = _days_since(reference_dt, now)

    if state == "active":
        if days_idle > CURATOR_STALE_AFTER_DAYS:
            return StateTransition(name=name, from_state="active", to_state="stale")
    elif state == "stale":
        if days_idle > CURATOR_ARCHIVE_AFTER_DAYS:
            return StateTransition(name=name, from_state="stale", to_state="archived")
        if days_idle <= CURATOR_STALE_AFTER_DAYS:
            return StateTransition(name=name, from_state="stale", to_state="active")

    return None


def compute_pending_transitions(
    deps: CoDeps,
    settings: SkillsSettings,
    now: datetime,
) -> list[StateTransition]:
    """Iterate per-skill sidecars and collect state transitions.

    Convenience wrapper used by curator phase 1 and /skills curator status.
    """
    from co_cli.skills.usage import iter_records

    transitions: list[StateTransition] = []
    for name, record in iter_records(deps):
        t = apply_state_transition_one(name, record, settings, now)
        if t is not None:
            transitions.append(t)
    return transitions


def archive_skill(deps: CoDeps, name: str) -> None:
    """Move <user_skills_dir>/<name>.md to <user_skills_dir>/.archive/<name>.md.

    Creates .archive/ if missing. Calls refresh_skills after move.
    Idempotent: if source is missing but archive exists, no error.
    Raises FileNotFoundError if neither source nor archive exists.
    """
    from co_cli.skills.lifecycle import refresh_skills

    source = deps.user_skills_dir / f"{name}.md"
    archive_dir = deps.user_skills_dir / ".archive"
    dest = archive_dir / f"{name}.md"

    if not source.exists():
        if dest.exists():
            return
        raise FileNotFoundError(f"Skill '{name}' not found in user skills or archive")

    archive_dir.mkdir(parents=True, exist_ok=True)
    source.rename(dest)
    refresh_skills(deps)


def restore_skill(deps: CoDeps, name: str) -> None:
    """Move <user_skills_dir>/.archive/<name>.md back to <user_skills_dir>/<name>.md.

    Calls refresh_skills after move.
    Raises FileNotFoundError if the skill is not in the archive.
    """
    from co_cli.skills.lifecycle import refresh_skills

    archive_dir = deps.user_skills_dir / ".archive"
    source = archive_dir / f"{name}.md"
    dest = deps.user_skills_dir / f"{name}.md"

    if not source.exists():
        raise FileNotFoundError(f"Skill '{name}' is not in the archive")

    dest.parent.mkdir(parents=True, exist_ok=True)
    source.rename(dest)
    refresh_skills(deps)


def _curator_state_path(deps: CoDeps) -> Path:
    return deps.user_skills_dir / CURATOR_STATE_FILENAME


def read_curator_state(deps: CoDeps) -> dict[str, Any]:
    """Read the curator state file. Returns defaults on missing or error."""
    path = _curator_state_path(deps)
    if not path.exists():
        return {"version": CURATOR_STATE_VERSION, "run_count": 0, "paused": False}
    try:
        raw = path.read_text(encoding="utf-8")
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError("not a dict")
        return data
    except (OSError, json.JSONDecodeError, ValueError):
        return {"version": CURATOR_STATE_VERSION, "run_count": 0, "paused": False}


def write_curator_state(deps: CoDeps, state: dict[str, Any]) -> None:
    """Atomically write the curator state file."""
    path = _curator_state_path(deps)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, json.dumps(state, indent=2))


# --- Curator agent ---


class CuratorOutput(BaseModel):
    """Structured output from the curator agent."""

    summary: str = ""
    skills_merged: list[str] = []
    skills_created: list[str] = []
    skills_updated: list[str] = []


def _curator_instructions(_deps: CoDeps) -> str:
    from co_cli.skills.curator_prompts import CURATOR_INSTRUCTIONS

    return CURATOR_INSTRUCTIONS


CURATOR_SPEC = TaskAgentSpec(
    name="skill_curator",
    instructions=_curator_instructions,
    tool_names=("skill_view", "skill_manage"),
    output_type=CuratorOutput,
    default_budget=CURATOR_MAX_ITERATIONS,
    error_message="",
)


def _summarize_skill_inventory(deps: CoDeps) -> str:
    """Build a text inventory of agent-created, non-archived, non-pinned skills."""
    from co_cli.skills.usage import is_agent_created, iter_records

    lines: list[str] = []
    for name, record in iter_records(deps):
        if record["state"] == "archived":
            continue
        if record["pinned"]:
            continue
        if not is_agent_created(name, deps):
            continue
        skill_path = deps.user_skills_dir / f"{name}.md"
        if not skill_path.exists():
            continue
        body = skill_path.read_text(encoding="utf-8")[:1000]
        lines.append(
            f"## {name}\n"
            f"state={record['state']}  "
            f"use_count={record['use_count']}  "
            f"last_used_at={record['last_used_at'] or 'never'}\n\n"
            f"{body}"
        )
    if not lines:
        return "(no agent-created skills eligible for curation)"
    return "\n\n---\n\n".join(lines)


def _make_run_dir(run_id: str) -> Path:
    """Return a Path for the per-run curator report directory (created)."""
    from co_cli.config.core import CURATOR_RUNS_DIR

    timestamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    suffix = run_id[-_RUN_ID_SUFFIX_LEN:] if run_id else "unknown"
    run_dir = CURATOR_RUNS_DIR / f"{timestamp}-{suffix}"
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir


def _write_curator_report(run_id: str, output: CuratorOutput, usage: object) -> None:
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

    atomic_write_text(run_dir / "run.json", json.dumps(report, indent=2))

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

    atomic_write_text(run_dir / "run.md", "\n".join(md_lines))


async def run_curator(deps: CoDeps) -> None:
    """Run the curator unconditionally.

    Caller is responsible for the time-gate / curator_enabled check.

    Phase 1: apply state transitions (stale/archive).
    Phase 2: fork consolidation agent.
    Phase 3: write report + update curator state.

    Errors are logged and swallowed — never propagate to the caller.
    """
    await asyncio.sleep(0)

    from co_cli.skills.usage import iter_records, write_record

    try:
        now = datetime.now(UTC)
        curator_state = read_curator_state(deps)
        seen_last_run_at = curator_state.get("last_run_at")

        # Phase 1 — state transitions
        transitions: list[StateTransition] = []
        for name, record in iter_records(deps):
            t = apply_state_transition_one(name, record, deps.config.skills, now)
            if t is None:
                continue
            transitions.append(t)
            record["state"] = t.to_state
            write_record(deps, name, record)

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
        phase2_usage: object = None
        phase2_run_id = "unknown"
        try:
            from co_cli.agent.run import run_standalone
            from co_cli.config.skills import CURATOR_TIMEOUT_SECONDS
            from co_cli.deps import fork_deps_for_curator
            from co_cli.skills.curator_prompts import CURATOR_PROMPT

            child_deps = fork_deps_for_curator(deps)
            inventory = _summarize_skill_inventory(deps)
            prompt = CURATOR_PROMPT.format(inventory=inventory)

            phase2_output, phase2_usage, phase2_run_id = await asyncio.wait_for(
                run_standalone(
                    CURATOR_SPEC,
                    child_deps,
                    prompt,
                    budget=CURATOR_MAX_ITERATIONS,
                    model_settings=deps.model.settings,
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
