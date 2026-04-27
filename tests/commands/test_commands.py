"""Functional tests for user-facing slash commands.

All tests use real agent/deps — no mocks, no stubs.
"""

import asyncio
from pathlib import Path

import pytest
from pydantic_ai import Agent
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    UserPromptPart,
)
from pydantic_ai.result import DeferredToolRequests
from tests._frontend import SilentFrontend
from tests._ollama import ensure_ollama_warm
from tests._settings import make_settings
from tests._timeouts import LLM_NON_REASONING_TIMEOUT_SECS, LLM_TOOL_CONTEXT_TIMEOUT_SECS

from co_cli.commands._commands import (
    CommandContext,
    LocalOnly,
    ReplaceTranscript,
    dispatch,
)
from co_cli.config._core import settings
from co_cli.context.orchestrate import run_turn
from co_cli.deps import CoDeps, CoSessionState
from co_cli.display._core import Frontend, console
from co_cli.knowledge._store import KnowledgeStore
from co_cli.llm._factory import build_model
from co_cli.tools.shell_backend import ShellBackend

_CONFIG = settings
# Exclude MCP servers: agent.run() spawns their processes inline per call; these tests cover built-in tools only.
_CONFIG_NO_MCP = _CONFIG.model_copy(update={"mcp_servers": {}})
_LLM_MODEL = build_model(_CONFIG_NO_MCP.llm)
_SUMM_MODEL = _CONFIG_NO_MCP.llm.model

# Tool registry and agent built once at module level.
# Uses noreason settings for fast, non-reasoning tool-calling tests.
from co_cli.agent._core import build_tool_registry

_TOOL_REG = build_tool_registry(_CONFIG_NO_MCP)
_AGENT = Agent(
    _LLM_MODEL.model,
    deps_type=CoDeps,
    model_settings=_LLM_MODEL.settings_noreason,
    retries=_CONFIG_NO_MCP.tool_retries,
    output_type=[str, DeferredToolRequests],
    toolsets=[_TOOL_REG.toolset, *(e.toolset for e in _TOOL_REG.mcp_toolsets)],
)


def _make_ctx(
    message_history: list | None = None,
    *,
    knowledge_dir: "Path | None" = None,
    frontend: "Frontend | None" = None,
) -> CommandContext:
    """Build a real CommandContext with live agent and deps."""
    deps = CoDeps(
        shell=ShellBackend(),
        model=_LLM_MODEL,
        tool_index=dict(_TOOL_REG.tool_index),
        config=_CONFIG_NO_MCP,
        session=CoSessionState(),
        **({"knowledge_dir": knowledge_dir} if knowledge_dir is not None else {}),
    )
    return CommandContext(
        message_history=message_history or [],
        deps=deps,
        agent=_AGENT,
        frontend=frontend or SilentFrontend(),
    )


# --- Dispatch routing ---


@pytest.mark.asyncio
async def test_cmd_help_includes_status_usage():
    """/help should carry enough /status usage detail to defer per-command help."""
    ctx = _make_ctx()
    with console.capture() as cap:
        await dispatch("/help", ctx)
    output = cap.get()

    assert "/status" in output
    assert "/status <task-id>" in output


# --- State-changing commands ---


@pytest.mark.asyncio
async def test_cmd_clear():
    """/clear returns ReplaceTranscript with empty history."""
    ctx = _make_ctx(message_history=["fake_msg_1", "fake_msg_2"])
    result = await dispatch("/clear", ctx)
    assert isinstance(result, ReplaceTranscript)
    assert result.history == []


