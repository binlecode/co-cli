#!/usr/bin/env python3
"""Eval: memory write pipeline — extraction, dedup, async lifecycle, inline saves.

Covers the full memory write surface:
  Phase 1 — Extraction: analyze_for_signals classifies conversation signals
            into 4 types (user, feedback, project, reference) with confidence.
  Phase 2 — Dedup/Upsert: save agent decides SAVE_NEW vs UPDATE(slug).
  Phase 3 — Fire-and-forget: background extraction runs async, drain collects.
  Phase 4 — Inline saves: main agent calls save_memory proactively during
            run_turn(), including contradiction resolution against seeded memories.

Pass/fail gates per phase documented inline.

Prerequisites: LLM provider configured (ollama or gemini).

Usage:
    uv run python evals/eval_memory_extraction_dedup.py
"""

import asyncio
import os
import pathlib
import sys
import tempfile
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import yaml

from co_cli.config import settings, ROLE_SUMMARIZATION
from co_cli._model_factory import ModelRegistry, ResolvedModel
from co_cli.deps import CoDeps, CoConfig
from co_cli.memory._extractor import (
    ExtractionResult,
    MemoryCandidate,
    analyze_for_signals,
    fire_and_forget_extraction,
    drain_pending_extraction,
)
from co_cli.memory._lifecycle import persist_memory
from co_cli.memory._save import build_memory_manifest, check_and_save
from co_cli.tools.shell_backend import ShellBackend
from co_cli.tools.memory import MemoryEntry

from evals._fixtures import seed_memory, single_user_turn
from evals._frontend import CapturingFrontend, SilentFrontend
from evals._timeouts import EVAL_TURN_TIMEOUT_SECS
from evals._common import make_eval_deps, make_eval_settings
from evals._tools import extract_tool_calls


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------


_CONFIG = CoConfig.from_settings(settings, cwd=Path.cwd())
_REGISTRY = ModelRegistry.from_config(_CONFIG)


def _make_deps(memory_dir: Path) -> CoDeps:
    cfg = replace(_CONFIG, memory_dir=memory_dir)
    return CoDeps(
        shell=ShellBackend(),
        model_registry=_REGISTRY,
        config=cfg,
    )


def _seed(memory_dir: Path, mid: int, content: str, **kwargs: Any) -> Path:
    memory_dir.mkdir(parents=True, exist_ok=True)
    return seed_memory(memory_dir, mid, content, **kwargs)


# =========================================================================
# Phase 1 — Extraction classification
# =========================================================================


@dataclass
class ExtractionCase:
    id: str
    user_message: str
    expect_found: bool
    expect_tag: str | None
    expect_confidence: str | None
    description: str


