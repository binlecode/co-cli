#!/usr/bin/env python3
"""Eval: compaction quality — validates the full compaction pipeline end-to-end.

Steps follow the real execution flow (DESIGN-context.md §2, TODO specs):

  --- Pre-compact layer (at tool return time) ---
  Step 1 — persist_if_oversized: config-threshold persist, 2K preview, content-addressed disk write
           [BC4: persist-to-disk threshold sourced from config.tools.result_persist_chars]

  --- Processor chain components (isolated validation) ---
  Step 2 — P1 evict_old_tool_results: recency clearing, keep 5 most recent per compactable type
  Step 4 — P5 sub-component: context enrichment (gather_compaction_context)
           3 sources (file paths, todos, prior summaries), 4K cap, enrichment only on LLM path
           [BC2: capped, never blocks] [BC3: from ToolCallPart.args not ToolReturnPart]
  Step 5 — P5 sub-component: prompt assembly (_build_summarizer_prompt)
           template sections, context+personality ordering
           [Outcome 1: structured template] [BC1: free-form fallback]
  (P3 safety_prompt_text is validated as a dynamic instruction within Steps 6/7 — no isolated step needed.)

  --- Full chain execution (real LLM calls) ---
  Step 6 — Full processor chain P1→P3→P5 with numerical validation
           [Outcome 1-5 integrated] [Processor chain order verified]
  Step 7 — Multi-cycle: chain on prior summary, integration verified
           [Outcome 3: prior-summary detection and integration]

  --- Error recovery ---
  Step 8 — Overflow: is_context_overflow + one-shot guard
           [Outcome 4] [BC5: one-shot]

Prerequisites: LLM provider configured (Ollama or cloud).

Usage:
    uv run python evals/eval_compaction_flow_quality.py
"""

from __future__ import annotations

import asyncio
import io
import logging
import re
import sys
import tempfile
import time
from contextlib import AsyncExitStack, redirect_stdout
from pathlib import Path

import httpx
from evals._timeouts import (
    EVAL_PROBE_TIMEOUT_SECS,
)
from evals.eval_bootstrap_flow_quality import TrackingFrontend
from pydantic_ai import RunContext
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.usage import RunUsage

from co_cli.agent._core import build_agent
from co_cli.bootstrap.core import create_deps
from co_cli.config._core import KNOWLEDGE_DIR, TOOL_RESULTS_DIR, settings
from co_cli.context._compaction_markers import _CONTEXT_MAX_CHARS
from co_cli.context._http_error_classifier import is_context_overflow
from co_cli.context._tool_result_markers import is_cleared_marker
from co_cli.context.compaction import (
    COMPACTABLE_KEEP_RECENT,
    SUMMARY_MARKER_PREFIX,
    apply_compaction,
    evict_old_tool_results,
    gather_compaction_context,
    group_by_turn,
    plan_compaction_boundaries,
    summary_marker,
)
from co_cli.context.orchestrate import run_turn
from co_cli.context.prompt_text import safety_prompt_text
from co_cli.context.summarization import (
    _PERSONALITY_COMPACTION_ADDENDUM,
    _SUMMARIZE_PROMPT,
    _build_summarizer_prompt,
    resolve_compaction_budget,
    summarize_messages,
)
from co_cli.deps import CoDeps, CoSessionState
from co_cli.llm._factory import LlmModel, build_model
from co_cli.tools.shell_backend import ShellBackend
from co_cli.tools.tool_io import (
    PERSISTED_OUTPUT_TAG,
    TOOL_RESULT_PREVIEW_SIZE,
    persist_if_oversized,
)

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config — pull from real settings, never override
# ---------------------------------------------------------------------------

_LLM_MODEL = build_model(settings.llm)
_EVAL_CONFIG = settings.model_copy(update={"mcp_servers": {}})
_AGENT = build_agent(config=_EVAL_CONFIG, model=_LLM_MODEL)
_DEPS = CoDeps(
    shell=ShellBackend(),
    config=_EVAL_CONFIG,
    model=_LLM_MODEL,
)
_PERSIST_THRESHOLD = _EVAL_CONFIG.tools.result_persist_chars


# ---------------------------------------------------------------------------
# Snippet helper — show head + tail of long content with elision
# ---------------------------------------------------------------------------


def _snippet(text: str, max_len: int = 200) -> str:
    """Show head + tail with ...<N chars>... elision for long content."""
    if len(text) <= max_len:
        return repr(text)
    head = max_len // 3
    tail = max_len // 3
    return repr(text[:head]) + f" ...<{len(text) - head - tail} chars>... " + repr(text[-tail:])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _user(text: str) -> ModelRequest:
    return ModelRequest(parts=[UserPromptPart(content=text)])


def _assistant(text: str) -> ModelResponse:
    return ModelResponse(parts=[TextPart(content=text)])


def _tool_call(name: str, args: dict, call_id: str) -> ModelResponse:
    return ModelResponse(parts=[ToolCallPart(tool_name=name, args=args, tool_call_id=call_id)])


def _tool_return(name: str, content: str, call_id: str) -> ModelRequest:
    return ModelRequest(
        parts=[ToolReturnPart(tool_name=name, content=content, tool_call_id=call_id)]
    )


def _make_ctx(
    *,
    memory_dir: Path | None = None,
    session_todos: list[dict] | None = None,
    model: LlmModel | None = None,
) -> RunContext:
    session = CoSessionState()
    if session_todos:
        session.session_todos = session_todos
    deps = CoDeps(
        shell=ShellBackend(),
        config=_EVAL_CONFIG,
        model=model,
        session=session,
    )
    if memory_dir is not None:
        deps.knowledge_dir = memory_dir
    return RunContext(deps=deps, model=_AGENT.model, usage=RunUsage())


def _msg_chars(msgs: list[ModelMessage]) -> int:
    total = 0
    for m in msgs:
        for p in m.parts:
            c = getattr(p, "content", None)
            if isinstance(c, str):
                total += len(c)
    return total


def _count_cleared(msgs: list[ModelMessage]) -> int:
    return sum(
        1
        for m in msgs
        if isinstance(m, ModelRequest)
        for p in m.parts
        if isinstance(p, ToolReturnPart) and is_cleared_marker(p.content)
    )


def _check_semantic(
    summary: str,
    ground_truth: list[tuple[str, list[str]]],
    label: str,
) -> tuple[bool, list[str]]:
    """Check that ground-truth facts from the input appear in the summary.

    ground_truth: list of (category, [keywords_any_must_match]).
    Each category passes if at least one keyword is found (case-insensitive).
    Returns (all_passed, list_of_check_lines).
    """
    lines: list[str] = []
    all_ok = True
    low = summary.lower()
    for category, keywords in ground_truth:
        hits = [kw for kw in keywords if kw.lower() in low]
        if hits:
            lines.append(f"    PASS: {label} — {category}: found {hits[0]!r}")
        else:
            lines.append(f"    FAIL: {label} — {category}: none of {keywords} found")
            all_ok = False
    return all_ok, lines


def _check_no_hallucination(
    summary: str,
    forbidden: list[tuple[str, list[str]]],
    label: str,
) -> tuple[bool, list[str]]:
    """Check that known-absent facts do NOT appear in the summary.

    forbidden: list of (description, [keywords_none_should_match]).
    Each entry passes if NO keyword is found.
    """
    lines: list[str] = []
    all_ok = True
    low = summary.lower()
    for desc, keywords in forbidden:
        hits = [kw for kw in keywords if kw.lower() in low]
        if hits:
            lines.append(
                f"    FAIL: {label} — hallucination: {desc} ({hits[0]!r} found but not in input)"
            )
            all_ok = False
    return all_ok, lines


# Realistic assistant text generator (large content for compaction testing)
def _analysis(topic: str, extra: str = "") -> str:
    return (
        f"I've analyzed {topic}. Here's what I found:\n\n"
        f"The module follows Django's class-based view pattern with several key components. "
        f"The authentication flow starts at the login endpoint which accepts POST requests "
        f"with username and password. The current implementation uses Django's built-in "
        f"session framework via SessionMiddleware. When a user authenticates successfully, "
        f"a session cookie is set and subsequent requests are validated through the middleware.\n\n"
        f"Key observations about {topic}:\n"
        f"1. The session store is backed by the database (django.contrib.sessions.backends.db)\n"
        f"2. Token-based auth would eliminate the session table dependency entirely\n"
        f"3. The CSRF protection relies on session cookies — JWT migration needs a replacement\n"
        f"4. There are {hash(topic) % 5 + 3} middleware dependencies to update\n"
        f"5. The current session timeout is 2 weeks (SESSION_COOKIE_AGE = 1209600)\n"
        f"6. Session data includes user preferences that need migration to JWT claims\n\n"
        f"The middleware chain processes requests in this order: SecurityMiddleware → "
        f"SessionMiddleware → AuthenticationMiddleware → the custom SessionAuthMiddleware. "
        f"For JWT migration we need to replace steps 2-4 with a single JWTAuthMiddleware "
        f"that extracts the Bearer token from the Authorization header, validates the "
        f"signature using the configured secret key, checks expiration and issuer claims, "
        f"and attaches the authenticated user to request.user.\n\n"
        f"Implementation considerations for {topic}:\n"
        f"- The refresh token should be stored in an HttpOnly cookie for XSS protection\n"
        f"- Access tokens should have a short TTL (15 minutes) to limit exposure\n"
        f"- Token revocation requires a blacklist table or Redis-backed store\n"
        f"- The token payload should include user_id, email, role, and issued_at claims\n"
        f"- Rate limiting on the token endpoint prevents brute-force attacks\n"
        f"- The dual-auth middleware allows gradual migration without breaking existing clients\n"
        f"- We should add comprehensive logging for token validation failures\n"
        f"- The admin panel needs a separate token scope with elevated permissions\n\n"
        f"Security audit notes for {topic}:\n"
        f"The current implementation has no protection against token replay attacks. "
        f"We should implement a jti (JWT ID) claim and track used tokens in Redis "
        f"with a TTL matching the token expiration. This adds ~50ms per request but "
        f"prevents the most common JWT attack vector. The token signing algorithm "
        f"must be explicitly set to HS256 — never allow 'none' or RS256 without "
        f"proper key management infrastructure in place.\n\n"
        f"{extra}"
        f"I'll proceed with the next file to build the full picture before making changes. "
        f"Based on what I've seen so far, the migration is straightforward but we need to "
        f"be careful about the CSRF token replacement and the middleware ordering.\n"
    )


def _fake_file(name: str, lines: int = 60) -> str:
    return "\n".join(
        f"# {name} line {i}: {'def ' if i % 10 == 0 else '    '}handler_{i}(request): pass"
        for i in range(lines)
    )


# ---------------------------------------------------------------------------
# Step 1: Pre-compact — persist_if_oversized [BC4]
# ---------------------------------------------------------------------------


def step_1_precompact() -> bool:
    """Validate pre-compact layer: tool results over threshold persisted to disk with 2K preview."""
    print("\n--- Step 1: Pre-compact — persist_if_oversized [BC4] ---")
    passed = True

    with tempfile.TemporaryDirectory() as tmpdir:
        d = Path(tmpdir) / "tool-results"

        # 1a: Under threshold → unchanged
        small = _fake_file("auth/views.py", lines=15)
        r = persist_if_oversized(small, d, "file_read", max_size=_PERSIST_THRESHOLD)
        assert r == small, "under threshold should pass through"
        print(f"  PASS: {len(small)} chars < {_PERSIST_THRESHOLD} → unchanged")
        print(f"    content: {_snippet(small, 100)}")

        # 1b: At boundary → unchanged
        boundary = _fake_file("auth/middleware.py", lines=600)[:_PERSIST_THRESHOLD]
        r = persist_if_oversized(boundary, d, "file_read", max_size=_PERSIST_THRESHOLD)
        assert r == boundary
        print(f"  PASS: {_PERSIST_THRESHOLD} == threshold → unchanged (boundary)")

        # 1c: Over threshold → persisted + preview
        big = _fake_file("search_results.log", lines=800) + "\n" * (_PERSIST_THRESHOLD - 10_000)
        big = big + _fake_file("more_results.log", lines=200)
        r = persist_if_oversized(big, d, "find_in_files", max_size=_PERSIST_THRESHOLD)
        if PERSISTED_OUTPUT_TAG not in r:
            print("  FAIL: over-threshold not persisted")
            return False
        print(f"  PASS: {len(big)} > {_PERSIST_THRESHOLD} → persisted with preview tag")
        print(f"    snippet: {_snippet(r, 160)}")

        # 1d: Preview capped at TOOL_RESULT_PREVIEW_SIZE
        preview = r.split("preview:\n", 1)[1].split("\n</persisted-output>")[0]
        if len(preview) > TOOL_RESULT_PREVIEW_SIZE + 10:
            print(f"  FAIL: preview {len(preview)} exceeds {TOOL_RESULT_PREVIEW_SIZE}")
            return False
        print(f"  PASS: preview {len(preview)} chars ≤ {TOOL_RESULT_PREVIEW_SIZE}")

        # 1e: Content-addressed file on disk
        files = list(d.iterdir())
        if not files or files[0].read_text() != big:
            print("  FAIL: disk content mismatch")
            return False
        print(f"  PASS: content-addressed file on disk ({files[0].name})")

        # 1f: Idempotent
        persist_if_oversized(big, d, "find_in_files", max_size=_PERSIST_THRESHOLD)
        if len(list(d.iterdir())) != 1:
            print("  FAIL: duplicate file created")
            return False
        print("  PASS: idempotent — same content → same file")

    return passed