@pytest.mark.asyncio
async def test_cmd_approvals_routing_and_clear(tmp_path):
    """/approvals list routes correctly; /approvals clear removes session approval rules."""
    from co_cli.deps import ApprovalKindEnum, SessionApprovalRule

    ctx = _make_ctx()
    ctx.deps.session.session_approval_rules.append(
        SessionApprovalRule(kind=ApprovalKindEnum.SHELL, value="git")
    )
    ctx.deps.session.session_approval_rules.append(
        SessionApprovalRule(kind=ApprovalKindEnum.DOMAIN, value="docs.python.org")
    )

    result = await dispatch("/approvals list", ctx)
    assert isinstance(result, LocalOnly)

    await dispatch("/approvals clear", ctx)
    assert ctx.deps.session.session_approval_rules == []


# --- Approval flow (programmatic, no TTY) ---

_PROMPT_SHELL = (
    "Use the shell tool to execute: git rev-parse --is-inside-work-tree\n"
    "Do NOT describe what you would do — call the tool now."
)


@pytest.mark.asyncio
@pytest.mark.local
async def test_approval_approve():
    """Approving a deferred tool call through production orchestration executes it and returns a response.

    run_turn() with SilentFrontend(approval_response="y") exercises the full approval loop:
    deferred tool → auto-approve → execution → LLM response.
    """
    deps = CoDeps(
        shell=ShellBackend(),
        model=_LLM_MODEL,
        tool_index=dict(_TOOL_REG.tool_index),
        config=_CONFIG_NO_MCP,
        session=CoSessionState(),
    )
    await ensure_ollama_warm(_SUMM_MODEL, _CONFIG_NO_MCP.llm.host)
    try:
        async with asyncio.timeout(LLM_TOOL_CONTEXT_TIMEOUT_SECS * 2):
            turn = await run_turn(
                agent=_AGENT,
                user_input=_PROMPT_SHELL,
                deps=deps,
                message_history=[],
                frontend=SilentFrontend(approval_response="y"),
            )
        # Verify a tool call was attempted (shell command was deferred and processed)
        tool_called = any(
            isinstance(part, ToolCallPart)
            for msg in turn.messages
            if isinstance(msg, ModelResponse)
            for part in msg.parts
        )
        assert tool_called, "Expected shell to be called and approved"
        assert isinstance(turn.output, str)
        assert len(turn.messages) > 0
    finally:
        deps.shell.cleanup()


@pytest.mark.asyncio
@pytest.mark.local
async def test_approval_deny():
    """Denying a deferred tool call through production orchestration; LLM still responds.

    run_turn() with SilentFrontend(approval_response="n") exercises the deny path:
    deferred tool → deny → LLM acknowledgement response.
    """
    deps = CoDeps(
        shell=ShellBackend(),
        model=_LLM_MODEL,
        tool_index=dict(_TOOL_REG.tool_index),
        config=_CONFIG_NO_MCP,
        session=CoSessionState(),
    )
    await ensure_ollama_warm(_SUMM_MODEL, _CONFIG_NO_MCP.llm.host)
    try:
        async with asyncio.timeout(LLM_TOOL_CONTEXT_TIMEOUT_SECS * 2):
            turn = await run_turn(
                agent=_AGENT,
                user_input=_PROMPT_SHELL,
                deps=deps,
                message_history=[],
                frontend=SilentFrontend(approval_response="n"),
            )
        assert isinstance(turn.output, str)
    finally:
        deps.shell.cleanup()


# --- /new session checkpoint ---


# --- Safe command classification ---


# --- Two-mode dispatch boundary ---


@pytest.mark.asyncio
async def test_compact_noop_empty_history():
    """/compact on empty history returns LocalOnly — nothing to compact."""
    ctx = _make_ctx(message_history=[])
    result = await dispatch("/compact", ctx)
    assert isinstance(result, LocalOnly)


