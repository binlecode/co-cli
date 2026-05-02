#!/usr/bin/env python3
"""Eval: canon-channel correctness — what evals/eval_memory_recall_agent.py can't reach.

Canon-only scope: scoring algorithm properties (score floor, rank dominance),
negative properties (bleed, no-personality), and channel-content correctness
that the agent eval doesn't exercise (no canon load-bearing fixture).

Sub-cases:
  canon-content     — query terms hit a known canon body; top hit's body carries query token
  canon-score-floor — every admitted hit scores >= 2 (one title-token OR two body-token matches)
  canon-rank-quality — top-1 score >= 2x hit-2 (clear winner, not a near-tie)
  bleed             — query with no canon-relevant tokens produces zero canon hits
  negative-no-canon — personality=None -> no canon channel rendered

Outputs: prepends a dated section to docs/REPORT-eval-canon-recall.md.

Prerequisites: LLM provider configured (settings load needs it; canon scoring is local).

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

from co_cli.agent.core import build_agent
from co_cli.config.core import settings
from co_cli.deps import CoDeps, CoSessionState
from co_cli.llm.factory import build_model
from co_cli.tools.memory.canon_recall import search_canon
from co_cli.tools.memory.recall import memory_search
from co_cli.tools.shell_backend import ShellBackend

_REPORT_PATH = Path(__file__).parent.parent / "docs" / "REPORT-eval-canon-recall.md"

_AGENT = build_agent(config=settings)
# CoDeps.model expects LlmModel (co-cli wrapper with .model/.settings/.context_window),
# not a raw pydantic-ai model. Real sessions get this via bootstrap; evals must match.
_LLM_MODEL = build_model(settings.llm)


def _make_ctx(tmp: Path, *, personality: str | None) -> RunContext:
    """Build a RunContext with explicit personality. Empty knowledge/session stores
    so memory_search's other channels return [] cleanly without seeding overhead."""
    cfg = settings.model_copy(update={"personality": personality})
    deps = CoDeps(
        shell=ShellBackend(),
        memory_store=None,
        session_store=None,
        model=_LLM_MODEL,
        config=cfg,
        session=CoSessionState(),
    )
    deps.knowledge_dir = tmp / "knowledge"
    deps.sessions_dir = tmp / "sessions"
    deps.knowledge_dir.mkdir(parents=True, exist_ok=True)
    deps.sessions_dir.mkdir(parents=True, exist_ok=True)
    return RunContext(deps=deps, model=_LLM_MODEL.model, usage=RunUsage())


async def run_eval(tmp: Path) -> dict[str, Any]:
    failures: list[str] = []
    ctx_tars = _make_ctx(tmp, personality="tars")

    # Sub-case 1 — canon content: query hits a known canon body, top hit body carries token.
    # The query "humor flat tactical" maps to tars-humor-is-tactical-front-loaded-delivered-flat
    # via three title-token hits (2x weight) + body matches.
    result = await memory_search(ctx_tars, "humor flat tactical")
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

    # Sub-case 2 — score floor: every admitted hit scores >= 2.
    # Score 1 is one incidental body-token overlap — pure noise that pads M and dilutes
    # the model's view. Without a floor, search_canon returns whatever scored > 0.
    if canon_hits:
        below_floor = [(h["title"][:40], h["score"]) for h in canon_hits if h["score"] < 2]
        if below_floor:
            failures.append(
                f"canon-score-floor: {len(below_floor)} admitted hit(s) below floor of 2: {below_floor}"
            )
            print(f"  [canon-score-floor] FAIL - {below_floor}")
        else:
            print("  [canon-score-floor] PASS - all admitted hits score >= 2")

    # Sub-case 3 — rank quality: top-1 dominates hit-2 by >= 2x.
    # Without a clear winner, the model receives a near-tie of canon scenes and has no
    # signal which is on-topic. 2x is the smallest gap that signals real ranking.
    if len(canon_hits) >= 2:
        top_score = canon_hits[0]["score"]
        next_score = canon_hits[1]["score"]
        if top_score < 2 * next_score:
            failures.append(f"canon-rank-quality: top={top_score} not 2x over hit-2={next_score}")
            print(f"  [canon-rank-quality] FAIL - top={top_score} hit2={next_score}")
        else:
            gap = top_score / max(next_score, 1)
            print(
                f"  [canon-rank-quality] PASS - top={top_score} hit2={next_score} ({gap:.1f}x gap)"
            )
    elif canon_hits:
        print(f"  [canon-rank-quality] PASS (single hit, score={canon_hits[0]['score']})")

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

    # Sub-case 5 — negative no-canon: personality=None -> search_canon returns [] -> no header.
    ctx_no_personality = _make_ctx(tmp, personality=None)
    neg_result = await memory_search(ctx_no_personality, "humor")
    if "**Character canon:**" in neg_result.return_value:
        failures.append(
            "negative-no-canon: header rendered when personality=None - render conditional broken"
        )
        print("  [negative-no-canon] FAIL - header rendered without canon hits")
    else:
        print("  [negative-no-canon] PASS - no header when personality=None")

    # Direct algorithm sanity: full search_canon dump for context in the report.
    direct_hits = search_canon("humor flat tactical", role="tars", limit=10)
    direct_below = [(h["title"][:40], h["score"]) for h in direct_hits if h["score"] < 2]
    if direct_below:
        print(
            f"  [direct-search-canon] {len(direct_hits)} hits, {len(direct_below)} below floor: {direct_below}"
        )
    else:
        print(f"  [direct-search-canon] {len(direct_hits)} hits, all >= floor")

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