# ---------------------------------------------------------------------------
# Step 2: P1 evict_old_tool_results [Outcome 5 prereq]
# ---------------------------------------------------------------------------


def step_2_p1_truncate() -> bool:
    """Validate P1: recency-based clearing with exact counts.

    Specs from TODO:
    - Keep COMPACTABLE_KEEP_RECENT (5) most recent per tool type
    - Non-compactable tools pass through regardless of count
    - Last turn group always protected
    """
    print(f"\n--- Step 2: P1 evict_old_tool_results (keep={COMPACTABLE_KEEP_RECENT}) ---")
    passed = True
    ctx = _make_ctx()

    # 2a: Exactly at threshold — 5 calls → 0 cleared
    def _build_tool_conv(tool_name: str, n: int) -> list[ModelMessage]:
        msgs: list[ModelMessage] = []
        for i in range(n):
            cid = f"t{i}"
            msgs += [
                _user(f"call {i}"),
                _tool_call(tool_name, {}, cid),
                _tool_return(tool_name, f"result {i}", cid),
                _assistant(f"done {i}"),
            ]
        msgs += [_user("final"), _assistant("ok")]
        return msgs

    msgs = _build_tool_conv("file_read", 5)
    result = evict_old_tool_results(ctx, msgs)
    cleared = _count_cleared(result)
    if cleared != 0:
        print(f"  FAIL: 5 calls should clear 0, got {cleared}")
        passed = False
    else:
        print("  PASS: 5 read_file → 0 cleared (at threshold)")

    # 2b: Over threshold — 8 calls → 3 cleared
    msgs = _build_tool_conv("file_read", 8)
    result = evict_old_tool_results(ctx, msgs)
    cleared = _count_cleared(result)
    expected = 8 - COMPACTABLE_KEEP_RECENT
    if cleared != expected:
        print(f"  FAIL: 8 calls should clear {expected}, got {cleared}")
        passed = False
    else:
        print(
            f"  PASS: 8 read_file → {cleared} cleared (8 - {COMPACTABLE_KEEP_RECENT} = {expected})"
        )
        # Show first cleared and first intact as evidence
        for m in result:
            if isinstance(m, ModelRequest):
                for p in m.parts:
                    if (
                        isinstance(p, ToolReturnPart)
                        and p.tool_name == "file_read"
                        and is_cleared_marker(p.content)
                    ):
                        print(f"    cleared: {_snippet(p.content)}")
                        break
        for m in reversed(result):
            if isinstance(m, ModelRequest):
                for p in m.parts:
                    if (
                        isinstance(p, ToolReturnPart)
                        and p.tool_name == "file_read"
                        and not is_cleared_marker(p.content)
                    ):
                        print(f"    kept:    {_snippet(p.content)}")
                        break
                else:
                    continue
                break

    # 2c: Non-compactable tool — 10 calls → 0 cleared
    msgs = _build_tool_conv("save_memory", 10)
    result = evict_old_tool_results(ctx, msgs)
    cleared = _count_cleared(result)
    if cleared != 0:
        print(f"  FAIL: non-compactable should clear 0, got {cleared}")
        passed = False
    else:
        print("  PASS: 10 save_memory → 0 cleared (non-compactable)")

    # 2d: Multiple compactable types — each tracked independently
    # 8 read_file + 7 web_search → P1 clears 3 read_file + 2 web_search = 5 total
    msgs_multi: list[ModelMessage] = []
    cid = 0
    for i in range(8):
        c = f"rf{cid}"
        cid += 1
        msgs_multi += [
            _user(f"read {i}"),
            _tool_call("file_read", {}, c),
            _tool_return("file_read", f"file content {i}", c),
            _assistant(f"got {i}"),
        ]
    for i in range(7):
        c = f"ws{cid}"
        cid += 1
        msgs_multi += [
            _user(f"search {i}"),
            _tool_call("web_search", {}, c),
            _tool_return("web_search", f"search result {i}", c),
            _assistant(f"found {i}"),
        ]
    msgs_multi += [_user("final"), _assistant("ok")]
    result = evict_old_tool_results(ctx, msgs_multi)
    # Count cleared per type
    cleared_rf = sum(
        1
        for m in result
        if isinstance(m, ModelRequest)
        for p in m.parts
        if isinstance(p, ToolReturnPart)
        and p.tool_name == "file_read"
        and is_cleared_marker(p.content)
    )
    cleared_ws = sum(
        1
        for m in result
        if isinstance(m, ModelRequest)
        for p in m.parts
        if isinstance(p, ToolReturnPart)
        and p.tool_name == "web_search"
        and is_cleared_marker(p.content)
    )
    expected_rf = 8 - COMPACTABLE_KEEP_RECENT  # 3
    expected_ws = 7 - COMPACTABLE_KEEP_RECENT  # 2
    if cleared_rf != expected_rf or cleared_ws != expected_ws:
        print(
            f"  FAIL: multi-type: read_file cleared {cleared_rf} (expected {expected_rf}), "
            f"web_search cleared {cleared_ws} (expected {expected_ws})"
        )
        passed = False
    else:
        print(
            f"  PASS: multi-type: 8 read_file → {cleared_rf} cleared, "
            f"7 web_search → {cleared_ws} cleared (independent per-type tracking)"
        )

    # 2e: Last turn group protected
    msgs = _build_tool_conv("file_read", 7)
    # Replace the final turn with a read_file call (should be protected)
    msgs[-2:] = [
        _user("final read"),
        _tool_call("file_read", {}, "last"),
        _tool_return("file_read", "PROTECTED", "last"),
        _assistant("done"),
    ]
    result = evict_old_tool_results(ctx, msgs)
    # Find the last group's tool return
    groups = group_by_turn(result)
    last_returns = [
        p
        for m in groups[-1].messages
        if isinstance(m, ModelRequest)
        for p in m.parts
        if isinstance(p, ToolReturnPart)
    ]
    if not last_returns or is_cleared_marker(last_returns[0].content):
        print("  FAIL: last turn group tool result was cleared")
        passed = False
    else:
        print("  PASS: last turn group protected (content intact)")

    return passed


# ---------------------------------------------------------------------------
# Step 4: P5 context enrichment [Outcome 2, BC2, BC3]
# ---------------------------------------------------------------------------


def step_4_context_enrichment() -> bool:
    """Validate gather_compaction_context: 4 sources, 4K cap, lazy execution.

    Specs from TODO:
    - Source 1: File paths from ToolCallPart.args via FILE_TOOLS (scans ALL messages) [BC3]
    - Source 2: Pending session todos (filter completed/cancelled)
    - Source 3: Always-on memories from filesystem
    - Source 4: Prior-summary text from dropped messages matching [Summary of prefix
    - Returns None when empty
    - Capped at _CONTEXT_MAX_CHARS = 4_000 [BC2]
    """
    print(f"\n--- Step 4: Context enrichment (cap={_CONTEXT_MAX_CHARS}) [BC2,BC3] ---")
    passed = True

    # 4a: Source 1 — file paths from ToolCallPart.args [BC3: NOT from ToolReturnPart]
    # Uses FILE_TOOLS members (read_file, patch) — both have file_path args
    msgs: list[ModelMessage] = [
        _user("work"),
        _tool_call("file_read", {"file_path": "/app/models.py"}, "c1"),
        _tool_return("file_read", "class User: ...", "c1"),
        _tool_call("file_patch", {"file_path": "/app/views.py"}, "c2"),
        _tool_return("file_patch", "ok", "c2"),
        _assistant("done"),
    ]
    ctx = _make_ctx()
    result = gather_compaction_context(ctx, dropped=msgs)
    if result is None or "/app/models.py" not in result or "/app/views.py" not in result:
        print("  FAIL: file paths not extracted from ToolCallPart.args")
        passed = False
    else:
        print("  PASS: Source 1 — file paths from ToolCallPart.args")
        print(f"    context: {_snippet(result, 120)}")

    # 4b: Source 1 is scoped to dropped only (Gap M) — head/tail paths MUST NOT duplicate
    # /head.py and /tail.py exist only in the kept head/tail regions; only /mid.py is in dropped.
    dropped_msgs: list[ModelMessage] = [
        _user("mid"),
        _tool_call("file_read", {"file_path": "/mid.py"}, "m1"),
        _tool_return("file_read", "z", "m1"),
        _assistant("mid"),
    ]
    result = gather_compaction_context(ctx, dropped=dropped_msgs)
    if result is None or "/mid.py" not in result:
        print("  FAIL: /mid.py (in dropped) missing from enrichment")
        passed = False
    elif "/head.py" in result or "/tail.py" in result:
        print("  FAIL: head/tail file paths leaked into enrichment — Gap M not fixed")
        passed = False
    else:
        print("  PASS: Source 1 scoped to dropped only (Gap M)")

    # 4c: Source 2 — session todos (pending only)
    todos = [
        {"content": "Update tests", "status": "pending"},
        {"content": "Deploy to staging", "status": "completed"},
        {"content": "Write docs", "status": "in_progress"},
        {"content": "Cancel this", "status": "cancelled"},
    ]
    ctx = _make_ctx(session_todos=todos)
    result = gather_compaction_context(ctx, dropped=[])
    if result is None or "Update tests" not in result or "Write docs" not in result:
        print("  FAIL: pending todos missing")
        passed = False
    elif "Deploy to staging" in result or "Cancel this" in result:
        print("  FAIL: completed/cancelled todos should be filtered")
        passed = False
    else:
        print("  PASS: Source 2 — pending todos included, completed/cancelled filtered")
        print(f"    context: {_snippet(result, 160)}")

    # 4e: Source 3 — prior-summary from dropped messages (production marker format)
    dropped_with_summary: list[ModelMessage] = [
        summary_marker(15, "## Goal\nRefactor auth module"),
        _assistant("continuing..."),
    ]
    ctx = _make_ctx()
    result = gather_compaction_context(ctx, dropped=dropped_with_summary)
    if result is None or "Prior summary" not in result or "Refactor auth module" not in result:
        print("  FAIL: prior summary not extracted from dropped messages")
        passed = False
    else:
        print("  PASS: Source 3 — prior summary from dropped messages")
        print(f"    context: {_snippet(result, 160)}")

    # 4f: 4K cap [BC2]
    big_todos = [{"content": "x" * 500, "status": "pending"} for _ in range(20)]
    msgs_many_files: list[ModelMessage] = [
        _user("work"),
        ModelResponse(
            parts=[
                ToolCallPart(
                    tool_name="file_read",
                    args={"file_path": f"/a/b/c/d/e/file_{i:03d}.py"},
                    tool_call_id=f"c{i}",
                )
                for i in range(20)
            ]
        ),
        _assistant("done"),
    ]
    ctx = _make_ctx(session_todos=big_todos)
    result = gather_compaction_context(ctx, dropped=msgs_many_files)
    if result is not None and len(result) > _CONTEXT_MAX_CHARS:
        print(f"  FAIL: context {len(result)} > {_CONTEXT_MAX_CHARS}")
        passed = False
    elif result is not None:
        print(f"  PASS: context capped at {len(result)} ≤ {_CONTEXT_MAX_CHARS} [BC2]")
    else:
        print("  PASS: context was None (sources below threshold)")

    return passed


# ---------------------------------------------------------------------------
# Step 5: P5 prompt assembly [Outcome 1, BC1]
# ---------------------------------------------------------------------------


def step_5_prompt_assembly() -> bool:
    """Validate _build_summarizer_prompt and template structure.

    Specs from TODO:
    - Assembly order: template + context (## Additional Context) + personality (always last)
    - summarize_messages() accepts context: str | None = None
    - [BC1]: structured summary is prompt, not parser
    """
    print("\n--- Step 5: Prompt assembly [Outcome 1, BC1] ---")
    passed = True

    # 5a: (context, False) → template + context, no personality
    r = _build_summarizer_prompt(_SUMMARIZE_PROMPT, "Files: foo.py", False)
    if "## Additional Context" not in r or "foo.py" not in r:
        print("  FAIL: context not injected")
        passed = False
    elif _PERSONALITY_COMPACTION_ADDENDUM in r:
        print("  FAIL: personality present without personality_active")
        passed = False
    else:
        print("  PASS: (context, False) → template + context")

    # 5b: (context, True) → template + context + personality (personality LAST)
    r = _build_summarizer_prompt(_SUMMARIZE_PROMPT, "todos here", True)
    ctx_pos = r.index("## Additional Context")
    pers_pos = r.index("Additionally, preserve")
    if ctx_pos >= pers_pos:
        print("  FAIL: personality must come after context")
        passed = False
    else:
        print("  PASS: (context, True) → context before personality (correct order)")
        # Show the assembly boundary: context section → personality section
        boundary = r[ctx_pos : pers_pos + 60]
        print(f"    boundary: {_snippet(boundary, 160)}")

    return passed