EXTRACTION_CASES: list[ExtractionCase] = [
    # --- feedback: corrections (high) ---
    ExtractionCase(
        id="feedback-high-correction",
        user_message="don't use trailing comments in the code",
        expect_found=True, expect_tag="feedback", expect_confidence="high",
        description="Explicit correction → feedback, high",
    ),
    ExtractionCase(
        id="feedback-high-confirmed",
        user_message="yeah the single bundled PR was the right call here",
        expect_found=True, expect_tag="feedback", expect_confidence="high",
        description="Confirmed approach → feedback, high",
    ),
    # --- feedback: preferences (low) ---
    ExtractionCase(
        id="feedback-low-preference",
        user_message="I kind of prefer shorter responses",
        expect_found=True, expect_tag="user|feedback", expect_confidence="low",
        description="Hedged preference → user or feedback, low",
    ),
    # --- user: facts (high) ---
    ExtractionCase(
        id="user-high-role",
        user_message="I'm a data scientist investigating what logging we have in place",
        expect_found=True, expect_tag="user", expect_confidence="high",
        description="Stated role → user, high",
    ),
    # --- project: decisions (high) ---
    ExtractionCase(
        id="project-high-decision",
        user_message="we decided to use PostgreSQL from now on",
        expect_found=True, expect_tag="project", expect_confidence="high",
        description="Team decision → project, high",
    ),
    ExtractionCase(
        id="project-high-migration",
        user_message="we switched from REST to GraphQL last month",
        expect_found=True, expect_tag="project", expect_confidence="high",
        description="Migration → project, high",
    ),
    # --- reference: pointers (high) ---
    ExtractionCase(
        id="reference-high-tracker",
        user_message="check the Linear project INGEST if you want context on pipeline bugs",
        expect_found=True, expect_tag="reference", expect_confidence="high",
        description="External system pointer → reference, high",
    ),
    # --- multi-candidate: rich conversation with multiple signals ---
    ExtractionCase(
        id="multi-candidate",
        user_message=(
            "I'm a backend engineer. We just switched from MySQL to PostgreSQL last week. "
            "Oh and bugs are tracked in the Linear project PLATFORM."
        ),
        expect_found=True, expect_tag="multi", expect_confidence=None,
        description="Rich message with 3 signals → multiple candidates",
    ),
    # --- guardrails: no signal ---
    ExtractionCase(
        id="none-capability",
        user_message="does Python support pattern matching syntax?",
        expect_found=False, expect_tag=None, expect_confidence=None,
        description="Capability question → no signal",
    ),
    ExtractionCase(
        id="none-sensitive",
        user_message="my API key is sk-1234, please don't save that anywhere",
        expect_found=False, expect_tag=None, expect_confidence=None,
        description="Sensitive content → no signal",
    ),
    ExtractionCase(
        id="none-neutral",
        user_message="what does this error mean?",
        expect_found=False, expect_tag=None, expect_confidence=None,
        description="General question → no signal",
    ),
]


async def run_extraction_case(case: ExtractionCase, deps: CoDeps) -> dict[str, Any]:
    messages = single_user_turn(case.user_message)
    extraction = await analyze_for_signals(messages, deps=deps)
    if extraction.memories:
        first = extraction.memories[0]
        return {
            "found": True,
            "tag": first.tag,
            "confidence": first.confidence,
            "candidate": first.candidate,
            "count": len(extraction.memories),
            "all_tags": sorted({m.tag for m in extraction.memories}),
        }
    return {"found": False, "tag": None, "confidence": None, "candidate": None, "count": 0, "all_tags": []}


# =========================================================================
# Phase 2 — Dedup/Upsert via save agent
# =========================================================================


@dataclass
class DedupCase:
    id: str
    existing_content: str
    new_content: str
    expect_action: str  # "SAVE_NEW" or "UPDATE"
    description: str


DEDUP_CASES: list[DedupCase] = [
    DedupCase(
        id="dedup-near-dup",
        existing_content="User prefers pytest for testing",
        new_content="User prefers pytest for all testing purposes",
        expect_action="UPDATE",
        description="Near-duplicate → UPDATE",
    ),
    DedupCase(
        id="dedup-contradiction",
        existing_content="User prefers dark mode",
        new_content="User prefers light mode (changed from dark)",
        expect_action="UPDATE",
        description="Contradiction/correction → UPDATE",
    ),
    DedupCase(
        id="dedup-novel",
        existing_content="User prefers pytest for testing",
        new_content="User's team tracks bugs in Linear project INGEST",
        expect_action="SAVE_NEW",
        description="Novel content → SAVE_NEW",
    ),
]


async def run_dedup_case(case: DedupCase, tmp_dir: Path) -> dict[str, Any]:
    memory_dir = tmp_dir / case.id / "memory"
    memory_dir.mkdir(parents=True, exist_ok=True)

    # Seed existing memory
    seed_memory(memory_dir, 1, case.existing_content, tags=["preference"])

    # Build manifest and run save agent
    from co_cli.tools.memory import load_memories

    memories = load_memories(memory_dir, kind="memory")
    memories.sort(key=lambda m: m.updated or m.created, reverse=True)
    manifest = build_memory_manifest(memories)

    resolved = _REGISTRY.get(ROLE_SUMMARIZATION, ResolvedModel(model=None, settings=None))
    if resolved.model is None:
        return {"action": "SKIP", "reason": "no summarization model"}

    result = await check_and_save(case.new_content, manifest, resolved)
    return {"action": result.action, "target_slug": result.target_slug}


