#!/usr/bin/env python3
"""Eval: memory — session history persistence + knowledge edit/recall + LLM context recall.

Phase 1 (no LLM): session transcript persist → load → content integrity
Phase 2 (no LLM): knowledge save/update/append → FTS5 reindex → search recall
Phase 3 (LLM):    restored session history → run_turn → agent references prior content

Writes: docs/REPORT-eval-memory.md (prepends dated section each run).

Prerequisites: LLM provider configured (ollama or gemini) for Phase 3.

Usage:
    uv run python evals/eval_memory.py
"""

import asyncio
import os
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from evals._deps import make_eval_deps
from evals._fixtures import build_message_history
from evals._frontend import SilentFrontend
from evals._timeouts import EVAL_TURN_TIMEOUT_SECS
from pydantic_ai import RunContext
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart
from pydantic_ai.usage import RunUsage

from co_cli.agent._core import build_agent
from co_cli.config._core import get_settings, settings
from co_cli.context.orchestrate import run_turn
from co_cli.deps import CoDeps, CoSessionState
from co_cli.knowledge._store import KnowledgeStore
from co_cli.memory.session import new_session_path
from co_cli.memory.transcript import load_transcript, persist_session_history
from co_cli.tools.knowledge.write import append_knowledge, save_knowledge, update_knowledge
from co_cli.tools.memory import search_memory
from co_cli.tools.shell_backend import ShellBackend

_REPORT_PATH = Path(__file__).parent.parent / "docs" / "REPORT-eval-memory.md"

_SENTINEL = "zyxwquartz-session-history-eval-unique"
_SENTINEL_BASE = "zyxwquartz-eval-mem-edit-unique"

_AGENT = build_agent(config=settings)


# ---------------------------------------------------------------------------
# FTS5 helper
# ---------------------------------------------------------------------------


def _make_ctx(
    memory_dir: Path,
    *,
    knowledge_store: KnowledgeStore | None = None,
) -> RunContext:
    cfg = get_settings()
    if cfg.knowledge.search_backend != "fts5":
        cfg = cfg.model_copy(
            update={"knowledge": cfg.knowledge.model_copy(update={"search_backend": "fts5"})}
        )
    deps = CoDeps(
        shell=ShellBackend(),
        knowledge_store=knowledge_store,
        config=cfg,
        session=CoSessionState(),
    )
    deps.knowledge_dir = memory_dir
    return RunContext(deps=deps, model=_AGENT.model, usage=RunUsage())


def _slug_from_ctx(ctx: RunContext) -> str | None:
    memory_dir: Path = ctx.deps.knowledge_dir
    files = sorted(memory_dir.glob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True)
    return files[0].stem if files else None


# ---------------------------------------------------------------------------
# Phase 1: session transcript persistence (no LLM)
# ---------------------------------------------------------------------------