@pytest.mark.asyncio
@pytest.mark.local
async def test_compact_resets_thrash_gate():
    """/compact resets consecutive_low_yield counter and hint flag after successful compaction.

    Simulates a thrashed session: counter at threshold, hint emitted. After a
    manual /compact the gate fields must be cleared so the next auto-compaction
    can run unblocked.
    """
    history = [
        ModelRequest(parts=[UserPromptPart(content="Hello, tell me about recursion.")]),
        ModelResponse(
            parts=[TextPart(content="Recursion is a technique where a function calls itself.")]
        ),
        ModelRequest(parts=[UserPromptPart(content="Give an example in Python.")]),
        ModelResponse(
            parts=[TextPart(content="def fact(n): return 1 if n <= 1 else n * fact(n-1)")]
        ),
    ]
    ctx = _make_ctx(message_history=history)
    # Seed thrashed state: gate active, hint already emitted.
    ctx.deps.runtime.consecutive_low_yield_proactive_compactions = 99
    ctx.deps.runtime.compaction_thrash_hint_emitted = True

    await ensure_ollama_warm(_SUMM_MODEL, _CONFIG_NO_MCP.llm.host)
    async with asyncio.timeout(LLM_NON_REASONING_TIMEOUT_SECS):
        result = await dispatch("/compact", ctx)

    assert isinstance(result, ReplaceTranscript)
    assert ctx.deps.runtime.consecutive_low_yield_proactive_compactions == 0
    assert ctx.deps.runtime.compaction_thrash_hint_emitted is False


@pytest.mark.asyncio
async def test_dispatch_unknown_command_returns_local_only():
    """Unknown slash command returns LocalOnly — stays local, no agent turn."""
    ctx = _make_ctx()
    result = await dispatch("/xyzzy-no-such-command", ctx)
    assert isinstance(result, LocalOnly)


# ---------------------------------------------------------------------------
# /memory list and /memory count
# ---------------------------------------------------------------------------


def _write_memory(
    memory_dir: Path, filename: str, entry_id: str, content: str, **fm_extra
) -> Path:
    """Write a canonical kind=knowledge artifact and return its path."""
    from datetime import UTC, datetime

    created = fm_extra.pop("created", datetime.now(UTC).isoformat())
    artifact_kind = fm_extra.pop("artifact_kind", fm_extra.pop("kind", "preference"))
    if artifact_kind == "memory":
        artifact_kind = "preference"
    tags = fm_extra.pop("tags", [])
    tags_yaml = "[" + ", ".join(tags) + "]"
    extra_lines = "".join(f"{k}: {v}\n" for k, v in fm_extra.items())
    raw = (
        f"---\nid: '{entry_id}'\nkind: knowledge\nartifact_kind: {artifact_kind}\n"
        f"created: '{created}'\ntags: {tags_yaml}\n"
        f"{extra_lines}---\n\n{content}\n"
    )
    path = memory_dir / filename
    path.write_text(raw, encoding="utf-8")
    return path


@pytest.mark.asyncio
async def test_cmd_memory_list_all(tmp_path):
    """/memory list with no filters shows all seeded memories."""
    memory_dir = tmp_path / "memory"
    memory_dir.mkdir()
    _write_memory(memory_dir, "a.md", "aaa-0001", "alpha content")
    _write_memory(memory_dir, "b.md", "bbb-0002", "beta content")
    _write_memory(memory_dir, "c.md", "ccc-0003", "gamma content")

    ctx = _make_ctx(knowledge_dir=memory_dir)
    with console.capture() as cap:
        result = await dispatch("/memory list", ctx)

    assert isinstance(result, LocalOnly)
    out = cap.get()
    assert "aaa-0001" in out
    assert "bbb-0002" in out
    assert "ccc-0003" in out


@pytest.mark.asyncio
async def test_cmd_memory_list_query(tmp_path):
    """/memory list <keyword> returns only entries whose content contains the keyword."""
    memory_dir = tmp_path / "memory"
    memory_dir.mkdir()
    _write_memory(memory_dir, "a.md", "aaa-0001", "unique-zeta-keyword in here")
    _write_memory(memory_dir, "b.md", "bbb-0002", "nothing special")
    _write_memory(memory_dir, "c.md", "ccc-0003", "also nothing relevant")

    ctx = _make_ctx(knowledge_dir=memory_dir)
    with console.capture() as cap:
        result = await dispatch("/memory list unique-zeta-keyword", ctx)

    assert isinstance(result, LocalOnly)
    out = cap.get()
    assert "aaa-0001" in out
    assert "bbb-0002" not in out
    assert "ccc-0003" not in out