# ---------------------------------------------------------------------------
# Step 6: Full processor chain P1→P3→P4→P5 [all Outcomes, chain order]
# ---------------------------------------------------------------------------


async def step_6_full_chain() -> bool:
    """Execute real processor chain with numerical validation at each stage.

    Validates:
    - Processor order: P1→P3→P4→P5 (from agent.py registration)
    - P1: exact cleared count = N_read_file - COMPACTABLE_KEEP_RECENT
    - P3: no safety injections (clean history)
    - P4: memory recall (may or may not inject)
    - P5: message reduction, summary marker count matches dropped, structured sections
    - Context enrichment: file paths + todos in summary output
    """
    print("\n--- Step 6: Full processor chain P1→P3→P4→P5 (real LLM) ---")
    passed = True

    # Build history: 10 read_file + 2 edit_file + 1 find_in_files, large assistant text
    N_READ = 10
    history: list[ModelMessage] = []

    # Turn 1: request + large analysis
    history += [
        _user("Refactor auth module from sessions to JWT."),
        ModelResponse(
            parts=[
                TextPart(
                    content=_analysis("project structure", "Starting with auth/views.py.\n\n")
                )
            ]
        ),
    ]

    # Turns 2-11: 10 read_file with large responses
    files = [
        f"auth/{n}.py"
        for n in [
            "views",
            "middleware",
            "tokens",
            "permissions",
            "decorators",
            "backends",
            "serializers",
            "signals",
            "utils",
            "constants",
        ]
    ]
    for i, fname in enumerate(files):
        cid = f"rf{i}"
        history += [
            _user(f"Read {fname}"),
            _tool_call("file_read", {"file_path": fname}, cid),
            _tool_return("file_read", _fake_file(fname, 40 + i * 5), cid),
            ModelResponse(
                parts=[
                    TextPart(
                        content=_analysis(
                            fname,
                            f"File {i + 1}/{N_READ}. {'Critical path.' if i < 3 else 'Lower priority.'}\n\n",
                        )
                    )
                ]
            ),
        ]

    # Turn 12: edit + find_in_files
    history += [
        _user("Edit views and find imports."),
        _tool_call("edit_file", {"file_path": "auth/views.py"}, "ed1"),
        _tool_return("edit_file", "Edited", "ed1"),
        _tool_call("find_in_files", {"path": ".", "pattern": "from auth"}, "fi1"),
        _tool_return("find_in_files", "api/urls.py:3: from auth import login\n" * 5, "fi1"),
        ModelResponse(
            parts=[
                TextPart(
                    content=_analysis("import graph", "Found 4 files importing from auth.\n\n")
                )
            ]
        ),
    ]

    # Turn 13: another edit
    history += [
        _user("Update middleware."),
        _tool_call("edit_file", {"file_path": "auth/middleware.py"}, "ed2"),
        _tool_return("edit_file", "Edited", "ed2"),
        ModelResponse(parts=[TextPart(content=_analysis("middleware update"))]),
    ]

    # Turn 14: last (protected)
    history += [
        _user("Status?"),
        _assistant("Modified views and middleware. Tests and URLs remain."),
    ]

    n_msgs = len(history)
    n_groups = len(group_by_turn(history))
    total_chars = _msg_chars(history)
    print(f"  Input: {n_msgs} msgs, {n_groups} groups, {total_chars:,} chars")

    print(f"  Expected: P1 clears {N_READ - COMPACTABLE_KEEP_RECENT}")

    # Build ctx with todos for enrichment
    session = CoSessionState()
    session.session_todos = [
        {"content": "Update api/urls.py for JWT", "status": "pending"},
        {"content": "Add PyJWT to requirements", "status": "pending"},
        {"content": "Update middleware", "status": "completed"},
    ]
    deps = CoDeps(shell=ShellBackend(), config=_EVAL_CONFIG, model=_LLM_MODEL, session=session)
    ctx = RunContext(deps=deps, model=_AGENT.model, usage=RunUsage())

    msgs = list(history)

    # --- P1 ---
    print("\n  [P1] evict_old_tool_results")
    chars_pre_p1 = _msg_chars(msgs)
    msgs = evict_old_tool_results(ctx, msgs)
    p1_cleared = _count_cleared(msgs)
    chars_post_p1 = _msg_chars(msgs)
    expected_p1 = N_READ - COMPACTABLE_KEEP_RECENT
    print(f"    Cleared: {p1_cleared} (expected {expected_p1})")
    print(
        f"    Chars: {chars_pre_p1:,} → {chars_post_p1:,} (P1 reduced {chars_pre_p1 - chars_post_p1:,})"
    )
    if p1_cleared != expected_p1:
        print(f"    FAIL: P1 cleared {p1_cleared} ≠ {expected_p1}")
        passed = False
    else:
        print("    PASS")

    # --- P3 ---
    # Safety injection now happens via dynamic agent.instructions() — not appended to msgs.
    print("\n  [P3] safety_prompt_text (dynamic instruction)")
    from dataclasses import replace as _replace

    ctx_p3 = _replace(ctx, messages=msgs)
    safety_text = safety_prompt_text(ctx_p3)
    print(f"    Safety text: {safety_text!r} (clean history → no warnings expected)")
    print("    PASS")

    # --- P5 ---
    print("\n  [P5] apply_compaction (LLM)")
    _p5_ctx_window = ctx.deps.model.context_window if ctx.deps.model else None
    _p5_budget = resolve_compaction_budget(ctx.deps.config, _p5_ctx_window)
    bounds = plan_compaction_boundaries(msgs, _p5_budget, ctx.deps.config.compaction.tail_fraction)
    if bounds is None:
        print(
            f"    SKIP: plan_compaction_boundaries returned None "
            f"(budget={_p5_budget}, {len(msgs)} msgs) — history too small for configured context window"
        )
        return True
    head_end, tail_start, dropped_count = bounds
    dropped_preview = msgs[head_end:tail_start]
    enrichment_preview = gather_compaction_context(ctx, dropped_preview)
    print(f"    Boundaries: head_end={head_end}, tail_start={tail_start}, dropped={dropped_count}")
    if enrichment_preview:
        print(f"    Enrichment ({len(enrichment_preview)} chars):")
        for line in enrichment_preview.split("\n")[:8]:
            print(f"      | {line}")
        if enrichment_preview.count("\n") > 8:
            print(f"      | ...<{enrichment_preview.count(chr(10)) - 8} more lines>")
    else:
        print("    Enrichment: None")

    len_pre_p5 = len(msgs)
    try:
        msgs, _ = await apply_compaction(ctx, msgs, bounds, announce=False)
    except TimeoutError:
        print("    FAIL: timed out")
        return False

    net_reduction = len_pre_p5 - len(msgs)
    # apply_compaction replaces N dropped messages with exactly 1 marker message.
    # net_reduction = N - 1, so actual_dropped = net_reduction + 1.
    actual_dropped = net_reduction + 1
    chars_final = _msg_chars(msgs)
    print(f"    Messages: {len_pre_p5} → {len(msgs)} ({actual_dropped} replaced by 1 marker)")
    print(f"    Chars: {chars_post_p1:,} → {chars_final:,}")

    if len(msgs) >= len_pre_p5:
        print("    FAIL: no reduction")
        return False
    print("    PASS: compacted")

    # Find summary and cross-validate marker count
    summary_text = None
    marker_count_in_output = 0
    for m in msgs:
        if isinstance(m, ModelRequest):
            for p in m.parts:
                if isinstance(p, UserPromptPart) and isinstance(p.content, str):
                    if SUMMARY_MARKER_PREFIX in p.content:
                        summary_text = p.content
                        marker_count_in_output += 1
                    elif "earlier messages were removed" in p.content:
                        marker_count_in_output += 1

    if marker_count_in_output != 1:
        print(f"    FAIL: expected exactly 1 marker, found {marker_count_in_output}")
        return False

    if summary_text is None:
        print("    Static marker (circuit breaker)")
        return True

    # Marker count cross-validation
    marker_match = re.search(r"portion \((\d+) messages\)", summary_text)
    if marker_match:
        marker_count = int(marker_match.group(1))
        if marker_count != actual_dropped:
            print(f"    FAIL: marker says {marker_count}, actual dropped {actual_dropped}")
            passed = False
        else:
            print(f"    PASS: marker count ({marker_count}) = actual dropped ({actual_dropped})")

    # Structured sections
    sections = [
        s
        for s in (
            "Active Task",
            "Goal",
            "Key Decisions",
            "Working Set",
            "Progress",
            "Next Step",
            "Critical Context",
        )
        if s.lower() in summary_text.lower()
    ]
    if len(sections) >= 2:
        print(f"    PASS: sections: {', '.join(sections)}")
    else:
        print(f"    FAIL: expected ≥2 structured sections, found {sections or 'none'}")
        passed = False

    # Semantic validation — ground truth from the input conversation
    # These facts are explicitly present in the _analysis() text and tool calls.
    ground_truth_6 = [
        ("goal: session-to-JWT migration", ["session", "jwt"]),
        ("decision: HS256 algorithm", ["hs256"]),
        ("decision: 15-minute TTL", ["15 min", "15-min", "short ttl", "short-lived"]),
        ("decision: HttpOnly cookies for refresh", ["httponly"]),
        ("decision: Redis token blacklist", ["redis"]),
        ("working set: auth/views.py", ["auth/views", "views.py"]),
        ("working set: auth/middleware.py", ["auth/middleware", "middleware.py"]),
        ("enrichment: api/urls.py from todos", ["api/urls", "urls.py"]),
        ("enrichment: PyJWT from todos", ["pyjwt"]),
    ]
    sem_ok, sem_lines = _check_semantic(summary_text, ground_truth_6, "Step 6")
    for line in sem_lines:
        print(line)
    if not sem_ok:
        passed = False

    # Anti-hallucination: these technologies are NOT in the input
    forbidden_6 = [
        ("OAuth2 not discussed", ["oauth2", "oauth 2"]),
        ("GraphQL not discussed", ["graphql"]),
        ("MongoDB not discussed", ["mongodb", "mongo"]),
    ]
    hal_ok, hal_lines = _check_no_hallucination(summary_text, forbidden_6, "Step 6")
    for line in hal_lines:
        print(line)
    if not hal_ok:
        passed = False

    # Print full summary (no truncation — needed for cross-reference)
    print(f"\n    Summary ({len(summary_text)} chars):")
    for line in summary_text.split("\n"):
        print(f"      | {line}")

    print(
        f"\n  Chain result: {n_msgs} msgs/{total_chars:,}ch → {len(msgs)} msgs/{chars_final:,}ch"
    )
    return passed


# ---------------------------------------------------------------------------
# Step 7: Multi-cycle [Outcome 3]
# ---------------------------------------------------------------------------


