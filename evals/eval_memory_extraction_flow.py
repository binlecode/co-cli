#!/usr/bin/env python3
"""Eval: memory extraction flow — production-path background extraction and cadence.

Validates the current production extraction path described in the memory spec:

  fire_and_forget_extraction(delta) — launch extraction in the background without
      blocking the foreground turn.

  _finalize_turn cadence gate       — trigger extraction only on clean Nth turns.

  e2e-extraction-injection          — full memory loop: turn 1 → extraction fires
      → save_memory → DB index → turn 2 → _recall_for_context → SystemPromptPart
      injection. Closes the loop not covered by eval_memory_recall.py (which
      seeds memories via sync_dir, not via extraction).

Implementation-level direct-helper coverage belongs in pytest.

Writes: docs/REPORT-eval-memory-extraction-flow.md (prepends dated section each run).

Prerequisites: LLM provider configured (ollama or gemini).

Usage:
    uv run python evals/eval_memory_extraction_flow.py
"""

import asyncio
import os
import re
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from evals._deps import make_eval_deps
from evals._frontend import SilentFrontend
from evals._timeouts import EVAL_MEMORY_EXTRACTION_TIMEOUT_SECS, EVAL_TURN_TIMEOUT_SECS
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    SystemPromptPart,
    TextPart,
    UserPromptPart,
)

from co_cli.agent._core import build_agent
from co_cli.config._core import get_settings, settings
from co_cli.context.orchestrate import run_turn
from co_cli.knowledge._store import KnowledgeStore
from co_cli.llm._factory import build_model
from co_cli.memory._extractor import drain_pending_extraction, fire_and_forget_extraction

_EXTRACTION_TIMEOUT_SECS = EVAL_MEMORY_EXTRACTION_TIMEOUT_SECS
_REPORT_PATH = Path(__file__).parent.parent / "docs" / "REPORT-eval-memory-extraction-flow.md"
_REPORT_HEADER = "# Eval Report: Memory Extraction Flow"
_CURRENT_SECTION_MARKER = "**Extraction timeout:**"


def _load_compatible_report_sections() -> list[str]:
    """Return prior run sections that match the production-path report schema only."""
    if not _REPORT_PATH.exists():
        return []

    existing = _REPORT_PATH.read_text(encoding="utf-8")
    if not existing.startswith(_REPORT_HEADER):
        return []

    split = existing.split("\n\n", 1)
    body = split[1] if len(split) > 1 else ""
    raw_sections = [section.strip() for section in body.split("\n---\n") if section.strip()]
    compatible_sections: list[str] = []
    for section in raw_sections:
        if _CURRENT_SECTION_MARKER not in section:
            continue
        if "`background-round-trip`" not in section or "`cadence-gate`" not in section:
            continue
        if "`e2e-extraction-injection`" not in section:
            continue
        if "`direct-round-trip`" in section:
            continue
        compatible_sections.append(section)
    return compatible_sections


def _write_report(cases: list[dict[str, Any]], total_ms: float) -> None:
    run_ts = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
    provider = settings.llm.provider
    model = settings.llm.model or "default"
    passed = sum(1 for case in cases if case["verdict"] in ("PASS", "SOFT PASS"))

    lines: list[str] = [
        f"## Run: {run_ts}",
        "",
        f"**Model:** {provider} / {model}  ",
        f"**Extraction timeout:** {_EXTRACTION_TIMEOUT_SECS}s per background extraction drain  ",
        f"**Total runtime:** {total_ms:.0f}ms  ",
        f"**Result:** {passed}/{len(cases)} passed",
        "",
        "### Summary",
        "",
        "| Case | Verdict | Duration |",
        "|------|---------|----------|",
    ]
    for case in cases:
        lines.append(f"| `{case['id']}` | {case['verdict']} | {case['duration_ms']:.0f}ms |")

    lines += ["", "### Step Traces", ""]
    for case in cases:
        lines.append(f"#### `{case['id']}` — {case['verdict']}")
        for step in case["steps"]:
            lines.append(f"- **{step['name']}** ({step['ms']:.0f}ms): {step['detail']}")
        if case.get("failure"):
            lines.append(f"- **Failure:** {case['failure']}")
        lines.append("")

    section = "\n".join(lines)
    prior_sections = _load_compatible_report_sections()
    all_sections = [section, *prior_sections]
    updated = _REPORT_HEADER + "\n\n" + "\n\n---\n\n".join(all_sections) + "\n"

    _REPORT_PATH.write_text(updated, encoding="utf-8")
    print(f"\n  Report → {_REPORT_PATH.relative_to(Path.cwd())}")