@pytest.mark.asyncio
async def test_cmd_memory_list_older_than(tmp_path):
    """/memory list --older-than 30 returns only entries older than 30 days."""
    from datetime import UTC, datetime, timedelta

    memory_dir = tmp_path / "memory"
    memory_dir.mkdir()
    old_date = (datetime.now(UTC) - timedelta(days=100)).isoformat()
    recent_date = (datetime.now(UTC) - timedelta(days=5)).isoformat()
    _write_memory(memory_dir, "old.md", "old-entry-id", "old content", created=old_date)
    _write_memory(memory_dir, "recent.md", "new-entry-id", "recent content", created=recent_date)

    ctx = _make_ctx(knowledge_dir=memory_dir)
    with console.capture() as cap:
        result = await dispatch("/memory list --older-than 30", ctx)

    assert isinstance(result, LocalOnly)
    out = cap.get()
    assert "old-entr" in out
    assert "new-entr" not in out


@pytest.mark.asyncio
async def test_cmd_memory_list_kind(tmp_path):
    """/memory list --kind feedback returns only feedback artifacts."""
    memory_dir = tmp_path / "memory"
    memory_dir.mkdir()
    _write_memory(memory_dir, "fb.md", "fb-entry-id", "feedback content", kind="feedback")
    _write_memory(memory_dir, "ru.md", "ru-entry-id", "rule content", kind="rule")

    ctx = _make_ctx(knowledge_dir=memory_dir)
    with console.capture() as cap:
        result = await dispatch("/memory list --kind feedback", ctx)

    assert isinstance(result, LocalOnly)
    out = cap.get()
    assert "fb-entry" in out
    assert "ru-entry" not in out


@pytest.mark.asyncio
async def test_cmd_memory_count_all(tmp_path):
    """/memory count prints the total number of memories."""
    memory_dir = tmp_path / "memory"
    memory_dir.mkdir()
    _write_memory(memory_dir, "a.md", "id-alpha", "alpha")
    _write_memory(memory_dir, "b.md", "id-beta", "beta")
    _write_memory(memory_dir, "c.md", "id-gamma", "gamma")

    ctx = _make_ctx(knowledge_dir=memory_dir)
    with console.capture() as cap:
        result = await dispatch("/memory count", ctx)

    assert isinstance(result, LocalOnly)
    assert "3" in cap.get()


@pytest.mark.asyncio
async def test_cmd_memory_count_query(tmp_path):
    """/memory count <keyword> prints the count of matching entries only."""
    memory_dir = tmp_path / "memory"
    memory_dir.mkdir()
    _write_memory(memory_dir, "a.md", "id-match1", "xylophone-unique-token here")
    _write_memory(memory_dir, "b.md", "id-match2", "xylophone-unique-token also")
    _write_memory(memory_dir, "c.md", "id-nomatch", "nothing relevant at all")

    ctx = _make_ctx(knowledge_dir=memory_dir)
    with console.capture() as cap:
        result = await dispatch("/memory count xylophone-unique-token", ctx)

    assert isinstance(result, LocalOnly)
    assert "2" in cap.get()


# ---------------------------------------------------------------------------
# /memory forget
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cmd_memory_forget_no_args(tmp_path):
    """/memory forget with no args prints usage and deletes nothing (BC-1)."""
    memory_dir = tmp_path / "memory"
    memory_dir.mkdir()
    mem_file = _write_memory(memory_dir, "a.md", "id-should-stay", "some content")

    ctx = _make_ctx(knowledge_dir=memory_dir)
    result = await dispatch("/memory forget", ctx)

    assert isinstance(result, LocalOnly)
    assert mem_file.exists(), "File must NOT be deleted when no args supplied"