async def run_persist_load_roundtrip(tmp_dir: Path) -> dict[str, Any]:
    """persist_session_history writes JSONL; load_transcript deserializes exact message count."""
    steps: list[dict[str, Any]] = []
    case_t0 = time.monotonic()
    sessions_dir = tmp_dir / "persist-load" / "sessions"
    sessions_dir.mkdir(parents=True, exist_ok=True)

    messages = build_message_history(
        [
            ("user", f"My preferred language is {_SENTINEL}-lang."),
            ("assistant", f"Understood. I will use {_SENTINEL}-lang."),
            ("user", "What is 2 + 2?"),
            ("assistant", "2 + 2 = 4."),
        ]
    )

    t = time.monotonic()
    session_path = new_session_path(sessions_dir)
    written_path = persist_session_history(
        session_path=session_path,
        sessions_dir=sessions_dir,
        messages=messages,
        persisted_message_count=0,
        history_compacted=False,
        reason="eval",
    )
    steps.append(
        {
            "name": "persist_session_history",
            "ms": (time.monotonic() - t) * 1000,
            "detail": f"wrote {len(messages)} messages to {written_path.name}",
        }
    )

    t = time.monotonic()
    loaded = load_transcript(written_path)
    steps.append(
        {
            "name": "load_transcript",
            "ms": (time.monotonic() - t) * 1000,
            "detail": f"loaded={len(loaded)} expected={len(messages)}",
        }
    )

    first_user_text = ""
    for msg in loaded:
        if isinstance(msg, ModelRequest):
            for part in msg.parts:
                if isinstance(part, UserPromptPart):
                    first_user_text = part.content
                    break
        if first_user_text:
            break
    steps.append(
        {
            "name": "content integrity check",
            "ms": 0,
            "detail": f"first_user_msg={first_user_text[:60]!r} sentinel_present={_SENTINEL in first_user_text}",
        }
    )

    if len(loaded) != len(messages):
        verdict, failure = (
            "FAIL",
            f"message count mismatch: wrote {len(messages)}, loaded {len(loaded)}",
        )
    elif _SENTINEL not in first_user_text:
        verdict, failure = (
            "FAIL",
            f"sentinel not found in restored first message: {first_user_text[:80]!r}",
        )
    else:
        verdict, failure = "PASS", None

    return {
        "id": "persist-load-roundtrip",
        "verdict": verdict,
        "failure": failure,
        "steps": steps,
        "duration_ms": (time.monotonic() - case_t0) * 1000,
    }


async def run_incremental_append(tmp_dir: Path) -> dict[str, Any]:
    """Subsequent turns append-only; persisted_message_count prevents re-writing old messages."""
    steps: list[dict[str, Any]] = []
    case_t0 = time.monotonic()
    sessions_dir = tmp_dir / "incremental" / "sessions"
    sessions_dir.mkdir(parents=True, exist_ok=True)
    session_path = new_session_path(sessions_dir)

    turn1 = build_message_history(
        [
            ("user", "First turn message."),
            ("assistant", "First turn response."),
        ]
    )
    turn2 = build_message_history(
        [
            ("user", "First turn message."),
            ("assistant", "First turn response."),
            ("user", f"Second turn with {_SENTINEL}-append-marker."),
            ("assistant", "Second turn response."),
        ]
    )

    t = time.monotonic()
    persist_session_history(
        session_path=session_path,
        sessions_dir=sessions_dir,
        messages=turn1,
        persisted_message_count=0,
        history_compacted=False,
        reason="eval",
    )
    steps.append(
        {
            "name": "persist turn 1 (2 messages)",
            "ms": (time.monotonic() - t) * 1000,
            "detail": "persisted_message_count=0 → appends all 2",
        }
    )

    t = time.monotonic()
    persist_session_history(
        session_path=session_path,
        sessions_dir=sessions_dir,
        messages=turn2,
        persisted_message_count=len(turn1),
        history_compacted=False,
        reason="eval",
    )
    steps.append(
        {
            "name": "persist turn 2 (4 messages, count=2)",
            "ms": (time.monotonic() - t) * 1000,
            "detail": f"persisted_message_count={len(turn1)} → appends only 2 new",
        }
    )

    t = time.monotonic()
    loaded = load_transcript(session_path)
    steps.append(
        {
            "name": "load_transcript",
            "ms": (time.monotonic() - t) * 1000,
            "detail": f"loaded={len(loaded)} expected={len(turn2)}",
        }
    )

    user_texts = [
        part.content
        for msg in loaded
        if isinstance(msg, ModelRequest)
        for part in msg.parts
        if isinstance(part, UserPromptPart)
    ]
    has_duplicate = len(user_texts) != len(set(user_texts))
    has_sentinel = any(_SENTINEL in t for t in user_texts)
    steps.append(
        {
            "name": "duplicate check",
            "ms": 0,
            "detail": f"user_messages={user_texts} has_duplicate={has_duplicate} has_sentinel={has_sentinel}",
        }
    )

    if len(loaded) != len(turn2):
        verdict, failure = "FAIL", f"expected {len(turn2)} messages, loaded {len(loaded)}"
    elif has_duplicate:
        verdict, failure = (
            "FAIL",
            "duplicate messages found — incremental append wrote redundant data",
        )
    elif not has_sentinel:
        verdict, failure = "FAIL", "sentinel from turn 2 not found in loaded transcript"
    else:
        verdict, failure = "PASS", None

    return {
        "id": "incremental-append",
        "verdict": verdict,
        "failure": failure,
        "steps": steps,
        "duration_ms": (time.monotonic() - case_t0) * 1000,
    }