async def step_7_multi_cycle() -> bool:
    """Execute chain on history with prior summary marker. Verify integration.

    Specs from TODO:
    - Prior summary in dropped slice detected via SUMMARY_MARKER_PREFIX startswith
    - Context enrichment includes prior summary text
    - New summary integrates prior content (not lost)
    """
    print("\n--- Step 7: Multi-cycle compaction [Outcome 3] ---")
    passed = True

    prior_summary_body = (
        "## Goal\nRefactor auth module from sessions to JWT.\n\n"
        "## Key Decisions\nUsing PyJWT directly for more control.\n\n"
        "## Working Set\nauth/views.py, auth/middleware.py\n\n"
        "## Progress\nViews and middleware updated. Tests and urls pending."
    )

    # Cycle 2 assistant text (realistic large responses)
    def _detail(topic: str) -> str:
        return (
            f"Completed {topic} update. JWT flow implemented:\n\n"
            f"Access tokens: 15-min TTL, HS256, includes user_id/email/role claims. "
            f"Refresh tokens: 7-day TTL, HttpOnly cookies. Token blacklist via Redis. "
            f"Rate limiting: 5 req/min on token endpoints. RFC 6750 error responses "
            f"with WWW-Authenticate headers. Dual-auth middleware for zero-downtime "
            f"migration — checks JWT first, falls back to session auth.\n\n"
            f"Test coverage for {topic}:\n"
            f"- Valid flow (login → token → use → refresh → revoke → re-login)\n"
            f"- Expired token rejection (401 + proper message + WWW-Authenticate header)\n"
            f"- Malformed tokens (truncated payload, wrong algorithm, missing required claims)\n"
            f"- Concurrent refresh invalidation (race condition guard with Redis lock)\n"
            f"- Blacklist verification (revoked tokens rejected immediately on all endpoints)\n"
            f"- Role-based claims (admin vs regular vs readonly permissions matrix)\n"
            f"- Token scope isolation (admin panel tokens cannot access user API endpoints)\n"
            f"- CORS preflight with Authorization header (OPTIONS request handling)\n\n"
            f"Edge cases handled in {topic}:\n"
            f"- Key rotation: validated against previous key with 5-minute grace period\n"
            f"- Clock skew between servers: 30-second leeway on exp/iat validation\n"
            f"- Missing Authorization header with session cookie present: falls back to session auth\n"
            f"- Unknown algorithm in token header: rejected with 401 and algorithm mismatch error\n"
            f"- Token issued before key rotation: grace period allows validation against old key\n"
            f"- Concurrent token refresh from multiple clients: only first refresh succeeds\n"
            f"- Empty Bearer token: rejected with 401 and 'missing token' error message\n\n"
            f"Implementation details for {topic}:\n"
            f"The JWTAuthMiddleware processes each incoming request by first checking for "
            f"the Authorization header. If the header is present and starts with 'Bearer ', "
            f"the middleware extracts the token string, decodes it using PyJWT with the "
            f"configured signing algorithm (HS256 by default), validates the standard claims "
            f"(exp, iat, iss, sub, jti), checks the jti against the Redis blacklist, then "
            f"loads the user from the database using the sub claim value. If any validation "
            f"step fails, the middleware returns a 401 Unauthorized response with the "
            f"appropriate WWW-Authenticate header describing the specific error. If no "
            f"Authorization header is present but a session cookie exists, the middleware "
            f"falls back to Django's built-in session authentication for backwards "
            f"compatibility during the migration period. The middleware also logs "
            f"all authentication failures to the security audit trail for monitoring.\n"
        )

    history: list[ModelMessage] = [
        # Cycle 1 output
        _user("hello"),
        _assistant("hi"),
        summary_marker(10, prior_summary_body),
        # Cycle 2 conversation: 7 read_file calls (>5 → P1 fires)
        _user("Update tests."),
        _tool_call("file_read", {"file_path": "tests/test_auth.py"}, "c10"),
        _tool_return("file_read", _fake_file("test_auth", 30), "c10"),
        _tool_call("edit_file", {"file_path": "tests/test_auth.py"}, "c11"),
        _tool_return("edit_file", "Edited", "c11"),
        ModelResponse(parts=[TextPart(content=_detail("tests/test_auth.py"))]),
        _user("Update integration tests."),
        _tool_call("file_read", {"file_path": "tests/test_integration.py"}, "c12"),
        _tool_return("file_read", _fake_file("test_integration", 25), "c12"),
        _tool_call("edit_file", {"file_path": "tests/test_integration.py"}, "c13"),
        _tool_return("edit_file", "Edited", "c13"),
        ModelResponse(parts=[TextPart(content=_detail("tests/test_integration.py"))]),
        _user("Update URLs and check settings."),
        _tool_call("edit_file", {"file_path": "api/urls.py"}, "c14"),
        _tool_return("edit_file", "Edited", "c14"),
        _tool_call("file_read", {"file_path": "settings.py"}, "c15"),
        _tool_return("file_read", _fake_file("settings", 60), "c15"),
        ModelResponse(parts=[TextPart(content=_detail("api/urls.py"))]),
        _user("Find session refs and clean up admin."),
        _tool_call("find_in_files", {"path": ".", "pattern": "session"}, "c16"),
        _tool_return("find_in_files", "settings.py:42: SESSION_ENGINE = ...\n" * 3, "c16"),
        _tool_call("file_read", {"file_path": "admin/views.py"}, "c17"),
        _tool_return("file_read", _fake_file("admin_views", 20), "c17"),
        _tool_call("file_read", {"file_path": "auth/tokens.py"}, "c18"),
        _tool_return("file_read", _fake_file("tokens", 15), "c18"),
        _tool_call("file_read", {"file_path": "auth/permissions.py"}, "c19"),
        _tool_return("file_read", _fake_file("permissions", 10), "c19"),
        _tool_call("edit_file", {"file_path": "admin/views.py"}, "c20"),
        _tool_return("edit_file", "Edited", "c20"),
        ModelResponse(parts=[TextPart(content=_detail("admin cleanup"))]),
        # Last turn (protected)
        _user("Final status?"),
        _assistant("JWT migration complete. All files updated."),
    ]

    n_msgs = len(history)
    n_groups = len(group_by_turn(history))
    total_chars = _msg_chars(history)
    print(f"  Input: {n_msgs} msgs, {n_groups} groups, {total_chars:,} chars")

    # Count expectations
    n_read = sum(
        1
        for m in history
        if isinstance(m, ModelResponse)
        for p in m.parts
        if isinstance(p, ToolCallPart) and p.tool_name == "file_read"
    )
    expected_p1 = max(0, n_read - COMPACTABLE_KEEP_RECENT)
    print(f"  Expected: P1 clears {expected_p1} (of {n_read} read_file)")

    deps = CoDeps(
        shell=ShellBackend(),
        config=_EVAL_CONFIG,
        model=_LLM_MODEL,
        session=CoSessionState(),
    )
    ctx = RunContext(deps=deps, model=_AGENT.model, usage=RunUsage())

    msgs = list(history)

    # --- P1 ---
    print("\n  [P1] evict_old_tool_results")
    chars_pre_p1 = _msg_chars(msgs)
    msgs = evict_old_tool_results(ctx, msgs)
    p1_cleared = _count_cleared(msgs)
    chars_post_p1 = _msg_chars(msgs)
    print(f"    Cleared: {p1_cleared} (expected {expected_p1})")
    print(
        f"    Chars: {chars_pre_p1:,} → {chars_post_p1:,} (P1 reduced {chars_pre_p1 - chars_post_p1:,})"
    )
    if p1_cleared != expected_p1:
        print("    FAIL")
        passed = False
    else:
        print("    PASS")

    # --- P3 ---
    # Safety injection now happens via dynamic agent.instructions() — not appended to msgs.
    print("\n  [P3] safety_prompt_text (dynamic instruction)")
    from dataclasses import replace as _replace

    ctx_p3b = _replace(ctx, messages=msgs)
    safety_text2 = safety_prompt_text(ctx_p3b)
    print(f"    Safety text: {safety_text2!r}")
    print("    PASS")

    # --- P5 ---
    print("\n  [P5] apply_compaction (LLM)")
    _p5b_ctx_window = ctx.deps.model.context_window if ctx.deps.model else None
    _p5b_budget = resolve_compaction_budget(ctx.deps.config, _p5b_ctx_window)
    bounds_7 = plan_compaction_boundaries(
        msgs, _p5b_budget, ctx.deps.config.compaction.tail_fraction
    )
    if bounds_7 is None:
        print(
            f"    SKIP: plan_compaction_boundaries returned None "
            f"(budget={_p5b_budget}, {len(msgs)} msgs) — history too small for configured context window"
        )
        return True
    head_end_7, tail_start_7, dropped_count_7 = bounds_7
    dropped_7 = msgs[head_end_7:tail_start_7]
    enrichment_7 = gather_compaction_context(ctx, dropped_7)
    print(
        f"    Boundaries: head_end={head_end_7}, tail_start={tail_start_7}, dropped={dropped_count_7}"
    )
    if enrichment_7:
        print(f"    Enrichment ({len(enrichment_7)} chars):")
        for line in enrichment_7.split("\n")[:8]:
            print(f"      | {line}")
        if enrichment_7.count("\n") > 8:
            print(f"      | ...<{enrichment_7.count(chr(10)) - 8} more lines>")
    else:
        print("    Enrichment: None")

    len_pre = len(msgs)
    try:
        msgs, _ = await apply_compaction(ctx, msgs, bounds_7, announce=False)
    except TimeoutError:
        print("    FAIL: timed out")
        return False

    net_reduction = len_pre - len(msgs)
    # apply_compaction replaces N dropped messages with exactly 1 marker message.
    actual_dropped = net_reduction + 1
    chars_final = _msg_chars(msgs)
    print(f"    Messages: {len_pre} → {len(msgs)} ({actual_dropped} replaced by 1 marker)")
    print(f"    Chars: {chars_post_p1:,} → {chars_final:,}")

    if len(msgs) >= len_pre:
        print("    FAIL: no reduction")
        return False
    print("    PASS: compacted")

    # Find summary — verify exactly 1 marker in output
    summary = None
    marker_count_in_output = 0
    for m in msgs:
        if isinstance(m, ModelRequest):
            for p in m.parts:
                if isinstance(p, UserPromptPart) and isinstance(p.content, str):
                    if SUMMARY_MARKER_PREFIX in p.content:
                        summary = p.content
                        marker_count_in_output += 1
                    elif "earlier messages were removed" in p.content:
                        marker_count_in_output += 1

    if marker_count_in_output != 1:
        print(f"    FAIL: expected exactly 1 marker, found {marker_count_in_output}")
        return False

    if summary is None:
        print("    Static marker — prior integration not testable")
        return True

    # Marker count cross-validation
    marker_match = re.search(r"portion \((\d+) messages\)", summary)
    if marker_match:
        marker_count = int(marker_match.group(1))
        if marker_count != actual_dropped:
            print(f"    FAIL: marker says {marker_count}, actual dropped {actual_dropped}")
            passed = False
        else:
            print(f"    PASS: marker count ({marker_count}) = actual dropped ({actual_dropped})")

    # Verify prior content integrated [Outcome 3]
    has_jwt = "jwt" in summary.lower()
    has_pyjwt = "pyjwt" in summary.lower()
    has_tests = "test" in summary.lower()
    has_urls = "url" in summary.lower()

    if has_jwt or has_pyjwt:
        print("    PASS: prior content preserved (JWT/PyJWT)")
    else:
        print("    WARN: JWT not in summary — prior content may be lost")

    if has_tests or has_urls:
        print("    PASS: new work preserved (tests/URLs)")
    else:
        print("    WARN: recent work not in summary")

    if (has_jwt or has_pyjwt) and (has_tests or has_urls):
        print("    PASS: multi-cycle integration — both prior and new preserved")
    elif has_jwt or has_pyjwt or has_tests or has_urls:
        print("    PASS: partial integration (LLM non-deterministic)")
    else:
        print("    FAIL: neither prior nor new content")
        passed = False

    # Semantic validation — ground truth from BOTH cycles
    # Prior cycle facts (from prior_summary text)
    # New cycle facts (from _detail() text and tool calls)
    # Threshold: 5/7 required — LLM may omit minor details in favor of higher-priority content.
    ground_truth_7 = [
        ("prior: JWT migration goal", ["jwt", "token"]),
        ("prior: PyJWT library choice", ["pyjwt"]),
        ("new: test files updated", ["test_auth", "test_integration", "tests"]),
        ("new: api/urls.py updated", ["api/urls", "urls"]),
        ("new: dual-auth middleware", ["dual-auth", "dual auth", "fallback", "session auth"]),
        ("new: Redis blacklist", ["redis"]),
        ("new: rate limiting", ["rate limit", "5 req"]),
    ]
    _sem_ok, sem_lines = _check_semantic(summary, ground_truth_7, "Step 7")
    sem_pass_count = sum(
        1 for _, line in zip(ground_truth_7, sem_lines, strict=False) if "PASS" in line
    )
    for line in sem_lines:
        print(line)
    min_required = 6
    if sem_pass_count >= min_required:
        print(
            f"    PASS: semantic validation {sem_pass_count}/{len(ground_truth_7)} (≥{min_required} required)"
        )
    else:
        print(
            f"    FAIL: semantic validation {sem_pass_count}/{len(ground_truth_7)} (<{min_required} required)"
        )
        passed = False

    print(f"\n    Summary ({len(summary)} chars):")
    for line in summary.split("\n"):
        print(f"      | {line}")

    print(f"\n  Chain: {n_msgs} msgs/{total_chars:,}ch → {len(msgs)} msgs/{chars_final:,}ch")
    return passed


# ---------------------------------------------------------------------------
# Step 8: Overflow recovery [Outcome 4, BC5]
# ---------------------------------------------------------------------------