@pytest.mark.asyncio
async def test_cmd_memory_forget_confirm_yes(tmp_path):
    """/memory forget <query> with y confirmation deletes all matched files."""
    memory_dir = tmp_path / "memory"
    memory_dir.mkdir()
    f1 = _write_memory(memory_dir, "a.md", "id-del-1", "zeta-forget-token content")
    f2 = _write_memory(memory_dir, "b.md", "id-del-2", "zeta-forget-token also here")
    f3 = _write_memory(memory_dir, "c.md", "id-keep", "unrelated content")

    ctx = _make_ctx(knowledge_dir=memory_dir, frontend=SilentFrontend(confirm_response=True))
    result = await dispatch("/memory forget zeta-forget-token", ctx)

    assert isinstance(result, LocalOnly)
    assert not f1.exists(), "Matched file 1 must be deleted"
    assert not f2.exists(), "Matched file 2 must be deleted"
    assert f3.exists(), "Unmatched file must NOT be deleted"


@pytest.mark.asyncio
async def test_cmd_memory_forget_confirm_no(tmp_path):
    """/memory forget aborts when user answers n — no files deleted."""
    memory_dir = tmp_path / "memory"
    memory_dir.mkdir()
    f1 = _write_memory(memory_dir, "a.md", "id-safe-1", "omega-abort-token content")
    f2 = _write_memory(memory_dir, "b.md", "id-safe-2", "omega-abort-token also")

    ctx = _make_ctx(knowledge_dir=memory_dir, frontend=SilentFrontend(confirm_response=False))
    result = await dispatch("/memory forget omega-abort-token", ctx)

    assert isinstance(result, LocalOnly)
    assert f1.exists(), "File 1 must NOT be deleted on n confirmation"
    assert f2.exists(), "File 2 must NOT be deleted on n confirmation"


@pytest.mark.asyncio
async def test_cmd_memory_forget_no_match(tmp_path):
    """/memory forget <query> with no matching entries prints No memories matched."""
    memory_dir = tmp_path / "memory"
    memory_dir.mkdir()
    _write_memory(memory_dir, "a.md", "id-exists", "some totally different content")

    ctx = _make_ctx(knowledge_dir=memory_dir, frontend=SilentFrontend(confirm_response=True))
    with console.capture() as cap:
        result = await dispatch("/memory forget nonexistent-zzz-query", ctx)

    assert isinstance(result, LocalOnly)
    assert "No memories matched" in cap.get()


@pytest.mark.asyncio
async def test_cmd_memory_forget_removes_db_entry(tmp_path):
    """/memory forget removes the matching entry from search.db, not just the file."""
    memory_dir = tmp_path / "memory"
    memory_dir.mkdir()
    token = "kappa-db-purge-token"
    f1 = _write_memory(memory_dir, "a.md", "id-db-del", f"{token} content")
    _write_memory(memory_dir, "b.md", "id-db-keep", "unrelated entry")

    idx = KnowledgeStore(config=make_settings(), knowledge_db_path=tmp_path / "search.db")
    try:
        idx.sync_dir("knowledge", memory_dir)
        # Confirm the entry is in the DB before forget
        before = idx.search(token, source="knowledge", limit=10)
        assert any(str(f1) in r.path for r in before), "Entry must be indexed before forget"

        deps = CoDeps(
            shell=ShellBackend(),
            model=_LLM_MODEL,
            tool_index=dict(_TOOL_REG.tool_index),
            config=_CONFIG_NO_MCP,
            session=CoSessionState(),
            knowledge_dir=memory_dir,
            knowledge_store=idx,
        )
        ctx = CommandContext(
            message_history=[],
            deps=deps,
            agent=_AGENT,
            frontend=SilentFrontend(confirm_response=True),
        )
        result = await dispatch(f"/memory forget {token}", ctx)

        assert isinstance(result, LocalOnly)
        assert not f1.exists(), "File must be deleted"
        after = idx.search(token, source="knowledge", limit=10)
        assert not any(str(f1) in r.path for r in after), "DB entry must be removed after forget"
    finally:
        idx.close()


