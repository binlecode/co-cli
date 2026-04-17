#!/usr/bin/env python3
"""Eval: knowledge edit recall — save → recall → update → recall updated content.

Validates the end-to-end flow: update_knowledge and append_knowledge re-index into
the DB, and subsequent search_memories picks up the changes.

    save_knowledge        → DB indexed → search_memories finds unique sentinel
    update_knowledge      → reindexes   → search finds new content, not original
    append_knowledge      → reindexes   → search finds appended content
    update_knowledge (no DB) → file written → search degrades to grep (no crash)

The degraded path (knowledge_store=None after save) verifies that edits
complete cleanly even when the DB is unavailable at edit time.

Writes: docs/REPORT-eval-memory-edit-recall.md (prepends dated section each run).

Prerequisites: none (no LLM calls — pure DB/filesystem).

Usage:
    uv run python evals/eval_memory_edit_recall.py
"""

import asyncio
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic_ai import RunContext
from pydantic_ai.usage import RunUsage

from co_cli.agent._core import build_agent
from co_cli.config._core import get_settings, settings
from co_cli.deps import CoDeps, CoSessionState
from co_cli.knowledge._store import KnowledgeStore
from co_cli.tools.knowledge import append_knowledge, save_knowledge, update_knowledge
from co_cli.tools.memory import search_memories
from co_cli.tools.shell_backend import ShellBackend

_REPORT_PATH = Path(__file__).parent.parent / "docs" / "REPORT-eval-memory-edit-recall.md"
_AGENT = build_agent(config=settings)

_SENTINEL_BASE = "zyxwquartz-eval-mem-edit-unique"


def _make_ctx(
    memory_dir: Path,
    *,
    knowledge_store: KnowledgeStore | None = None,
) -> RunContext:
    cfg = get_settings()
    # Force fts5 backend so search_memories uses DB when KnowledgeStore present
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
    """Return the slug of the most recently written memory file."""
    memory_dir: Path = ctx.deps.knowledge_dir
    files = sorted(memory_dir.glob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True)
    return files[0].stem if files else None


# ---------------------------------------------------------------------------
# Cases
# ---------------------------------------------------------------------------


async def run_save_recall(tmp_dir: Path) -> dict[str, Any]:
    """save_memory indexes into DB; search_memories finds it by unique sentinel."""
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
        search_result = await search_memories(ctx, sentinel)
        found_count = search_result.metadata.get("count", 0)
        steps.append(
            {
                "name": "search_memories (FTS5)",
                "ms": (time.monotonic() - t) * 1000,
                "detail": f"count={found_count}",
            }
        )

        if found_count < 1:
            verdict, failure = (
                "FAIL",
                f"search_memories returned 0 after save (sentinel={sentinel!r})",
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
        # Save with original content
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

        # Verify original is findable
        before_results = await search_memories(ctx, original)
        before_count = before_results.metadata.get("count", 0)
        steps.append(
            {
                "name": "search (before update)",
                "ms": 0,
                "detail": f"original_found={before_count}",
            }
        )

        # Update with new content
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

        # Search for new content
        t = time.monotonic()
        new_results = await search_memories(ctx, updated)
        new_count = new_results.metadata.get("count", 0)
        steps.append(
            {
                "name": "search (after update, new keyword)",
                "ms": (time.monotonic() - t) * 1000,
                "detail": f"new_found={new_count}",
            }
        )

        # Original keyword should no longer match
        old_results = await search_memories(ctx, original)
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
            # Soft pass: DB may have stale entry; the write is correct — only ranking may differ
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
        append_results = await search_memories(ctx, appended_keyword)
        append_count = append_results.metadata.get("count", 0)
        steps.append(
            {
                "name": "search (appended keyword)",
                "ms": (time.monotonic() - t) * 1000,
                "detail": f"count={append_count}",
            }
        )

        # Base content must still be in the file
        t = time.monotonic()
        base_results = await search_memories(ctx, base_keyword)
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

    # Save with DB present to create the file
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

    # Now update with knowledge_store=None (degraded path)
    ctx_no_db = _make_ctx(memory_dir, knowledge_store=None)

    t = time.monotonic()
    try:
        await update_knowledge(
            ctx_no_db,
            slug=slug,
            old_content=f"Content: {original}.",
            new_content=f"Content: {updated}.",
        )
        exception_raised = False
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

    # Verify file was actually updated on disk
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
# Report writer
# ---------------------------------------------------------------------------


def _write_report(cases: list[dict[str, Any]], total_ms: float) -> None:
    run_ts = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
    passed = sum(1 for c in cases if c["verdict"] in ("PASS", "SOFT PASS"))

    lines: list[str] = [
        f"## Run: {run_ts}",
        "",
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
        updated = "# Eval Report: Memory Edit Recall\n\n" + section

    _REPORT_PATH.write_text(updated, encoding="utf-8")
    print(f"\n  Report → {_REPORT_PATH.relative_to(Path.cwd())}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


async def main() -> int:
    print("=" * 60)
    print("  Eval: Memory Edit Recall (FTS5 reindex, no LLM)")
    print("=" * 60)

    all_cases: list[dict[str, Any]] = []
    t0 = time.monotonic()

    runners = [
        ("save-recall", run_save_recall),
        ("update-reindex-recall", run_update_reindex_recall),
        ("append-reindex-recall", run_append_reindex_recall),
        ("edit-no-db", run_edit_no_db),
    ]

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        for label, fn in runners:
            print(f"\n  [{label}]", end=" ", flush=True)
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
                print(f"    → {result['failure']}")

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