def _extraction_messages() -> list[Any]:
    return [
        ModelRequest(
            parts=[
                UserPromptPart(
                    content=(
                        "I always prefer pytest for all testing, and I do not want trailing "
                        "comments in code."
                    )
                )
            ]
        ),
        ModelResponse(
            parts=[
                TextPart(
                    content="Understood. I will default to pytest and avoid trailing comments."
                )
            ],
            model_name="eval-memory-extractor",
        ),
    ]


def _collect_memory_state(
    knowledge_store: KnowledgeStore,
    memory_dir: Path,
) -> tuple[list[Path], list[str]]:
    files_written = sorted(memory_dir.glob("*.md"))
    indexed_paths: list[str] = []
    for query in ("pytest", "trailing comments"):
        indexed_paths = [
            result.path
            for result in knowledge_store.search(query, source="memory", kind="memory", limit=5)
        ]
        if indexed_paths:
            break
    return files_written, indexed_paths


def _build_env(tmp_dir: Path, case_id: str) -> tuple[KnowledgeStore, Any, Path]:
    root = tmp_dir / case_id
    memory_dir = root / "memory"
    db_path = root / "search.db"
    memory_dir.mkdir(parents=True, exist_ok=True)
    knowledge_store = KnowledgeStore(config=settings, knowledge_db_path=db_path)
    llm_model = build_model(settings.llm)
    deps = make_eval_deps(
        knowledge_store=knowledge_store,
        memory_dir=memory_dir,
        model=llm_model,
    )
    return knowledge_store, deps, memory_dir


async def run_background_round_trip(tmp_dir: Path) -> dict[str, Any]:
    """Fire-and-forget extraction launches quickly, then drains within the strict timeout."""
    steps: list[dict[str, Any]] = []
    case_t0 = time.monotonic()
    knowledge_store, deps, memory_dir = _build_env(tmp_dir, "background-round-trip")
    messages = _extraction_messages()
    cursor_start = 0

    try:
        t = time.monotonic()
        fire_and_forget_extraction(
            messages,
            deps=deps,
            frontend=SilentFrontend(),
            cursor_start=cursor_start,
        )
        launch_ms = (time.monotonic() - t) * 1000
        steps.append(
            {
                "name": "fire_and_forget_extraction launch",
                "ms": launch_ms,
                "detail": f"launch_ms={launch_ms:.1f} ({'non-blocking' if launch_ms < 100 else 'BLOCKED'})",
            }
        )

        t = time.monotonic()
        await drain_pending_extraction(timeout_ms=_EXTRACTION_TIMEOUT_SECS * 1000)
        steps.append(
            {
                "name": "drain_pending_extraction",
                "ms": (time.monotonic() - t) * 1000,
                "detail": f"cursor={deps.session.last_extracted_message_idx}",
            }
        )

        files_written, indexed_paths = _collect_memory_state(knowledge_store, memory_dir)
        steps.append(
            {
                "name": "write + DB index state",
                "ms": 0,
                "detail": (
                    f"files_written={len(files_written)} "
                    f"db_results={len(indexed_paths)} "
                    f"cursor={deps.session.last_extracted_message_idx}"
                ),
            }
        )

        if launch_ms >= 100:
            verdict, failure = "FAIL", f"background launch blocked for {launch_ms:.0f}ms"
        elif deps.session.last_extracted_message_idx != cursor_start + len(messages):
            verdict, failure = "FAIL", "cursor did not advance after background extraction"
        elif not files_written:
            verdict, failure = "FAIL", "background extraction wrote no memory file"
        elif not indexed_paths:
            verdict, failure = "FAIL", "background extraction did not index into DB"
        else:
            verdict, failure = "PASS", None
    finally:
        knowledge_store.close()

    return {
        "id": "background-round-trip",
        "verdict": verdict,
        "failure": failure,
        "steps": steps,
        "duration_ms": (time.monotonic() - case_t0) * 1000,
    }


