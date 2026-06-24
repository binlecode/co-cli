"""Behavioural tests for slash-command dispatch (WF 2.3).

Covers:
  - Argument expansion ($1/$2/$N, $ARGUMENTS, no-args passthrough)
  - Off-by-one safety for $10 (reversed enumerate trick)
  - Built-in name protection (skills cannot shadow builtins)
  - DelegateToAgent payload (skill_env, skill_name)
  - _apply_command_outcome for DelegateToAgent
  - cleanup_skill_run_state restores env
  - Unknown command → LocalOnly
  - Blocked skill-env key (PATH) filtered at load time
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from tests._settings import SETTINGS

from co_cli.agent.core import build_native_toolset
from co_cli.commands.core import dispatch
from co_cli.commands.types import (
    CommandContext,
    DelegateToAgent,
    LocalOnly,
    ReplaceTranscript,
)
from co_cli.deps import CoDeps, CoSessionState
from co_cli.display.headless import HeadlessFrontend
from co_cli.main import _apply_command_outcome
from co_cli.skills.lifecycle import cleanup_skill_run_state
from co_cli.skills.loader import load_skills
from co_cli.tools.shell_backend import ShellBackend

_BUNDLED_SKILLS_DIR = Path("co_cli/skills")


def _make_deps(tmp_path: Path) -> CoDeps:
    skill_catalog = load_skills(_BUNDLED_SKILLS_DIR, user_skills_dir=tmp_path)
    _, tool_catalog = build_native_toolset()
    return CoDeps(
        shell=ShellBackend(),
        config=SETTINGS,
        tool_catalog=tool_catalog,
        session=CoSessionState(),
        skill_catalog=skill_catalog,
        skills_dir=_BUNDLED_SKILLS_DIR,
        user_skills_dir=tmp_path,
        tool_results_dir=tmp_path / "tool-results",
    )


def _make_ctx(deps: CoDeps) -> CommandContext:
    return CommandContext(
        message_history=[],
        deps=deps,
        agent=None,  # type: ignore[arg-type]  # not needed for dispatch tests
        frontend=HeadlessFrontend(),
        completer=None,
    )


def _write_skill(skills_dir: Path, name: str, body: str, extra_frontmatter: str = "") -> None:
    """Write a minimal slash-invocable skill into skills_dir as <name>/SKILL.md.

    Sets ``user-invocable: true`` explicitly because that is now opt-in (the loader
    default is false) — these cases exercise the slash-dispatch path.
    """
    content = f"---\ndescription: Test skill {name}\nuser-invocable: true\n{extra_frontmatter}---\n\n{body}\n"
    skill_dir = skills_dir / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(content, encoding="utf-8")


# ---------------------------------------------------------------------------
# 1. Argument expansion — positional $N and off-by-one for $10
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_positional_arg_expansion_three_args(tmp_path: Path) -> None:
    """$1 $2 $3 in body are replaced by the three supplied arguments.

    Expansion is triggered by the presence of $ARGUMENTS in the body — the
    same code path also replaces positional $N tokens.

    Regression guard: if positional replacement is broken, the raw body with
    unexpanded $N tokens is sent to the agent instead of the real arguments.
    """
    _write_skill(tmp_path, "myskill", "$ARGUMENTS → $1 $2 $3")
    deps = _make_deps(tmp_path)
    ctx = _make_ctx(deps)
    outcome = await dispatch("/myskill a b c", ctx)
    assert isinstance(outcome, DelegateToAgent)
    assert outcome.delegated_input == "a b c → a b c"


@pytest.mark.asyncio
async def test_positional_arg_expansion_ten_args_no_smash(tmp_path: Path) -> None:
    """$10 is expanded to the 10th arg, not $1 followed by literal 0.

    Expansion is triggered by $ARGUMENTS being present. The reversed enumerate
    trick processes higher-numbered tokens first so $10 is matched whole before
    $1 is substituted — a naive forward pass would turn $10 into '<arg1>0'.

    Regression guard: forward-order replacement would corrupt any $N where N >= 10
    whose prefix digit matches a smaller N already expanded.
    """
    args_body = "$ARGUMENTS | " + " ".join(f"${i}" for i in range(1, 11))
    _write_skill(tmp_path, "bigskill", args_body)
    deps = _make_deps(tmp_path)
    ctx = _make_ctx(deps)
    raw = "/bigskill" + "".join(f" arg{i}" for i in range(1, 11))
    outcome = await dispatch(raw, ctx)
    assert isinstance(outcome, DelegateToAgent)
    full_args = " ".join(f"arg{i}" for i in range(1, 11))
    positional_part = " ".join(f"arg{i}" for i in range(1, 11))
    assert outcome.delegated_input == f"{full_args} | {positional_part}"


# ---------------------------------------------------------------------------
# 2. $ARGUMENTS raw blob substitution
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_arguments_raw_blob_substituted(tmp_path: Path) -> None:
    """$ARGUMENTS is replaced by the full raw args string.

    Regression guard: if $ARGUMENTS substitution is skipped, the skill body
    still contains the literal '$ARGUMENTS' token when delegated to the agent.
    """
    _write_skill(tmp_path, "myskill", "Do: $ARGUMENTS")
    deps = _make_deps(tmp_path)
    ctx = _make_ctx(deps)
    outcome = await dispatch("/myskill foo bar", ctx)
    assert isinstance(outcome, DelegateToAgent)
    assert outcome.delegated_input == "Do: foo bar"


# ---------------------------------------------------------------------------
# 3. No-args body unchanged
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_args_body_unchanged(tmp_path: Path) -> None:
    """Calling a skill with no arguments leaves its body unmodified.

    Regression guard: if the expansion path runs unconditionally, an empty args
    string would incorrectly trigger template replacement on a plain body.
    """
    _write_skill(tmp_path, "myskill", "Just do this")
    deps = _make_deps(tmp_path)
    ctx = _make_ctx(deps)
    outcome = await dispatch("/myskill", ctx)
    assert isinstance(outcome, DelegateToAgent)
    assert outcome.delegated_input == "Just do this"


@pytest.mark.asyncio
async def test_arguments_token_not_leaked_on_bare_invocation(tmp_path: Path) -> None:
    """A body with $ARGUMENTS/$0 but no args delegates with neither literal token.

    When a skill body references $ARGUMENTS (and $0) but is invoked with no
    arguments, interpolation must still run and substitute empty/name — the raw
    placeholders must never survive into the delegated input.

    Regression guard: the old `if args and "$ARGUMENTS" in body` guard skipped
    interpolation entirely when args were empty, leaking the literal '$ARGUMENTS'
    (and '$0') token to the agent.
    """
    _write_skill(tmp_path, "argskill", "Plan: $ARGUMENTS (via $0)")
    deps = _make_deps(tmp_path)
    ctx = _make_ctx(deps)
    outcome = await dispatch("/argskill", ctx)
    assert isinstance(outcome, DelegateToAgent)
    assert "$ARGUMENTS" not in outcome.delegated_input
    assert "$0" not in outcome.delegated_input
    assert outcome.delegated_input == "Plan:  (via argskill)"


# ---------------------------------------------------------------------------
# 3b. Real bundled `plan` skill carries an inline task / renders clean empty
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bundled_plan_skill_carries_inline_task(tmp_path: Path) -> None:
    """/plan <task> delegates a body that contains the inline task text.

    The bundled `plan` skill advertises `argument-hint: "[what to plan]"`; its body
    must honour that by carrying the task into the delegated input so the agent
    plans the actual request rather than the methodology alone.

    Regression guard: if the $ARGUMENTS placeholder is dropped from the body, the
    task text is silently discarded and the agent plans nothing concrete.
    """
    deps = _make_deps(tmp_path)
    ctx = _make_ctx(deps)
    outcome = await dispatch("/plan write a literature review on topic X", ctx)
    assert isinstance(outcome, DelegateToAgent)
    assert "write a literature review on topic X" in outcome.delegated_input


@pytest.mark.asyncio
async def test_bundled_plan_skill_bare_renders_clean(tmp_path: Path) -> None:
    """Bare /plan delegates the methodology body with no placeholder token left.

    A bare invocation must still deliver the planning methodology (so the agent
    can plan the conversation's most-recent request) with no literal $ARGUMENTS/$0
    surviving into the delegated input.

    Regression guard: a leaked '$ARGUMENTS' token would read as a stray placeholder
    to the agent; a mangled body would lose the methodology.
    """
    deps = _make_deps(tmp_path)
    ctx = _make_ctx(deps)
    outcome = await dispatch("/plan", ctx)
    assert isinstance(outcome, DelegateToAgent)
    assert "$ARGUMENTS" not in outcome.delegated_input
    assert "$0" not in outcome.delegated_input
    assert "Phase 1 — Scope" in outcome.delegated_input


# ---------------------------------------------------------------------------
# 4. Built-in name protection (skill cannot shadow a builtin)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_builtin_cannot_be_shadowed_by_skill(tmp_path: Path) -> None:
    """/clear invokes the builtin, not a user skill named 'clear'.

    Regression guard: if builtins are checked after the skill registry, a user
    skill named 'clear' would hijack the command and run arbitrary agent input
    instead of clearing history.
    """
    _write_skill(tmp_path, "clear", "Do something malicious")
    deps = _make_deps(tmp_path)
    ctx = _make_ctx(deps)
    outcome = await dispatch("/clear", ctx)
    # Positive: the builtin /clear ran — it returns ReplaceTranscript(history=[]),
    # never DelegateToAgent (which would mean the shadowing skill hijacked it).
    assert not isinstance(outcome, DelegateToAgent), (
        "A skill named 'clear' must not shadow the /clear builtin"
    )
    assert isinstance(outcome, ReplaceTranscript)
    assert outcome.history == []


# ---------------------------------------------------------------------------
# 5. DelegateToAgent payload — skill_env and skill_name
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delegate_to_agent_carries_skill_env_and_name(tmp_path: Path) -> None:
    """DelegateToAgent carries the skill's env vars and name.

    Regression guard: if skill_env or skill_name are dropped from the outcome,
    _apply_command_outcome cannot inject env vars or set active_skill_name,
    breaking skill isolation.
    """
    _write_skill(tmp_path, "myskill", "Run the thing", "skill-env:\n  MY_VAR: hello\n")
    deps = _make_deps(tmp_path)
    ctx = _make_ctx(deps)
    outcome = await dispatch("/myskill", ctx)
    assert isinstance(outcome, DelegateToAgent)
    assert outcome.skill_env == {"MY_VAR": "hello"}
    assert outcome.skill_name == "myskill"


# ---------------------------------------------------------------------------
# 6. _apply_command_outcome for DelegateToAgent
# ---------------------------------------------------------------------------


def test_apply_command_outcome_delegate_sets_env_and_skill_name(tmp_path: Path) -> None:
    """_apply_command_outcome injects skill_env into os.environ and sets active_skill_name.

    Regression guard: if saved_env snapshot, os.environ update, or
    active_skill_name assignment is broken, skill env isolation and the
    active_skill_name guard in the agent loop silently stop working.
    """
    deps = _make_deps(tmp_path)
    original_history = [{"role": "user", "content": "hi"}]
    test_key = "TEST_VAR_XYZ_DISPATCH"

    # Ensure the key is absent before we start
    os.environ.pop(test_key, None)

    outcome = DelegateToAgent(
        delegated_input="do something",
        skill_env={test_key: "newval"},
        skill_name="test-skill",
    )
    frontend = HeadlessFrontend()
    should_continue, new_history, user_input, saved_env = _apply_command_outcome(
        outcome, original_history, deps, frontend
    )

    try:
        assert should_continue is False
        assert new_history is original_history
        assert user_input == "do something"
        assert test_key in saved_env
        assert saved_env[test_key] is None, (
            "key was absent before the call; saved snapshot must be None"
        )
        assert os.environ[test_key] == "newval"
        assert deps.runtime.active_skill_name == "test-skill"
    finally:
        os.environ.pop(test_key, None)


# ---------------------------------------------------------------------------
# 7. cleanup_skill_run_state restores env
# ---------------------------------------------------------------------------


def test_cleanup_skill_run_state_restores_and_removes(tmp_path: Path) -> None:
    """cleanup_skill_run_state restores existing keys and removes None-snapshotted keys.

    Regression guard: if pop() is not called for None-snapshotted keys, stale
    env vars linger after the skill finishes, leaking state into the next turn.
    """
    deps = _make_deps(tmp_path)

    existing_key = "CO_TEST_EXISTING_KEY_ABC"
    absent_key = "CO_TEST_ABSENT_KEY_XYZ"

    original_val = "original_value"
    os.environ[existing_key] = original_val
    os.environ.pop(absent_key, None)

    # Simulate what _apply_command_outcome snapshots before injecting skill env
    saved_env: dict[str, str | None] = {
        existing_key: original_val,
        absent_key: None,
    }
    deps.runtime.active_skill_name = "my-skill"

    # Simulate the skill modifying env
    os.environ[existing_key] = "skill_modified_value"
    os.environ[absent_key] = "skill_added_value"

    cleanup_skill_run_state(saved_env, deps)

    try:
        assert os.environ[existing_key] == original_val, (
            "existing key must be restored to its pre-skill value"
        )
        assert absent_key not in os.environ, (
            "key that was absent before the skill must be removed after cleanup"
        )
        assert deps.runtime.active_skill_name is None
    finally:
        os.environ.pop(existing_key, None)
        os.environ.pop(absent_key, None)


# ---------------------------------------------------------------------------
# 8. Unknown command → LocalOnly
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unknown_command_returns_local_only(tmp_path: Path) -> None:
    """Dispatching an unknown slash command returns LocalOnly.

    Regression guard: if the fallthrough path raises or returns a wrong type,
    every typo in a slash command crashes the REPL.
    """
    deps = _make_deps(tmp_path)
    ctx = _make_ctx(deps)
    outcome = await dispatch("/nonexistent_xyz_command", ctx)
    assert isinstance(outcome, LocalOnly)


@pytest.mark.asyncio
async def test_skill_without_flag_is_not_slash_invocable(tmp_path: Path) -> None:
    """A skill carrying no user-invocable flag is NOT reachable as a slash command.

    Slash exposure is opt-in: the loader defaults user-invocable to false, and
    dispatch only delegates to a user-invocable skill. So a skill the agent
    fabricates (reviewer, merge, manual drop) with no flag is model-selectable only
    and never mounts as /<name>.

    Regression guard: if either the default flips back to true or dispatch stops
    checking the flag, agent-authored skills leak into the slash-command surface.
    """
    skill_dir = tmp_path / "modelonly"
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        "---\ndescription: Model-only skill\n---\n\nDo the thing.\n",
        encoding="utf-8",
    )
    deps = _make_deps(tmp_path)
    ctx = _make_ctx(deps)
    outcome = await dispatch("/modelonly", ctx)
    assert not isinstance(outcome, DelegateToAgent)
    assert isinstance(outcome, LocalOnly)


# ---------------------------------------------------------------------------
# 9. Blocked skill-env key (PATH) dropped at load time
# ---------------------------------------------------------------------------


def test_blocked_skill_env_key_filtered_at_load(tmp_path: Path) -> None:
    """PATH in skill-env frontmatter is silently dropped; safe keys are kept.

    Regression guard: if _SKILL_ENV_BLOCKED filtering is removed from
    _load_skill_file, skills can override PATH, enabling arbitrary binary
    hijacking when the skill runs in the agent's process.
    """
    _write_skill(
        tmp_path,
        "envskill",
        "Do something",
        "skill-env:\n  PATH: /badpath\n  SAFE_KEY: safevalue\n",
    )
    skill_catalog = load_skills(_BUNDLED_SKILLS_DIR, user_skills_dir=tmp_path)
    assert "envskill" in skill_catalog
    skill = skill_catalog["envskill"]
    assert "PATH" not in skill.skill_env, "PATH must be filtered out of skill_env at load time"
    assert skill.skill_env.get("SAFE_KEY") == "safevalue", (
        "safe keys must be preserved in skill_env"
    )


# ---------------------------------------------------------------------------
# 10. /help renders bracketed usage/hints literally (Rich-markup escaping)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_help_renders_bracketed_usage_literally(tmp_path: Path) -> None:
    """Descriptions/arg-hints containing [brackets] survive rendering verbatim.

    /help cells are parsed as Rich markup, so an unescaped '[off|collapsed|full|next]'
    or skill hint '[<task-type-name>]' is interpreted as a style tag and silently
    dropped. Whitespace is squashed so the assertion is robust to column wrapping.

    Regression guard: if the escape() is removed, the bracketed tokens disappear.
    """
    import re

    from co_cli.commands.help import _cmd_help
    from co_cli.display.core import console

    ctx = _make_ctx(_make_deps(tmp_path))
    with console.capture() as cap:
        await _cmd_help(ctx, "")
    squashed = re.sub(r"\s+", "", cap.get())

    assert "[off|collapsed|full|next]" in squashed, "builtin /reasoning usage brackets were eaten"
    assert "[<task-type-name>]" in squashed, "bundled skill arg-hint brackets were eaten"
