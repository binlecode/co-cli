"""Longitudinal fixture loader for phase-2 behavioral evals.

Each fixture is a directory under ``evals/_fixtures/<name>/`` mirroring a
``~/.co-cli/`` subtree:

- ``<name>/knowledge/*.md`` — pre-seeded knowledge artifacts (canonical
  frontmatter + body). Committed to the repo; the loader copies them into
  ``deps.memory_dir`` and re-stamps mtimes so a fixture loaded on any
  date looks "fresh" to the FTS / dream cycle.
- Session JSONLs — NOT committed. Built at load-time via
  ``_build_session_jsonl`` from ``_SESSION_SPECS[name]`` so the pydantic-ai
  ``ModelMessage`` schema stays current as the library evolves.

After load, ``deps.memory_store.sync_dir(deps.memory_dir)``
runs so FTS sees the new content; eval cases can then run ``memory_search``
against the seeded artifacts directly.

Reruns are idempotent: bytes-equivalent files overwrite in place. Session
JSONLs use a fixed naming scheme per fixture so reruns don't accumulate.

Usage:
    handle = load_fixture("user_model_baseline", deps)
    assert handle.knowledge_paths  # 3 preference artifacts in deps.memory_dir
    assert handle.session_paths    # 2 session JSONLs in deps.sessions_dir
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextPart,
    UserPromptPart,
)

from co_cli.session.persistence import append_messages

if TYPE_CHECKING:
    from co_cli.deps import CoDeps

_FIXTURES_DIR = Path(__file__).parent / "_fixtures"


@dataclass(frozen=True)
class FixtureHandle:
    """Result of ``load_fixture`` — paths seeded into ``~/.co-cli/`` for assertions."""

    name: str
    knowledge_paths: list[Path] = field(default_factory=list)
    session_paths: list[Path] = field(default_factory=list)
    workspace_paths: list[Path] = field(default_factory=list)


def load_fixture(
    name: str,
    deps: CoDeps,
    *,
    re_stamp_mtimes: bool = True,
) -> FixtureHandle:
    """Copy a fixture's knowledge artifacts into ``~/.co-cli/``, sync FTS, build sessions.

    A fixture may also carry an optional ``<name>/workspace/`` subtree. When present
    it is copied into ``deps.workspace_dir`` (the eval-isolated ``EVAL_WORKSPACE_DIR``
    re-anchored by ``apply_eval_workspace``) so a behavioral case has real files the
    agent under test can actually reach — e.g. a codebase to plan a refactor over.
    Without this, file-based cases run against an empty workspace and the agent
    correctly reports "nothing here", invalidating the behavioral signal.

    Args:
        name: Fixture name — must match a subdir under ``evals/_fixtures/``.
        deps: Production ``CoDeps`` from ``make_eval_deps()``. Paths,
            ``workspace_dir``, and ``memory_store`` come from here.
        re_stamp_mtimes: When True (default), ``os.utime`` each copied knowledge
            file to ``now`` so fixtures look fresh regardless of how old the
            committed bytes are. Set False for cases that explicitly want to
            test mtime-aware behavior (e.g. B3.D decay simulation).

    Returns:
        FixtureHandle with absolute paths to seeded artifacts, session JSONLs, and
        any workspace files.

    Raises:
        FileNotFoundError if the fixture dir doesn't exist.
        AttributeError if ``deps.memory_store`` is None (bootstrap incomplete).
    """
    source = _FIXTURES_DIR / name / "memory"
    if not source.exists():
        raise FileNotFoundError(
            f"Fixture {name!r} not found at {source} — expected directory with memory/*.md inside."
        )

    deps.memory_dir.mkdir(parents=True, exist_ok=True)
    knowledge_paths: list[Path] = []
    now = time.time()
    for src_path in sorted(source.glob("*.md")):
        dst_path = deps.memory_dir / src_path.name
        dst_path.write_bytes(src_path.read_bytes())
        if re_stamp_mtimes:
            os.utime(dst_path, (now, now))
        knowledge_paths.append(dst_path)

    if deps.memory_store is not None:
        deps.memory_store.sync_dir(deps.memory_dir)

    workspace_source = _FIXTURES_DIR / name / "workspace"
    workspace_paths: list[Path] = []
    if workspace_source.exists():
        deps.workspace_dir.mkdir(parents=True, exist_ok=True)
        for src_path in sorted(workspace_source.rglob("*")):
            if src_path.is_dir():
                continue
            dst_path = deps.workspace_dir / src_path.relative_to(workspace_source)
            dst_path.parent.mkdir(parents=True, exist_ok=True)
            dst_path.write_bytes(src_path.read_bytes())
            if re_stamp_mtimes:
                os.utime(dst_path, (now, now))
            workspace_paths.append(dst_path)

    session_paths: list[Path] = []
    for index, turns in enumerate(_SESSION_SPECS.get(name, [])):
        path = _build_session_jsonl(deps, name, index, turns)
        session_paths.append(path)

    return FixtureHandle(
        name=name,
        knowledge_paths=knowledge_paths,
        session_paths=session_paths,
        workspace_paths=workspace_paths,
    )


def _build_session_jsonl(
    deps: CoDeps,
    fixture_name: str,
    index: int,
    turns: list[tuple[str, str]],
) -> Path:
    """Write a session JSONL from ``(user_text, assistant_text)`` turns.

    Deterministic filename: ``fixture-<fixture_name>-<index>.jsonl`` so reruns
    overwrite in place. Truncates first to avoid append-on-rerun growth.
    """
    deps.sessions_dir.mkdir(parents=True, exist_ok=True)
    path = deps.sessions_dir / f"fixture-{fixture_name}-{index}.jsonl"
    if path.exists():
        path.unlink()

    messages: list[ModelMessage] = []
    for user_text, assistant_text in turns:
        messages.append(ModelRequest(parts=[UserPromptPart(content=user_text)]))
        messages.append(ModelResponse(parts=[TextPart(content=assistant_text)]))
    append_messages(path, messages)
    return path


_SESSION_SPECS: dict[str, list[list[tuple[str, str]]]] = {
    "groundedness_baseline": [],
    "user_model_baseline": [
        [
            (
                "Can you keep responses short? I don't need long explanations.",
                "Got it — terse it is.",
            ),
            (
                "I'm mostly working in Python lately, just so you know.",
                "Acknowledged — Python by default.",
            ),
        ],
        [
            (
                "Also worth noting: I'm in PST.",
                "Noted — PST timezone.",
            ),
        ],
    ],
    "multistep_research_baseline": [
        [
            (
                "We decided to use sqlite for project Helios after the architecture review.",
                "Recording the decision.",
            ),
        ],
    ],
}