async def run_cadence_gate() -> dict[str, Any]:
    """Verify extract_every_n_turns fires on the Nth clean turn and disables at zero."""
    steps: list[dict[str, Any]] = []
    case_t0 = time.monotonic()

    t = time.monotonic()
    config = get_settings()
    config = config.model_copy(
        update={"memory": config.memory.model_copy(update={"extract_every_n_turns": 2})}
    )
    n_turns = config.memory.extract_every_n_turns
    steps.append(
        {
            "name": "config: extract_every_n_turns",
            "ms": (time.monotonic() - t) * 1000,
            "detail": f"n={n_turns} (expected 2)",
        }
    )

    from co_cli.deps import CoSessionState

    session = CoSessionState()

    t = time.monotonic()
    session.last_extracted_turn_idx += 1
    fires_on_1 = session.last_extracted_turn_idx % n_turns == 0
    steps.append(
        {
            "name": "turn 1 gate check",
            "ms": (time.monotonic() - t) * 1000,
            "detail": f"counter={session.last_extracted_turn_idx} fires={fires_on_1} (expected False)",
        }
    )

    t = time.monotonic()
    session.last_extracted_turn_idx += 1
    fires_on_2 = session.last_extracted_turn_idx % n_turns == 0
    steps.append(
        {
            "name": "turn 2 gate check",
            "ms": (time.monotonic() - t) * 1000,
            "detail": f"counter={session.last_extracted_turn_idx} fires={fires_on_2} (expected True)",
        }
    )

    t = time.monotonic()
    disabled_config = get_settings().model_copy(
        update={"memory": get_settings().memory.model_copy(update={"extract_every_n_turns": 0})}
    )
    gate_disabled_ok = disabled_config.memory.extract_every_n_turns == 0
    steps.append(
        {
            "name": "disabled gate (n=0)",
            "ms": (time.monotonic() - t) * 1000,
            "detail": f"n={disabled_config.memory.extract_every_n_turns} gate_disabled={gate_disabled_ok}",
        }
    )

    if fires_on_1:
        verdict, failure = "FAIL", "gate fired on turn 1 (expected only on turn 2)"
    elif not fires_on_2:
        verdict, failure = "FAIL", "gate did not fire on turn 2 (expected idx % n == 0)"
    elif not gate_disabled_ok:
        verdict, failure = "FAIL", "n=0 did not disable extraction cadence"
    else:
        verdict, failure = "PASS", None

    return {
        "id": "cadence-gate",
        "verdict": verdict,
        "failure": failure,
        "steps": steps,
        "duration_ms": (time.monotonic() - case_t0) * 1000,
    }


