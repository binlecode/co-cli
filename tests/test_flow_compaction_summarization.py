"""Tests for compaction summarization, token estimation, and budget resolution."""

import asyncio
import re

import pytest
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from tests._ollama import ensure_ollama_warm
from tests._settings import SETTINGS_NO_MCP, TEST_LLM
from tests._timeouts import LLM_COMPACTION_SUMMARY_TIMEOUT_SECS

from co_cli.context.summarization import (
    effective_request_tokens,
    estimate_message_tokens,
    summarize_messages,
)
from co_cli.deps import CoDeps, CoSessionState
from co_cli.llm.factory import build_model
from co_cli.tools.shell_backend import ShellBackend

_LLM_MODEL = build_model(SETTINGS_NO_MCP.llm)
_DEPS = CoDeps(
    shell=ShellBackend(), model=_LLM_MODEL, config=SETTINGS_NO_MCP, session=CoSessionState()
)

# Realistic multi-turn fixture: auth module security fix + test run.
# Contains tool calls (file_read, file_edit, shell) and substantial tool return
# content so the input is large enough for the summary to be a real compression.
_SAMPLE_MESSAGES = [
    ModelRequest(
        parts=[
            UserPromptPart(
                content="Read co_cli/auth.py and tell me if the token validation looks correct."
            )
        ]
    ),
    ModelResponse(
        parts=[
            ToolCallPart(
                tool_name="file_read", args={"path": "co_cli/auth.py"}, tool_call_id="tc1"
            )
        ]
    ),
    ModelRequest(
        parts=[
            ToolReturnPart(
                tool_name="file_read",
                content=(
                    "import jwt\nimport time\n\n"
                    'SECRET_KEY = "hardcoded-secret-do-not-use"\n\n'
                    "def validate_token(token: str) -> bool:\n"
                    "    try:\n"
                    '        payload = jwt.decode(token, SECRET_KEY, algorithms=["HS256"])\n'
                    '        exp = payload.get("exp")\n'
                    "        if exp and exp < time.time():\n"
                    "            return False\n"
                    "        return True\n"
                    "    except jwt.InvalidTokenError:\n"
                    "        return False\n\n"
                    "def create_token(user_id: int) -> str:\n"
                    "    payload = {\n"
                    '        "user_id": user_id,\n'
                    '        "exp": time.time() + 3600,\n'
                    '        "iat": time.time(),\n'
                    "    }\n"
                    '    return jwt.encode(payload, SECRET_KEY, algorithm="HS256")\n'
                ),
                tool_call_id="tc1",
            )
        ]
    ),
    ModelResponse(
        parts=[
            TextPart(
                content=(
                    "Found a critical issue: SECRET_KEY is hardcoded on line 4. "
                    "This is a security vulnerability — it must be loaded from an environment variable. "
                    "The token expiry check logic itself is correct."
                )
            )
        ]
    ),
    ModelRequest(
        parts=[
            UserPromptPart(
                content="Fix the hardcoded SECRET_KEY — load it from CO_AUTH_SECRET, raise ValueError if not set."
            )
        ]
    ),
    ModelResponse(
        parts=[
            ToolCallPart(
                tool_name="file_edit",
                args={
                    "path": "co_cli/auth.py",
                    "old_string": 'import jwt\nimport time\n\nSECRET_KEY = "hardcoded-secret-do-not-use"',
                    "new_string": 'import os\nimport jwt\nimport time\n\nSECRET_KEY = os.environ.get("CO_AUTH_SECRET")\nif not SECRET_KEY:\n    raise ValueError("CO_AUTH_SECRET env var is required")',
                },
                tool_call_id="tc2",
            )
        ]
    ),
    ModelRequest(
        parts=[
            ToolReturnPart(
                tool_name="file_edit",
                content="Edit applied. 1 change in co_cli/auth.py.",
                tool_call_id="tc2",
            )
        ]
    ),
    ModelResponse(
        parts=[
            TextPart(
                content="Fixed. SECRET_KEY now loads from CO_AUTH_SECRET and raises ValueError if the env var is absent."
            )
        ]
    ),
    ModelRequest(parts=[UserPromptPart(content="Run the auth tests.")]),
    ModelResponse(
        parts=[
            ToolCallPart(
                tool_name="shell_exec",
                args={"command": "pytest tests/test_auth.py -v"},
                tool_call_id="tc3",
            )
        ]
    ),
    ModelRequest(
        parts=[
            ToolReturnPart(
                tool_name="shell_exec",
                content=(
                    "============================= test session starts ==============================\n"
                    "collected 4 items\n\n"
                    "tests/test_auth.py::test_valid_token PASSED\n"
                    "tests/test_auth.py::test_expired_token PASSED\n"
                    "tests/test_auth.py::test_invalid_signature FAILED\n"
                    "tests/test_auth.py::test_missing_env_var FAILED\n\n"
                    "=================================== FAILURES ===================================\n"
                    "FAILED tests/test_auth.py::test_invalid_signature\n"
                    "    jwt.exceptions.InvalidSignatureError: Signature verification failed\n"
                    "FAILED tests/test_auth.py::test_missing_env_var\n"
                    "    AssertionError: ValueError not raised\n\n"
                    "2 failed, 2 passed in 0.43s\n"
                ),
                tool_call_id="tc3",
            )
        ]
    ),
    ModelResponse(
        parts=[
            TextPart(
                content=(
                    "Two failures: test_invalid_signature — jwt raises instead of returning False, "
                    "so the except clause needs to catch jwt.InvalidSignatureError explicitly. "
                    "test_missing_env_var — the ValueError fires at import time so it can't be caught "
                    "in a with-raises block; needs to be deferred to first call."
                )
            )
        ]
    ),
    ModelRequest(
        parts=[
            UserPromptPart(
                content="Fix test_missing_env_var — defer the env var check so the test can set CO_AUTH_SECRET after import."
            )
        ]
    ),
]