# ---------------------------------------------------------------------------
# /memory registration
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cmd_memory_registered(tmp_path):
    """/memory list dispatches successfully — command is registered in BUILTIN_COMMANDS."""
    ctx = _make_ctx(knowledge_dir=tmp_path)
    result = await dispatch("/memory list", ctx)
    assert isinstance(result, LocalOnly)


# ---------------------------------------------------------------------------
# /knowledge dream | restore | decay-review
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cmd_knowledge_dream_dry_run(tmp_path):
    """/knowledge dream --dry runs a cycle without writing state or touching artifacts."""
    knowledge_dir = tmp_path / "knowledge"
    knowledge_dir.mkdir()
    mem_file = _write_memory(knowledge_dir, "a.md", "id-dream-dry", "alpha content")

    ctx = _make_ctx(knowledge_dir=knowledge_dir)
    with console.capture() as cap:
        result = await dispatch("/knowledge dream --dry", ctx)

    assert isinstance(result, LocalOnly)
    out = cap.get()
    assert "Dry run" in out or "dry run" in out
    assert "extracted" in out
    assert "merged" in out
    assert "decayed" in out
    assert mem_file.exists(), "File must not be touched in dry-run mode"
    assert not (knowledge_dir / "_dream_state.json").exists(), (
        "Dry run must not persist dream state"
    )


@pytest.mark.asyncio
async def test_cmd_knowledge_dream_real_run_writes_state(tmp_path):
    """/knowledge dream runs the full cycle and persists dream state when any work happens."""
    knowledge_dir = tmp_path / "knowledge"
    knowledge_dir.mkdir()

    ctx = _make_ctx(knowledge_dir=knowledge_dir)
    ctx.deps.sessions_dir = tmp_path / "sessions-absent"
    with console.capture() as cap:
        result = await dispatch("/knowledge dream", ctx)

    assert isinstance(result, LocalOnly)
    out = cap.get()
    assert "Dream cycle complete" in out
    state_path = knowledge_dir / "_dream_state.json"
    assert state_path.exists(), "Non-dry dream run must persist _dream_state.json"
    state = state_path.read_text(encoding="utf-8")
    assert "last_dream_at" in state


@pytest.mark.asyncio
async def test_cmd_knowledge_restore_empty_archive(tmp_path):
    """/knowledge restore with no arg on an empty archive prints an empty-state message."""
    knowledge_dir = tmp_path / "knowledge"
    knowledge_dir.mkdir()

    ctx = _make_ctx(knowledge_dir=knowledge_dir)
    with console.capture() as cap:
        result = await dispatch("/knowledge restore", ctx)

    assert isinstance(result, LocalOnly)
    assert "No archived artifacts" in cap.get()


@pytest.mark.asyncio
async def test_cmd_knowledge_restore_moves_file_back(tmp_path):
    """/knowledge restore <slug> moves an archived file back to knowledge_dir."""
    knowledge_dir = tmp_path / "knowledge"
    knowledge_dir.mkdir()
    archive_dir = knowledge_dir / "_archive"
    archive_dir.mkdir()
    archived_file = _write_memory(
        archive_dir, "my-note-abcd1234.md", "id-restore", "restored body"
    )

    ctx = _make_ctx(knowledge_dir=knowledge_dir)
    result = await dispatch("/knowledge restore my-note-abcd1234", ctx)

    assert isinstance(result, LocalOnly)
    assert not archived_file.exists(), "File must leave _archive/ after restore"
    restored_path = knowledge_dir / "my-note-abcd1234.md"
    assert restored_path.exists(), "File must reappear in knowledge_dir after restore"


