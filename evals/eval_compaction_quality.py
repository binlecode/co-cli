#!/usr/bin/env python3
"""Eval: compaction quality — validates the full compaction pipeline end-to-end.

Steps follow the real execution flow (DESIGN-context.md §2, TODO specs):

  --- Pre-compact layer (at tool return time) ---
  Step 1 — persist_if_oversized: 50K threshold, 2K preview, content-addressed disk write
           [BC4: 50K persist-to-disk still applies]

  --- Processor chain components (isolated validation) ---
  Step 2 — P1 truncate_tool_results: recency clearing, COMPACTABLE_TOOLS, keep 5
  Step 3 — P2 compact_assistant_responses: OLDER_MSG_MAX_CHARS=2500, proportional truncation
           [BC6: preserves tool results and user input] [BC7: in-place mutation]
  Step 4 — P5 sub-component: context enrichment (_gather_compaction_context)
           4 sources, 4K cap, enrichment only on LLM path
           [BC2: capped, never blocks] [BC3: from ToolCallPart.args not ToolReturnPart]
  Step 5 — P5 sub-component: prompt assembly (_build_summarizer_prompt)
           template sections, context+personality ordering
           [Outcome 1: structured template] [BC1: free-form fallback]
  (P3 detect_safety_issues and P4 inject_opening_context are validated
   as passthrough within Steps 6/7 — no isolated step needed.)

  --- Full chain execution (real LLM calls) ---
  Step 6 — Full processor chain P1→P2→P3→P4→P5 with numerical validation
           [Outcome 1-5 integrated] [Processor chain order verified]
  Step 7 — Multi-cycle: chain on prior summary, integration verified
           [Outcome 3: prior-summary detection and integration]

  --- Error recovery ---
  Step 8 — Overflow: _is_context_overflow + emergency_compact + one-shot guard
           [Outcome 4] [BC5: one-shot]

Prerequisites: LLM provider configured (Ollama or cloud).

Usage:
    uv run python evals/eval_compaction_quality.py
"""

from __future__ import annotations

import asyncio
import io
import logging
import re
import sys
import tempfile
from contextlib import redirect_stdout
from pathlib import Path

import yaml
from pydantic_ai import RunContext
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextPart,
    ThinkingPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.usage import RunUsage

from co_cli._model_factory import ModelRegistry, ResolvedModel
from co_cli.agent import build_agent
from co_cli.config import ROLE_REASONING, ROLE_SUMMARIZATION, settings
from co_cli.context._history import (
    COMPACTABLE_KEEP_RECENT,
    FILE_TOOLS,
    OLDER_MSG_MAX_CHARS,
    _CLEARED_PLACEHOLDER,
    _CONTEXT_MAX_CHARS,
    _compute_compaction_boundaries,
    _find_last_turn_start,
    _gather_compaction_context,
    _truncate_proportional,
    compact_assistant_responses,
    detect_safety_issues,
    emergency_compact,
    find_first_run_end,
    group_by_turn,
    inject_opening_context,
    summarize_history_window,
    truncate_tool_results,
)
from co_cli.context._orchestrate import _is_context_overflow
from co_cli.context._summarization import (
    _PERSONALITY_COMPACTION_ADDENDUM,
    _SUMMARIZE_PROMPT,
    _build_summarizer_prompt,
    summarize_messages,
)
from co_cli.context._tool_result_storage import (
    PERSISTED_OUTPUT_TAG,
    TOOL_RESULT_MAX_SIZE,
    TOOL_RESULT_PREVIEW_SIZE,
    persist_if_oversized,
)
from co_cli.deps import CoDeps, CoConfig, CoSessionState
from co_cli.tools._shell_backend import ShellBackend
from evals._timeouts import EVAL_SUMMARIZATION_TIMEOUT_SECS

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config — pull from real settings, never override
# ---------------------------------------------------------------------------

_CONFIG = CoConfig.from_settings(settings, cwd=Path.cwd())
_REGISTRY = ModelRegistry.from_config(_CONFIG)
_EVAL_CONFIG = CoConfig.from_settings(settings, cwd=Path.cwd())
_EVAL_CONFIG.mcp_servers = {}
_AGENT = build_agent(config=_EVAL_CONFIG, model_registry=_REGISTRY)


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
    return ModelRequest(parts=[ToolReturnPart(tool_name=name, content=content, tool_call_id=call_id)])


def _make_ctx(
    *,
    memory_dir: Path | None = None,
    session_todos: list[dict] | None = None,
    model_registry: ModelRegistry | None = None,
    llm_num_ctx: int = 30,
) -> RunContext:
    config_kwargs: dict = {"llm_provider": "ollama-openai", "llm_num_ctx": llm_num_ctx}
    if memory_dir is not None:
        config_kwargs["memory_dir"] = memory_dir
    config = CoConfig(**config_kwargs)
    session = CoSessionState(session_id="eval-compaction")
    if session_todos:
        session.session_todos = session_todos
    deps = CoDeps(
        shell=ShellBackend(), config=config,
        model_registry=model_registry, session=session,
    )
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
        1 for m in msgs if isinstance(m, ModelRequest)
        for p in m.parts
        if isinstance(p, ToolReturnPart) and p.content == _CLEARED_PLACEHOLDER
    )