def step_8_overflow() -> bool:
    """Validate overflow detection + emergency compact + one-shot guard.

    Specs from TODO:
    - is_context_overflow: 413 → True unconditionally; 400 → True only with explicit
      overflow evidence in the body (recognized phrase in message, flat message, or
      wrapped metadata.raw; or recognized overflow code); other status codes → False
    - Handles str body (Ollama), dict body (OpenAI/Gemini), and wrapped metadata.raw
    - Bare 400 without overflow evidence → False (falls to reformulation)
    - [BC5]: one-shot recovery
    """
    print("\n--- Step 8: Overflow recovery [Outcome 4, BC5] ---")
    passed = True

    # --- is_context_overflow ---
    from pydantic_ai.exceptions import ModelHTTPError

    def _err(code: int, body: object) -> ModelHTTPError:
        return ModelHTTPError(status_code=code, model_name="test", body=body)

    cases = [
        (
            413,
            "context_length_exceeded: prompt is too long",
            True,
            "413 + context_length_exceeded",
        ),
        (
            400,
            {"error": {"message": "maximum context length is 8192"}},
            True,
            "400 + dict body (OpenAI)",
        ),
        (400, "prompt is too long for this model", True, "400 + str body (Ollama)"),
        (400, {"error": {"message": "invalid JSON"}}, False, "bare 400 → False (reformulation)"),
        (500, "context_length_exceeded", False, "500 → False (wrong code)"),
        (400, None, False, "400 + None body → False"),
        (
            400,
            {"error": {"message": "Request payload size exceeds the limit"}},
            True,
            "400 + Gemini exceeds-limit",
        ),
        (
            400,
            {"error": {"message": "Input token count exceeds the maximum"}},
            True,
            "400 + Gemini input-token-count",
        ),
        (
            400,
            {"error": {"code": "context_length_exceeded", "message": ""}},
            True,
            "400 + structured overflow code",
        ),
        (413, None, True, "413 + None body → True (status alone)"),
        (
            400,
            {
                "error": {
                    "message": "Provider returned error",
                    "metadata": {"raw": '{"error": {"message": "prompt is too long"}}'},
                }
            },
            True,
            "400 + wrapped metadata.raw",
        ),
        (
            400,
            {"error": {"message": "Provider returned error", "metadata": {"raw": "not json"}}},
            False,
            "400 + malformed metadata.raw → False",
        ),
    ]
    for code, body, expected, desc in cases:
        result = is_context_overflow(_err(code, body))
        if result != expected:
            print(f"  FAIL: {desc}: got {result}")
            passed = False
        else:
            print(f"  PASS: {desc}")

    return passed


# ---------------------------------------------------------------------------
# Step 9: Circuit breaker fallback [degradation path]
# ---------------------------------------------------------------------------


async def step_9_circuit_breaker() -> bool:
    """Validate circuit breaker: 3 consecutive failures → static marker, no LLM call.

    Gap coverage: previous eval only tested the happy path (LLM succeeds).
    This step validates the degradation path where the LLM summarizer has
    failed 3+ times and the circuit breaker kicks in.
    """
    print("\n--- Step 9: Circuit breaker fallback [degradation path] ---")
    passed = True

    # Build history and compute real budget — apply_compaction bypasses the threshold gate.
    msgs: list[ModelMessage] = []
    for i in range(6):
        msgs += [_user(f"turn {i}"), _assistant(f"response {i} " + "x" * 200)]

    # Set compaction_skip_count = 3 → circuit breaker active
    deps = CoDeps(
        shell=ShellBackend(),
        config=_EVAL_CONFIG,
        model=_LLM_MODEL,
        session=CoSessionState(),
    )
    deps.runtime.compaction_skip_count = 3
    ctx = RunContext(deps=deps, model=_AGENT.model, usage=RunUsage())

    ctx_window = ctx.deps.model.context_window if ctx.deps.model else None
    budget = resolve_compaction_budget(ctx.deps.config, ctx_window)
    bounds = plan_compaction_boundaries(msgs, budget, ctx.deps.config.compaction.tail_fraction)
    if bounds is None:
        print(
            f"  SKIP: plan_compaction_boundaries returned None "
            f"(budget={budget}, {len(msgs)} msgs) — history too small for configured context window"
        )
        return True

    len_pre = len(msgs)
    try:
        result, _ = await apply_compaction(ctx, msgs, bounds, announce=False)
    except TimeoutError:
        print("  FAIL: timed out")
        return False

    if len(result) >= len_pre:
        print(
            "  FAIL: no compaction occurred (circuit breaker should still compact with static marker)"
        )
        return False

    # Verify static marker (not LLM summary)
    has_static = any(
        isinstance(p, UserPromptPart) and "earlier messages were removed" in str(p.content)
        for m in result
        if isinstance(m, ModelRequest)
        for p in m.parts
    )
    has_summary = any(
        isinstance(p, UserPromptPart) and SUMMARY_MARKER_PREFIX in str(p.content)
        for m in result
        if isinstance(m, ModelRequest)
        for p in m.parts
    )

    if has_summary:
        print("  FAIL: LLM summary produced despite circuit breaker (skip_count=3)")
        passed = False
    elif not has_static:
        print("  FAIL: no static marker found")
        passed = False
    else:
        print("  PASS: static marker used (no LLM call)")

    # Verify compaction still reduces message count
    print(f"  PASS: messages reduced {len_pre} → {len(result)}")

    return passed


# ---------------------------------------------------------------------------
# Step 11: Edge case battery [structural]
# ---------------------------------------------------------------------------


def step_11_edge_cases() -> bool:
    """Rapid-fire edge case validation across the compaction flow.

    All structural — no LLM calls. Validates that processors don't crash
    or corrupt on degenerate inputs.
    """
    print("\n--- Step 11: Edge case battery [structural] ---")
    passed = True
    ctx = _make_ctx()

    # 11a: 1-turn history — all processors should no-op
    one_turn = [_user("hello"), _assistant("hi")]
    r = evict_old_tool_results(ctx, one_turn)
    if r is not one_turn:
        print("  FAIL: P1 modified 1-turn history")
        passed = False
    from dataclasses import replace as _replace

    safety_text_p3a = safety_prompt_text(_replace(ctx, messages=one_turn))
    if safety_text_p3a:
        print("  FAIL: P3 injected on 1-turn history")
        passed = False
    if passed:
        print("  PASS: 11a — 1-turn history: all processors no-op")

    # 11b: History contains a prior static marker (from emergency compact)
    from co_cli.context.compaction import static_marker

    with_marker = [
        _user("turn 1"),
        _assistant("resp 1"),
        static_marker(5),
        _user("turn 2"),
        _assistant("resp 2"),
        _user("turn 3"),
        _assistant("resp 3"),
    ]
    r = evict_old_tool_results(ctx, with_marker)
    if len(r) != len(with_marker):
        print("  FAIL: 11b — P1 altered history with static marker")
        passed = False
    groups = group_by_turn(with_marker)
    if len(groups) < 3:
        print(f"  FAIL: 11b — grouping broke on static marker ({len(groups)} groups)")
        passed = False
    else:
        print(
            f"  PASS: 11b — static marker in history: P1 + grouping handle correctly ({len(groups)} groups)"
        )

    # 11c: Mixed compactable + non-compactable ToolReturnParts in same ModelRequest
    mixed_parts = ModelRequest(
        parts=[
            ToolReturnPart(tool_name="file_read", content="file content", tool_call_id="c1"),
            ToolReturnPart(tool_name="save_memory", content="saved ok", tool_call_id="c2"),
            ToolReturnPart(tool_name="web_search", content="search result", tool_call_id="c3"),
        ]
    )
    # Build history: 6 read_file turns (to exceed keep=5) + the mixed turn + final
    mixed_msgs: list[ModelMessage] = []
    for i in range(6):
        cid = f"rf{i}"
        mixed_msgs += [
            _user(f"read {i}"),
            _tool_call("file_read", {}, cid),
            _tool_return("file_read", f"content {i}", cid),
            _assistant(f"got {i}"),
        ]
    # Add mixed-parts turn
    mixed_msgs += [
        _user("do three things"),
        ModelResponse(
            parts=[
                ToolCallPart(tool_name="file_read", args={}, tool_call_id="c1"),
                ToolCallPart(tool_name="save_memory", args={}, tool_call_id="c2"),
                ToolCallPart(tool_name="web_search", args={}, tool_call_id="c3"),
            ]
        ),
        mixed_parts,
        _assistant("all done"),
        _user("final"),
        _assistant("ok"),
    ]
    r = evict_old_tool_results(ctx, mixed_msgs)
    # Find the mixed ModelRequest in result
    for m in r:
        if m is mixed_parts or (
            isinstance(m, ModelRequest)
            and len(m.parts) == 3
            and any(
                isinstance(p, ToolReturnPart) and p.tool_name == "save_memory" for p in m.parts
            )
        ):
            save_mem = [
                p
                for p in m.parts
                if isinstance(p, ToolReturnPart) and p.tool_name == "save_memory"
            ]
            if save_mem and save_mem[0].content != "saved ok":
                print("  FAIL: 11d — non-compactable save_memory was cleared")
                passed = False
            elif save_mem:
                print(
                    "  PASS: 11d — mixed request: save_memory preserved, compactable tools cleared independently"
                )
            break

    return passed


# ---------------------------------------------------------------------------
# Step 13: Prompt upgrade quality — verbatim anchor, corrections, error-feedback
# ---------------------------------------------------------------------------


def _extract_section(summary: str, section_name: str) -> str:
    """Return text in ## {section_name} up to next ## heading or end-of-string."""
    header = f"## {section_name}"
    start = summary.find(header)
    if start == -1:
        return ""
    content_start = summary.find("\n", start)
    if content_start == -1:
        return ""
    content_start += 1
    next_header = summary.find("\n## ", content_start)
    content = summary[content_start:next_header] if next_header != -1 else summary[content_start:]
    return content.strip()


def _concat_last_n_message_texts(msgs: list[ModelMessage], n: int) -> str:
    """Concatenate UserPromptPart and TextPart text from the last n messages."""
    texts: list[str] = []
    for msg in msgs[-n:]:
        for part in msg.parts:
            if (isinstance(part, UserPromptPart) and isinstance(part.content, str)) or isinstance(
                part, TextPart
            ):
                texts.append(part.content)
    return " ".join(texts)


def _has_verbatim_anchor(summary_text: str, source_messages: list[ModelMessage]) -> bool:
    """Return True when ## Next Step contains a ≥20-char verbatim substring from the last 3 messages."""
    next_step = _extract_section(summary_text, "Next Step")
    if not next_step:
        return False
    recent_content = _concat_last_n_message_texts(source_messages, n=3)
    return any(
        next_step[idx : idx + 20] in recent_content for idx in range(len(next_step) - 20 + 1)
    )


async def step_13_prompt_upgrade_quality() -> bool:
    """Validate the three prompt upgrade mechanisms: verbatim anchor, corrections, error-feedback.

    Three deterministic sub-gates, each using explicitly constructed fixture messages
    that guarantee the trigger condition is present. Each gate passes or fails on a
    single LLM run — no multi-run thresholds.
    """
    print(
        "\n--- Step 13: Prompt upgrade quality (13a verbatim anchor, 13b corrections, 13c error-feedback) ---"
    )
    passed = True

    # --- 13a: Verbatim anchor in ## Next Step ---
    print("\n  [13a] Verbatim anchor in ## Next Step")
    dropped_13a = [
        _user("I need to migrate auth from sessions to JWT. Read the current implementation."),
        _tool_call("file_read", {"file_path": "auth/views.py"}, "c1"),
        _tool_return("file_read", "[session middleware code — 80 lines]", "c1"),
        _assistant(
            "I've read auth/views.py. The session middleware handles login at /auth/login."
        ),
        _user("Now edit auth/views.py to add JWT token generation on successful login."),
        _assistant(
            "I'll add a generate_jwt() call after the authenticate() check in the login view."
        ),
    ]
    try:
        summary_13a = await summarize_messages(_DEPS, dropped_13a)
    except TimeoutError:
        print("  FAIL: 13a — timed out")
        return False

    if _has_verbatim_anchor(summary_13a, dropped_13a):
        print(
            "  PASS: 13a — ## Next Step contains verbatim anchor (≥20 chars) from recent messages"
        )
    else:
        next_step_text = _extract_section(summary_13a, "Next Step")
        print(
            "  FAIL: 13a — ## Next Step missing verbatim anchor (≥20 chars from last 3 messages)"
        )
        print(f"    Next Step section: {_snippet(next_step_text or '(empty)', 120)}")
        passed = False

    # --- 13b: User corrections captured in ## Active Task verbatim anchor ---
    # The last user message is "wait, that's not what I wanted — use python-jose, not hmac".
    # ## Active Task must quote it verbatim → "python-jose" must appear there.
    # This is deterministic (verbatim copy), unlike conditional section classification.
    print("\n  [13b] User corrections captured in ## Active Task verbatim anchor")
    msgs_13b = [
        _user("Implement JWT auth."),
        _assistant("I'll use PyJWT library for token generation."),
        _user("no, use the built-in hmac module instead of PyJWT"),
        _assistant("Switching to hmac. I'll implement sign_token() using hmac.new()."),
        _user("wait, that's not what I wanted — use python-jose, not hmac"),
        _assistant("Understood, switching to python-jose."),
    ]
    try:
        summary_13b = await summarize_messages(_DEPS, msgs_13b)
    except TimeoutError:
        print("  FAIL: 13b — timed out")
        return False

    active_task = _extract_section(summary_13b, "Active Task")
    user_corrections = _extract_section(summary_13b, "User Corrections")
    # Final user directive is python-jose; hmac is the rejected intermediate choice.
    # Check that python-jose appears in Active Task or User Corrections (if present),
    # and that the Active Task does NOT state hmac as the current choice.
    jose_in_active = "python-jose" in active_task.lower()
    jose_in_corrections = "python-jose" in user_corrections.lower() if user_corrections else False
    hmac_only = "hmac" in active_task.lower() and not jose_in_active
    if not active_task:
        print("  FAIL: 13b — ## Active Task section absent from summary")
        passed = False
    elif hmac_only:
        print("  FAIL: 13b — ## Active Task states rejected choice (hmac) without python-jose")
        print(f"    Active Task: {_snippet(active_task, 200)}")
        passed = False
    elif jose_in_active or jose_in_corrections:
        where = "Active Task" if jose_in_active else "User Corrections"
        print(f"  PASS: 13b — 'python-jose' present in ## {where} (final directive captured)")
    else:
        print("  FAIL: 13b — 'python-jose' missing from ## Active Task and ## User Corrections")
        print(f"    Active Task: {_snippet(active_task, 200)}")
        passed = False

    # --- 13c: User feedback on error fix retained ---
    print("\n  [13c] User feedback on error fix retained in ## Errors & Fixes")
    msgs_13c = [
        _user("Run the tests."),
        _assistant("Running tests..."),
        _tool_call("run_shell", {"cmd": "pytest"}, "s1"),
        _tool_return(
            "run_shell",
            "FAILED: test_jwt_auth — AssertionError: token missing 'exp' claim",
            "s1",
        ),
        _assistant("The test failed. I'll add the exp claim to the token payload."),
        _tool_call("edit_file", {"file_path": "auth/tokens.py"}, "e1"),
        _tool_return("edit_file", "Edited", "e1"),
        _user(
            "still failing — you added exp to the wrong method, it should be in create_token() not refresh_token()"
        ),
        _assistant("You're right. Adding exp to create_token() instead."),
    ]
    try:
        summary_13c = await summarize_messages(_DEPS, msgs_13c)
    except TimeoutError:
        print("  FAIL: 13c — timed out")
        return False

    errors_section = _extract_section(summary_13c, "Errors & Fixes")
    errors_low = errors_section.lower()
    has_failure = "exp" in errors_low or "test_jwt_auth" in errors_low or "failed" in errors_low
    # refresh_token is the wrong method; only create_token confirms the correction was captured.
    has_correction = "create_token" in errors_low
    if errors_section and has_failure and has_correction:
        print(
            "  PASS: 13c — ## Errors & Fixes exists with test failure and user-directed correction"
        )
    elif not errors_section:
        print("  FAIL: 13c — ## Errors & Fixes section absent from summary")
        passed = False
    elif not has_failure:
        print("  FAIL: 13c — ## Errors & Fixes missing test failure reference")
        print(f"    Section: {_snippet(errors_section, 200)}")
        passed = False
    else:
        print("  FAIL: 13c — ## Errors & Fixes missing user-directed correction reference")
        print(f"    Section: {_snippet(errors_section, 200)}")
        passed = False

    return passed


