#!/usr/bin/env python3
"""Eval: canon-channel correctness — FTS-based recall over character memory files.

Canon recall now uses the unified FTS5 pipeline (source='canon' in MemoryStore).
Eval verifies the FTS-based path end-to-end against the real tars soul.

Sub-cases:
  canon-content         — query terms hit a known canon body; top hit's body carries query token
  canon-fts-match       — BM25 result score > 0.0 for a matching query
  canon-top-hit-relevant — top hit title matches query keyword (relevance sanity)
  bleed                 — query with no canon-relevant tokens produces zero canon hits
  negative-no-canon     — personality=None -> no canon channel rendered

Outputs: prepends a dated section to docs/REPORT-eval-canon-recall.md.

Prerequisites: LLM provider configured (memory_search calls the agent which needs a model).

Usage:
    uv run python evals/eval_canon_recall.py
"""

from __future__ import annotations

import asyncio
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic_ai import RunContext
from pydantic_ai.usage import RunUsage

from co_cli.bootstrap.core import _sync_canon_store
from co_cli.config.core import settings
from co_cli.deps import CoDeps, CoSessionState
from co_cli.llm.factory import build_model
from co_cli.memory.memory_store import MemoryStore
from co_cli.tools.memory.recall import memory_search
from co_cli.tools.shell_backend import ShellBackend

_REPORT_PATH = Path(__file__).parent.parent / "docs" / "REPORT-eval-canon-recall.md"


class _SilentFrontend:
    def on_status(self, msg: str) -> None:
        pass


def _make_ctx_with_store(tmp: Path, *, personality: str | None) -> RunContext:
    """Build a RunContext with a real MemoryStore and canon indexed from real tars soul.

    Used for all sub-cases that exercise canon recall.
    """
    cfg = settings.model_copy(update={"personality": personality})
    store_cfg = cfg.model_copy(
        update={
            "knowledge": cfg.knowledge.model_copy(
                update={
                    "search_backend": "fts5",
                    "embedding_provider": "none",
                    "cross_encoder_reranker_url": None,
                }
            )
        }
    )
    store = MemoryStore(config=store_cfg, memory_db_path=tmp / "search.db")
    if personality:
        _sync_canon_store(store, cfg, _SilentFrontend())
    llm_model = build_model(cfg.llm)
    deps = CoDeps(
        shell=ShellBackend(),
        config=cfg,
        session=CoSessionState(),
        memory_store=store,
        sessions_dir=tmp / "sessions",
        knowledge_dir=tmp / "knowledge",
        model=llm_model,
    )
    return RunContext(deps=deps, model=llm_model.model, usage=RunUsage())


def _make_ctx(tmp: Path, *, personality: str | None) -> RunContext:
    """Build a RunContext with no MemoryStore (grep backend). Used for negative-no-canon."""
    cfg = settings.model_copy(update={"personality": personality})
    llm_model = build_model(cfg.llm)
    deps = CoDeps(
        shell=ShellBackend(),
        config=cfg,
        session=CoSessionState(),
        memory_store=None,
        sessions_dir=tmp / "sessions",
        knowledge_dir=tmp / "knowledge",
        model=llm_model,
    )
    return RunContext(deps=deps, model=llm_model.model, usage=RunUsage())