@pytest.mark.asyncio
async def test_cmd_knowledge_restore_ambiguous(tmp_path):
    """/knowledge restore with an ambiguous slug (multiple matches) reports failure."""
    knowledge_dir = tmp_path / "knowledge"
    knowledge_dir.mkdir()
    archive_dir = knowledge_dir / "_archive"
    archive_dir.mkdir()
    _write_memory(archive_dir, "note-1111.md", "id-amb-1", "first")
    _write_memory(archive_dir, "note-2222.md", "id-amb-2", "second")

    ctx = _make_ctx(knowledge_dir=knowledge_dir)
    with console.capture() as cap:
        result = await dispatch("/knowledge restore note", ctx)

    assert isinstance(result, LocalOnly)
    assert "Restore failed" in cap.get()
    # Both files must remain in the archive since the slug was ambiguous
    assert (archive_dir / "note-1111.md").exists()
    assert (archive_dir / "note-2222.md").exists()


@pytest.mark.asyncio
async def test_cmd_knowledge_decay_review_dry_lists_only(tmp_path):
    """/knowledge decay-review --dry lists stale candidates without archiving."""
    from datetime import UTC, datetime, timedelta

    knowledge_dir = tmp_path / "knowledge"
    knowledge_dir.mkdir()
    old_date = (datetime.now(UTC) - timedelta(days=365)).isoformat()
    stale = _write_memory(
        knowledge_dir, "stale.md", "id-stale-1", "stale content", created=old_date
    )
    fresh = _write_memory(knowledge_dir, "fresh.md", "id-fresh-1", "fresh content")

    ctx = _make_ctx(knowledge_dir=knowledge_dir)
    with console.capture() as cap:
        result = await dispatch("/knowledge decay-review --dry", ctx)

    assert isinstance(result, LocalOnly)
    out = cap.get()
    assert "stale" in out
    assert "1 decay candidate" in out
    assert stale.exists(), "Dry run must not archive candidates"
    assert fresh.exists(), "Non-stale files must not be touched"
    assert not (knowledge_dir / "_archive").exists(), "Dry run must not create _archive/"


@pytest.mark.asyncio
async def test_cmd_knowledge_decay_review_confirm_archives(tmp_path):
    """/knowledge decay-review with y confirmation archives every candidate."""
    from datetime import UTC, datetime, timedelta

    knowledge_dir = tmp_path / "knowledge"
    knowledge_dir.mkdir()
    old_date = (datetime.now(UTC) - timedelta(days=365)).isoformat()
    stale = _write_memory(
        knowledge_dir, "stale.md", "id-stale-arch", "stale content", created=old_date
    )

    ctx = _make_ctx(knowledge_dir=knowledge_dir, frontend=SilentFrontend(confirm_response=True))
    with console.capture() as cap:
        result = await dispatch("/knowledge decay-review", ctx)

    assert isinstance(result, LocalOnly)
    assert "Archived 1" in cap.get()
    assert not stale.exists(), "Stale file must be moved out of knowledge_dir"
    assert (knowledge_dir / "_archive" / "stale.md").exists(), (
        "Stale file must appear in _archive/"
    )


@pytest.mark.asyncio
async def test_cmd_knowledge_decay_review_abort(tmp_path):
    """/knowledge decay-review with n confirmation keeps every candidate in place."""
    from datetime import UTC, datetime, timedelta

    knowledge_dir = tmp_path / "knowledge"
    knowledge_dir.mkdir()
    old_date = (datetime.now(UTC) - timedelta(days=365)).isoformat()
    stale = _write_memory(
        knowledge_dir, "stale.md", "id-stale-abort", "stale content", created=old_date
    )

    ctx = _make_ctx(knowledge_dir=knowledge_dir, frontend=SilentFrontend(confirm_response=False))
    with console.capture() as cap:
        result = await dispatch("/knowledge decay-review", ctx)

    assert isinstance(result, LocalOnly)
    assert "Aborted" in cap.get()
    assert stale.exists(), "File must remain when user declines"