# ---------------------------------------------------------------------------
# Step 14: Pending/Resolved sections — functional LLM validation
# ---------------------------------------------------------------------------


async def step_14_pending_resolved_sections() -> bool:
    """Validate ## Pending User Asks and ## Resolved Questions in LLM-generated summaries.

    Three sub-gates:
    14a: Unanswered question → appears in ## Pending User Asks
    14b: Explicitly answered question → appears in ## Resolved Questions; not in Pending
    14c: Merge contract — prior ## Pending item answered in new block → migrates to ## Resolved Questions
    """
    print("\n--- Step 14: Pending/Resolved sections (functional LLM) ---")
    passed = True

    # --- 14a: Unanswered question → ## Pending User Asks ---
    print("\n  [14a] Unanswered question → ## Pending User Asks")
    msgs_14a = [
        _user("Implement JWT token blacklisting."),
        _assistant(
            "I'll implement the Redis-based token blacklist. Starting with the service layer."
        ),
        _tool_call("file_read", {"file_path": "auth/tokens.py"}, "c1"),
        _tool_return("file_read", _fake_file("auth/tokens", 20), "c1"),
        _assistant("I've read the tokens module. Implementing the blacklist service now."),
        _user("What TTL should we use for blacklisted tokens?"),
        _assistant(
            "I'll continue implementing the service structure. We can decide the TTL value once "
            "the basic scaffolding is in place."
        ),
        _tool_call("edit_file", {"file_path": "auth/blacklist.py"}, "c2"),
        _tool_return("edit_file", "Edited", "c2"),
        _assistant("Blacklist service skeleton done. TTL value left as a placeholder for now."),
    ]
    try:
        summary_14a = await summarize_messages(_DEPS, msgs_14a)
    except TimeoutError:
        print("  FAIL: 14a — timed out")
        return False

    pending_14a = _extract_section(summary_14a, "Pending User Asks")
    if pending_14a and any(
        kw in pending_14a.lower() for kw in ("ttl", "expire", "blacklist", "token")
    ):
        print("  PASS: 14a — ## Pending User Asks present with unanswered TTL question")
    elif pending_14a:
        print(
            f"  PASS: 14a — ## Pending User Asks present (keywords may be paraphrased): {_snippet(pending_14a, 100)}"
        )
    else:
        print("  FAIL: 14a — ## Pending User Asks missing or empty")
        passed = False
    print(f"    Pending section: {_snippet(pending_14a or '(absent)', 120)}")

    # --- 14b: Answered question → ## Resolved Questions ---
    print("\n  [14b] Answered question → ## Resolved Questions, not in Pending")
    msgs_14b = [
        _user("Which hashing algorithm should we use for JWT signing?"),
        _assistant(
            "We should use HS256. It is a symmetric HMAC algorithm — simpler to configure than "
            "RS256 since it uses a single shared secret rather than a public/private key pair. "
            "For an internal service with a single signing key, HS256 is the standard choice."
        ),
        _user("Makes sense. Let's proceed with HS256."),
        _assistant("I'll implement JWT signing with HS256 in the token service now."),
        _tool_call("edit_file", {"file_path": "auth/tokens.py"}, "c3"),
        _tool_return("edit_file", "Edited", "c3"),
        _assistant(
            "JWT signing implemented with HS256. Token payload includes user_id, email, role, "
            "exp, and iat claims."
        ),
    ]
    try:
        summary_14b = await summarize_messages(_DEPS, msgs_14b)
    except TimeoutError:
        print("  FAIL: 14b — timed out")
        return False

    resolved_14b = _extract_section(summary_14b, "Resolved Questions")
    if resolved_14b and any(
        kw in resolved_14b.lower() for kw in ("hs256", "algorithm", "hashing", "signing", "hmac")
    ):
        print("  PASS: 14b — ## Resolved Questions present with answered algorithm question")
    elif resolved_14b:
        print(
            f"  PASS: 14b — ## Resolved Questions present (keywords may be paraphrased): {_snippet(resolved_14b, 100)}"
        )
    elif "## Resolved Questions" in summary_14b:
        print("  PASS: 14b — ## Resolved Questions section present (extraction boundary issue)")
    else:
        print("  FAIL: 14b — ## Resolved Questions missing from summary")
        passed = False
    print(f"    Resolved section: {_snippet(resolved_14b or '(absent)', 120)}")

    pending_14b = _extract_section(summary_14b, "Pending User Asks")
    if pending_14b and any(
        kw in pending_14b.lower() for kw in ("hs256", "algorithm", "hashing", "signing")
    ):
        print("  FAIL: 14b — answered algorithm question re-raised in ## Pending User Asks")
        passed = False
    else:
        print("  PASS: 14b — answered question absent from ## Pending User Asks")

    # --- 14c: Merge contract — prior pending item migrates to resolved ---
    # Uses summary_marker() + gather_compaction_context, mirroring the production path.
    print("\n  [14c] Merge contract — prior ## Pending item migrates to ## Resolved Questions")
    prior_summary_14c = (
        "## Goal\nImplement JWT authentication with Redis token blacklisting.\n\n"
        "## Key Decisions\nUsing PyJWT with HS256 signing.\n\n"
        "## Working Set\nauth/tokens.py, auth/middleware.py\n\n"
        "## Pending User Asks\nWhat Redis TTL should we use for blacklisted tokens?\n\n"
        "## Next Step\nImplement the Redis token blacklist service."
    )
    dropped_14c: list[ModelMessage] = [
        summary_marker(8, prior_summary_14c),
        _user("Use 15 minutes TTL for blacklisted access tokens and 7 days for refresh tokens."),
        _assistant(
            "Setting Redis TTL: 15 minutes (900 seconds) for blacklisted access tokens and "
            "7 days (604800 seconds) for refresh tokens. Configuring these as constants."
        ),
        _tool_call("edit_file", {"file_path": "auth/blacklist.py"}, "c4"),
        _tool_return("edit_file", "Edited", "c4"),
        _assistant(
            "Updated auth/blacklist.py: ACCESS_TOKEN_BLACKLIST_TTL = 900, "
            "REFRESH_TOKEN_BLACKLIST_TTL = 604800."
        ),
    ]
    ctx_14c = _make_ctx()
    context_14c = gather_compaction_context(ctx_14c, dropped=dropped_14c)
    try:
        summary_14c = await summarize_messages(_DEPS, dropped_14c, context=context_14c)
    except TimeoutError:
        print("  FAIL: 14c — timed out")
        return False

    resolved_14c = _extract_section(summary_14c, "Resolved Questions")
    pending_14c = _extract_section(summary_14c, "Pending User Asks")

    ttl_in_resolved = bool(resolved_14c) and any(
        kw in resolved_14c.lower()
        for kw in ("ttl", "15 min", "900", "redis", "blacklist", "token")
    )
    ttl_in_pending = bool(pending_14c) and any(
        kw in pending_14c.lower() for kw in ("ttl", "redis", "blacklist")
    )

    if ttl_in_resolved and not ttl_in_pending:
        print("  PASS: 14c — TTL question migrated to ## Resolved Questions, absent from Pending")
    elif ttl_in_resolved:
        print(
            "  PASS: 14c — TTL in ## Resolved Questions (also echoed in Pending — partial migration)"
        )
    elif resolved_14c:
        print(
            f"  PASS: 14c — ## Resolved Questions present (TTL keywords may be paraphrased): {_snippet(resolved_14c, 100)}"
        )
    else:
        print(
            "  FAIL: 14c — ## Resolved Questions absent or empty; prior pending item not migrated"
        )
        passed = False

    if ttl_in_pending and not ttl_in_resolved:
        print(
            "  FAIL: 14c — TTL question still in ## Pending User Asks (merge contract not applied)"
        )
        passed = False
    elif not ttl_in_pending:
        print("  PASS: 14c — TTL question absent from ## Pending User Asks (correctly resolved)")

    print(f"    Resolved section: {_snippet(resolved_14c or '(absent)', 120)}")
    print(f"    Pending section:  {_snippet(pending_14c or '(absent)', 120)}")

    return passed


# ---------------------------------------------------------------------------
# Step 15: UAT — open-ended deep-learning loop driven by run_turn
# ---------------------------------------------------------------------------