async def run_empty_history_new_session(tmp_dir: Path) -> dict[str, Any]:
    """Empty history persists nothing; load_transcript returns []. No crash."""
    steps: list[dict[str, Any]] = []
    case_t0 = time.monotonic()
    sessions_dir = tmp_dir / "empty-history" / "sessions"
    sessions_dir.mkdir(parents=True, exist_ok=True)
    session_path = new_session_path(sessions_dir)

    t = time.monotonic()
    written_path = persist_session_history(
        session_path=session_path,
        sessions_dir=sessions_dir,
        messages=[],
        persisted_message_count=0,
        history_compacted=False,
        reason="eval",
    )
    steps.append(
        {
            "name": "persist_session_history (empty messages)",
            "ms": (time.monotonic() - t) * 1000,
            "detail": f"file_exists={written_path.exists()}",
        }
    )

    t = time.monotonic()
    loaded = load_transcript(written_path)
    steps.append(
        {
            "name": "load_transcript",
            "ms": (time.monotonic() - t) * 1000,
            "detail": f"loaded={len(loaded)} (expected 0)",
        }
    )

    if len(loaded) != 0:
        verdict, failure = "FAIL", f"expected 0 messages, got {len(loaded)}"
    else:
        verdict, failure = "PASS", None

    return {
        "id": "empty-history-new-session",
        "verdict": verdict,
        "failure": failure,
        "steps": steps,
        "duration_ms": (time.monotonic() - case_t0) * 1000,
    }


# ---------------------------------------------------------------------------
# Phase 2: knowledge edit / FTS5 recall (no LLM)
# ---------------------------------------------------------------------------


async def run_save_recall(tmp_dir: Path) -> dict[str, Any]:
    """save_knowledge indexes into DB; search_memory finds it by unique sentinel."""
    steps: list[dict[str, Any]] = []
    case_t0 = time.monotonic()
    memory_dir = tmp_dir / "save-recall" / "memory"
    db_path = tmp_dir / "save-recall" / "search.db"
    memory_dir.mkdir(parents=True, exist_ok=True)

    ks = KnowledgeStore(config=settings, knowledge_db_path=db_path)
    ctx = _make_ctx(memory_dir, knowledge_store=ks)
    sentinel = f"{_SENTINEL_BASE}-save"

    try:
        t = time.monotonic()
        save_result = await save_knowledge(
            ctx,
            content=f"User prefers {sentinel} for all testing.",
            artifact_kind="preference",
            title="test-preference",
        )
        steps.append(
            {
                "name": "save_knowledge",
                "ms": (time.monotonic() - t) * 1000,
                "detail": f"saved={save_result.metadata.get('saved')}",
            }
        )

        t = time.monotonic()
        search_result = await search_memory(ctx, sentinel)
        found_count = search_result.metadata.get("count", 0)
        steps.append(
            {
                "name": "search_memory (FTS5)",
                "ms": (time.monotonic() - t) * 1000,
                "detail": f"count={found_count}",
            }
        )

        if found_count < 1:
            verdict, failure = (
                "FAIL",
                f"search_memory returned 0 after save (sentinel={sentinel!r})",
            )
        else:
            verdict, failure = "PASS", None
    finally:
        ks.close()

    return {
        "id": "save-recall",
        "verdict": verdict,
        "failure": failure,
        "steps": steps,
        "duration_ms": (time.monotonic() - case_t0) * 1000,
    }