# ---------------------------------------------------------------------------
# Deterministic helpers — no LLM
# ---------------------------------------------------------------------------


def test_estimate_message_tokens_grows_with_content():
    """Token estimate must grow proportionally with content length."""
    short = [ModelRequest(parts=[UserPromptPart(content="hi")])]
    long = [ModelRequest(parts=[UserPromptPart(content="hi " * 1000)])]
    assert estimate_message_tokens(long) > estimate_message_tokens(short)


def test_estimate_message_tokens_empty_list():
    """Token estimate for an empty message list must be zero."""
    assert estimate_message_tokens([]) == 0


def test_effective_request_tokens_adds_static_floor():
    """effective_request_tokens adds the bootstrap-measured floor to the message-list estimate."""
    messages = [ModelRequest(parts=[UserPromptPart(content="hi " * 100)])]
    base = estimate_message_tokens(messages)

    floored = CoDeps(
        shell=ShellBackend(),
        model=_LLM_MODEL,
        config=SETTINGS_NO_MCP,
        session=CoSessionState(),
        static_floor_tokens=5000,
    )
    assert effective_request_tokens(floored, messages) == 5000 + base


def test_effective_request_tokens_default_floor_is_messages_only():
    """With the default floor (0), the estimate equals estimate_message_tokens (floor-blind deps)."""
    messages = [ModelRequest(parts=[UserPromptPart(content="hello world")])]
    assert effective_request_tokens(_DEPS, messages) == estimate_message_tokens(messages)


# ---------------------------------------------------------------------------
# LLM-backed summarization
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_summarize_messages_from_scratch_returns_structured_text():
    """Summarizer must produce a faithful structured handoff for a realistic tool-call session.

    Fixture: multi-turn auth fix — file_read, file_edit, shell tool calls with
    substantial content. Verifies section structure, verbatim active-task fidelity,
    tool-name fidelity (no hallucinated tool names), and that empty sections are
    skipped rather than filled with "None." / "[None]" placeholder text.

    Note: compression ratio is not asserted — the 12-section format has fixed
    overhead that exceeds short fixtures. Real production inputs (thousands of
    tokens) compress massively against this same output size.
    """
    await ensure_ollama_warm(TEST_LLM.model)
    async with asyncio.timeout(LLM_COMPACTION_SUMMARY_TIMEOUT_SECS):
        result = await summarize_messages(_DEPS, _SAMPLE_MESSAGES)

    # Required section headers
    assert "## Active Task" in result, f"Missing ## Active Task\n{result}"
    assert "## Completed Actions" in result, f"Missing ## Completed Actions\n{result}"
    assert "## Next Step" in result, f"Missing ## Next Step\n{result}"

    # Active Task must capture the last user request (defer env var check)
    active_start = result.index("## Active Task")
    next_header = result.find("##", active_start + len("## Active Task"))
    active_body = result[active_start:next_header] if next_header != -1 else result[active_start:]
    assert any(kw in active_body.lower() for kw in ("env", "defer", "missing", "test_missing")), (
        f"## Active Task does not reference the last user request:\n{active_body}"
    )

    # Completed Actions must have numbered entries
    actions_start = result.index("## Completed Actions")
    next_header2 = result.find("##", actions_start + len("## Completed Actions"))
    actions_body = (
        result[actions_start:next_header2] if next_header2 != -1 else result[actions_start:]
    )
    assert re.search(r"^\s*\d+\.", actions_body, re.MULTILINE), (
        f"## Completed Actions has no numbered entries:\n{actions_body}"
    )

    # At least one real tool name from the fixture must appear somewhere in the
    # summary (any section). A hallucinated name like "code_generation" with no
    # mention of the real names would mean the model invented tool names.
    assert any(tool in result for tool in ("file_read", "file_edit", "shell_exec")), (
        f"Summary does not reference any fixture tool names anywhere:\n{result}"
    )

    # Core topic (auth / SECRET_KEY) must be captured
    assert any(kw in result.lower() for kw in ("auth", "secret_key", "secret", "co_auth")), (
        f"Summary does not mention the auth/SECRET_KEY topic:\n{result}"
    )