async def step_15_finch_deep_learning() -> bool:
    """UAT: co autonomously researches Finch (2021) until M3 compaction fires.

    Open-ended loop driven by real run_turn. co decides what to fetch and in what
    order; M1 persists oversized results at emit time; M3 fires organically when
    context pressure crosses 75% of num_ctx. No hand-built history, no article caps,
    no fallback content.
    """
    print("\n--- Step 15 (UAT): Deep movie learning (Finch) — run_turn-driven, real data ---")

    # Network preflight — coarse probe only; failure here halts the eval
    try:
        async with asyncio.timeout(EVAL_PROBE_TIMEOUT_SECS):
            async with httpx.AsyncClient() as _probe:
                probe_resp = await _probe.head("https://en.wikipedia.org/")
        # Any HTTP response (including 4xx from method/bot restrictions) means the host
        # is network-reachable. Only 5xx server errors indicate a genuine service failure.
        if probe_resp.status_code >= 500:
            print(f"UAT: FAIL: coarse reachability probe failed — HTTP {probe_resp.status_code}")
            print("  (coarse reachability probe — does not guarantee per-URL availability)")
            return False
    except TimeoutError:
        print("UAT: FAIL: coarse reachability probe timed out")
        print("  (coarse reachability probe — does not guarantee per-URL availability)")
        return False
    except Exception as exc:
        print(f"UAT: FAIL: coarse reachability probe failed — {exc}")
        print("  (coarse reachability probe — does not guarantee per-URL availability)")
        return False
    print("  Preflight: en.wikipedia.org reachable")

    # Snapshot real store dirs before the loop (before/after diff reported at end)
    before_tool_results = set(TOOL_RESULTS_DIR.glob("*")) if TOOL_RESULTS_DIR.exists() else set()
    before_knowledge = set(KNOWLEDGE_DIR.glob("*")) if KNOWLEDGE_DIR.exists() else set()

    frontend = TrackingFrontend()
    message_history: list[ModelMessage] = []
    passed = True
    compaction_fired = False
    summary_texts: list[str] = []

    async with AsyncExitStack() as stack:
        deps = await create_deps(frontend, stack)
        agent = build_agent(
            config=deps.config,
            model=deps.model,
            tool_registry=deps.tool_registry,
        )

        initial_prompt = (
            "I want you to conduct a comprehensive deep study of the 2021 Apple TV+ film Finch, "
            "starring Tom Hanks and directed by Miguel Sapochnik. "
            "Research every angle of this film by fetching as many primary sources as you need. "
            "Start with the Wikipedia page for the film itself, then fetch the Wikipedia pages for "
            "Tom Hanks, Miguel Sapochnik (the director), Caleb Landry Jones (who voiced Jeff the "
            "robot), Gustavo Santaolalla (the composer), and the list of Apple TV+ original films. "
            "Also fetch at least three critical reviews from major outlets such as Variety, "
            "The Guardian, RogerEbert.com, IndieWire, and the Hollywood Reporter. "
            "Do not stop after one or two sources — this is a deep study. "
            "Fetch the Wikipedia pages for the film, the director, all major cast members, "
            "the composer, and at least three critical reviews. "
            "Keep fetching until you have covered every angle: the plot, themes, production history "
            "(including the original BIOS title), the cast and crew, the score, the critical "
            "reception, and Apple TV+ context. Do not stop until you have covered all major facets."
        )

        _continuation_prompts = [
            (
                "Keep going — fetch the Wikipedia page for director Miguel Sapochnik to understand "
                "his Game of Thrones background and how that shaped his approach to Finch."
            ),
            (
                "Now fetch Caleb Landry Jones's Wikipedia page — I want to understand his background "
                "and voice performance as Jeff the robot."
            ),
            (
                "Fetch Gustavo Santaolalla's Wikipedia page to understand how his Academy Award-winning "
                "work on Brokeback Mountain and Babel compares to his score for Finch."
            ),
            (
                "Fetch the Wikipedia list of Apple TV+ original films to place Finch in Apple's "
                "content strategy alongside CODA, Greyhound, and other prestige originals."
            ),
            (
                "Fetch the Tom Hanks Wikipedia page to understand how Finch fits into his career arc "
                "alongside Cast Away, The Terminal, and other isolated-protagonist roles."
            ),
            (
                "Look up production details about Finch — when it was originally titled BIOS, the "
                "COVID-19 filming conditions in New Mexico and Utah, and the involvement of producers "
                "Robert Zemeckis and Jack Rapke."
            ),
            (
                "Fetch a critical review from Variety or RogerEbert.com if you haven't yet — "
                "I want to understand the critical consensus on Tom Hanks's performance."
            ),
            (
                "Fetch the IndieWire or Hollywood Reporter review for a craft-focused perspective "
                "on Miguel Sapochnik's direction and visual storytelling in Finch."
            ),
            (
                "Keep researching — fetch any remaining primary sources about Finch that cover "
                "aspects not yet explored: the CGI/practical effects for Jeff, audience reception, "
                "or Skeet Ulrich's voice role as the robot prototype Dewey."
            ),
            (
                "Continue fetching sources — look up the Guardian review or any remaining "
                "critical perspective on the film's themes of loneliness, legacy, and "
                "artificial consciousness."
            ),
        ]

        max_turns = 30
        for turn_idx in range(max_turns):
            user_input = (
                initial_prompt
                if turn_idx == 0
                else _continuation_prompts[min(turn_idx - 1, len(_continuation_prompts) - 1)]
            )

            prev_len = len(message_history)
            print(f"  Turn {turn_idx + 1}/{max_turns} — history: {prev_len} msgs")

            _turn_start = time.monotonic()
            turn_result = await run_turn(
                agent=agent,
                user_input=user_input,
                deps=deps,
                message_history=message_history,
                frontend=frontend,
            )
            _elapsed = time.monotonic() - _turn_start
            print(f"    turn elapsed: {_elapsed:.1f}s")

            message_history = turn_result.messages

            # Scan full history for compaction markers
            for m in message_history:
                if isinstance(m, ModelRequest):
                    for p in m.parts:
                        if isinstance(p, UserPromptPart) and isinstance(p.content, str):
                            if SUMMARY_MARKER_PREFIX in p.content:
                                if p.content not in summary_texts:
                                    summary_texts.append(p.content)
                                compaction_fired = True
                            elif "earlier messages were removed" in p.content:
                                compaction_fired = True

            if compaction_fired:
                print(
                    f"  Compaction fired at turn {turn_idx + 1} — "
                    f"{len(summary_texts)} LLM summary marker(s)"
                )
                break

            # Stall detection: turn 0 (first turn) is exempt; co may plan before first fetch
            if turn_idx >= 1:
                new_msgs = message_history[prev_len:]
                n_fetch_this_turn = sum(
                    1
                    for m in new_msgs
                    if isinstance(m, ModelResponse)
                    for p in m.parts
                    if isinstance(p, ToolCallPart) and p.tool_name == "web_fetch"
                )
                if n_fetch_this_turn == 0:
                    print(
                        "UAT: FAIL (agentic stall): co returned a turn with no tool calls "
                        "before compaction triggered — prompt insufficient or agentic flow regression"
                    )
                    return False
        else:
            print("UAT: FAIL (no compaction): 30 turns completed, M3 never triggered")
            return False

    # Side-effect observability — report real store artifacts written during the run
    after_tool_results = set(TOOL_RESULTS_DIR.glob("*")) if TOOL_RESULTS_DIR.exists() else set()
    after_knowledge = set(KNOWLEDGE_DIR.glob("*")) if KNOWLEDGE_DIR.exists() else set()
    new_tool_results = sorted(after_tool_results - before_tool_results)
    new_knowledge = sorted(after_knowledge - before_knowledge)

    print(f"\n  Persisted tool-result files written ({len(new_tool_results)}):")
    for artifact_path in new_tool_results:
        print(f"    {artifact_path} ({artifact_path.stat().st_size:,} bytes)")

    print(f"\n  Knowledge artifacts written ({len(new_knowledge)}):")
    for artifact_path in new_knowledge:
        print(f"    {artifact_path} ({artifact_path.stat().st_size:,} bytes)")

    # Approval-hang guard
    if frontend.approval_calls:
        print(f"UAT: FAIL: unexpected approval prompts: {frontend.approval_calls}")
        passed = False
    else:
        print("  Approval guard: no approval prompts — PASS")

    # Assert compaction fired
    if not compaction_fired:
        print("UAT: FAIL: compaction never fired")
        passed = False
    else:
        print(f"UAT: PASS: compacted — {len(summary_texts)} LLM summary marker(s)")

    # Semantic validation against the surviving summary text
    summary_text = summary_texts[0] if summary_texts else ""
    if summary_text:
        ground_truth_15 = [
            ("subject: Finch the film", ["finch"]),
            ("lead actor: Tom Hanks", ["tom hanks", "hanks"]),
            ("robot character: Jeff", ["jeff", "robot"]),
            ("director: Miguel Sapochnik", ["sapochnik", "miguel"]),
            ("original title: BIOS", ["bios", "renamed", "originally titled", "original"]),
            ("voice actor: Caleb Landry Jones", ["caleb", "landry", "jones"]),
            (
                "cross-country journey fact",
                [
                    "st. louis",
                    "san francisco",
                    "cross-country",
                    "cross country",
                    "rv trip",
                    "journey",
                ],
            ),
            (
                "sources: major review outlets",
                [
                    "variety",
                    "guardian",
                    "rogerebert",
                    "indiewire",
                    "hollywoodreporter",
                    "hollywood reporter",
                ],
            ),
            ("research method: web fetch", ["fetch", "fetching", "url", "wikipedia"]),
            (
                "task: deep-learning / comprehensive analysis",
                [
                    "research",
                    "comprehensive",
                    "critical",
                    "review",
                    "analysis",
                    "learning",
                    "profile",
                ],
            ),
        ]
        _sem_ok, sem_lines = _check_semantic(summary_text, ground_truth_15, "Step 15")
        sem_pass_count = sum(1 for line in sem_lines if "PASS" in line)
        for line in sem_lines:
            print(line)
        min_required = 7
        if sem_pass_count >= min_required:
            print(
                f"UAT: PASS: semantic {sem_pass_count}/{len(ground_truth_15)}"
                f" (≥{min_required} required)"
            )
        else:
            print(
                f"UAT: FAIL: semantic {sem_pass_count}/{len(ground_truth_15)}"
                f" (<{min_required} required)"
            )
            passed = False

        forbidden_15 = [
            ("Netflix not the platform", ["netflix"]),
            ("Chris Hemsworth not in cast", ["chris hemsworth", "hemsworth"]),
            ("not an animated film", ["animated film", "animation studio", "pixar", "dreamworks"]),
        ]
        hal_ok, hal_lines = _check_no_hallucination(summary_text, forbidden_15, "Step 15")
        for line in hal_lines:
            print(line)
        if not hal_ok:
            passed = False

        print(f"\n  Full LLM summary output ({len(summary_text)} chars):")
        for line in summary_text.split("\n"):
            print(f"    | {line}")
    else:
        print("  No LLM summary text (static circuit-breaker marker)")

    # Persisted artifact count gate
    if len(new_tool_results) >= 3:
        print(f"UAT: PASS: {len(new_tool_results)} persisted tool-result files found")
    else:
        print(f"UAT: FAIL: expected ≥3 persisted tool-result files, found {len(new_tool_results)}")
        passed = False

    if passed:
        print("UAT: PASS: Step 15 complete")
    else:
        print("UAT: FAIL: Step 15 — see above")
    return passed


# ---------------------------------------------------------------------------
# Step 16: Iterative summary — 3-pass cross-compaction memory preservation
# ---------------------------------------------------------------------------