async def run_eval(tmp: Path) -> dict[str, Any]:
    failures: list[str] = []
    ctx_tars = _make_ctx_with_store(tmp, personality="tars")

    # Sub-case 1 — canon content: query hits a known canon body, top hit body carries token.
    # The query "humor deadpan" maps to tars-humor-is-tactical-front-loaded-delivered-flat
    # via body-token matches (both "humor" and "deadpan" appear in the body text).
    result = await memory_search(ctx_tars, "humor deadpan")
    rendered = result.return_value
    canon_hits = [r for r in result.metadata["results"] if r["channel"] == "canon"]

    if not canon_hits:
        failures.append("canon-content: no hit for tars humor query")
        print("  [canon-content] FAIL - no hit")
    elif "**Character canon:**" not in rendered:
        failures.append("canon-content: '**Character canon:**' header missing despite hits")
        print("  [canon-content] FAIL - header missing")
    elif "humor" not in (canon_hits[0].get("body") or "").lower():
        failures.append("canon-content: top body lacks 'humor' - frontmatter strip regression?")
        print("  [canon-content] FAIL - body lacks 'humor'")
    else:
        print(
            f"  [canon-content] PASS - top hit '{canon_hits[0]['title'][:40]}' body carries 'humor'"
        )

    # Sub-case 2 — FTS match: BM25 result score > 0.0 for a matching query.
    # BM25 scores from MemoryStore are normalized to (0, 1] via normalize_bm25().
    if canon_hits:
        below_zero = [h for h in canon_hits if h["score"] <= 0.0]
        if below_zero:
            failures.append(
                f"canon-fts-match: {len(below_zero)} hit(s) with score <= 0.0: "
                f"{[(h['title'][:40], h['score']) for h in below_zero]}"
            )
            print(f"  [canon-fts-match] FAIL - {len(below_zero)} hits with score <= 0.0")
        else:
            min_score = min(h["score"] for h in canon_hits)
            print(
                f"  [canon-fts-match] PASS - all {len(canon_hits)} hits score > 0.0 (min={min_score:.4f})"
            )
    else:
        print("  [canon-fts-match] SKIP - no hits to check")

    # Sub-case 3 — top hit relevance: top hit title matches query keyword.
    # Relevance sanity: the highest-ranked result should relate to the query topic.
    if canon_hits:
        top_title = (canon_hits[0].get("title") or "").lower()
        if "humor" not in top_title:
            failures.append(
                f"canon-top-hit-relevant: top hit '{canon_hits[0]['title'][:40]}' "
                f"title does not contain 'humor'"
            )
            print(f"  [canon-top-hit-relevant] FAIL - top title lacks 'humor': {top_title[:50]}")
        else:
            print(
                f"  [canon-top-hit-relevant] PASS - top hit title contains 'humor': {top_title[:50]}"
            )
    else:
        print("  [canon-top-hit-relevant] SKIP - no hits to check")

    # Sub-case 4 — bleed: query has no canon-relevant tokens. Should produce zero canon hits.
    bleed_query = "json parse exception traceback"
    bleed_result = await memory_search(ctx_tars, bleed_query)
    bleed_canon = [r for r in bleed_result.metadata["results"] if r["channel"] == "canon"]
    if bleed_canon:
        leaked = [(h["title"][:40], h["score"]) for h in bleed_canon]
        failures.append(
            f"bleed: '{bleed_query}' produced {len(bleed_canon)} canon hit(s) {leaked}"
        )
        print(f"  [bleed] FAIL - {leaked}")
    elif "**Character canon:**" in bleed_result.return_value:
        failures.append(f"bleed: header rendered for '{bleed_query}' despite zero canon hits")
        print("  [bleed] FAIL - header rendered without hits")
    else:
        print(f"  [bleed] PASS - '{bleed_query}' produced zero canon hits")

    # Sub-case 5 — negative no-canon: personality=None -> memory_store=None -> no header.
    ctx_no_personality = _make_ctx(tmp, personality=None)
    neg_result = await memory_search(ctx_no_personality, "humor")
    if "**Character canon:**" in neg_result.return_value:
        failures.append(
            "negative-no-canon: header rendered when personality=None - render conditional broken"
        )
        print("  [negative-no-canon] FAIL - header rendered without canon hits")
    else:
        print("  [negative-no-canon] PASS - no header when personality=None")

    # Clean up the store opened in _make_ctx_with_store
    if ctx_tars.deps.memory_store is not None:
        ctx_tars.deps.memory_store.close()

    verdict = "PASS" if not failures else "FAIL"
    print(f"\nVerdict: {verdict}" + ("" if not failures else f" - {len(failures)} failures"))
    return {"verdict": verdict, "failures": failures}


def _write_report(result: dict[str, Any], total_ms: float) -> None:
    run_ts = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
    lines: list[str] = [
        f"## Run: {run_ts}",
        "",
        f"**Total runtime:** {total_ms:.0f}ms ({total_ms / 1000:.1f}s)  ",
        f"**Verdict:** {result['verdict']}",
        "",
    ]
    if result.get("failures"):
        lines += ["### Failures", ""]
        for f in result["failures"]:
            lines.append(f"- {f}")
        lines.append("")
    lines += ["---", ""]
    section = "\n".join(lines)

    if _REPORT_PATH.exists():
        existing = _REPORT_PATH.read_text(encoding="utf-8")
        head, sep, body = existing.partition("\n")
        updated = head + sep + "\n" + section + body
    else:
        updated = "# Eval Report: canon-channel correctness\n\n" + section

    _REPORT_PATH.write_text(updated, encoding="utf-8")
    print(f"\n  Report -> {_REPORT_PATH.relative_to(Path(__file__).parent.parent)}")


async def main() -> int:
    print("=" * 60)
    print("  Eval: canon-channel correctness")
    print("=" * 60)
    t0 = time.monotonic()
    with tempfile.TemporaryDirectory() as tmpdir:
        result = await run_eval(Path(tmpdir))
    total_ms = (time.monotonic() - t0) * 1000
    _write_report(result, total_ms)
    print("=" * 60)
    return 0 if result["verdict"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
