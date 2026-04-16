#!/usr/bin/env python3
"""Eval: article fetch flow — save → FTS5 index → search → read + URL consolidation.

Validates the full article pipeline without an LLM turn:
    save_article (new)       → DB index + chunk index → search_articles (FTS5) finds it
    save_article (same URL)  → consolidation → 1 file, tags merged, action="consolidated"
    read_article             → full body returned by slug
    search_articles          → grep fallback when knowledge_store=None

These cover the storage and retrieval path that evals/eval_reranker_comparison.py
skips (synthetic corpus, no article-specific write path tested).

Writes: docs/REPORT-eval-article-fetch-flow.md (prepends dated section each run).

Prerequisites: none (no LLM calls — pure DB/filesystem).

Usage:
    uv run python evals/eval_article_fetch_flow.py
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
from co_cli.tools.articles import read_article, save_article, search_articles
from co_cli.tools.shell_backend import ShellBackend

_REPORT_PATH = Path(__file__).parent.parent / "docs" / "REPORT-eval-article-fetch-flow.md"
_AGENT = build_agent(config=settings)

# Sentinel keyword with no false-positive risk in any real knowledge base
_SENTINEL = "zyxwquartz-eval-article-fetch-unique"


def _make_ctx(
    library_dir: Path,
    *,
    knowledge_store: KnowledgeStore | None = None,
    search_backend: str = "fts5",
) -> RunContext:
    cfg = get_settings()
    if cfg.knowledge.search_backend != search_backend:
        cfg = cfg.model_copy(
            update={
                "knowledge": cfg.knowledge.model_copy(update={"search_backend": search_backend})
            }
        )
    deps = CoDeps(
        shell=ShellBackend(),
        knowledge_store=knowledge_store,
        config=cfg,
        session=CoSessionState(),
    )
    deps.knowledge_dir = library_dir
    return RunContext(deps=deps, model=_AGENT.model, usage=RunUsage())


# ---------------------------------------------------------------------------
# Cases
# ---------------------------------------------------------------------------


async def run_fts5_save_search(tmp_dir: Path) -> dict[str, Any]:
    """save_article indexes into FTS5; search_articles finds it by unique keyword."""
    steps: list[dict[str, Any]] = []
    case_t0 = time.monotonic()
    library_dir = tmp_dir / "fts5-save-search" / "library"
    db_path = tmp_dir / "fts5-save-search" / "search.db"
    library_dir.mkdir(parents=True, exist_ok=True)

    ks = KnowledgeStore(config=settings, knowledge_db_path=db_path)
    ctx = _make_ctx(library_dir, knowledge_store=ks, search_backend="fts5")

    try:
        t = time.monotonic()
        save_result = await save_article(
            ctx,
            content=f"This article covers {_SENTINEL} in depth.",
            title="FTS5 Search Test",
            origin_url="https://example.com/fts5-test",
            tags=["eval", "fts5"],
        )
        steps.append(
            {
                "name": "save_article",
                "ms": (time.monotonic() - t) * 1000,
                "detail": f"action={save_result.metadata.get('action')} id={save_result.metadata.get('article_id', '')[:8]}",
            }
        )

        t = time.monotonic()
        search_result = await search_articles(ctx, _SENTINEL)
        found_count = search_result.metadata.get("count", 0)
        found_results = search_result.metadata.get("results", [])
        steps.append(
            {
                "name": "search_articles (FTS5)",
                "ms": (time.monotonic() - t) * 1000,
                "detail": f"count={found_count} results={[r.get('title') for r in found_results]}",
            }
        )

        # Direct DB search as secondary check
        t = time.monotonic()
        db_results = ks.search(_SENTINEL, source="library", kind="article", limit=5)
        steps.append(
            {
                "name": "KnowledgeStore.search direct",
                "ms": (time.monotonic() - t) * 1000,
                "detail": f"db_count={len(db_results)}",
            }
        )

        if save_result.metadata.get("action") != "saved":
            verdict, failure = (
                "FAIL",
                f"expected action='saved', got {save_result.metadata.get('action')!r}",
            )
        elif found_count < 1:
            verdict, failure = "FAIL", "search_articles returned 0 results after FTS5 save"
        elif len(db_results) < 1:
            verdict, failure = "FAIL", "KnowledgeStore.search found 0 results after index"
        else:
            verdict, failure = "PASS", None
    finally:
        ks.close()

    return {
        "id": "fts5-save-search",
        "verdict": verdict,
        "failure": failure,
        "steps": steps,
        "duration_ms": (time.monotonic() - case_t0) * 1000,
    }


async def run_url_consolidation(tmp_dir: Path) -> dict[str, Any]:
    """Saving the same origin_url twice consolidates (not duplicates) and merges tags."""
    steps: list[dict[str, Any]] = []
    case_t0 = time.monotonic()
    library_dir = tmp_dir / "consolidation" / "library"
    db_path = tmp_dir / "consolidation" / "search.db"
    library_dir.mkdir(parents=True, exist_ok=True)

    ks = KnowledgeStore(config=settings, knowledge_db_path=db_path)
    ctx = _make_ctx(library_dir, knowledge_store=ks)
    url = f"https://example.com/{_SENTINEL}-consolidation"

    try:
        t = time.monotonic()
        result1 = await save_article(
            ctx,
            content="Version 1 content.",
            title="Consolidation Test",
            origin_url=url,
            tags=["tagA"],
        )
        steps.append(
            {
                "name": "save_article (first)",
                "ms": (time.monotonic() - t) * 1000,
                "detail": f"action={result1.metadata.get('action')}",
            }
        )

        t = time.monotonic()
        result2 = await save_article(
            ctx,
            content="Version 2 content — updated.",
            title="Consolidation Test Updated",
            origin_url=url,
            tags=["tagB"],
        )
        steps.append(
            {
                "name": "save_article (second, same URL)",
                "ms": (time.monotonic() - t) * 1000,
                "detail": f"action={result2.metadata.get('action')}",
            }
        )

        files = list(library_dir.glob("*.md"))
        file_count = len(files)
        raw = files[0].read_text(encoding="utf-8") if files else ""
        import yaml as _yaml

        fm = _yaml.safe_load(raw.split("---")[1]) if "---" in raw else {}
        merged_tags = fm.get("tags", [])
        steps.append(
            {
                "name": "filesystem check",
                "ms": 0,
                "detail": f"file_count={file_count} merged_tags={merged_tags}",
            }
        )

        if result1.metadata.get("action") != "saved":
            verdict, failure = (
                "FAIL",
                f"first save: expected 'saved', got {result1.metadata.get('action')!r}",
            )
        elif result2.metadata.get("action") != "consolidated":
            verdict, failure = (
                "FAIL",
                f"second save: expected 'consolidated', got {result2.metadata.get('action')!r}",
            )
        elif file_count != 1:
            verdict, failure = "FAIL", f"expected 1 file after consolidation, found {file_count}"
        elif "tagA" not in merged_tags or "tagB" not in merged_tags:
            verdict, failure = "FAIL", f"tags not merged: {merged_tags}"
        elif "Version 2" not in raw:
            verdict, failure = "FAIL", "consolidated file missing new content"
        else:
            verdict, failure = "PASS", None
    finally:
        ks.close()

    return {
        "id": "url-consolidation",
        "verdict": verdict,
        "failure": failure,
        "steps": steps,
        "duration_ms": (time.monotonic() - case_t0) * 1000,
    }


async def run_read_full_body(tmp_dir: Path) -> dict[str, Any]:
    """search_articles returns slug; read_article returns full body and metadata."""
    steps: list[dict[str, Any]] = []
    case_t0 = time.monotonic()
    library_dir = tmp_dir / "read-full-body" / "library"
    library_dir.mkdir(parents=True, exist_ok=True)
    # Use grep path (no KnowledgeStore needed for read_article)
    ctx = _make_ctx(library_dir, search_backend="grep")
    body = f"Full body: {_SENTINEL}-read-body-marker.\n\nSecond paragraph here."

    t = time.monotonic()
    save_result = await save_article(
        ctx,
        content=body,
        title="Read Body Test",
        origin_url=f"https://example.com/{_SENTINEL}-read",
        tags=["eval"],
    )
    steps.append(
        {
            "name": "save_article",
            "ms": (time.monotonic() - t) * 1000,
            "detail": f"action={save_result.metadata.get('action')}",
        }
    )

    slug = next(iter(library_dir.glob("*.md"))).stem

    t = time.monotonic()
    read_result = await read_article(ctx, slug)
    steps.append(
        {
            "name": "read_article",
            "ms": (time.monotonic() - t) * 1000,
            "detail": f"title={read_result.metadata.get('title')!r} content_len={len(read_result.metadata.get('content') or '')}",
        }
    )

    content = read_result.metadata.get("content") or ""
    if read_result.metadata.get("article_id") is None:
        verdict, failure = "FAIL", "article_id is None (article not found)"
    elif read_result.metadata.get("title") != "Read Body Test":
        verdict, failure = "FAIL", f"title mismatch: {read_result.metadata.get('title')!r}"
    elif f"{_SENTINEL}-read-body-marker" not in content:
        verdict, failure = "FAIL", "sentinel keyword missing from full body"
    elif "Second paragraph" not in content:
        verdict, failure = "FAIL", "second paragraph missing from full body"
    else:
        verdict, failure = "PASS", None

    return {
        "id": "read-full-body",
        "verdict": verdict,
        "failure": failure,
        "steps": steps,
        "duration_ms": (time.monotonic() - case_t0) * 1000,
    }


async def run_grep_fallback(tmp_dir: Path) -> dict[str, Any]:
    """search_articles falls back to grep when knowledge_store=None."""
    steps: list[dict[str, Any]] = []
    case_t0 = time.monotonic()
    library_dir = tmp_dir / "grep-fallback" / "library"
    library_dir.mkdir(parents=True, exist_ok=True)

    # Save with no KnowledgeStore (grep path)
    ctx = _make_ctx(library_dir, knowledge_store=None, search_backend="grep")

    t = time.monotonic()
    await save_article(
        ctx,
        content=f"Grep fallback: {_SENTINEL}-grep-marker content.",
        title="Grep Fallback Test",
        origin_url=f"https://example.com/{_SENTINEL}-grep",
        tags=["eval"],
    )
    steps.append(
        {
            "name": "save_article (no KnowledgeStore)",
            "ms": (time.monotonic() - t) * 1000,
            "detail": "knowledge_store=None",
        }
    )

    t = time.monotonic()
    search_result = await search_articles(ctx, f"{_SENTINEL}-grep-marker")
    found_count = search_result.metadata.get("count", 0)
    steps.append(
        {
            "name": "search_articles (grep fallback)",
            "ms": (time.monotonic() - t) * 1000,
            "detail": f"count={found_count}",
        }
    )

    if found_count < 1:
        verdict, failure = "FAIL", "grep fallback returned 0 results"
    else:
        verdict, failure = "PASS", None

    return {
        "id": "grep-fallback",
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
        updated = "# Eval Report: Article Fetch Flow\n\n" + section

    _REPORT_PATH.write_text(updated, encoding="utf-8")
    print(f"\n  Report → {_REPORT_PATH.relative_to(Path.cwd())}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


async def main() -> int:
    print("=" * 60)
    print("  Eval: Article Fetch Flow (FTS5/grep, no LLM)")
    print("=" * 60)

    all_cases: list[dict[str, Any]] = []
    t0 = time.monotonic()

    runners = [
        ("fts5-save-search", run_fts5_save_search, True),
        ("url-consolidation", run_url_consolidation, True),
        ("read-full-body", run_read_full_body, True),
        ("grep-fallback", run_grep_fallback, True),
    ]

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        for label, fn, needs_tmp in runners:
            print(f"\n  [{label}]", end=" ", flush=True)
            try:
                result = await (fn(tmp_path) if needs_tmp else fn())
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