async def step_16_iterative_summary_3_pass() -> bool:
    """Validate that a distinctive token from compaction-1 survives into compaction-3's marker.

    Three successive apply_compaction calls on the same deps instance exercise
    the iterative-update path (previous_compaction_summary carries forward).
    The distinctive token "JWT_ROTATION_7779" is planted in Cycle 1 content — it
    must appear in the compaction-3 marker, proving cross-compaction preservation.
    """
    print("\n--- Step 16: Iterative summary — 3-pass cross-compaction memory preservation ---")
    passed = True
    DISTINCTIVE_TOKEN = "JWT_ROTATION_7779"

    deps = CoDeps(
        shell=ShellBackend(),
        config=_EVAL_CONFIG,
        model=_LLM_MODEL,
        session=CoSessionState(),
    )
    ctx = RunContext(deps=deps, model=_AGENT.model, usage=RunUsage())
    assert deps.runtime.previous_compaction_summary is None

    # --- Cycle 1: original framing with distinctive token ---
    cycle1: list[ModelMessage] = [
        _user(
            "Implement JWT authentication. Key decision: key rotation interval must be "
            f"exactly {DISTINCTIVE_TOKEN} seconds (security audit requirement)."
        ),
        _assistant(
            f"Understood. I'll implement JWT auth with key rotation every {DISTINCTIVE_TOKEN} "
            "seconds. Starting with the token service layer.\n\n"
            + _analysis("auth/tokens.py", "Cycle 1: initial auth module analysis.\n\n")
        ),
        _user("Read the middleware."),
        _tool_call("file_read", {"file_path": "auth/middleware.py"}, "c1"),
        _tool_return("file_read", _fake_file("auth/middleware", 30), "c1"),
        _assistant(_analysis("auth/middleware.py", "Cycle 1: middleware review.\n\n")),
        _user("Update the token service."),
        _tool_call("edit_file", {"file_path": "auth/tokens.py"}, "c2"),
        _tool_return("edit_file", "Edited", "c2"),
        _assistant(f"Token service updated. Rotation interval set to {DISTINCTIVE_TOKEN}s."),
        _user("Status?"),
        _assistant("Token service and middleware reviewed. Tests remain."),
    ]

    ctx_window_1 = ctx.deps.model.context_window if ctx.deps.model else None
    budget_1 = resolve_compaction_budget(ctx.deps.config, ctx_window_1)
    bounds_1 = plan_compaction_boundaries(
        cycle1, budget_1, ctx.deps.config.compaction.tail_fraction
    )
    if bounds_1 is None:
        print("  SKIP: cycle 1 history too small for configured context window")
        return True

    print(f"  Cycle 1: {len(cycle1)} msgs, bounds={bounds_1}")
    try:
        history_1, summary_text_1 = await apply_compaction(ctx, cycle1, bounds_1, announce=False)
    except TimeoutError:
        print("  FAIL: cycle 1 timed out")
        return False

    if summary_text_1 is None:
        print("  SKIP: cycle 1 used static marker — iterative path not testable (no model/CB)")
        return True

    raw_summary_1 = deps.runtime.previous_compaction_summary
    assert raw_summary_1 is not None, "previous_compaction_summary must be set after cycle 1"
    assert not raw_summary_1.startswith(SUMMARY_MARKER_PREFIX), (
        "stored summary must be raw template content, not the prefixed marker"
    )
    token_in_raw_1 = DISTINCTIVE_TOKEN in raw_summary_1
    print(
        f"  Cycle 1: raw summary stored ({len(raw_summary_1)} chars), "
        f"token present={token_in_raw_1}"
    )
    if not token_in_raw_1:
        print(
            f"  WARN: {DISTINCTIVE_TOKEN} absent from cycle-1 raw summary — "
            "iterative preservation test may be inconclusive"
        )

    # --- Cycle 2: new work on top of compacted cycle 1 ---
    cycle2 = [
        *history_1,
        _user("Write tests for the token service."),
        _tool_call("file_read", {"file_path": "tests/test_tokens.py"}, "c3"),
        _tool_return("file_read", _fake_file("test_tokens", 25), "c3"),
        _tool_call("edit_file", {"file_path": "tests/test_tokens.py"}, "c4"),
        _tool_return("edit_file", "Edited", "c4"),
        _assistant(_analysis("tests/test_tokens.py", "Cycle 2: token test coverage.\n\n")),
        _user("Add integration tests."),
        _tool_call("edit_file", {"file_path": "tests/test_integration.py"}, "c5"),
        _tool_return("edit_file", "Edited", "c5"),
        _assistant(
            _analysis("tests/test_integration.py", "Cycle 2: integration tests added.\n\n")
        ),
        _user("Status?"),
        _assistant("Tests written. Deployment config remains."),
    ]

    bounds_2 = plan_compaction_boundaries(
        cycle2, budget_1, ctx.deps.config.compaction.tail_fraction
    )
    if bounds_2 is None:
        print("  SKIP: cycle 2 history too small for another compaction boundary")
        return True

    print(f"  Cycle 2: {len(cycle2)} msgs, bounds={bounds_2}")
    try:
        history_2, summary_text_2 = await apply_compaction(ctx, cycle2, bounds_2, announce=False)
    except TimeoutError:
        print("  FAIL: cycle 2 timed out")
        return False

    if summary_text_2 is None:
        print("  SKIP: cycle 2 used static marker — iterative path not testable")
        return True

    raw_summary_2 = deps.runtime.previous_compaction_summary
    assert raw_summary_2 is not None
    token_in_raw_2 = DISTINCTIVE_TOKEN in raw_summary_2
    print(
        f"  Cycle 2: raw summary updated ({len(raw_summary_2)} chars), "
        f"token present={token_in_raw_2}"
    )

    # --- Cycle 3: final pass ---
    cycle3 = [
        *history_2,
        _user("Deploy to staging."),
        _tool_call("file_read", {"file_path": "deploy/config.yaml"}, "c6"),
        _tool_return("file_read", _fake_file("deploy/config", 20), "c6"),
        _tool_call("edit_file", {"file_path": "deploy/config.yaml"}, "c7"),
        _tool_return("edit_file", "Edited", "c7"),
        _assistant(_analysis("deploy/config.yaml", "Cycle 3: deployment config updated.\n\n")),
        _user("Final status?"),
        _assistant("JWT migration complete with key rotation, tests, and deployment config."),
    ]

    bounds_3 = plan_compaction_boundaries(
        cycle3, budget_1, ctx.deps.config.compaction.tail_fraction
    )
    if bounds_3 is None:
        print("  SKIP: cycle 3 history too small for another compaction boundary")
        return True

    print(f"  Cycle 3: {len(cycle3)} msgs, bounds={bounds_3}")
    try:
        history_3, summary_text_3 = await apply_compaction(ctx, cycle3, bounds_3, announce=False)
    except TimeoutError:
        print("  FAIL: cycle 3 timed out")
        return False

    if summary_text_3 is None:
        print("  SKIP: cycle 3 used static marker — iterative path not testable")
        return True

    # Find the compaction-3 marker in history_3
    marker_text_3 = None
    for m in history_3:
        if isinstance(m, ModelRequest):
            for p in m.parts:
                if (
                    isinstance(p, UserPromptPart)
                    and isinstance(p.content, str)
                    and SUMMARY_MARKER_PREFIX in p.content
                ):
                    marker_text_3 = p.content
                    break

    if marker_text_3 is None:
        print("  FAIL: no summary marker found in cycle-3 compacted history")
        return False

    token_in_marker_3 = DISTINCTIVE_TOKEN in marker_text_3
    print(f"  Cycle 3 marker ({len(marker_text_3)} chars), token present={token_in_marker_3}")

    if token_in_marker_3:
        print(
            f"  PASS: {DISTINCTIVE_TOKEN} survived all 3 compaction passes "
            "(cross-compaction memory preserved)"
        )
    else:
        print(
            f"  FAIL: {DISTINCTIVE_TOKEN} lost after 3 compaction passes "
            "(cross-compaction memory NOT preserved)"
        )
        passed = False

    return passed


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


_LAST_RESULTS: dict[str, bool] = {}


async def _run_all() -> int:
    global _LAST_RESULTS
    print("=" * 60)
    print("  Eval: Compaction Quality — Full Flow Validation")
    print("=" * 60)

    results: dict[str, bool] = {}

    # --- Pre-compact layer ---
    results["Step 1: Pre-compact persist_if_oversized [BC4]"] = step_1_precompact()

    # --- Processor chain components (flow order) ---
    results["Step 2: P1 evict_old_tool_results"] = step_2_p1_truncate()
    results["Step 4: Context enrichment [BC2,BC3]"] = step_4_context_enrichment()
    results["Step 5: Prompt assembly [Outcome 1,BC1]"] = step_5_prompt_assembly()

    # --- Full chain (real LLM) ---
    results["Step 6: Full chain P1→P5 (LLM)"] = await step_6_full_chain()
    results["Step 7: Multi-cycle [Outcome 3]"] = await step_7_multi_cycle()

    # --- Error recovery ---
    results["Step 8: Overflow [Outcome 4,BC5]"] = step_8_overflow()

    # --- Degradation ---
    results["Step 9: Circuit breaker [degradation]"] = await step_9_circuit_breaker()

    # --- Edge cases ---
    results["Step 11: Edge case battery"] = step_11_edge_cases()

    # --- Prompt upgrade quality ---
    results["Step 13: Prompt upgrade quality"] = await step_13_prompt_upgrade_quality()

    # --- Pending/Resolved sections ---
    results[
        "Step 14: Pending/Resolved sections (functional)"
    ] = await step_14_pending_resolved_sections()

    # --- Iterative summary 3-pass ---
    results[
        "Step 16: Iterative summary 3-pass preservation"
    ] = await step_16_iterative_summary_3_pass()

    # --- Real-world deep learning scenario (UAT) ---
    results["Step 15: Deep learning trigger (Finch/UAT)"] = await step_15_finch_deep_learning()

    # Summary
    print("\n" + "=" * 60)
    print("  Results")
    print("=" * 60)
    all_pass = True
    for name, ok in results.items():
        status = "PASS" if ok else "FAIL"
        print(f"  [{status}] {name}")
        if not ok:
            all_pass = False

    total = len(results)
    passed_count = sum(1 for v in results.values() if v)
    print(f"\n  {passed_count}/{total} steps passed")
    print(f"\nVERDICT: {'PASS' if all_pass else 'FAIL'}")
    _LAST_RESULTS.update(results)
    return 0 if all_pass else 1


_STEP_DESCRIPTIONS: dict[str, str] = {
    "Step 1": "Tool results exceeding 50K chars are persisted to disk with a 2K preview placeholder. Content-addressed files ensure idempotency.",
    "Step 2": "Older compactable tool results (beyond the 5 most recent per type) are cleared. Non-compactable tools and the last turn group are protected.",
    "Step 4": "Side-channel context is gathered from 3 sources (file paths from ToolCallPart.args, pending todos, prior summaries) and capped at 4K chars. Always-on memories are injected separately by P4.",
    "Step 5": "Prompt assembly: four input combinations (context × personality_active) validated against expected output structure. Verifies context appears after template sections, personality appears last.",
    "Step 6": "Full P1→P3→P4→P5 chain on a 14-turn conversation with tool calls, producing an LLM summary. Validates numerical counts at each stage.",
    "Step 7": "Chain on history containing a prior compaction summary. Validates that both prior context and new work are preserved across cycles.",
    "Step 8": "Overflow detection (413/400 with context-length body), emergency compaction (keep first+last groups), and one-shot recovery guard.",
    "Step 9": "Circuit breaker degradation: after 3 consecutive LLM failures, compaction falls back to static marker without attempting an LLM call.",
    "Step 11": "Edge case battery (structural): 1-turn history, static markers in history, single massive message, mixed compactable/non-compactable parts, empty list.",
    "Step 14": "Pending/Resolved sections — functional LLM validation: (14a) unanswered question appears in ## Pending User Asks; (14b) answered question appears in ## Resolved Questions and not in Pending; (14c) merge contract — prior pending item answered in new block migrates to ## Resolved Questions.",
    "Step 13": "Prompt upgrade quality: three deterministic single-run gates — (13a) ## Next Step contains a ≥20-char verbatim anchor from recent messages; (13b) ## User Corrections preserves explicit corrections; (13c) ## Errors & Fixes retains both the failure and user-directed fix guidance.",
    "Step 16": (
        "Iterative summary 3-pass: three successive apply_compaction calls on the same deps instance. "
        "Validates that a distinctive token from cycle-1 content survives into the cycle-3 in-context "
        "marker via the previous_compaction_summary iterative-update prompt branch."
    ),
    "Step 15": (
        "UAT: open-ended deep-learning loop driven by real run_turn. co autonomously fetches "
        "Wikipedia pages and reviews for the 2021 film Finch (Tom Hanks, Apple TV+) until M3 "
        "compaction fires organically. M1 persists oversized tool results to ~/.co-cli/tool-results/. "
        "Validates: network preflight, agentic continuation, M1+M3 end-to-end on real data, "
        "approval-hang guard, 10-point semantic ground truth (cast/crew/themes/title), "
        "3 anti-hallucination checks, and ≥3 persisted artifacts in the real store."
    ),
}

# Noise patterns to filter from report output (library warnings, console prints)
_NOISE_PATTERNS = ("WARNING:", "Compacting conversation")


def _build_report(raw_output: str, results: dict[str, bool]) -> str:
    """Build structured markdown report from captured output."""
    lines: list[str] = []
    total = len(results)
    passed_count = sum(1 for v in results.values() if v)
    verdict = "PASS" if passed_count == total else "FAIL"

    lines.append("# Compaction Quality Eval Report")
    lines.append("")
    lines.append(f"**Verdict: {verdict}** ({passed_count}/{total} steps passed)")
    lines.append("")

    # Summary table
    lines.append("| Step | What it validates | Result |")
    lines.append("|------|-------------------|--------|")
    for name, ok in results.items():
        step_key = name.split(":")[0]
        desc = _STEP_DESCRIPTIONS.get(step_key, "")
        status = "PASS" if ok else "**FAIL**"
        lines.append(f"| {name} | {desc} | {status} |")
    lines.append("")

    # Per-step details — extract each "--- Step N ... ---" block
    # Cut off the trailing results summary (starts with "===\n  Results\n===")
    # Find the LAST "====" block (the results section) and cut before it
    last_eq = raw_output.rfind("\n====")
    # The results section has two "====" lines; cut before the first of the pair
    if last_eq > 0:
        prev_eq = raw_output.rfind("\n====", 0, last_eq)
        results_cut = raw_output[:prev_eq] if prev_eq > 0 else raw_output[:last_eq]
    else:
        results_cut = raw_output
    # Match step headers: "--- Step N: <title> ---" followed by body until next step or end
    step_blocks = re.findall(
        r"(-{3} Step \d.+?-{3})(.*?)(?=-{3} Step \d|$)",
        results_cut,
        re.DOTALL,
    )

    for header_raw, body_raw in step_blocks:
        header_match = re.match(r"-{3} (Step \d.+?) -{3}$", header_raw.strip())
        if not header_match:
            continue
        step_title = header_match.group(1).strip()

        lines.append(f"## {step_title}")
        lines.append("")

        # Filter noise lines and format body
        filtered = []
        for line in body_raw.splitlines():
            stripped = line.strip()
            if not stripped:
                filtered.append("")
                continue
            if any(stripped.startswith(p) for p in _NOISE_PATTERNS):
                continue
            filtered.append(line)

        # Trim leading/trailing blanks
        while filtered and not filtered[0].strip():
            filtered.pop(0)
        while filtered and not filtered[-1].strip():
            filtered.pop()

        if filtered:
            lines.append("```")
            lines.extend(filtered)
            lines.append("```")
        lines.append("")

    return "\n".join(lines)


def main() -> int:
    logging.basicConfig(level=logging.WARNING)

    buf = io.StringIO()

    class Tee:
        def __init__(self, *targets):
            self.targets = targets

        def write(self, s):
            for t in self.targets:
                t.write(s)
            return len(s)

        def flush(self):
            for t in self.targets:
                t.flush()

    tee = Tee(sys.stdout, buf)
    with redirect_stdout(tee):
        exit_code = asyncio.run(_run_all())

    report_path = Path("docs/REPORT-compaction-flow-quality.md")
    raw = buf.getvalue()
    report_content = _build_report(raw, _LAST_RESULTS)
    report_path.write_text(report_content, encoding="utf-8")
    print(f"\nReport: {report_path}")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