async def run_update_reindex_recall(tmp_dir: Path) -> dict[str, Any]:
    """update_knowledge rewrites content and reindexes; search finds new, not original."""
    steps: list[dict[str, Any]] = []
    case_t0 = time.monotonic()
    memory_dir = tmp_dir / "update-recall" / "memory"
    db_path = tmp_dir / "update-recall" / "search.db"
    memory_dir.mkdir(parents=True, exist_ok=True)

    ks = KnowledgeStore(config=settings, knowledge_db_path=db_path)
    ctx = _make_ctx(memory_dir, knowledge_store=ks)
    original = f"{_SENTINEL_BASE}-update-original"
    updated = f"{_SENTINEL_BASE}-update-new"

    try:
        t = time.monotonic()
        await save_knowledge(
            ctx,
            content=f"Uses {original} for testing.",
            artifact_kind="preference",
            title="update-test",
        )
        slug = _slug_from_ctx(ctx)
        steps.append(
            {
                "name": "save_knowledge",
                "ms": (time.monotonic() - t) * 1000,
                "detail": f"slug={slug}",
            }
        )

        before_results = await search_memory(ctx, original)
        before_count = before_results.metadata.get("count", 0)
        steps.append(
            {
                "name": "search (before update)",
                "ms": 0,
                "detail": f"original_found={before_count}",
            }
        )

        t = time.monotonic()
        update_result = await update_knowledge(
            ctx,
            slug=slug,
            old_content=f"Uses {original} for testing.",
            new_content=f"Now uses {updated} exclusively.",
        )
        steps.append(
            {
                "name": "update_knowledge + reindex",
                "ms": (time.monotonic() - t) * 1000,
                "detail": f"updated={update_result.metadata.get('updated')}",
            }
        )

        t = time.monotonic()
        new_results = await search_memory(ctx, updated)
        new_count = new_results.metadata.get("count", 0)
        steps.append(
            {
                "name": "search (after update, new keyword)",
                "ms": (time.monotonic() - t) * 1000,
                "detail": f"new_found={new_count}",
            }
        )

        old_results = await search_memory(ctx, original)
        old_still_found = old_results.metadata.get("count", 0)
        steps.append(
            {
                "name": "search (after update, original keyword)",
                "ms": 0,
                "detail": f"original_still_found={old_still_found} (expected 0)",
            }
        )

        if before_count < 1:
            verdict, failure = "FAIL", "original content not found before update"
        elif new_count < 1:
            verdict, failure = "FAIL", "new content not found after update + reindex"
        elif old_still_found > 0:
            verdict, failure = "SOFT PASS", None
        else:
            verdict, failure = "PASS", None
    finally:
        ks.close()

    return {
        "id": "update-reindex-recall",
        "verdict": verdict,
        "failure": failure,
        "steps": steps,
        "duration_ms": (time.monotonic() - case_t0) * 1000,
    }


async def run_append_reindex_recall(tmp_dir: Path) -> dict[str, Any]:
    """append_knowledge adds content and reindexes; search finds appended keyword."""
    steps: list[dict[str, Any]] = []
    case_t0 = time.monotonic()
    memory_dir = tmp_dir / "append-recall" / "memory"
    db_path = tmp_dir / "append-recall" / "search.db"
    memory_dir.mkdir(parents=True, exist_ok=True)

    ks = KnowledgeStore(config=settings, knowledge_db_path=db_path)
    ctx = _make_ctx(memory_dir, knowledge_store=ks)
    base_keyword = f"{_SENTINEL_BASE}-append-base"
    appended_keyword = f"{_SENTINEL_BASE}-append-addendum"

    try:
        t = time.monotonic()
        await save_knowledge(
            ctx,
            content=f"Base content: {base_keyword}.",
            artifact_kind="rule",
            title="append-test",
        )
        slug = _slug_from_ctx(ctx)
        steps.append(
            {
                "name": "save_knowledge",
                "ms": (time.monotonic() - t) * 1000,
                "detail": f"slug={slug}",
            }
        )

        t = time.monotonic()
        await append_knowledge(ctx, slug=slug, content=f"Appended: {appended_keyword}.")
        steps.append(
            {
                "name": "append_knowledge + reindex",
                "ms": (time.monotonic() - t) * 1000,
                "detail": f"appended keyword={appended_keyword!r}",
            }
        )

        t = time.monotonic()
        append_results = await search_memory(ctx, appended_keyword)
        append_count = append_results.metadata.get("count", 0)
        steps.append(
            {
                "name": "search (appended keyword)",
                "ms": (time.monotonic() - t) * 1000,
                "detail": f"count={append_count}",
            }
        )

        t = time.monotonic()
        base_results = await search_memory(ctx, base_keyword)
        base_count = base_results.metadata.get("count", 0)
        steps.append(
            {
                "name": "search (base keyword — must survive append)",
                "ms": (time.monotonic() - t) * 1000,
                "detail": f"count={base_count}",
            }
        )

        if append_count < 1:
            verdict, failure = "FAIL", "appended content not found after reindex"
        elif base_count < 1:
            verdict, failure = "FAIL", "base content lost after append (should be preserved)"
        else:
            verdict, failure = "PASS", None
    finally:
        ks.close()

    return {
        "id": "append-reindex-recall",
        "verdict": verdict,
        "failure": failure,
        "steps": steps,
        "duration_ms": (time.monotonic() - case_t0) * 1000,
    }