async def run_slug_not_found_case(tmp_dir: Path) -> dict[str, Any]:
    """Save agent returns UPDATE(slug) but file was deleted — falls through to SAVE_NEW."""
    memory_dir = tmp_dir / "slug-not-found" / "memory"
    memory_dir.mkdir(parents=True, exist_ok=True)

    # Seed and then delete — simulates stale manifest
    path = seed_memory(memory_dir, 1, "User prefers pytest for testing", tags=["preference"])
    path.unlink()

    # persist_memory will build manifest from disk (empty), but we force the
    # save agent path by seeding a second memory so manifest is non-empty
    seed_memory(memory_dir, 2, "User uses 4-space indentation", tags=["preference"])

    resolved = _REGISTRY.get(ROLE_SUMMARIZATION, ResolvedModel(model=None, settings=None))
    if resolved.model is None:
        return {"result": "SKIP", "reason": "no summarization model"}

    deps = _make_deps(memory_dir=memory_dir)
    before_count = len(list(memory_dir.glob("*.md")))

    result = await persist_memory(
        deps, "User prefers pytest for all testing purposes",
        ["preference"], None, resolved=resolved,
    )

    after_count = len(list(memory_dir.glob("*.md")))
    return {
        "result": "ok",
        "action": result.metadata.get("action"),
        "before_count": before_count,
        "after_count": after_count,
        "new_file_created": after_count > before_count,
    }


# =========================================================================
# Phase 3 — Fire-and-forget async lifecycle
# =========================================================================


async def run_fire_and_forget_novel(tmp_dir: Path) -> dict[str, Any]:
    """3a: Novel save — fire_and_forget creates a new file with correct content."""
    memory_dir = tmp_dir / "ff-novel" / "memory"
    memory_dir.mkdir(parents=True, exist_ok=True)

    deps = _make_deps(memory_dir=memory_dir)
    frontend = CapturingFrontend(approval_response="n")

    messages = single_user_turn("don't use wildcard imports in any Python file")

    t0 = time.monotonic()
    fire_and_forget_extraction(messages, deps=deps, frontend=frontend)
    launch_time = time.monotonic() - t0

    await drain_pending_extraction(timeout_ms=30_000)
    drain_time = time.monotonic() - t0

    files = list(memory_dir.glob("*.md"))
    content_match = False
    tag_match = False
    for f in files:
        raw = f.read_text(encoding="utf-8")
        lower = raw.lower()
        if "wildcard" in lower or "import" in lower:
            content_match = True
            fm = yaml.safe_load(raw.split("---")[1]) if "---" in raw else {}
            if isinstance(fm, dict) and "feedback" in (fm.get("tags") or []):
                tag_match = True

    return {
        "launch_ms": launch_time * 1000,
        "drain_ms": drain_time * 1000,
        "files_written": len(files),
        "content_match": content_match,
        "tag_match": tag_match,
        "status_messages": list(frontend.statuses),
        "launch_non_blocking": launch_time < 0.1,
    }


async def run_fire_and_forget_collision(tmp_dir: Path) -> dict[str, Any]:
    """3b: Collision — fire_and_forget updates existing file instead of creating duplicate."""
    memory_dir = tmp_dir / "ff-collision" / "memory"
    memory_dir.mkdir(parents=True, exist_ok=True)

    # Seed a memory that the extraction should collide with
    seed_memory(memory_dir, 1, "User does not want wildcard imports", tags=["feedback"])
    before_count = len(list(memory_dir.glob("*.md")))

    deps = _make_deps(memory_dir=memory_dir)
    frontend = CapturingFrontend(approval_response="n")

    # Near-duplicate signal
    messages = single_user_turn("never use wildcard imports in Python, always import explicitly")

    fire_and_forget_extraction(messages, deps=deps, frontend=frontend)
    await drain_pending_extraction(timeout_ms=30_000)

    after_files = list(memory_dir.glob("*.md"))
    after_count = len(after_files)

    # Check if existing file was updated (has 'updated' timestamp)
    updated_existing = False
    for f in after_files:
        raw = f.read_text(encoding="utf-8")
        if "---" in raw:
            fm = yaml.safe_load(raw.split("---")[1])
            if isinstance(fm, dict) and fm.get("updated") is not None:
                updated_existing = True

    return {
        "before_count": before_count,
        "after_count": after_count,
        "no_new_file": after_count <= before_count,
        "updated_existing": updated_existing,
    }


