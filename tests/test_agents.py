"""Functional tests for delegation tool wiring and deps isolation."""

from pathlib import Path

from tests._settings import make_settings

from co_cli.deps import CoDeps, CoRuntimeState, CoSessionState, fork_deps
from co_cli.tools.shell_backend import ShellBackend


def test_fork_deps_resets_session_state() -> None:
    """fork_deps() shares handles by reference, inherits session fields, resets isolated fields."""
    from co_cli.commands._skill_types import SkillConfig
    from co_cli.deps import ApprovalKindEnum, SessionApprovalRule

    skill = SkillConfig(name="my-skill", body="do it")
    base = CoDeps(
        shell=ShellBackend(),
        skill_commands={"my-skill": skill},
        config=make_settings(
            brave_search_api_key="test-key",
            memory=make_settings().memory.model_copy(update={"injection_max_chars": 5000}),
        ),
        session=CoSessionState(
            session_path=Path("/tmp/parent-session.jsonl"),
            google_creds_resolved=True,
            session_approval_rules=[SessionApprovalRule(ApprovalKindEnum.SHELL, "git")],
            drive_page_tokens={"folder": ["tok1"]},
            session_todos=[{"task": "do something"}],
        ),
        runtime=CoRuntimeState(),
    )

    isolated = fork_deps(base)

    # service handles shared by reference
    assert isolated.shell is base.shell
    assert isolated.skill_commands is base.skill_commands

    # Session: inherited fields carry over
    assert isolated.session.google_creds_resolved is True
    assert isolated.session.session_approval_rules == [
        SessionApprovalRule(ApprovalKindEnum.SHELL, "git")
    ]

    # CoSessionState no longer carries skill fields — they are on capabilities
    assert not hasattr(CoSessionState(), "skill_commands")

    # Approval rules are a copy, not the same list (delegation grants must not leak to parent)
    assert isolated.session.session_approval_rules is not base.session.session_approval_rules

    # Session: isolated fields reset to clean defaults
    assert isolated.session.drive_page_tokens == {}
    assert isolated.session.session_todos == []
    assert isolated.session.session_path == Path()

    # Runtime resets to clean defaults (agent_depth incremented)
    assert isolated.runtime.turn_usage is None
    assert isolated.runtime.agent_depth == base.runtime.agent_depth + 1

    # Config inherited from parent
    assert isolated.config.brave_search_api_key == "test-key"
    assert isolated.config.memory.injection_max_chars == 5000

    # Service handles shared (same objects)
    assert isolated.shell is base.shell
    assert isolated.model is base.model