async def run_edit_no_db(tmp_dir: Path) -> dict[str, Any]:
    """update_knowledge completes cleanly when knowledge_store=None; no crash, file updated."""
    steps: list[dict[str, Any]] = []
    case_t0 = time.monotonic()
    memory_dir = tmp_dir / "edit-no-db" / "memory"
    db_path = tmp_dir / "edit-no-db" / "search.db"
    memory_dir.mkdir(parents=True, exist_ok=True)

    ks = KnowledgeStore(config=settings, knowledge_db_path=db_path)
    ctx_with_db = _make_ctx(memory_dir, knowledge_store=ks)
    original = f"{_SENTINEL_BASE}-no-db-original"
    updated = f"{_SENTINEL_BASE}-no-db-updated"

    try:
        await save_knowledge(
            ctx_with_db,
            content=f"Content: {original}.",
            artifact_kind="rule",
            title="no-db-test",
        )
    finally:
        ks.close()

    slug = _slug_from_ctx(ctx_with_db)
    steps.append(
        {
            "name": "save_knowledge (with DB, for file creation)",
            "ms": 0,
            "detail": f"slug={slug}",
        }
    )

    ctx_no_db = _make_ctx(memory_dir, knowledge_store=None)

    t = time.monotonic()
    exception_raised = False
    exception_msg = ""
    try:
        await update_knowledge(
            ctx_no_db,
            slug=slug,
            old_content=f"Content: {original}.",
            new_content=f"Content: {updated}.",
        )
    except Exception as exc:
        exception_raised = True
        exception_msg = str(exc)

    steps.append(
        {
            "name": "update_knowledge (knowledge_store=None)",
            "ms": (time.monotonic() - t) * 1000,
            "detail": f"exception={exception_raised}",
        }
    )

    memory_file = next(iter(memory_dir.glob(f"{slug}.md")), None)
    file_content = memory_file.read_text(encoding="utf-8") if memory_file else ""
    file_updated = updated in file_content
    steps.append(
        {
            "name": "filesystem check",
            "ms": 0,
            "detail": f"file_updated={file_updated} contains_new={updated in file_content}",
        }
    )

    if exception_raised:
        verdict, failure = "FAIL", f"update_knowledge raised exception without DB: {exception_msg}"
    elif not file_updated:
        verdict, failure = "FAIL", "file not updated on disk when knowledge_store=None"
    else:
        verdict, failure = "PASS", None

    return {
        "id": "edit-no-db",
        "verdict": verdict,
        "failure": failure,
        "steps": steps,
        "duration_ms": (time.monotonic() - case_t0) * 1000,
    }


# ---------------------------------------------------------------------------
# Phase 3: LLM context recall
# ---------------------------------------------------------------------------