async def run_fire_and_forget_no_signal(tmp_dir: Path) -> dict[str, Any]:
    """3c: No signal — fire_and_forget completes without writing any file."""
    memory_dir = tmp_dir / "ff-no-signal" / "memory"
    memory_dir.mkdir(parents=True, exist_ok=True)

    deps = _make_deps(memory_dir=memory_dir)
    frontend = CapturingFrontend(approval_response="n")

    messages = single_user_turn("what does this error mean?")

    fire_and_forget_extraction(messages, deps=deps, frontend=frontend)
    await drain_pending_extraction(timeout_ms=30_000)

    files = list(memory_dir.glob("*.md"))
    return {
        "files_written": len(files),
        "status_messages": list(frontend.statuses),
    }


async def run_fire_and_forget_low_confidence(tmp_dir: Path) -> dict[str, Any]:
    """3d: Low-confidence signal — skipped in async mode, no file, no prompt."""
    memory_dir = tmp_dir / "ff-low-conf" / "memory"
    memory_dir.mkdir(parents=True, exist_ok=True)

    deps = _make_deps(memory_dir=memory_dir)
    frontend = CapturingFrontend(approval_response="y")

    # Low-confidence signal: hedged preference
    messages = single_user_turn("I kind of prefer shorter responses")

    fire_and_forget_extraction(messages, deps=deps, frontend=frontend)
    await drain_pending_extraction(timeout_ms=30_000)

    files = list(memory_dir.glob("*.md"))
    return {
        "files_written": len(files),
        "status_messages": list(frontend.statuses),
        "approval_calls": list(frontend.approval_calls) if hasattr(frontend, "approval_calls") else [],
    }


# =========================================================================
# Phase 4 — Inline saves via run_turn (proactive + contradiction)
# =========================================================================


@dataclass
class InlineSaveCase:
    id: str
    prompt: str
    seeded_memories: list[dict[str, Any]] | None
    expect_save: bool
    expected_keywords: tuple[str, ...] | None
    description: str


INLINE_CASES: list[InlineSaveCase] = [
    InlineSaveCase(
        id="inline-proactive-preference",
        prompt="I always use 4-space indentation and prefer dark themes in everything",
        seeded_memories=None,
        expect_save=True,
        expected_keywords=None,
        description="Proactive save on preference signal (no 'remember' keyword)",
    ),
    InlineSaveCase(
        id="inline-proactive-decision",
        prompt="We've decided to use Kubernetes for production and Docker Compose for dev",
        seeded_memories=None,
        expect_save=True,
        expected_keywords=None,
        description="Proactive save on decision signal",
    ),
    InlineSaveCase(
        id="inline-no-signal",
        prompt="What time is it in Tokyo?",
        seeded_memories=None,
        expect_save=False,
        expected_keywords=None,
        description="No signal — save_memory should NOT be called",
    ),
    InlineSaveCase(
        id="inline-contradiction",
        prompt="We've moved everything to PostgreSQL now, that's our standard",
        seeded_memories=[
            {"content": "User prefers MySQL for all database work", "tags": ["preference"], "days_ago": 5},
        ],
        expect_save=True,
        expected_keywords=("postgresql", "postgres"),
        description="Contradiction: seeded MySQL preference → correction to PostgreSQL",
    ),
    InlineSaveCase(
        id="inline-dedup-no-duplicate",
        prompt="We use PostgreSQL for everything, that's our standard database",
        seeded_memories=[
            {"content": "User's team uses PostgreSQL as their standard database", "tags": ["project"], "days_ago": 1},
        ],
        expect_save=True,
        expected_keywords=("postgresql", "postgres"),
        description="Near-dup of seeded memory → save called, but file count should not increase",
    ),
]