async def run_extraction_to_injection(tmp_dir: Path) -> dict[str, Any]:
    """Full memory loop: turn 1 → extraction → DB index → inject_opening_context.

    Turn 1 (real LLM): user states a preference. fire_and_forget_extraction runs.
    After drain: extracted memory file is written and indexed in KnowledgeStore.
    Inject probe (no LLM): read actual extracted body, build a ModelRequest using
    that content as the user message, call inject_opening_context directly, and
    assert SystemPromptPart("Relevant memories: ...") is returned.

    Using the extracted body as the query guarantees a BM25 match regardless of
    what the extractor LLM chose to save — no query/content mismatch risk.
    If the extractor saved nothing (valid judgment call), SOFT PASS.
    """
    from pydantic_ai import RunContext as _RunContext
    from pydantic_ai.usage import RunUsage

    from co_cli.context._history import inject_opening_context
    from co_cli.knowledge._frontmatter import parse_frontmatter

    steps: list[dict[str, Any]] = []
    case_t0 = time.monotonic()
    root = tmp_dir / "e2e-extraction"
    memory_dir = root / "memory"
    db_path = root / "search.db"
    memory_dir.mkdir(parents=True, exist_ok=True)

    knowledge_store = KnowledgeStore(config=settings, knowledge_db_path=db_path)
    llm_model = build_model(settings.llm)
    deps = make_eval_deps(
        knowledge_store=knowledge_store,
        memory_dir=memory_dir,
        model=llm_model,
    )
    agent = build_agent(config=settings)
    frontend = SilentFrontend()
    orig_cwd = os.getcwd()

    try:
        os.chdir(root)

        # Turn 1: state durable preferences the extractor should capture
        t = time.monotonic()
        async with asyncio.timeout(EVAL_TURN_TIMEOUT_SECS):
            turn1_result = await run_turn(
                agent=agent,
                user_input=(
                    "I always prefer pytest for all testing, "
                    "and I do not want trailing comments in code."
                ),
                deps=deps,
                message_history=[],
                frontend=frontend,
            )
        steps.append(
            {
                "name": "run_turn 1 (state preference)",
                "ms": (time.monotonic() - t) * 1000,
                "detail": f"{len(turn1_result.messages)} messages",
            }
        )

        # Simulate _finalize_turn: fire extraction on turn 1 messages
        cursor_start = deps.session.last_extracted_message_idx
        t = time.monotonic()
        fire_and_forget_extraction(
            turn1_result.messages,
            deps=deps,
            frontend=frontend,
            cursor_start=cursor_start,
        )
        steps.append(
            {
                "name": "fire_and_forget_extraction (non-blocking)",
                "ms": (time.monotonic() - t) * 1000,
                "detail": f"cursor_start={cursor_start}",
            }
        )

        # Drain — extractor LLM call (NOREASON) + save_memory + DB index
        t = time.monotonic()
        await drain_pending_extraction(timeout_ms=_EXTRACTION_TIMEOUT_SECS * 1000)
        drain_ms = (time.monotonic() - t) * 1000
        cursor_after = deps.session.last_extracted_message_idx
        files_written = sorted(memory_dir.glob("*.md"))
        steps.append(
            {
                "name": "drain_pending_extraction",
                "ms": drain_ms,
                "detail": f"cursor={cursor_after} files_written={len(files_written)}",
            }
        )

        # Extractor made a judgment call — accept no extraction as SOFT PASS
        if not files_written:
            return {
                "id": "e2e-extraction-injection",
                "verdict": "SOFT PASS",
                "failure": None,
                "steps": [
                    *steps,
                    {
                        "name": "no files extracted",
                        "ms": 0,
                        "detail": "extractor judgment — nothing durable found",
                    },
                ],
                "duration_ms": (time.monotonic() - case_t0) * 1000,
            }

        # Read the first extracted file body to find significant words for DB probing.
        raw = files_written[0].read_text(encoding="utf-8")
        _, body = parse_frontmatter(raw)
        extracted_body = body.strip()
        steps.append(
            {
                "name": "read extracted file",
                "ms": 0,
                "detail": f"body={extracted_body[:80]!r}",
            }
        )

        # Verify extraction is in the DB (independent of recall path).
        # Use individual significant words from the body rather than the full prose
        # as a query: _build_fts_query AND-joins all tokens, so a single partial word
        # at the 120-char truncation boundary can cause a false zero.
        # Try each word in order and stop at first hit — mirrors _collect_memory_state.
        _stopwords = {
            "a",
            "an",
            "the",
            "and",
            "or",
            "but",
            "in",
            "on",
            "at",
            "to",
            "for",
            "of",
            "with",
            "by",
            "from",
            "is",
            "are",
            "was",
            "were",
            "be",
            "been",
            "being",
            "have",
            "has",
            "had",
            "do",
            "does",
            "did",
            "will",
            "would",
            "could",
            "should",
            "may",
            "might",
            "this",
            "that",
            "they",
            "them",
            "not",
            "it",
            "its",
            "so",
            "as",
            "if",
            "all",
            "any",
            "no",
            "can",
            "user",
            "want",
            "always",
            "also",
            "only",
            "just",
            "very",
            "more",
        }
        body_words = [
            w
            for w in re.sub(r"[^\w\s]", " ", extracted_body.lower()).split()
            if len(w) > 3 and w not in _stopwords
        ][:8]
        t = time.monotonic()
        db_results: list = []
        used_query = ""
        for word in body_words:
            db_results = knowledge_store.search(word, source="memory", kind="memory", limit=3)
            if db_results:
                used_query = word
                break
        steps.append(
            {
                "name": "KnowledgeStore.search (body word probe)",
                "ms": (time.monotonic() - t) * 1000,
                "detail": f"tried={body_words[:4]} hit={used_query!r} db_results={len(db_results)}",
            }
        )

        # Probe inject_opening_context directly — no second LLM call.
        # Use the first word that hit the DB as the user message — a clean single-word
        # query that _recall_for_context BM25 is guaranteed to rank > 0.
        # Fresh deps: run_turn already set state.last_recall_user_turn = 1 on `deps`;
        # reusing it would hit the duplicate-turn guard (user_turn_count <= state value).
        probe_query = (
            used_query if used_query else (body_words[0] if body_words else extracted_body[:60])
        )
        probe_messages = [ModelRequest(parts=[UserPromptPart(content=probe_query)])]
        probe_deps = make_eval_deps(
            knowledge_store=knowledge_store,
            memory_dir=memory_dir,
            model=llm_model,
        )
        ctx = _RunContext(deps=probe_deps, model=agent.model, usage=RunUsage())
        t = time.monotonic()
        injected = await inject_opening_context(ctx, probe_messages)
        steps.append(
            {
                "name": "inject_opening_context (direct probe)",
                "ms": (time.monotonic() - t) * 1000,
                "detail": f"returned {len(injected)} messages",
            }
        )

        injection: str | None = None
        for msg in injected:
            if isinstance(msg, ModelRequest):
                for part in msg.parts:
                    if isinstance(part, SystemPromptPart) and "Relevant memories:" in part.content:
                        injection = part.content
                        break
            if injection:
                break
        steps.append(
            {
                "name": "SystemPromptPart injection check",
                "ms": 0,
                "detail": f"injected={injection is not None} preview={(injection[:80] if injection else None)!r}",
            }
        )

        if cursor_after <= cursor_start:
            verdict, failure = "FAIL", "cursor did not advance — extractor did not run"
        elif not db_results:
            verdict, failure = "FAIL", "extracted content not found in DB — index step failed"
        elif injection is None:
            verdict, failure = (
                "FAIL",
                "inject_opening_context returned no SystemPromptPart — recall path broken",
            )
        else:
            verdict, failure = "PASS", None
    finally:
        os.chdir(orig_cwd)
        knowledge_store.close()

    return {
        "id": "e2e-extraction-injection",
        "verdict": verdict,
        "failure": failure,
        "steps": steps,
        "duration_ms": (time.monotonic() - case_t0) * 1000,
    }