@pytest.mark.asyncio
async def test_cmd_knowledge_unknown_subcommand_prints_usage(tmp_path):
    """Unknown /knowledge subcommand falls into the usage branch and still returns LocalOnly."""
    ctx = _make_ctx(knowledge_dir=tmp_path)
    with console.capture() as cap:
        result = await dispatch("/knowledge bogus-subcmd", ctx)

    assert isinstance(result, LocalOnly)
    out = cap.get()
    assert "Unknown /knowledge subcommand" in out
    assert "dream" in out
    assert "restore" in out
    assert "decay-review" in out


@pytest.mark.asyncio
async def test_cmd_knowledge_stats_empty_dir(tmp_path):
    """/knowledge stats on an empty dir reports zero artifacts and no dream state."""
    knowledge_dir = tmp_path / "knowledge"
    knowledge_dir.mkdir()

    ctx = _make_ctx(knowledge_dir=knowledge_dir)
    with console.capture() as cap:
        result = await dispatch("/knowledge stats", ctx)

    assert isinstance(result, LocalOnly)
    out = cap.get()
    assert "Knowledge: 0 artifacts" in out
    assert "Last dream: never" in out
    assert "Decay candidates: 0" in out


@pytest.mark.asyncio
async def test_cmd_knowledge_stats_counts_accurately(tmp_path):
    """/knowledge stats reports correct kind breakdown, decay-protected, archived, and decay counts."""
    import json
    from datetime import UTC, datetime, timedelta

    knowledge_dir = tmp_path / "knowledge"
    knowledge_dir.mkdir()
    archive_dir = knowledge_dir / "_archive"
    archive_dir.mkdir()

    # Active artifacts: 2 preference, 1 feedback (one decay-protected)
    _write_memory(knowledge_dir, "p1.md", "id-p1", "prefer dark mode", artifact_kind="preference")
    _write_memory(
        knowledge_dir,
        "p2.md",
        "id-p2",
        "prefer pytest",
        artifact_kind="preference",
    )
    _write_memory(
        knowledge_dir,
        "fb.md",
        "id-fb",
        "likes concise output",
        artifact_kind="feedback",
        decay_protected="true",
    )

    # Archived artifact
    _write_memory(archive_dir, "old.md", "id-old", "old stuff", artifact_kind="preference")

    # Dream state with 1 cycle
    old_date = (datetime.now(UTC) - timedelta(days=365)).isoformat()
    dream_state = {
        "last_dream_at": "2026-04-15T22:00:00+00:00",
        "processed_sessions": [],
        "stats": {"total_cycles": 1, "total_extracted": 3, "total_merged": 2, "total_decayed": 1},
    }
    (knowledge_dir / "_dream_state.json").write_text(json.dumps(dream_state), encoding="utf-8")

    # Stale decay candidate (old, never recalled)
    _write_memory(knowledge_dir, "stale.md", "id-stale", "stale content", created=old_date)

    ctx = _make_ctx(knowledge_dir=knowledge_dir)
    with console.capture() as cap:
        result = await dispatch("/knowledge stats", ctx)

    assert isinstance(result, LocalOnly)
    out = cap.get()

    # 4 active artifacts (p1, p2, fb, stale)
    assert "Knowledge: 4 artifacts" in out
    # kind breakdown includes both present kinds
    assert "preference: 3" in out
    assert "feedback: 1" in out
    # archived count
    assert "Archived: 1" in out
    # dream state timestamp present
    assert "2026-04-15" in out
    assert "3 extracted" in out
    # decay candidate (stale.md is over 90 days old, never recalled)
    assert "Decay candidates: 1" in out