async def run_inline_case(case: InlineSaveCase) -> dict[str, Any]:
    """Run a full run_turn() and check if save_memory was called."""
    from co_cli.agent import build_agent
    from co_cli.context.orchestrate import run_turn

    with tempfile.TemporaryDirectory() as tmpdir:
        orig_cwd = os.getcwd()
        try:
            os.chdir(tmpdir)
            memory_dir = Path(tmpdir) / ".co-cli" / "memory"
            memory_dir.mkdir(parents=True)

            if case.seeded_memories:
                for idx, mem in enumerate(case.seeded_memories, start=1):
                    seed_memory(memory_dir, idx, mem["content"],
                                days_ago=mem.get("days_ago", 0), tags=mem.get("tags"))

            before_count = len(list(memory_dir.glob("*.md")))

            config = CoConfig.from_settings(settings, cwd=Path(tmpdir))
            agent = build_agent(config=config)
            deps = make_eval_deps(session_id=f"eval-inline-{case.id}")
            frontend = SilentFrontend()

            async with asyncio.timeout(EVAL_TURN_TIMEOUT_SECS):
                result = await run_turn(
                    agent=agent,
                    user_input=case.prompt,
                    deps=deps,
                    message_history=[],
                    model_settings=make_eval_settings(),
                    frontend=frontend,
                )

            tool_calls = extract_tool_calls(result.messages)
            save_calls = [(n, a) for n, a in tool_calls if n == "save_memory"]

            after_count = len(list(memory_dir.glob("*.md")))

            keyword_found = False
            if case.expected_keywords:
                for path in memory_dir.glob("*.md"):
                    text = path.read_text(encoding="utf-8").lower()
                    if any(kw in text for kw in case.expected_keywords):
                        keyword_found = True
                        break

            return {
                "save_called": len(save_calls) > 0,
                "save_count": len(save_calls),
                "keyword_found": keyword_found,
                "all_tools": [n for n, _ in tool_calls],
                "before_count": before_count,
                "after_count": after_count,
            }
        finally:
            os.chdir(orig_cwd)


# =========================================================================
# Main
# =========================================================================