async def run_restored_history_in_context(tmp_dir: Path) -> dict[str, Any]:
    """Restored history is used as context; agent can reference content from prior turns."""
    steps: list[dict[str, Any]] = []
    case_t0 = time.monotonic()
    sessions_dir = tmp_dir / "context-turn" / "sessions"
    sessions_dir.mkdir(parents=True, exist_ok=True)

    prior_history = [
        ModelRequest(
            parts=[UserPromptPart(content=f"My secret project codename is {_SENTINEL}-codename.")]
        ),
        ModelResponse(
            parts=[TextPart(content=f"Got it, your project codename is {_SENTINEL}-codename.")],
            model_name="eval-history",
        ),
    ]

    t = time.monotonic()
    session_path = new_session_path(sessions_dir)
    persist_session_history(
        session_path=session_path,
        sessions_dir=sessions_dir,
        messages=prior_history,
        persisted_message_count=0,
        history_compacted=False,
        reason="eval",
    )
    steps.append(
        {
            "name": "persist prior history",
            "ms": (time.monotonic() - t) * 1000,
            "detail": f"wrote {len(prior_history)} messages",
        }
    )

    t = time.monotonic()
    restored = load_transcript(session_path)
    steps.append(
        {
            "name": "load_transcript",
            "ms": (time.monotonic() - t) * 1000,
            "detail": f"loaded={len(restored)} messages",
        }
    )

    orig_cwd = os.getcwd()
    try:
        os.chdir(tmp_dir / "context-turn")
        deps = make_eval_deps()
        agent = build_agent(config=settings)

        t = time.monotonic()
        async with asyncio.timeout(EVAL_TURN_TIMEOUT_SECS):
            result = await run_turn(
                agent=agent,
                user_input="What codename did I mention earlier?",
                deps=deps,
                message_history=restored,
                frontend=SilentFrontend(),
            )
        turn_ms = (time.monotonic() - t) * 1000
        steps.append(
            {
                "name": "run_turn (with restored history)",
                "ms": turn_ms,
                "detail": "asked about prior codename",
            }
        )

        assistant_text = ""
        for msg in result.messages:
            if isinstance(msg, ModelResponse):
                for part in msg.parts:
                    if isinstance(part, TextPart):
                        assistant_text += part.content

        keyword = f"{_SENTINEL}-codename"
        found_keyword = keyword.lower() in assistant_text.lower()
        steps.append(
            {
                "name": "response analysis",
                "ms": 0,
                "detail": f"sentinel_in_response={found_keyword} preview={assistant_text[:120]!r}",
            }
        )

        if len(restored) != len(prior_history):
            verdict, failure = (
                "FAIL",
                f"history not fully restored: {len(restored)}/{len(prior_history)}",
            )
        elif not found_keyword:
            partial = _SENTINEL.split("-")[0] in assistant_text.lower()
            if partial:
                verdict, failure = "SOFT PASS", None
            else:
                verdict, failure = (
                    "FAIL",
                    "restored history not used as context — sentinel not found in response",
                )
        else:
            verdict, failure = "PASS", None
    finally:
        os.chdir(orig_cwd)

    return {
        "id": "restored-history-in-context",
        "verdict": verdict,
        "failure": failure,
        "steps": steps,
        "duration_ms": (time.monotonic() - case_t0) * 1000,
    }


# ---------------------------------------------------------------------------
# Report writer
# ---------------------------------------------------------------------------