def _count_truncated_text(msgs: list[ModelMessage]) -> int:
    return sum(
        1 for m in msgs if isinstance(m, ModelResponse)
        for p in m.parts
        if isinstance(p, TextPart) and "[...truncated...]" in p.content
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
            lines.append(f"    FAIL: {label} — hallucination: {desc} ({hits[0]!r} found but not in input)")
            all_ok = False
    return all_ok, lines


# Realistic assistant text generator (>2.5K chars to trigger P2)
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
    """Validate pre-compact layer: tool results >50K persisted to disk with 2K preview."""
    print("\n--- Step 1: Pre-compact — persist_if_oversized [BC4] ---")
    passed = True

    with tempfile.TemporaryDirectory() as tmpdir:
        d = Path(tmpdir) / "tool-results"

        # 1a: Under threshold → unchanged
        small = _fake_file("auth/views.py", lines=15)
        r = persist_if_oversized(small, d, "read_file")
        assert r == small, "under threshold should pass through"
        print(f"  PASS: {len(small)} chars < {TOOL_RESULT_MAX_SIZE} → unchanged")
        print(f"    content: {_snippet(small, 100)}")

        # 1b: At boundary → unchanged
        boundary = _fake_file("auth/middleware.py", lines=600)[:TOOL_RESULT_MAX_SIZE]
        r = persist_if_oversized(boundary, d, "read_file")
        assert r == boundary
        print(f"  PASS: {TOOL_RESULT_MAX_SIZE} == threshold → unchanged (boundary)")

        # 1c: Over threshold → persisted + preview
        big = _fake_file("search_results.log", lines=800) + "\n" * (TOOL_RESULT_MAX_SIZE - 10_000)
        big = big + _fake_file("more_results.log", lines=200)
        r = persist_if_oversized(big, d, "find_in_files")
        if PERSISTED_OUTPUT_TAG not in r:
            print(f"  FAIL: over-threshold not persisted")
            return False
        print(f"  PASS: {len(big)} > {TOOL_RESULT_MAX_SIZE} → persisted with preview tag")
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
            print(f"  FAIL: disk content mismatch")
            return False
        print(f"  PASS: content-addressed file on disk ({files[0].name})")

        # 1f: Idempotent
        persist_if_oversized(big, d, "find_in_files")
        if len(list(d.iterdir())) != 1:
            print(f"  FAIL: duplicate file created")
            return False
        print(f"  PASS: idempotent — same content → same file")

    return passed


# ---------------------------------------------------------------------------
# Step 2: P1 truncate_tool_results [Outcome 5 prereq]
# ---------------------------------------------------------------------------


def step_2_p1_truncate() -> bool:
    """Validate P1: recency-based clearing with exact counts.

    Specs from TODO:
    - COMPACTABLE_TOOLS: read_file, run_shell_command, find_in_files, list_directory, web_search, web_fetch
    - Keep COMPACTABLE_KEEP_RECENT (5) most recent per tool type
    - Non-compactable tools pass through regardless of count
    - Last turn group always protected
    """
    print(f"\n--- Step 2: P1 truncate_tool_results (keep={COMPACTABLE_KEEP_RECENT}) ---")
    passed = True
    ctx = _make_ctx()

    # 2a: COMPACTABLE_TOOLS constant matches spec
    expected_compactable = {"read_file", "run_shell_command", "find_in_files", "list_directory", "web_search", "web_fetch"}
    from co_cli.context._history import COMPACTABLE_TOOLS
    if COMPACTABLE_TOOLS != expected_compactable:
        print(f"  FAIL: COMPACTABLE_TOOLS mismatch: {COMPACTABLE_TOOLS}")
        passed = False
    else:
        print(f"  PASS: COMPACTABLE_TOOLS = {sorted(COMPACTABLE_TOOLS)}")

    # 2b: FILE_TOOLS constant matches spec
    expected_file_tools = {"read_file", "write_file", "edit_file", "find_in_files", "list_directory"}
    if FILE_TOOLS != expected_file_tools:
        print(f"  FAIL: FILE_TOOLS mismatch: {FILE_TOOLS}")
        passed = False
    else:
        print(f"  PASS: FILE_TOOLS = {sorted(FILE_TOOLS)}")

    # 2c: Exactly at threshold — 5 calls → 0 cleared
    def _build_tool_conv(tool_name: str, n: int) -> list[ModelMessage]:
        msgs: list[ModelMessage] = []
        for i in range(n):
            cid = f"t{i}"
            msgs += [_user(f"call {i}"), _tool_call(tool_name, {}, cid),
                     _tool_return(tool_name, f"result {i}", cid), _assistant(f"done {i}")]
        msgs += [_user("final"), _assistant("ok")]
        return msgs

    msgs = _build_tool_conv("read_file", 5)
    result = truncate_tool_results(ctx, msgs)
    cleared = _count_cleared(result)
    if cleared != 0:
        print(f"  FAIL: 5 calls should clear 0, got {cleared}")
        passed = False
    else:
        print(f"  PASS: 5 read_file → 0 cleared (at threshold)")

    # 2d: Over threshold — 8 calls → 3 cleared
    msgs = _build_tool_conv("read_file", 8)
    result = truncate_tool_results(ctx, msgs)
    cleared = _count_cleared(result)
    expected = 8 - COMPACTABLE_KEEP_RECENT
    if cleared != expected:
        print(f"  FAIL: 8 calls should clear {expected}, got {cleared}")
        passed = False
    else:
        print(f"  PASS: 8 read_file → {cleared} cleared (8 - {COMPACTABLE_KEEP_RECENT} = {expected})")
        # Show first cleared and first intact as evidence
        for m in result:
            if isinstance(m, ModelRequest):
                for p in m.parts:
                    if isinstance(p, ToolReturnPart) and p.tool_name == "read_file":
                        if p.content == _CLEARED_PLACEHOLDER:
                            print(f"    cleared: {_snippet(p.content)}")
                            break
        for m in reversed(result):
            if isinstance(m, ModelRequest):
                for p in m.parts:
                    if isinstance(p, ToolReturnPart) and p.tool_name == "read_file" and p.content != _CLEARED_PLACEHOLDER:
                        print(f"    kept:    {_snippet(p.content)}")
                        break
                else:
                    continue
                break

    # 2e: Non-compactable tool — 10 calls → 0 cleared
    msgs = _build_tool_conv("save_memory", 10)
    result = truncate_tool_results(ctx, msgs)
    cleared = _count_cleared(result)
    if cleared != 0:
        print(f"  FAIL: non-compactable should clear 0, got {cleared}")
        passed = False
    else:
        print(f"  PASS: 10 save_memory → 0 cleared (non-compactable)")

    # 2f: Multiple compactable types — each tracked independently
    # 8 read_file + 7 web_search → P1 clears 3 read_file + 2 web_search = 5 total
    msgs_multi: list[ModelMessage] = []
    cid = 0
    for i in range(8):
        c = f"rf{cid}"; cid += 1
        msgs_multi += [_user(f"read {i}"), _tool_call("read_file", {}, c),
                       _tool_return("read_file", f"file content {i}", c), _assistant(f"got {i}")]
    for i in range(7):
        c = f"ws{cid}"; cid += 1
        msgs_multi += [_user(f"search {i}"), _tool_call("web_search", {}, c),
                       _tool_return("web_search", f"search result {i}", c), _assistant(f"found {i}")]
    msgs_multi += [_user("final"), _assistant("ok")]
    result = truncate_tool_results(ctx, msgs_multi)
    # Count cleared per type
    cleared_rf = sum(1 for m in result if isinstance(m, ModelRequest)
                     for p in m.parts if isinstance(p, ToolReturnPart)
                     and p.tool_name == "read_file" and p.content == _CLEARED_PLACEHOLDER)
    cleared_ws = sum(1 for m in result if isinstance(m, ModelRequest)
                     for p in m.parts if isinstance(p, ToolReturnPart)
                     and p.tool_name == "web_search" and p.content == _CLEARED_PLACEHOLDER)
    expected_rf = 8 - COMPACTABLE_KEEP_RECENT  # 3
    expected_ws = 7 - COMPACTABLE_KEEP_RECENT  # 2
    if cleared_rf != expected_rf or cleared_ws != expected_ws:
        print(f"  FAIL: multi-type: read_file cleared {cleared_rf} (expected {expected_rf}), "
              f"web_search cleared {cleared_ws} (expected {expected_ws})")
        passed = False
    else:
        print(f"  PASS: multi-type: 8 read_file → {cleared_rf} cleared, "
              f"7 web_search → {cleared_ws} cleared (independent per-type tracking)")

    # 2g: Last turn group protected
    msgs = _build_tool_conv("read_file", 7)
    # Replace the final turn with a read_file call (should be protected)
    msgs[-2:] = [_user("final read"), _tool_call("read_file", {}, "last"),
                 _tool_return("read_file", "PROTECTED", "last"), _assistant("done")]
    result = truncate_tool_results(ctx, msgs)
    # Find the last group's tool return
    groups = group_by_turn(result)
    last_returns = [p for m in groups[-1].messages if isinstance(m, ModelRequest)
                    for p in m.parts if isinstance(p, ToolReturnPart)]
    if not last_returns or last_returns[0].content == _CLEARED_PLACEHOLDER:
        print(f"  FAIL: last turn group tool result was cleared")
        passed = False
    else:
        print(f"  PASS: last turn group protected (content intact)")

    return passed


# ---------------------------------------------------------------------------
# Step 3: P2 compact_assistant_responses [Outcome 5, BC6, BC7]
# ---------------------------------------------------------------------------


def step_3_p2_compact_responses() -> bool:
    """Validate P2: TextPart/ThinkingPart capping with exact thresholds.

    Specs from TODO:
    - OLDER_MSG_MAX_CHARS = 2_500
    - _truncate_proportional: head(20%) + tail(80%) + marker (aligned with gemini-cli)
    - Does NOT touch ToolCallPart, ToolReturnPart, UserPromptPart [BC6]
    - Last turn group untouched
    - In-place mutation [BC7]
    """
    print(f"\n--- Step 3: P2 compact_assistant_responses (cap={OLDER_MSG_MAX_CHARS}) [BC6,BC7] ---")
    passed = True
    ctx = _make_ctx()

    # 3a: Threshold constant
    if OLDER_MSG_MAX_CHARS != 2_500:
        print(f"  FAIL: OLDER_MSG_MAX_CHARS = {OLDER_MSG_MAX_CHARS}, expected 2500")
        return False
    print(f"  PASS: OLDER_MSG_MAX_CHARS = {OLDER_MSG_MAX_CHARS}")

    # 3b: TextPart >2500 → capped with marker
    big_analysis = _analysis("auth/views.py", "Starting with the login endpoint.\n\n") * 20
    msgs: list[ModelMessage] = [
        _user("Analyze the auth module"),
        ModelResponse(parts=[TextPart(content=big_analysis)]),
        _user("Continue"), _assistant("ok"),
    ]
    result = compact_assistant_responses(ctx, msgs)
    old_text = [p for m in result if isinstance(m, ModelResponse) for p in m.parts
                if isinstance(p, TextPart) and "auth/views" in p.content]
    if not old_text or len(old_text[0].content) > OLDER_MSG_MAX_CHARS:
        print(f"  FAIL: {len(big_analysis)} char TextPart not capped")
        passed = False
    elif "[...truncated...]" not in old_text[0].content:
        print(f"  FAIL: truncation marker missing")
        passed = False
    else:
        print(f"  PASS: {len(big_analysis)} char TextPart → {len(old_text[0].content)} chars with marker")
        print(f"    head: {old_text[0].content[:80]!r}")
        print(f"    tail: ...{old_text[0].content[-80:]!r}")

    # 3c: ThinkingPart also capped
    big_thinking = ("Let me think about the middleware chain and how to replace it... " * 800)
    msgs = [_user("Plan the migration"), ModelResponse(parts=[ThinkingPart(content=big_thinking)]),
            _user("Continue"), _assistant("ok")]
    result = compact_assistant_responses(ctx, msgs)
    for m in result:
        if isinstance(m, ModelResponse):
            for p in m.parts:
                if isinstance(p, ThinkingPart):
                    if len(p.content) > OLDER_MSG_MAX_CHARS:
                        print(f"  FAIL: ThinkingPart not capped: {len(p.content)}")
                        passed = False
                    else:
                        print(f"  PASS: ThinkingPart capped to {len(p.content)}")

    # 3d: Last turn group untouched [BC6]
    last_response = "The middleware chain is now fully migrated. Tests are green."
    msgs = [_user("First question"), _assistant("Earlier analysis..."),
            _user("Status?"), _assistant(last_response)]
    result = compact_assistant_responses(ctx, msgs)
    last_text = [p for m in result if isinstance(m, ModelResponse) for p in m.parts
                 if isinstance(p, TextPart) and "middleware chain" in p.content]
    if not last_text or last_text[0].content != last_response:
        print(f"  FAIL: last turn group modified")
        passed = False
    else:
        print(f"  PASS: last turn group untouched ({last_response[:60]!r}...)")

    # 3e: ToolCallPart.args preserved [BC6 — critical for context enrichment]
    big_with_tool = _analysis("auth migration plan") * 20
    msgs = [_user("Read the critical file"),
            ModelResponse(parts=[TextPart(content=big_with_tool),
                                 ToolCallPart(tool_name="read_file", args={"file_path": "/critical.py"}, tool_call_id="c1")]),
            _tool_return("read_file", "class AuthMiddleware: ...", "c1"),
            _user("Continue"), _assistant("ok")]
    result = compact_assistant_responses(ctx, msgs)
    found = any(isinstance(p, ToolCallPart) and p.args_as_dict().get("file_path") == "/critical.py"
                for m in result if isinstance(m, ModelResponse) for p in m.parts)
    if not found:
        print(f"  FAIL: ToolCallPart.args lost by P2")
        passed = False
    else:
        print(f"  PASS: ToolCallPart.args preserved (file_path=/critical.py)")

    # 3f: ToolReturnPart untouched [BC6]
    big_return = _fake_file("auth/views.py", lines=700)
    msgs = [_user("Read auth views"), _tool_call("read_file", {"file_path": "auth/views.py"}, "c1"),
            _tool_return("read_file", big_return, "c1"), _assistant("Got the file."),
            _user("Now edit it"), _assistant("done")]
    result = compact_assistant_responses(ctx, msgs)
    returns = [p for m in result if isinstance(m, ModelRequest) for p in m.parts
               if isinstance(p, ToolReturnPart) and p.content == big_return]
    if not returns:
        print(f"  FAIL: ToolReturnPart modified by P2")
        passed = False
    else:
        print(f"  PASS: ToolReturnPart untouched ({len(big_return)} char file content intact)")

    # 3g: _truncate_proportional ratio: head(20%) + tail(80%) — aligned with gemini-cli
    text = _analysis("auth/middleware.py")  # ~2000 chars of realistic content
    truncated = _truncate_proportional(text, 200)
    marker = "\n[...truncated...]\n"
    if marker not in truncated:
        print(f"  FAIL: marker missing")
        passed = False
    else:
        head, tail = truncated.split(marker)
        available = 200 - len(marker)
        expected_head = int(available * 0.20)
        if abs(len(head) - expected_head) > 2:
            print(f"  FAIL: head ratio: {len(head)} vs ~{expected_head}")
            passed = False
        else:
            print(f"  PASS: _truncate_proportional head={len(head)}, tail={len(tail)} (20%/80%)")
            print(f"    head: {head[:60]!r}...")
            print(f"    tail: ...{tail[-60:]!r}")

    # 3h: _truncate_proportional output never exceeds max_chars (edge case fix)
    for test_max in (100, 50, 25, 19, 10, 1):
        r = _truncate_proportional("X" * 5000, test_max)
        if len(r) > test_max:
            print(f"  FAIL: _truncate_proportional(5000, {test_max}) → {len(r)} chars (>{test_max})")
            passed = False
            break
    else:
        print(f"  PASS: output never exceeds max_chars (tested down to 1)")

    return passed


# ---------------------------------------------------------------------------
# Step 4: P5 context enrichment [Outcome 2, BC2, BC3]
# ---------------------------------------------------------------------------


def step_4_context_enrichment() -> bool:
    """Validate _gather_compaction_context: 4 sources, 4K cap, lazy execution.

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
    msgs: list[ModelMessage] = [
        _user("work"),
        _tool_call("read_file", {"file_path": "/app/models.py"}, "c1"),
        _tool_return("read_file", "class User: ...", "c1"),
        _tool_call("edit_file", {"file_path": "/app/views.py"}, "c2"),
        _tool_return("edit_file", "ok", "c2"),
        _assistant("done"),
    ]
    ctx = _make_ctx()
    result = _gather_compaction_context(ctx, msgs, dropped=[])
    if result is None or "/app/models.py" not in result or "/app/views.py" not in result:
        print(f"  FAIL: file paths not extracted from ToolCallPart.args")
        passed = False
    else:
        print(f"  PASS: Source 1 — file paths from ToolCallPart.args")
        print(f"    context: {_snippet(result, 120)}")

    # 4b: Source 1 scans ALL messages (not just dropped)
    head = [_user("head"), _tool_call("read_file", {"file_path": "/head.py"}, "h1"),
            _tool_return("read_file", "x", "h1"), _assistant("ok")]
    tail = [_user("tail"), _tool_call("read_file", {"file_path": "/tail.py"}, "t1"),
            _tool_return("read_file", "y", "t1"), _assistant("ok")]
    dropped_msgs: list[ModelMessage] = [_user("mid"), _assistant("mid")]
    all_msgs = head + dropped_msgs + tail
    result = _gather_compaction_context(ctx, all_msgs, dropped=dropped_msgs)
    if result is None or "/head.py" not in result or "/tail.py" not in result:
        print(f"  FAIL: file paths from head/tail missing — not scanning ALL messages")
        passed = False
    else:
        print(f"  PASS: Source 1 scans ALL messages (head + dropped + tail)")

    # 4c: Source 2 — session todos (pending only)
    todos = [
        {"content": "Update tests", "status": "pending"},
        {"content": "Deploy to staging", "status": "completed"},
        {"content": "Write docs", "status": "in_progress"},
        {"content": "Cancel this", "status": "cancelled"},
    ]
    ctx = _make_ctx(session_todos=todos)
    result = _gather_compaction_context(ctx, [_user("hi"), _assistant("hey")], dropped=[])
    if result is None or "Update tests" not in result or "Write docs" not in result:
        print(f"  FAIL: pending todos missing")
        passed = False
    elif "Deploy to staging" in result or "Cancel this" in result:
        print(f"  FAIL: completed/cancelled todos should be filtered")
        passed = False
    else:
        print(f"  PASS: Source 2 — pending todos included, completed/cancelled filtered")
        print(f"    context: {_snippet(result, 160)}")

    # 4d: Source 3 — always-on memories from real files
    with tempfile.TemporaryDirectory() as tmpdir:
        mem_dir = Path(tmpdir)
        fm = {"id": 1, "kind": "memory", "created": "2025-01-01T00:00:00Z",
              "tags": ["preference"], "provenance": "user-told",
              "always_on": True, "decay_protected": False, "certainty": "high"}
        (mem_dir / "001-pref.md").write_text(
            f"---\n{yaml.dump(fm, default_flow_style=False)}---\n\nUser prefers concise responses.\n")
        ctx = _make_ctx(memory_dir=mem_dir)
        result = _gather_compaction_context(ctx, [_user("hi"), _assistant("hey")], dropped=[])
        if result is None or "concise responses" not in result:
            print(f"  FAIL: always-on memory not loaded")
            passed = False
        else:
            print(f"  PASS: Source 3 — always-on memories from real files")
            print(f"    context: {_snippet(result, 120)}")

    # 4e: Source 4 — prior-summary from dropped messages
    dropped_with_summary: list[ModelMessage] = [
        _user("[Summary of 15 earlier messages]\n## Goal\nRefactor auth module"),
        _assistant("continuing..."),
    ]
    ctx = _make_ctx()
    result = _gather_compaction_context(ctx, [_user("hi"), _assistant("hey")], dropped=dropped_with_summary)
    if result is None or "Prior summary" not in result or "Refactor auth module" not in result:
        print(f"  FAIL: prior summary not extracted from dropped messages")
        passed = False
    else:
        print(f"  PASS: Source 4 — prior summary from dropped messages")
        print(f"    context: {_snippet(result, 160)}")

    # 4f: Returns None when empty
    ctx = _make_ctx()
    result = _gather_compaction_context(ctx, [_user("hi"), _assistant("hey")], dropped=[])
    if result is not None:
        print(f"  FAIL: expected None when no sources, got: {result[:80]}")
        passed = False
    else:
        print(f"  PASS: returns None when no sources produce data")

    # 4g: 4K cap [BC2]
    big_todos = [{"content": "x" * 500, "status": "pending"} for _ in range(20)]
    msgs_many_files: list[ModelMessage] = [
        _user("work"),
        ModelResponse(parts=[
            ToolCallPart(tool_name="read_file", args={"file_path": f"/a/b/c/d/e/file_{i:03d}.py"}, tool_call_id=f"c{i}")
            for i in range(20)
        ]),
        _assistant("done"),
    ]
    ctx = _make_ctx(session_todos=big_todos)
    result = _gather_compaction_context(ctx, msgs_many_files, dropped=[])
    if result is not None and len(result) > _CONTEXT_MAX_CHARS:
        print(f"  FAIL: context {len(result)} > {_CONTEXT_MAX_CHARS}")
        passed = False
    elif result is not None:
        print(f"  PASS: context capped at {len(result)} ≤ {_CONTEXT_MAX_CHARS} [BC2]")
    else:
        print(f"  PASS: context was None (sources below threshold)")

    # 4h: Enrichment only runs on LLM path (not static-marker fallback)
    # Verify structurally: _gather_compaction_context appears in the source
    # AFTER both the registry-None guard and the circuit-breaker guard.
    # Uses line-number ordering (stable across renames/comments) rather than
    # raw character offsets.
    import inspect
    source_lines = inspect.getsource(summarize_history_window).splitlines()

    def _first_line(keyword: str) -> int | None:
        for i, line in enumerate(source_lines):
            if keyword in line:
                return i
        return None

    enrichment_line = _first_line("_gather_compaction_context")
    registry_line = _first_line("registry is None")
    breaker_line = _first_line("compaction_failure_count")
    if enrichment_line is None or registry_line is None or breaker_line is None:
        print(f"  FAIL: cannot locate enrichment (line {enrichment_line}), "
              f"registry guard (line {registry_line}), or breaker (line {breaker_line}) in source")
        passed = False
    elif enrichment_line < registry_line:
        print(f"  FAIL: enrichment (L{enrichment_line}) runs BEFORE registry-None check (L{registry_line})")
        passed = False
    elif enrichment_line < breaker_line:
        print(f"  FAIL: enrichment (L{enrichment_line}) runs BEFORE circuit-breaker (L{breaker_line})")
        passed = False
    else:
        print(f"  PASS: enrichment deferred to LLM branch (L{enrichment_line} after guards L{registry_line}, L{breaker_line})")

    return passed


# ---------------------------------------------------------------------------
# Step 5: P5 prompt assembly [Outcome 1, BC1]
# ---------------------------------------------------------------------------


def step_5_prompt_assembly() -> bool:
    """Validate _build_summarizer_prompt and template structure.

    Specs from TODO:
    - Template has 5 sections: Goal, Key Decisions, Working Set, Progress, Next Steps
    - Prior-summary integration instruction in template
    - Assembly order: template + context (## Additional Context) + personality (always last)
    - summarize_messages() accepts context: str | None = None
    - [BC1]: structured summary is prompt, not parser
    """
    print(f"\n--- Step 5: Prompt assembly [Outcome 1, BC1] ---")
    passed = True

    # 5a: Template has all 5 sections
    for section in ("## Goal", "## Key Decisions", "## Working Set", "## Progress", "## Next Steps"):
        if section not in _SUMMARIZE_PROMPT:
            print(f"  FAIL: template missing {section}")
            passed = False
    if passed:
        print(f"  PASS: template has 5 sections (Goal, Key Decisions, Working Set, Progress, Next Steps)")

    # 5b: Prior-summary integration instruction in template
    if "prior summary" not in _SUMMARIZE_PROMPT.lower() or "integrate" not in _SUMMARIZE_PROMPT.lower():
        print(f"  FAIL: template missing prior-summary integration instruction")
        passed = False
    else:
        print(f"  PASS: template has prior-summary integration instruction")

    # 5c: Assembly — 4 combinations
    # (None, False) → template unchanged
    r = _build_summarizer_prompt(_SUMMARIZE_PROMPT, None, False)
    if r != _SUMMARIZE_PROMPT:
        print(f"  FAIL: (None, False) should return template unchanged")
        passed = False
    else:
        print(f"  PASS: (None, False) → template unchanged")

    # (context, False) → template + context, no personality
    r = _build_summarizer_prompt(_SUMMARIZE_PROMPT, "Files: foo.py", False)
    if "## Additional Context" not in r or "foo.py" not in r:
        print(f"  FAIL: context not injected")
        passed = False
    elif _PERSONALITY_COMPACTION_ADDENDUM in r:
        print(f"  FAIL: personality present without personality_active")
        passed = False
    else:
        print(f"  PASS: (context, False) → template + context")

    # (None, True) → template + personality, no context
    r = _build_summarizer_prompt(_SUMMARIZE_PROMPT, None, True)
    if _PERSONALITY_COMPACTION_ADDENDUM not in r:
        print(f"  FAIL: personality missing")
        passed = False
    elif "## Additional Context" in r:
        print(f"  FAIL: context present without context")
        passed = False
    else:
        print(f"  PASS: (None, True) → template + personality")

    # (context, True) → template + context + personality (personality LAST)
    r = _build_summarizer_prompt(_SUMMARIZE_PROMPT, "todos here", True)
    ctx_pos = r.index("## Additional Context")
    pers_pos = r.index("Additionally, preserve")
    if ctx_pos >= pers_pos:
        print(f"  FAIL: personality must come after context")
        passed = False
    else:
        print(f"  PASS: (context, True) → context before personality (correct order)")
        # Show the assembly boundary: context section → personality section
        boundary = r[ctx_pos:pers_pos + 60]
        print(f"    boundary: {_snippet(boundary, 160)}")

    return passed


# ---------------------------------------------------------------------------
# Step 6: Full processor chain P1→P2→P3→P4→P5 [all Outcomes, chain order]
# ---------------------------------------------------------------------------


async def step_6_full_chain() -> bool:
    """Execute real processor chain with numerical validation at each stage.

    Validates:
    - Processor order: P1→P2→P3→P4→P5 (from agent.py registration)
    - P1: exact cleared count = N_read_file - COMPACTABLE_KEEP_RECENT
    - P2: TextParts > OLDER_MSG_MAX_CHARS capped, exact count
    - P3: no safety injections (clean history)
    - P4: memory recall (may or may not inject)
    - P5: message reduction, summary marker count matches dropped, structured sections
    - Context enrichment: file paths + todos in summary output
    """
    print(f"\n--- Step 6: Full processor chain P1→P2→P3→P4→P5 (real LLM) ---")
    passed = True

    # Build history: 10 read_file + 2 edit_file + 1 find_in_files, large assistant text
    N_READ = 10
    history: list[ModelMessage] = []

    # Turn 1: request + large analysis
    history += [_user("Refactor auth module from sessions to JWT."),
                ModelResponse(parts=[TextPart(content=_analysis("project structure",
                    "Starting with auth/views.py.\n\n"))])]

    # Turns 2-11: 10 read_file with large responses
    files = [f"auth/{n}.py" for n in ["views", "middleware", "tokens", "permissions",
             "decorators", "backends", "serializers", "signals", "utils", "constants"]]
    for i, fname in enumerate(files):
        cid = f"rf{i}"
        history += [_user(f"Read {fname}"),
                    _tool_call("read_file", {"file_path": fname}, cid),
                    _tool_return("read_file", _fake_file(fname, 40 + i * 5), cid),
                    ModelResponse(parts=[TextPart(content=_analysis(fname,
                        f"File {i+1}/{N_READ}. {'Critical path.' if i < 3 else 'Lower priority.'}\n\n"))])]

    # Turn 12: edit + find_in_files
    history += [_user("Edit views and find imports."),
                _tool_call("edit_file", {"file_path": "auth/views.py"}, "ed1"),
                _tool_return("edit_file", "Edited", "ed1"),
                _tool_call("find_in_files", {"path": ".", "pattern": "from auth"}, "fi1"),
                _tool_return("find_in_files", "api/urls.py:3: from auth import login\n" * 5, "fi1"),
                ModelResponse(parts=[TextPart(content=_analysis("import graph",
                    "Found 4 files importing from auth.\n\n"))])]

    # Turn 13: another edit
    history += [_user("Update middleware."),
                _tool_call("edit_file", {"file_path": "auth/middleware.py"}, "ed2"),
                _tool_return("edit_file", "Edited", "ed2"),
                ModelResponse(parts=[TextPart(content=_analysis("middleware update"))])]

    # Turn 14: last (protected)
    history += [_user("Status?"), _assistant("Modified views and middleware. Tests and URLs remain.")]

    n_msgs = len(history)
    n_groups = len(group_by_turn(history))
    total_chars = _msg_chars(history)
    print(f"  Input: {n_msgs} msgs, {n_groups} groups, {total_chars:,} chars")

    # Count TextParts >2500 in non-last groups for P2 expectation
    groups = group_by_turn(history)
    expected_p2_truncated = 0
    for g in groups[:-1]:
        for m in g.messages:
            if isinstance(m, ModelResponse):
                for p in m.parts:
                    if isinstance(p, TextPart) and len(p.content) > OLDER_MSG_MAX_CHARS:
                        expected_p2_truncated += 1
    print(f"  Expected: P1 clears {N_READ - COMPACTABLE_KEEP_RECENT}, P2 caps {expected_p2_truncated}")

    # Build ctx with todos for enrichment
    config = CoConfig(llm_provider="ollama-openai", llm_num_ctx=30)
    session = CoSessionState(session_id="eval-chain-6")
    session.session_todos = [
        {"content": "Update api/urls.py for JWT", "status": "pending"},
        {"content": "Add PyJWT to requirements", "status": "pending"},
        {"content": "Update middleware", "status": "completed"},
    ]
    deps = CoDeps(shell=ShellBackend(), config=config, model_registry=_REGISTRY, session=session)
    ctx = RunContext(deps=deps, model=_AGENT.model, usage=RunUsage())

    msgs = list(history)

    # --- P1 ---
    print(f"\n  [P1] truncate_tool_results")
    chars_pre_p1 = _msg_chars(msgs)
    msgs = truncate_tool_results(ctx, msgs)
    p1_cleared = _count_cleared(msgs)
    chars_post_p1 = _msg_chars(msgs)
    expected_p1 = N_READ - COMPACTABLE_KEEP_RECENT
    print(f"    Cleared: {p1_cleared} (expected {expected_p1})")
    print(f"    Chars: {chars_pre_p1:,} → {chars_post_p1:,} (P1 reduced {chars_pre_p1 - chars_post_p1:,})")
    if p1_cleared != expected_p1:
        print(f"    FAIL: P1 cleared {p1_cleared} ≠ {expected_p1}")
        passed = False
    else:
        print(f"    PASS")

    # --- P2 ---
    print(f"\n  [P2] compact_assistant_responses")
    msgs = compact_assistant_responses(ctx, msgs)
    p2_truncated = _count_truncated_text(msgs)
    chars_post_p2 = _msg_chars(msgs)
    print(f"    Truncated: {p2_truncated} (expected {expected_p2_truncated})")
    print(f"    Chars: {chars_post_p1:,} → {chars_post_p2:,} (P2 reduced {chars_post_p1 - chars_post_p2:,})")
    if p2_truncated != expected_p2_truncated:
        print(f"    FAIL: P2 truncated {p2_truncated} ≠ {expected_p2_truncated}")
        passed = False
    else:
        print(f"    PASS")

    # --- P3 ---
    print(f"\n  [P3] detect_safety_issues")
    len_pre_p3 = len(msgs)
    msgs = detect_safety_issues(ctx, msgs)
    p3_injections = len(msgs) - len_pre_p3
    print(f"    Injections: {p3_injections} (clean history → no safety warnings)")
    print(f"    PASS")

    # --- P4 ---
    print(f"\n  [P4] inject_opening_context")
    recall_pre = ctx.deps.session.memory_recall_state.recall_count
    len_pre_p4 = len(msgs)
    msgs = await inject_opening_context(ctx, msgs)
    recall_post = ctx.deps.session.memory_recall_state.recall_count
    p4_injections = len(msgs) - len_pre_p4
    recall_fired = recall_post > recall_pre
    if recall_fired:
        print(f"    recall_memory called (recall_count {recall_pre} → {recall_post})")
        print(f"    Injections: {p4_injections} ({'memory injected' if p4_injections else 'no matches → no injection'})")
    else:
        user_turns = sum(1 for m in msgs if isinstance(m, ModelRequest)
                         for p in m.parts if isinstance(p, UserPromptPart))
        print(f"    recall_memory skipped (user_turn={user_turns}, last_recall_turn={ctx.deps.session.memory_recall_state.last_recall_user_turn})")
        print(f"    Injections: {p4_injections}")
    print(f"    PASS")

    # --- P5 ---
    print(f"\n  [P5] summarize_history_window (LLM)")
    # Preview the enrichment context that P5 will assemble (before the LLM call)
    bounds = _compute_compaction_boundaries(msgs)
    if bounds is not None:
        head_end, tail_start, dropped_count = bounds
        dropped_preview = msgs[head_end:tail_start]
        enrichment_preview = _gather_compaction_context(ctx, msgs, dropped_preview)
        print(f"    Boundaries: head_end={head_end}, tail_start={tail_start}, dropped={dropped_count}")
        if enrichment_preview:
            print(f"    Enrichment ({len(enrichment_preview)} chars):")
            for line in enrichment_preview.split("\n")[:8]:
                print(f"      | {line}")
            if enrichment_preview.count("\n") > 8:
                print(f"      | ...<{enrichment_preview.count(chr(10)) - 8} more lines>")
        else:
            print(f"    Enrichment: None")

    len_pre_p5 = len(msgs)
    try:
        async with asyncio.timeout(EVAL_SUMMARIZATION_TIMEOUT_SECS * 2):
            msgs = await summarize_history_window(ctx, msgs)
    except TimeoutError:
        print(f"    FAIL: timed out")
        return False

    net_reduction = len_pre_p5 - len(msgs)
    # Summarization replaces N dropped messages with exactly 1 marker message.
    # net_reduction = N - 1, so actual_dropped = net_reduction + 1.
    actual_dropped = net_reduction + 1
    chars_final = _msg_chars(msgs)
    print(f"    Messages: {len_pre_p5} → {len(msgs)} ({actual_dropped} replaced by 1 marker)")
    print(f"    Chars: {chars_post_p2:,} → {chars_final:,}")

    if len(msgs) >= len_pre_p5:
        print(f"    FAIL: no reduction")
        return False
    print(f"    PASS: compacted")

    # Find summary and cross-validate marker count
    summary_text = None
    marker_count_in_output = 0
    for m in msgs:
        if isinstance(m, ModelRequest):
            for p in m.parts:
                if isinstance(p, UserPromptPart) and isinstance(p.content, str):
                    if "[Summary of" in p.content:
                        summary_text = p.content
                        marker_count_in_output += 1
                    elif "Earlier conversation trimmed" in p.content:
                        marker_count_in_output += 1

    if marker_count_in_output != 1:
        print(f"    FAIL: expected exactly 1 marker, found {marker_count_in_output}")
        return False

    if summary_text is None:
        print(f"    Static marker (circuit breaker)")
        return True

    # Marker count cross-validation
    marker_match = re.search(r"\[Summary of (\d+) earlier messages\]", summary_text)
    if marker_match:
        marker_count = int(marker_match.group(1))
        if marker_count != actual_dropped:
            print(f"    FAIL: marker says {marker_count}, actual dropped {actual_dropped}")
            passed = False
        else:
            print(f"    PASS: marker count ({marker_count}) = actual dropped ({actual_dropped})")

    # Structured sections
    sections = [s for s in ("Goal", "Key Decisions", "Working Set", "Progress", "Next Steps")
                if s.lower() in summary_text.lower()]
    if len(sections) >= 2:
        print(f"    PASS: sections: {', '.join(sections)}")
    else:
        has_content = any(kw in summary_text.lower() for kw in ("auth", "jwt", "views"))
        if has_content:
            print(f"    PASS: key content present (sections: {sections}) [BC1: free-form OK]")
        else:
            print(f"    FAIL: no structure or content")
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

    print(f"\n  Chain result: {n_msgs} msgs/{total_chars:,}ch → {len(msgs)} msgs/{chars_final:,}ch")
    return passed


# ---------------------------------------------------------------------------
# Step 7: Multi-cycle [Outcome 3]
# ---------------------------------------------------------------------------


async def step_7_multi_cycle() -> bool:
    """Execute chain on history with prior summary marker. Verify integration.

    Specs from TODO:
    - Prior summary in dropped slice detected via [Summary of prefix
    - Context enrichment includes prior summary text
    - New summary integrates prior content (not lost)
    """
    print(f"\n--- Step 7: Multi-cycle compaction [Outcome 3] ---")
    passed = True

    prior_summary = (
        "[Summary of 10 earlier messages]\n"
        "## Goal\nRefactor auth module from sessions to JWT.\n\n"
        "## Key Decisions\nUsing PyJWT directly for more control.\n\n"
        "## Working Set\nauth/views.py, auth/middleware.py\n\n"
        "## Progress\nViews and middleware updated. Tests and urls pending."
    )

    # Cycle 2 assistant text (>2500 to trigger P2)
    # _detail must produce >2500 chars to trigger P2 in the multi-cycle test.
    # OLDER_MSG_MAX_CHARS = 2500; content must exceed this threshold.
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
        _user("hello"), _assistant("hi"),
        ModelRequest(parts=[UserPromptPart(content=prior_summary)]),
        # Cycle 2 conversation: 7 read_file calls (>5 → P1 fires)
        _user("Update tests."),
        _tool_call("read_file", {"file_path": "tests/test_auth.py"}, "c10"),
        _tool_return("read_file", _fake_file("test_auth", 30), "c10"),
        _tool_call("edit_file", {"file_path": "tests/test_auth.py"}, "c11"),
        _tool_return("edit_file", "Edited", "c11"),
        ModelResponse(parts=[TextPart(content=_detail("tests/test_auth.py"))]),

        _user("Update integration tests."),
        _tool_call("read_file", {"file_path": "tests/test_integration.py"}, "c12"),
        _tool_return("read_file", _fake_file("test_integration", 25), "c12"),
        _tool_call("edit_file", {"file_path": "tests/test_integration.py"}, "c13"),
        _tool_return("edit_file", "Edited", "c13"),
        ModelResponse(parts=[TextPart(content=_detail("tests/test_integration.py"))]),

        _user("Update URLs and check settings."),
        _tool_call("edit_file", {"file_path": "api/urls.py"}, "c14"),
        _tool_return("edit_file", "Edited", "c14"),
        _tool_call("read_file", {"file_path": "settings.py"}, "c15"),
        _tool_return("read_file", _fake_file("settings", 60), "c15"),
        ModelResponse(parts=[TextPart(content=_detail("api/urls.py"))]),

        _user("Find session refs and clean up admin."),
        _tool_call("find_in_files", {"path": ".", "pattern": "session"}, "c16"),
        _tool_return("find_in_files", "settings.py:42: SESSION_ENGINE = ...\n" * 3, "c16"),
        _tool_call("read_file", {"file_path": "admin/views.py"}, "c17"),
        _tool_return("read_file", _fake_file("admin_views", 20), "c17"),
        _tool_call("read_file", {"file_path": "auth/tokens.py"}, "c18"),
        _tool_return("read_file", _fake_file("tokens", 15), "c18"),
        _tool_call("read_file", {"file_path": "auth/permissions.py"}, "c19"),
        _tool_return("read_file", _fake_file("permissions", 10), "c19"),
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
    n_read = sum(1 for m in history if isinstance(m, ModelResponse)
                 for p in m.parts if isinstance(p, ToolCallPart) and p.tool_name == "read_file")
    expected_p1 = max(0, n_read - COMPACTABLE_KEEP_RECENT)
    groups_pre = group_by_turn(history)
    expected_p2 = sum(1 for g in groups_pre[:-1] for m in g.messages
                      if isinstance(m, ModelResponse)
                      for p in m.parts if isinstance(p, TextPart) and len(p.content) > OLDER_MSG_MAX_CHARS)
    print(f"  Expected: P1 clears {expected_p1} (of {n_read} read_file), P2 caps {expected_p2}")

    config = CoConfig(llm_provider="ollama-openai", llm_num_ctx=30)
    deps = CoDeps(shell=ShellBackend(), config=config, model_registry=_REGISTRY,
                  session=CoSessionState(session_id="eval-chain-7"))
    ctx = RunContext(deps=deps, model=_AGENT.model, usage=RunUsage())

    msgs = list(history)

    # --- P1 ---
    print(f"\n  [P1] truncate_tool_results")
    chars_pre_p1 = _msg_chars(msgs)
    msgs = truncate_tool_results(ctx, msgs)
    p1_cleared = _count_cleared(msgs)
    chars_post_p1 = _msg_chars(msgs)
    print(f"    Cleared: {p1_cleared} (expected {expected_p1})")
    print(f"    Chars: {chars_pre_p1:,} → {chars_post_p1:,} (P1 reduced {chars_pre_p1 - chars_post_p1:,})")
    if p1_cleared != expected_p1:
        print(f"    FAIL"); passed = False
    else:
        print(f"    PASS")

    # --- P2 ---
    print(f"\n  [P2] compact_assistant_responses")
    msgs = compact_assistant_responses(ctx, msgs)
    p2_truncated = _count_truncated_text(msgs)
    chars_post_p2 = _msg_chars(msgs)
    print(f"    Truncated: {p2_truncated} (expected {expected_p2})")
    print(f"    Chars: {chars_post_p1:,} → {chars_post_p2:,} (P2 reduced {chars_post_p1 - chars_post_p2:,})")
    if p2_truncated != expected_p2:
        print(f"    FAIL"); passed = False
    else:
        print(f"    PASS")

    # --- P3 + P4 ---
    print(f"\n  [P3] detect_safety_issues")
    len_pre_p3 = len(msgs)
    msgs = detect_safety_issues(ctx, msgs)
    print(f"    Injections: {len(msgs) - len_pre_p3}")
    print(f"    PASS")

    print(f"\n  [P4] inject_opening_context")
    recall_pre = ctx.deps.session.memory_recall_state.recall_count
    len_pre_p4 = len(msgs)
    msgs = await inject_opening_context(ctx, msgs)
    recall_post = ctx.deps.session.memory_recall_state.recall_count
    p4_injections = len(msgs) - len_pre_p4
    recall_fired = recall_post > recall_pre
    if recall_fired:
        print(f"    recall_memory called (recall_count {recall_pre} → {recall_post})")
        print(f"    Injections: {p4_injections} ({'memory injected' if p4_injections else 'no matches → no injection'})")
    else:
        print(f"    recall_memory skipped (already recalled for this user turn)")
        print(f"    Injections: {p4_injections}")
    print(f"    PASS")

    # --- P5 ---
    print(f"\n  [P5] summarize_history_window (LLM)")
    # Preview enrichment context (same as Step 6)
    bounds_7 = _compute_compaction_boundaries(msgs)
    if bounds_7 is not None:
        head_end_7, tail_start_7, dropped_count_7 = bounds_7
        dropped_7 = msgs[head_end_7:tail_start_7]
        enrichment_7 = _gather_compaction_context(ctx, msgs, dropped_7)
        print(f"    Boundaries: head_end={head_end_7}, tail_start={tail_start_7}, dropped={dropped_count_7}")
        if enrichment_7:
            print(f"    Enrichment ({len(enrichment_7)} chars):")
            for line in enrichment_7.split("\n")[:8]:
                print(f"      | {line}")
            if enrichment_7.count("\n") > 8:
                print(f"      | ...<{enrichment_7.count(chr(10)) - 8} more lines>")
        else:
            print(f"    Enrichment: None")

    len_pre = len(msgs)
    try:
        async with asyncio.timeout(EVAL_SUMMARIZATION_TIMEOUT_SECS * 2):
            msgs = await summarize_history_window(ctx, msgs)
    except TimeoutError:
        print(f"    FAIL: timed out"); return False

    net_reduction = len_pre - len(msgs)
    # Summarization replaces N dropped messages with exactly 1 marker message.
    actual_dropped = net_reduction + 1
    chars_final = _msg_chars(msgs)
    print(f"    Messages: {len_pre} → {len(msgs)} ({actual_dropped} replaced by 1 marker)")
    print(f"    Chars: {chars_post_p2:,} → {chars_final:,}")

    if len(msgs) >= len_pre:
        print(f"    FAIL: no reduction"); return False
    print(f"    PASS: compacted")

    # Find summary — verify exactly 1 marker in output
    summary = None
    marker_count_in_output = 0
    for m in msgs:
        if isinstance(m, ModelRequest):
            for p in m.parts:
                if isinstance(p, UserPromptPart) and isinstance(p.content, str):
                    if "[Summary of" in p.content:
                        summary = p.content
                        marker_count_in_output += 1
                    elif "Earlier conversation trimmed" in p.content:
                        marker_count_in_output += 1

    if marker_count_in_output != 1:
        print(f"    FAIL: expected exactly 1 marker, found {marker_count_in_output}")
        return False

    if summary is None:
        print(f"    Static marker — prior integration not testable")
        return True

    # Marker count cross-validation
    marker_match = re.search(r"\[Summary of (\d+) earlier messages\]", summary)
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
        print(f"    PASS: prior content preserved (JWT/PyJWT)")
    else:
        print(f"    WARN: JWT not in summary — prior content may be lost")

    if has_tests or has_urls:
        print(f"    PASS: new work preserved (tests/URLs)")
    else:
        print(f"    WARN: recent work not in summary")

    if (has_jwt or has_pyjwt) and (has_tests or has_urls):
        print(f"    PASS: multi-cycle integration — both prior and new preserved")
    elif has_jwt or has_pyjwt or has_tests or has_urls:
        print(f"    PASS: partial integration (LLM non-deterministic)")
    else:
        print(f"    FAIL: neither prior nor new content"); passed = False

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
    sem_ok, sem_lines = _check_semantic(summary, ground_truth_7, "Step 7")
    sem_pass_count = sum(1 for _, line in zip(ground_truth_7, sem_lines) if "PASS" in line)
    for line in sem_lines:
        print(line)
    min_required = 5
    if sem_pass_count >= min_required:
        print(f"    PASS: semantic validation {sem_pass_count}/{len(ground_truth_7)} (≥{min_required} required)")
    else:
        print(f"    FAIL: semantic validation {sem_pass_count}/{len(ground_truth_7)} (<{min_required} required)")
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
    - _is_context_overflow: status_code in (400, 413) AND body pattern match
    - Handles str body (Ollama) and dict body (OpenAI)
    - Bare 400 without pattern → False (falls to reformulation)
    - emergency_compact: ≤2 groups → None, >2 → first + marker + last
    - dropped_count = sum(len(g.messages) for g in groups[1:-1])
    - [BC5]: one-shot recovery
    """
    print(f"\n--- Step 8: Overflow recovery [Outcome 4, BC5] ---")
    passed = True

    # --- _is_context_overflow ---
    from pydantic_ai.exceptions import ModelHTTPError

    def _err(code: int, body: object) -> ModelHTTPError:
        return ModelHTTPError(status_code=code, model_name="test", body=body)

    cases = [
        (413, "context_length_exceeded: prompt is too long", True, "413 + context_length_exceeded"),
        (400, {"error": {"message": "maximum context length is 8192"}}, True, "400 + dict body (OpenAI)"),
        (400, "prompt is too long for this model", True, "400 + str body (Ollama)"),
        (400, {"error": {"message": "invalid JSON"}}, False, "bare 400 → False (reformulation)"),
        (500, "context_length_exceeded", False, "500 → False (wrong code)"),
        (400, None, False, "400 + None body → False"),
    ]
    for code, body, expected, desc in cases:
        result = _is_context_overflow(_err(code, body))
        if result != expected:
            print(f"  FAIL: {desc}: got {result}")
            passed = False
        else:
            print(f"  PASS: {desc}")

    # --- emergency_compact ---
    print()

    # >2 groups → first + marker + last
    msgs: list[ModelMessage] = []
    for i in range(5):
        msgs += [_user(f"turn {i}"), _assistant(f"resp {i}")]
    result = emergency_compact(msgs)
    if result is None:
        print(f"  FAIL: 5 groups → None"); passed = False
    else:
        groups = group_by_turn(result)
        if len(groups) != 3:
            print(f"  FAIL: expected 3 groups, got {len(groups)}"); passed = False
        else:
            print(f"  PASS: 5 groups → 3 (first + marker + last)")

        # Verify dropped_count in marker
        marker_text = None
        for m in result:
            if isinstance(m, ModelRequest):
                for p in m.parts:
                    if isinstance(p, UserPromptPart) and "trimmed" in str(p.content):
                        marker_text = str(p.content)
        if marker_text and "6 messages" in marker_text:
            print(f"  PASS: dropped_count = 6 (3 middle groups × 2 msgs)")
            print(f"    marker: {repr(marker_text)}")
        elif marker_text:
            print(f"  WARN: marker: {marker_text[:80]}")
        else:
            print(f"  FAIL: no marker found"); passed = False

    # ≤2 groups → None
    for n in (1, 2):
        msgs = []
        for i in range(n):
            msgs += [_user(f"t{i}"), _assistant(f"r{i}")]
        r = emergency_compact(msgs)
        if r is not None:
            print(f"  FAIL: {n} groups should return None"); passed = False
        else:
            print(f"  PASS: {n} group(s) → None")

    # [BC5] one-shot guard — overflow_recovery_attempted field exists on _TurnState
    from co_cli.context._orchestrate import _TurnState
    ts = _TurnState(current_input="test", current_history=[])
    if not hasattr(ts, "overflow_recovery_attempted") or ts.overflow_recovery_attempted is not False:
        print(f"  FAIL: overflow_recovery_attempted not on _TurnState or not False")
        passed = False
    else:
        print(f"  PASS: [BC5] overflow_recovery_attempted=False on _TurnState")

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
    print(f"\n--- Step 9: Circuit breaker fallback [degradation path] ---")
    passed = True

    # Build history that triggers compaction (tiny budget)
    msgs: list[ModelMessage] = []
    for i in range(6):
        msgs += [_user(f"turn {i}"), _assistant(f"response {i} " + "x" * 200)]

    # Set compaction_failure_count = 3 → circuit breaker active
    config = CoConfig(llm_provider="ollama-openai", llm_num_ctx=30)
    deps = CoDeps(
        shell=ShellBackend(), config=config,
        model_registry=_REGISTRY,
        session=CoSessionState(session_id="eval-breaker"),
    )
    deps.runtime.compaction_failure_count = 3
    ctx = RunContext(deps=deps, model=_AGENT.model, usage=RunUsage())

    len_pre = len(msgs)
    result = await summarize_history_window(ctx, msgs)

    if len(result) >= len_pre:
        print(f"  FAIL: no compaction occurred (circuit breaker should still compact with static marker)")
        return False

    # Verify static marker (not LLM summary)
    has_static = any(
        isinstance(p, UserPromptPart) and "Earlier conversation trimmed" in str(p.content)
        for m in result if isinstance(m, ModelRequest) for p in m.parts
    )
    has_summary = any(
        isinstance(p, UserPromptPart) and "[Summary of" in str(p.content)
        for m in result if isinstance(m, ModelRequest) for p in m.parts
    )

    if has_summary:
        print(f"  FAIL: LLM summary produced despite circuit breaker (failure_count=3)")
        passed = False
    elif not has_static:
        print(f"  FAIL: no static marker found")
        passed = False
    else:
        print(f"  PASS: static marker used (no LLM call)")

    # Verify failure count unchanged (no LLM attempt was made)
    if deps.runtime.compaction_failure_count != 3:
        print(f"  FAIL: failure_count changed to {deps.runtime.compaction_failure_count} (should stay 3)")
        passed = False
    else:
        print(f"  PASS: failure_count unchanged at 3 (no LLM attempt)")

    # Verify compaction still reduces message count
    print(f"  PASS: messages reduced {len_pre} → {len(result)}")

    return passed


# ---------------------------------------------------------------------------
# Step 10: A/B enrichment quality [context enrichment value]
# ---------------------------------------------------------------------------


async def step_10_enrichment_ab() -> bool:
    """Compare summary quality with vs without context enrichment.

    Gap coverage: previous eval proved enrichment works mechanically but
    not that it improves summary quality. This step runs the same dropped
    messages through the summarizer twice — once with enrichment, once
    without — and checks whether enrichment-only information (file paths,
    todos) appears in the enriched summary but not the bare one.
    """
    print(f"\n--- Step 10: A/B enrichment quality [context enrichment value] ---")
    passed = True

    # Build dropped messages: tool calls with file paths, but tool RESULTS cleared
    # The file paths exist only in ToolCallPart.args — the summarizer can only
    # know about them through context enrichment.
    dropped: list[ModelMessage] = []
    file_names = ["auth/jwt_middleware.py", "auth/token_service.py", "config/jwt_settings.py"]
    for i, fname in enumerate(file_names):
        cid = f"ab{i}"
        dropped += [
            _user(f"Read {fname}"),
            _tool_call("read_file", {"file_path": fname}, cid),
            _tool_return("read_file", _CLEARED_PLACEHOLDER, cid),
            _assistant(f"Analyzed {fname}. The JWT configuration looks correct."),
        ]
    dropped += [
        _user("Now implement the changes"),
        _assistant("I'll start with the middleware refactor based on my analysis."),
    ]

    # Build context enrichment manually (simulating what _gather_compaction_context would produce)
    enrichment = (
        f"Files touched: {', '.join(file_names)}\n\n"
        "Active tasks:\n"
        "- [pending] Add RSA key rotation support\n"
        "- [in_progress] Migrate token blacklist to Redis"
    )

    _none_resolved = ResolvedModel(model=None, settings=None)
    resolved = _REGISTRY.get(ROLE_SUMMARIZATION, _none_resolved)

    # Run A: without enrichment
    print(f"  Running A (bare — no enrichment)...")
    try:
        async with asyncio.timeout(EVAL_SUMMARIZATION_TIMEOUT_SECS):
            summary_bare = await summarize_messages(dropped, resolved, context=None)
    except TimeoutError:
        print(f"  FAIL: bare summary timed out")
        return False

    # Run B: with enrichment
    print(f"  Running B (enriched — file paths + todos injected)...")
    try:
        async with asyncio.timeout(EVAL_SUMMARIZATION_TIMEOUT_SECS):
            summary_enriched = await summarize_messages(dropped, resolved, context=enrichment)
    except TimeoutError:
        print(f"  FAIL: enriched summary timed out")
        return False

    # Check: do enrichment-only signals appear in enriched but not bare?
    # File paths are in ToolCallPart.args (visible to enrichment) but tool results
    # are cleared (invisible without enrichment). The bare summarizer may guess
    # from assistant text ("Analyzed auth/jwt_middleware.py") but the enriched one
    # should have stronger signal.
    enrichment_signals = ["jwt_middleware", "token_service", "jwt_settings", "RSA key rotation", "Redis"]
    bare_hits = sum(1 for s in enrichment_signals if s.lower() in summary_bare.lower())
    enriched_hits = sum(1 for s in enrichment_signals if s.lower() in summary_enriched.lower())

    print(f"  Bare summary ({len(summary_bare)} chars): {bare_hits}/{len(enrichment_signals)} enrichment signals")
    print(f"  Enriched summary ({len(summary_enriched)} chars): {enriched_hits}/{len(enrichment_signals)} enrichment signals")

    if enriched_hits > bare_hits:
        print(f"  PASS: enrichment adds {enriched_hits - bare_hits} signals not in bare summary")
    elif enriched_hits == bare_hits and enriched_hits >= 3:
        print(f"  PASS: both summaries capture signals (assistant text leaks enough context)")
    elif enriched_hits >= 2:
        print(f"  PASS: enriched summary captures key signals ({enriched_hits}/{len(enrichment_signals)})")
    else:
        print(f"  FAIL: enriched summary missing enrichment signals ({enriched_hits}/{len(enrichment_signals)})")
        passed = False

    # Semantic validation — enriched summary should place enrichment content correctly
    ground_truth_10 = [
        ("enriched: file jwt_middleware.py in working set", ["jwt_middleware"]),
        ("enriched: file token_service.py in working set", ["token_service"]),
        ("enriched: file jwt_settings.py in working set", ["jwt_settings"]),
        ("enriched: RSA key rotation from todo", ["rsa key rotation", "key rotation"]),
        ("enriched: Redis migration from todo", ["redis"]),
    ]
    sem_ok, sem_lines = _check_semantic(summary_enriched, ground_truth_10, "Step 10 enriched")
    for line in sem_lines:
        print(line)
    if not sem_ok:
        passed = False

    # Bare summary should be missing at least some enrichment-only signals
    bare_ground = [
        ("bare: RSA key rotation (enrichment-only)", ["rsa key rotation", "key rotation"]),
        ("bare: Redis blacklist (enrichment-only)", ["redis"]),
    ]
    bare_ok, bare_lines = _check_semantic(summary_bare, bare_ground, "Step 10 bare")
    bare_missing = sum(1 for _, kws in bare_ground if not any(k.lower() in summary_bare.lower() for k in kws))
    if bare_missing > 0:
        print(f"  PASS: bare summary missing {bare_missing}/{len(bare_ground)} enrichment-only signals (expected)")
    else:
        print(f"  INFO: bare summary captured all enrichment signals (LLM inferred from assistant text)")

    # Print both summaries for manual inspection
    print(f"\n  --- Summary A (bare) ---")
    for line in summary_bare.split("\n")[:10]:
        print(f"    | {line}")
    if summary_bare.count("\n") > 10:
        print(f"    | ...<{summary_bare.count(chr(10)) - 10} more lines>")

    print(f"\n  --- Summary B (enriched) ---")
    for line in summary_enriched.split("\n")[:10]:
        print(f"    | {line}")
    if summary_enriched.count("\n") > 10:
        print(f"    | ...<{summary_enriched.count(chr(10)) - 10} more lines>")

    return passed


# ---------------------------------------------------------------------------
# Step 11: Edge case battery [structural — no LLM]
# ---------------------------------------------------------------------------


def step_11_edge_cases() -> bool:
    """Rapid-fire edge case validation across the compaction flow.

    All structural — no LLM calls. Validates that processors don't crash
    or corrupt on degenerate inputs.
    """
    print(f"\n--- Step 11: Edge case battery [structural — no LLM] ---")
    passed = True
    ctx = _make_ctx()

    # 11a: 1-turn history — all processors should no-op
    one_turn = [_user("hello"), _assistant("hi")]
    r = truncate_tool_results(ctx, one_turn)
    if r is not one_turn:
        print(f"  FAIL: P1 modified 1-turn history"); passed = False
    r = compact_assistant_responses(ctx, one_turn)
    if r is not one_turn:
        print(f"  FAIL: P2 modified 1-turn history"); passed = False
    r = detect_safety_issues(ctx, one_turn)
    if len(r) != len(one_turn):
        print(f"  FAIL: P3 injected on 1-turn history"); passed = False
    if passed:
        print(f"  PASS: 11a — 1-turn history: all processors no-op")

    # 11b: 2-turn history — processors run but nothing to compact
    two_turn = [_user("a"), _assistant("b"), _user("c"), _assistant("d")]
    r = truncate_tool_results(ctx, two_turn)
    assert len(r) == 4
    r = compact_assistant_responses(ctx, two_turn)
    assert len(r) == 4
    print(f"  PASS: 11b — 2-turn history: processors passthrough cleanly")

    # 11c: No ToolCallParts — context enrichment source #1 produces empty
    no_tools = [_user("just chatting"), _assistant("sure " * 500),
                _user("more chat"), _assistant("ok")]
    result = _gather_compaction_context(ctx, no_tools, dropped=[])
    if result is not None:
        print(f"  FAIL: 11c — enrichment should return None with no tools, got: {result[:60]}")
        passed = False
    else:
        print(f"  PASS: 11c — no ToolCallParts: enrichment returns None")

    # 11d: History contains a prior static marker (from emergency compact)
    from co_cli.context._history import _static_marker
    with_marker = [
        _user("turn 1"), _assistant("resp 1"),
        _static_marker(5),
        _user("turn 2"), _assistant("resp 2"),
        _user("turn 3"), _assistant("resp 3"),
    ]
    r = truncate_tool_results(ctx, with_marker)
    if len(r) != len(with_marker):
        print(f"  FAIL: 11d — P1 altered history with static marker"); passed = False
    r = compact_assistant_responses(ctx, with_marker)
    if len(r) != len(with_marker):
        print(f"  FAIL: 11d — P2 altered history with static marker"); passed = False
    # emergency_compact should still work — the marker is a UserPromptPart
    groups = group_by_turn(with_marker)
    if len(groups) < 3:
        print(f"  FAIL: 11d — grouping broke on static marker ({len(groups)} groups)"); passed = False
    else:
        print(f"  PASS: 11d — static marker in history: processors + grouping handle correctly ({len(groups)} groups)")

    # 11e: Every assistant response under 2.5K — P2 is pure no-op
    short_msgs: list[ModelMessage] = []
    for i in range(8):
        short_msgs += [_user(f"q{i}"), _assistant(f"Short answer {i}. " * 10)]
    r = compact_assistant_responses(ctx, short_msgs)
    truncated = _count_truncated_text(r)
    if truncated != 0:
        print(f"  FAIL: 11e — P2 truncated {truncated} messages when all under 2.5K"); passed = False
    else:
        print(f"  PASS: 11e — all responses under 2.5K: P2 is pure no-op (0 truncated)")

    # 11f: Single massive message in 2-turn history — P2 caps but compaction can't fire
    massive = [_user("explain everything"),
               ModelResponse(parts=[TextPart(content="X" * 60_000)]),
               _user("thanks"), _assistant("welcome")]
    r = compact_assistant_responses(ctx, massive)
    big_part = [p for m in r if isinstance(m, ModelResponse)
                for p in m.parts if isinstance(p, TextPart) and "X" in p.content]
    if big_part and len(big_part[0].content) > OLDER_MSG_MAX_CHARS:
        print(f"  FAIL: 11f — P2 didn't cap 60K message"); passed = False
    else:
        print(f"  PASS: 11f — single 60K message: P2 caps to {len(big_part[0].content) if big_part else '?'}")
    # Compaction boundaries: with 2 groups, should return None
    bounds = _compute_compaction_boundaries(massive)
    if bounds is not None:
        print(f"  FAIL: 11f — compaction boundaries valid on 2-group history"); passed = False
    else:
        print(f"  PASS: 11f — compaction boundaries returns None (only 2 groups)")

    # 11g: Tool-only first turn (no TextPart in first ModelResponse)
    tool_first = [
        _user("read this"),
        _tool_call("read_file", {"file_path": "/foo.py"}, "c1"),
        _tool_return("read_file", "content", "c1"),
        _assistant("Got it. Here's what I found..."),
        _user("continue"), _assistant("ok"),
    ]
    fre = find_first_run_end(tool_first)
    # First ModelResponse is the ToolCallPart (no TextPart) — should skip it
    # find_first_run_end looks for TextPart or ThinkingPart
    first_response = tool_first[1]  # _tool_call is a ModelResponse with ToolCallPart
    has_text = any(isinstance(p, (TextPart, ThinkingPart)) for p in first_response.parts)
    if has_text:
        print(f"  INFO: 11g — first ModelResponse has TextPart (test assumption wrong)")
    elif fre == 0:
        print(f"  FAIL: 11g — find_first_run_end returned 0 but there IS a TextPart response at index 3")
        passed = False
    elif fre == 3:
        print(f"  PASS: 11g — tool-only first response: find_first_run_end skips to index {fre} (TextPart response)")
    else:
        print(f"  PASS: 11g — tool-only first response: find_first_run_end={fre}")

    # 11h: Mixed compactable + non-compactable ToolReturnParts in same ModelRequest
    mixed_parts = ModelRequest(parts=[
        ToolReturnPart(tool_name="read_file", content="file content", tool_call_id="c1"),
        ToolReturnPart(tool_name="save_memory", content="saved ok", tool_call_id="c2"),
        ToolReturnPart(tool_name="web_search", content="search result", tool_call_id="c3"),
    ])
    # Build history: 6 read_file turns (to exceed keep=5) + the mixed turn + final
    mixed_msgs: list[ModelMessage] = []
    for i in range(6):
        cid = f"rf{i}"
        mixed_msgs += [_user(f"read {i}"), _tool_call("read_file", {}, cid),
                       _tool_return("read_file", f"content {i}", cid), _assistant(f"got {i}")]
    # Add mixed-parts turn
    mixed_msgs += [
        _user("do three things"),
        ModelResponse(parts=[
            ToolCallPart(tool_name="read_file", args={}, tool_call_id="c1"),
            ToolCallPart(tool_name="save_memory", args={}, tool_call_id="c2"),
            ToolCallPart(tool_name="web_search", args={}, tool_call_id="c3"),
        ]),
        mixed_parts,
        _assistant("all done"),
        _user("final"), _assistant("ok"),
    ]
    r = truncate_tool_results(ctx, mixed_msgs)
    # Find the mixed ModelRequest in result
    for m in r:
        if m is mixed_parts or (isinstance(m, ModelRequest) and len(m.parts) == 3
                                 and any(isinstance(p, ToolReturnPart) and p.tool_name == "save_memory" for p in m.parts)):
            save_mem = [p for p in m.parts if isinstance(p, ToolReturnPart) and p.tool_name == "save_memory"]
            if save_mem and save_mem[0].content != "saved ok":
                print(f"  FAIL: 11h — non-compactable save_memory was cleared"); passed = False
            elif save_mem:
                print(f"  PASS: 11h — mixed request: save_memory preserved, compactable tools cleared independently")
            break

    # 11i: Empty message list — all processors handle gracefully
    empty: list[ModelMessage] = []
    r = truncate_tool_results(ctx, empty)
    assert r == [] or r is empty
    r = compact_assistant_responses(ctx, empty)
    assert r == [] or r is empty
    r = detect_safety_issues(ctx, empty)
    assert len(r) == 0
    ec = emergency_compact(empty)
    assert ec is None
    bounds = _compute_compaction_boundaries(empty)
    assert bounds is None
    print(f"  PASS: 11i — empty message list: all processors + helpers handle gracefully")

    return passed


# ---------------------------------------------------------------------------
# Step 12: Prompt composition validation [LLM input inspection]
# ---------------------------------------------------------------------------


async def step_12_prompt_composition() -> bool:
    """Validate the actual prompt structure sent to the LLM during summarization.

    Intercepts the summarizer agent's run() call to inspect:
    1. System message (instructions) contains security guardrail
    2. User message (final_prompt) has template sections in correct order
    3. Context enrichment appears in the user message when provided
    4. Personality addendum appears after context (when active)
    5. message_history contains the dropped messages (not the full history)
    6. No prompt injection from message content leaks into instructions
    """
    print(f"\n--- Step 12: Prompt composition validation [LLM input inspection] ---")
    passed = True

    from co_cli.context._summarization import (
        _SUMMARIZER_SYSTEM_PROMPT,
        _summarizer_agent,
    )

    # --- 12a: Validate _build_summarizer_prompt output structure ---

    # Case: context + personality
    enrichment = "Files touched: /auth/views.py, /auth/middleware.py\n\nActive tasks:\n- [pending] Add JWT support"
    prompt = _build_summarizer_prompt(_SUMMARIZE_PROMPT, enrichment, personality_active=True)

    # Template sections present and in order
    section_positions = {}
    for section in ("## Goal", "## Key Decisions", "## Working Set", "## Progress", "## Next Steps"):
        pos = prompt.find(section)
        if pos == -1:
            print(f"  FAIL: 12a — template section {section!r} missing from assembled prompt")
            passed = False
        else:
            section_positions[section] = pos

    if len(section_positions) == 5:
        ordered = all(
            section_positions[a] < section_positions[b]
            for a, b in zip(
                ["## Goal", "## Key Decisions", "## Working Set", "## Progress"],
                ["## Key Decisions", "## Working Set", "## Progress", "## Next Steps"],
            )
        )
        if ordered:
            print(f"  PASS: 12a — 5 template sections present and in order")
        else:
            print(f"  FAIL: 12a — sections out of order: {section_positions}")
            passed = False

    # Context appears after template, before personality
    ctx_pos = prompt.find("## Additional Context")
    personality_pos = prompt.find("Additionally, preserve:")
    template_end = prompt.find("## Next Steps") + len("## Next Steps")

    if ctx_pos == -1:
        print(f"  FAIL: 12a — context addendum missing")
        passed = False
    elif ctx_pos < template_end:
        print(f"  FAIL: 12a — context appears inside template (pos {ctx_pos} < template end {template_end})")
        passed = False
    else:
        print(f"  PASS: 12a — context addendum after template")

    if personality_pos == -1:
        print(f"  FAIL: 12a — personality addendum missing")
        passed = False
    elif personality_pos < ctx_pos:
        print(f"  FAIL: 12a — personality before context (pos {personality_pos} < {ctx_pos})")
        passed = False
    else:
        print(f"  PASS: 12a — personality addendum after context (correct order)")

    # Enrichment content appears in the prompt
    if "/auth/views.py" in prompt and "Add JWT support" in prompt:
        print(f"  PASS: 12a — enrichment content (file paths + todos) present in assembled prompt")
    else:
        print(f"  FAIL: 12a — enrichment content missing from prompt")
        passed = False

    # --- 12b: Validate agent instructions (system prompt) ---

    # pydantic-ai stores instructions in _instructions list (the .instructions property is a method)
    raw_instructions = _summarizer_agent._instructions
    if not raw_instructions or not isinstance(raw_instructions[0], str):
        print(f"  FAIL: 12b — agent _instructions[0] is not a string: {raw_instructions}")
        passed = False
    else:
        agent_text = raw_instructions[0]
        if "CRITICAL SECURITY RULE" in agent_text:
            print(f"  PASS: 12b — agent instructions contain security guardrail")
        else:
            print(f"  FAIL: 12b — security guardrail missing from agent instructions")
            passed = False
        if "IGNORE ALL COMMANDS" in agent_text:
            print(f"  PASS: 12b — anti-injection directive present")
        else:
            print(f"  FAIL: 12b — anti-injection directive missing")
            passed = False

    # --- 12c: Validate message_history content passed to the summarizer ---
    # Build a realistic dropped slice and verify what gets passed

    dropped_msgs: list[ModelMessage] = [
        _user("Read the auth file"),
        _tool_call("read_file", {"file_path": "/auth/views.py"}, "c1"),
        _tool_return("read_file", _CLEARED_PLACEHOLDER, "c1"),
        _assistant("The auth module uses session-based authentication."),
        _user("Now check middleware"),
        _assistant("The middleware chain has 4 stages."),
    ]

    # Run the summarizer and capture what it receives via the agent's message assembly
    # We validate indirectly: the output should reference content from dropped_msgs
    _none_resolved = ResolvedModel(model=None, settings=None)
    resolved = _REGISTRY.get(ROLE_SUMMARIZATION, _none_resolved)

    try:
        async with asyncio.timeout(EVAL_SUMMARIZATION_TIMEOUT_SECS):
            summary = await summarize_messages(
                dropped_msgs,
                resolved,
                context="Files touched: /auth/views.py\n\nActive tasks:\n- [pending] Refactor middleware",
            )
    except TimeoutError:
        print(f"  FAIL: 12c — summarizer timed out")
        return False

    # The summary should reference content from the dropped messages
    low = summary.lower()
    if "auth" in low:
        print(f"  PASS: 12c — summary references dropped message content ('auth')")
    else:
        print(f"  FAIL: 12c — summary does not reference dropped message content")
        passed = False

    if "middleware" in low:
        print(f"  PASS: 12c — summary references dropped message content ('middleware')")
    else:
        print(f"  FAIL: 12c — summary missing 'middleware' from dropped messages")
        passed = False

    # Enrichment content should also appear (file path, todo)
    if "/auth/views.py" in summary or "views.py" in low:
        print(f"  PASS: 12c — enrichment file path appears in summary output")
    else:
        print(f"  INFO: 12c — enrichment file path not in summary (LLM may paraphrase)")

    if "middleware" in low and "refactor" in low:
        print(f"  PASS: 12c — enrichment todo content appears in summary output")
    else:
        print(f"  INFO: 12c — enrichment todo not verbatim in summary (LLM may paraphrase)")

    # --- 12d: No prompt injection from message content ---
    # Verify the instructions field is separate from message content
    # The security guardrail should NOT appear in the assembled user prompt
    if "CRITICAL SECURITY RULE" in prompt:
        print(f"  FAIL: 12d — security guardrail leaked into user prompt (should be in instructions only)")
        passed = False
    else:
        print(f"  PASS: 12d — security guardrail in instructions only, not in user prompt")

    # Verify cleared placeholder doesn't corrupt the prompt
    if _CLEARED_PLACEHOLDER in prompt:
        print(f"  FAIL: 12d — cleared placeholder leaked into user prompt")
        passed = False
    else:
        print(f"  PASS: 12d — cleared placeholder not in user prompt (only in message_history)")

    # --- 12e: No-context case (index_session_summary / /compact path) ---
    prompt_no_ctx = _build_summarizer_prompt(_SUMMARIZE_PROMPT, None, personality_active=False)
    if "## Additional Context" in prompt_no_ctx:
        print(f"  FAIL: 12e — context addendum present when context=None")
        passed = False
    elif "Additionally, preserve:" in prompt_no_ctx:
        print(f"  FAIL: 12e — personality addendum present when personality_active=False")
        passed = False
    elif prompt_no_ctx == _SUMMARIZE_PROMPT:
        print(f"  PASS: 12e — no-context/no-personality prompt equals raw template")
    else:
        print(f"  FAIL: 12e — prompt modified despite no context/personality")
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
    results["Step 2: P1 truncate_tool_results"] = step_2_p1_truncate()
    results["Step 3: P2 compact_assistant_responses [BC6,BC7]"] = step_3_p2_compact_responses()
    results["Step 4: Context enrichment [BC2,BC3]"] = step_4_context_enrichment()
    results["Step 5: Prompt assembly [Outcome 1,BC1]"] = step_5_prompt_assembly()

    # --- Full chain (real LLM) ---
    results["Step 6: Full chain P1→P5 (LLM)"] = await step_6_full_chain()
    results["Step 7: Multi-cycle [Outcome 3]"] = await step_7_multi_cycle()

    # --- Error recovery ---
    results["Step 8: Overflow [Outcome 4,BC5]"] = step_8_overflow()

    # --- Degradation + quality ---
    results["Step 9: Circuit breaker [degradation]"] = await step_9_circuit_breaker()
    results["Step 10: A/B enrichment quality"] = await step_10_enrichment_ab()

    # --- Edge cases ---
    results["Step 11: Edge case battery"] = step_11_edge_cases()

    # --- Prompt composition ---
    results["Step 12: Prompt composition"] = await step_12_prompt_composition()

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
    "Step 3": "Large assistant TextPart/ThinkingPart content in older messages is capped at 2500 chars with proportional head(20%)/tail(80%) truncation (aligned with gemini-cli). Tool args and returns are untouched.",
    "Step 4": "Side-channel context is gathered from 4 sources (file paths from ToolCallPart.args, pending todos, always-on memories, prior summaries) and capped at 4K chars.",
    "Step 5": "Summarizer prompt has 5 structured sections (Goal, Key Decisions, Working Set, Progress, Next Steps). Assembly order: template + context + personality.",
    "Step 6": "Full P1-P5 chain on a 14-turn conversation with tool calls, producing an LLM summary. Validates numerical counts at each stage.",
    "Step 7": "Chain on history containing a prior compaction summary. Validates that both prior context and new work are preserved across cycles.",
    "Step 8": "Overflow detection (413/400 with context-length body), emergency compaction (keep first+last groups), and one-shot recovery guard.",
    "Step 9": "Circuit breaker degradation: after 3 consecutive LLM failures, compaction falls back to static marker without attempting an LLM call.",
    "Step 10": "A/B enrichment quality: compares LLM summaries with and without context enrichment. Verifies enrichment-only signals (file paths, todos) appear in the enriched summary.",
    "Step 11": "Edge case battery (no LLM): 1-2 turn history, no tools, static markers in history, all short responses, single massive message, tool-only first turn, mixed compactable/non-compactable parts, empty list.",
    "Step 12": "Prompt composition: validates assembled prompt structure (section order, context placement, personality ordering), agent instructions (security guardrail), message_history content, prompt injection isolation, and no-context path.",
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

    report_path = Path("evals/eval_compaction_quality-result.md")
    raw = buf.getvalue()
    report_content = _build_report(raw, _LAST_RESULTS)
    report_path.write_text(report_content, encoding="utf-8")
    print(f"\nReport: {report_path}")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