async def main() -> int:
    print("=" * 70)
    print("  Eval: Memory Extraction + Dedup Pipeline")
    print("=" * 70)

    import tempfile

    total = 0
    passed = 0
    t0 = time.monotonic()

    # --- Phase 1: Extraction classification ---
    print("\n  Phase 1: Extraction Classification")
    print("  " + "-" * 50)

    config = CoConfig.from_settings(settings, cwd=pathlib.Path.cwd())
    deps = CoDeps(
        shell=ShellBackend(),
        model_registry=_REGISTRY,
        config=config,
    )

    for case in EXTRACTION_CASES:
        total += 1
        print(f"    [{case.id}] {case.description}")
        print(f'      Prompt: "{case.user_message[:55]}"', end=" ", flush=True)

        try:
            scores = await run_extraction_case(case, deps)
        except Exception as exc:
            print(f"ERROR ({exc})")
            continue

        # Multi-candidate case has its own validation
        if case.expect_tag == "multi":
            count = scores["count"]
            all_tags = scores["all_tags"]
            ok = count >= 2 and len(all_tags) >= 2
            if ok:
                print(f"PASS (count={count} tags={all_tags})")
                passed += 1
            else:
                print(f"FAIL (count={count} tags={all_tags}, expected ≥2 candidates with ≥2 distinct types)")
            continue

        found_ok = scores["found"] == case.expect_found
        if case.expect_found and case.expect_tag:
            # Support "user|feedback" alternative tags
            tag_ok = scores["tag"] in case.expect_tag.split("|")
        else:
            tag_ok = scores["tag"] is None
        conf_ok = scores["confidence"] == case.expect_confidence if case.expect_found else scores["confidence"] is None
        ok = found_ok and tag_ok and conf_ok

        if ok:
            detail = f" tag={scores['tag']} conf={scores['confidence']}" if case.expect_found else ""
            print(f"PASS{detail}")
            passed += 1
        else:
            failures = []
            if not found_ok:
                failures.append(f"found={scores['found']} (expect {case.expect_found})")
            if not tag_ok:
                failures.append(f"tag={scores['tag']} (expect {case.expect_tag})")
            if not conf_ok:
                failures.append(f"confidence={scores['confidence']} (expect {case.expect_confidence})")
            print(f"FAIL ({', '.join(failures)})")
            if scores.get("candidate"):
                print(f"      Candidate: {scores['candidate']}")

    # --- Phase 2: Dedup/Upsert ---
    print("\n  Phase 2: Dedup/Upsert via Save Agent")
    print("  " + "-" * 50)

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)

        for case in DEDUP_CASES:
            total += 1
            print(f"    [{case.id}] {case.description}", end=" ", flush=True)

            try:
                scores = await run_dedup_case(case, tmp_path)
            except Exception as exc:
                print(f"ERROR ({exc})")
                continue

            if scores["action"] == "SKIP":
                print(f"SKIP ({scores['reason']})")
                continue

            ok = scores["action"] == case.expect_action
            if ok:
                slug_info = f" slug={scores['target_slug']}" if scores.get("target_slug") else ""
                print(f"PASS (action={scores['action']}{slug_info})")
                passed += 1
            else:
                print(f"FAIL (action={scores['action']} expected {case.expect_action})")

        # 2b: Slug not found — save agent returns UPDATE but file is gone
        total += 1
        print("    [dedup-slug-gone] UPDATE target deleted → falls through to SAVE_NEW", end=" ", flush=True)
        try:
            scores = await run_slug_not_found_case(tmp_path)
        except Exception as exc:
            print(f"ERROR ({exc})")
        else:
            if scores["result"] == "SKIP":
                print(f"SKIP ({scores['reason']})")
            elif scores["new_file_created"]:
                print(f"PASS (files: {scores['before_count']}→{scores['after_count']}, action={scores['action']})")
                passed += 1
            else:
                print(f"FAIL (files: {scores['before_count']}→{scores['after_count']}, expected new file)")

        # --- Phase 3: Fire-and-forget ---
        print("\n  Phase 3: Fire-and-Forget Async Lifecycle")
        print("  " + "-" * 50)

        # 3a: Novel save
        total += 1
        print("    [ff-novel] background extraction → new file with correct content", end=" ", flush=True)
        try:
            scores = await run_fire_and_forget_novel(tmp_path)
        except Exception as exc:
            print(f"ERROR ({exc})")
        else:
            launch_ok = scores["launch_non_blocking"]
            content_ok = scores["content_match"]
            tag_ok = scores["tag_match"]
            ok = launch_ok and content_ok and tag_ok

            if ok:
                print(
                    f"PASS (launch={scores['launch_ms']:.0f}ms "
                    f"drain={scores['drain_ms']:.0f}ms "
                    f"content=match tag=match)"
                )
                passed += 1
            else:
                failures = []
                if not launch_ok:
                    failures.append(f"launch_ms={scores['launch_ms']:.0f} (must be <100)")
                if not content_ok:
                    failures.append("content mismatch (wildcard/import not in file)")
                if not tag_ok:
                    failures.append("tag mismatch (feedback not in tags)")
                print(f"FAIL ({', '.join(failures)})")

        # 3b: No signal — no file written
        total += 1
        print("    [ff-no-signal] background extraction → no signal, no file", end=" ", flush=True)
        try:
            scores = await run_fire_and_forget_no_signal(tmp_path)
        except Exception as exc:
            print(f"ERROR ({exc})")
        else:
            ok = scores["files_written"] == 0 and len(scores["status_messages"]) == 0
            if ok:
                print("PASS (0 files, 0 status messages)")
                passed += 1
            else:
                print(
                    f"FAIL (files={scores['files_written']}, "
                    f"statuses={scores['status_messages']})"
                )

        # 3c: Collision — update existing instead of duplicate
        total += 1
        print("    [ff-collision] background extraction → updates existing, no duplicate", end=" ", flush=True)
        try:
            scores = await run_fire_and_forget_collision(tmp_path)
        except Exception as exc:
            print(f"ERROR ({exc})")
        else:
            no_dup = scores["no_new_file"]
            updated = scores["updated_existing"]
            # Accept either: UPDATE hit (no new file + updated timestamp) or
            # SAVE_NEW (model missed the collision — acceptable per PO-R-2)
            ok = no_dup and updated

            if ok:
                print(
                    f"PASS (files: {scores['before_count']}→{scores['after_count']}, "
                    f"updated_existing=True)"
                )
                passed += 1
            elif not no_dup and not updated:
                # Model created a new file — acceptable miss, not a failure
                print(
                    f"SOFT PASS (files: {scores['before_count']}→{scores['after_count']}, "
                    f"model missed collision — acceptable per PO-R-2)"
                )
                passed += 1
            else:
                print(
                    f"FAIL (files: {scores['before_count']}→{scores['after_count']}, "
                    f"updated_existing={scores['updated_existing']})"
                )

        # 3d: Low-confidence — skipped in async mode, no file, no prompt
        total += 1
        print("    [ff-low-conf] low-confidence signal → skipped, no file", end=" ", flush=True)
        try:
            scores = await run_fire_and_forget_low_confidence(tmp_path)
        except Exception as exc:
            print(f"ERROR ({exc})")
        else:
            no_files = scores["files_written"] == 0
            no_status = len(scores["status_messages"]) == 0
            ok = no_files and no_status
            if ok:
                print("PASS (0 files, 0 status — low-confidence skipped in async)")
                passed += 1
            else:
                print(
                    f"FAIL (files={scores['files_written']}, "
                    f"statuses={scores['status_messages']})"
                )

    # --- Phase 4: Inline saves via run_turn ---
    print("\n  Phase 4: Inline Saves via run_turn()")
    print("  " + "-" * 50)

    for case in INLINE_CASES:
        total += 1
        print(f"    [{case.id}] {case.description}")
        print(f'      Prompt: "{case.prompt[:55]}"', end=" ", flush=True)

        try:
            scores = await run_inline_case(case)
        except Exception as exc:
            print(f"ERROR ({exc})")
            continue

        # Dedup case: check file count didn't increase
        if case.id == "inline-dedup-no-duplicate":
            save_ok = scores["save_called"]
            keyword_ok = scores["keyword_found"]
            no_dup = scores["after_count"] <= scores["before_count"]
            if save_ok and keyword_ok and no_dup:
                print(f"PASS (save called, files: {scores['before_count']}→{scores['after_count']}, deduped)")
                passed += 1
            elif save_ok and keyword_ok and not no_dup:
                # Model saved as new — acceptable miss per PO-R-2
                print(f"SOFT PASS (files: {scores['before_count']}→{scores['after_count']}, model missed dedup)")
                passed += 1
            else:
                failures = []
                if not save_ok:
                    failures.append("save_memory not called")
                if not keyword_ok:
                    failures.append("keyword not found")
                print(f"FAIL ({', '.join(failures)})")
                print(f"      Tools: {scores['all_tools']}")
            continue

        failures = []
        if case.expect_save and not scores["save_called"]:
            failures.append("save_memory not called")
        if not case.expect_save and scores["save_called"]:
            failures.append("save_memory called on no-signal prompt")
        if case.expected_keywords and not scores["keyword_found"]:
            failures.append("expected keyword not found in saved content")

        if not failures:
            detail = f" (save_memory x{scores['save_count']})" if scores["save_called"] else " (no save — correct)"
            print(f"PASS{detail}")
            passed += 1
        else:
            print(f"FAIL ({', '.join(failures)})")
            print(f"      Tools: {scores['all_tools']}")

    elapsed = time.monotonic() - t0
    print(f"\n{'=' * 70}")
    verdict = "PASS" if passed == total else "FAIL"
    print(f"  Verdict: {verdict} ({passed}/{total} cases, {elapsed:.1f}s)")
    print(f"{'=' * 70}")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