def _write_report(cases: list[dict[str, Any]], total_ms: float) -> None:
    run_ts = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
    passed = sum(1 for c in cases if c["verdict"] in ("PASS", "SOFT PASS"))

    lines: list[str] = [
        f"## Run: {run_ts}",
        "",
        f"**Model:** {settings.llm.provider} / {settings.llm.model or 'default'}  ",
        f"**Total runtime:** {total_ms:.0f}ms  ",
        f"**Result:** {passed}/{len(cases)} passed",
        "",
        "### Summary",
        "",
        "| Case | Verdict | Duration |",
        "|------|---------|----------|",
    ]
    for c in cases:
        lines.append(f"| `{c['id']}` | {c['verdict']} | {c['duration_ms']:.0f}ms |")

    lines += ["", "### Step Traces", ""]
    for c in cases:
        lines.append(f"#### `{c['id']}` — {c['verdict']}")
        for step in c["steps"]:
            lines.append(f"- **{step['name']}** ({step['ms']:.0f}ms): {step['detail']}")
        if c.get("failure"):
            lines.append(f"- **Failure:** {c['failure']}")
        lines.append("")

    lines += ["---", ""]
    section = "\n".join(lines)

    if _REPORT_PATH.exists():
        existing = _REPORT_PATH.read_text(encoding="utf-8")
        split = existing.split("\n", 2)
        updated = split[0] + "\n\n" + section + ("\n".join(split[1:]) if len(split) > 1 else "")
    else:
        updated = "# Eval Report: Memory\n\n" + section

    _REPORT_PATH.write_text(updated, encoding="utf-8")
    print(f"\n  Report → {_REPORT_PATH.relative_to(Path.cwd())}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


async def main() -> int:
    print("=" * 60)
    print("  Eval: Memory")
    print("=" * 60)

    all_cases: list[dict[str, Any]] = []
    t0 = time.monotonic()

    phase1_runners = [
        ("persist-load-roundtrip", run_persist_load_roundtrip),
        ("incremental-append", run_incremental_append),
        ("empty-history-new-session", run_empty_history_new_session),
    ]
    phase2_runners = [
        ("save-recall", run_save_recall),
        ("update-reindex-recall", run_update_reindex_recall),
        ("append-reindex-recall", run_append_reindex_recall),
        ("edit-no-db", run_edit_no_db),
    ]
    phase3_runners = [
        ("restored-history-in-context", run_restored_history_in_context),
    ]

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)

        print("\n  Phase 1: Session transcript persistence (no LLM)")
        print("  " + "-" * 44)
        for label, fn in phase1_runners:
            print(f"    [{label}]", end=" ", flush=True)
            try:
                result = await fn(tmp_path)
            except Exception as exc:
                result = {
                    "id": label,
                    "verdict": "ERROR",
                    "failure": str(exc),
                    "steps": [],
                    "duration_ms": 0,
                }
            all_cases.append(result)
            print(f"{result['verdict']} ({result['duration_ms']:.0f}ms)")
            if result.get("failure"):
                print(f"      → {result['failure']}")

        print("\n  Phase 2: Knowledge edit / FTS5 recall (no LLM)")
        print("  " + "-" * 44)
        for label, fn in phase2_runners:
            print(f"    [{label}]", end=" ", flush=True)
            try:
                result = await fn(tmp_path)
            except Exception as exc:
                result = {
                    "id": label,
                    "verdict": "ERROR",
                    "failure": str(exc),
                    "steps": [],
                    "duration_ms": 0,
                }
            all_cases.append(result)
            print(f"{result['verdict']} ({result['duration_ms']:.0f}ms)")
            if result.get("failure"):
                print(f"      → {result['failure']}")

        print("\n  Phase 3: LLM context recall")
        print("  " + "-" * 44)
        for label, fn in phase3_runners:
            print(f"    [{label}]", end=" ", flush=True)
            try:
                result = await fn(tmp_path)
            except Exception as exc:
                result = {
                    "id": label,
                    "verdict": "ERROR",
                    "failure": str(exc),
                    "steps": [],
                    "duration_ms": 0,
                }
            all_cases.append(result)
            print(f"{result['verdict']} ({result['duration_ms']:.0f}ms)")
            if result.get("failure"):
                print(f"      → {result['failure']}")

    total_ms = (time.monotonic() - t0) * 1000
    passed = sum(1 for c in all_cases if c["verdict"] in ("PASS", "SOFT PASS"))
    _write_report(all_cases, total_ms)

    print(f"\n{'=' * 60}")
    verdict = "PASS" if passed == len(all_cases) else "FAIL"
    print(f"  Verdict: {verdict} ({passed}/{len(all_cases)} cases, {total_ms:.0f}ms)")
    print(f"{'=' * 60}")
    return 0 if passed == len(all_cases) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