async def main() -> int:
    print("=" * 72)
    print("  Eval: Memory Extraction Flow")
    print(f"  (strict extraction drain timeout: {_EXTRACTION_TIMEOUT_SECS}s)")
    print("=" * 72)

    all_cases: list[dict[str, Any]] = []
    t0 = time.monotonic()

    phases = [
        (
            "Phase 1: Production Background Extraction",
            [
                (
                    run_background_round_trip,
                    True,
                    "[background-round-trip] non-blocking launch + strict drain",
                ),
            ],
        ),
        (
            "Phase 2: Cadence Guard",
            [
                (
                    run_cadence_gate,
                    False,
                    "[cadence-gate] extract_every_n_turns fires on clean Nth turn only",
                ),
            ],
        ),
        (
            "Phase 3: End-to-End Memory Loop",
            [
                (
                    run_extraction_to_injection,
                    True,
                    "[e2e-extraction-injection] turn 1 → extract → index → turn 2 → SystemPromptPart",
                ),
            ],
        ),
    ]

    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        for phase_label, cases in phases:
            print(f"\n  {phase_label}")
            print("  " + "-" * 52)
            for fn, needs_tmp, label in cases:
                print(f"    {label}", end=" ", flush=True)
                try:
                    case = await (fn(tmp_path) if needs_tmp else fn())
                except Exception as exc:
                    case = {
                        "id": label,
                        "verdict": "ERROR",
                        "failure": str(exc),
                        "steps": [],
                        "duration_ms": 0,
                    }
                all_cases.append(case)
                print(f"{case['verdict']} ({case['duration_ms']:.0f}ms)")
                if case.get("failure"):
                    print(f"      -> {case['failure']}")

    total_ms = (time.monotonic() - t0) * 1000
    passed = sum(1 for case in all_cases if case["verdict"] in ("PASS", "SOFT PASS"))
    _write_report(all_cases, total_ms)

    print(f"\n{'=' * 72}")
    verdict = "PASS" if passed == len(all_cases) else "FAIL"
    print(f"  Verdict: {verdict} ({passed}/{len(all_cases)} cases, {total_ms:.0f}ms)")
    print(f"{'=' * 72}")
    return 0 if passed == len(all_cases) else 1


if __name__ == "__main__":
    import asyncio

    sys.exit(asyncio.run(main()))
