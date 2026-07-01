"""Owned subagent driver (OQ-4 option b).

The load-bearing Phase-2 subagent gate: this real-Ollama test runs one spec through the
owned ``run_standalone_owned`` and asserts it produces a valid ``spec.output_type``
instance — AND that the co-owned ``final_result`` tool def the model sees preserves the
tuned contract (``final_result`` name + title-stripped JSON schema), since output-type
validity alone can't prove it (G1-1).
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from pydantic import BaseModel
from tests._ollama import ensure_ollama_warm
from tests._settings import SETTINGS_NO_MCP as _CONFIG_NO_MCP
from tests._settings import TEST_LLM
from tests._timeouts import LLM_TOOL_CONTEXT_TIMEOUT_SECS

from co_cli.agent.core import build_native_toolset
from co_cli.agent.loop import run_standalone_owned
from co_cli.agent.preflight import build_output_toolset
from co_cli.agent.spec import TaskAgentSpec
from co_cli.deps import CoDeps, CoSessionState
from co_cli.display.headless import HeadlessFrontend
from co_cli.llm.factory import build_model
from co_cli.tools.shell_backend import ShellBackend

_, _CATALOG = build_native_toolset()


class _ReviewNote(BaseModel):
    summary: str


def _spec() -> TaskAgentSpec:
    return TaskAgentSpec(
        name="owned_subagent_probe",
        instructions=lambda deps: (
            "You summarize a short fact into one sentence and return it via the "
            "final_result tool. Do not call any other tools."
        ),
        tool_names=(),
        output_type=_ReviewNote,
        default_budget=4,
    )


def _make_deps() -> CoDeps:
    return CoDeps(
        shell=ShellBackend(),
        model=build_model(_CONFIG_NO_MCP.llm),
        config=_CONFIG_NO_MCP,
        session=CoSessionState(),
        model_max_context_tokens=_CONFIG_NO_MCP.llm.max_context_tokens,
    )


_PROMPT = "The fact: the Eiffel Tower is in Paris. Summarize it in one sentence."


def test_owned_subagent_final_result_tool_is_the_tuned_contract() -> None:
    """co owns the final_result output tool the subagent model sees: it is named
    'final_result', its schema strips per-property titles (the contract the dream-reviewer
    model was tuned on), and its validator turns a final_result call's args — dict or
    fenced JSON string — into the output_type instance."""
    defs, validator = build_output_toolset(_ReviewNote)
    assert len(defs) == 1
    tool = defs[0]
    assert tool.name == "final_result"
    assert tool.kind == "output"
    props = tool.parameters_json_schema["properties"]
    assert "summary" in props
    # tuned contract: per-property titles are stripped (what the tuned model expects).
    assert all("title" not in prop for prop in props.values())
    # the validator round-trips a final_result call's args into the output_type instance,
    # both as a dict and as a markdown-fenced JSON string.
    assert validator.validate({"summary": "in paris"}) == _ReviewNote(summary="in paris")
    fenced = '```json\n{"summary": "in paris"}\n```'
    assert validator.validate(fenced) == _ReviewNote(summary="in paris")


@pytest.mark.skipif(
    not _CONFIG_NO_MCP.llm.uses_ollama(), reason="real-LLM subagent run needs Ollama"
)
@pytest.mark.asyncio
async def test_owned_subagent_produces_schema_valid_output() -> None:
    await ensure_ollama_warm(TEST_LLM.model, TEST_LLM.host)
    spec = _spec()

    # Owned driver — returns the validated structured output.
    owned_deps = _make_deps()
    async with asyncio.timeout(LLM_TOOL_CONTEXT_TIMEOUT_SECS):
        owned_result = await run_standalone_owned(spec, owned_deps, _PROMPT)
    assert isinstance(owned_result, _ReviewNote)
    assert owned_result.summary.strip()


def _write_spec() -> TaskAgentSpec:
    return TaskAgentSpec(
        name="owned_subagent_write_probe",
        instructions=lambda deps: (
            "You handle one write subtask, then call final_result with a one-sentence "
            "summary of what you did."
        ),
        tool_names=("file_write",),
        output_type=_ReviewNote,
        default_budget=4,
    )


def _write_deps(workspace: Path) -> CoDeps:
    return CoDeps(
        shell=ShellBackend(),
        model=build_model(_CONFIG_NO_MCP.llm),
        tool_catalog=_CATALOG,
        config=_CONFIG_NO_MCP,
        session=CoSessionState(),
        workspace_dir=workspace,
        model_max_context_tokens=_CONFIG_NO_MCP.llm.max_context_tokens,
    )


_WRITE_ASK = (
    "Use the file_write tool to create a file named note.txt in the current directory "
    "with the content `hello`. Use only file_write, then call final_result. Do not use "
    "any other tool."
)


@pytest.mark.skipif(
    not _CONFIG_NO_MCP.llm.uses_ollama(), reason="real-LLM subagent approval flow needs Ollama"
)
@pytest.mark.asyncio
async def test_owned_subagent_propagated_approval_executes_on_approve(tmp_path: Path) -> None:
    """A write-capable subagent's gated call surfaces on the supplied frontend (marked
    delegated-origin); a scripted approval lets the write happen on disk."""
    await ensure_ollama_warm(TEST_LLM.model, TEST_LLM.host)
    target = tmp_path / "note.txt"
    deps = _write_deps(tmp_path)
    frontend = HeadlessFrontend(approval_response="y")

    async with asyncio.timeout(LLM_TOOL_CONTEXT_TIMEOUT_SECS * 2):
        await run_standalone_owned(
            _write_spec(),
            deps,
            _WRITE_ASK,
            settings=deps.model.settings_noreason,
            propagate_approvals=True,
            frontend=frontend,
        )

    assert frontend.approval_calls, "the subagent's file_write was gated for approval"
    assert any("[delegated subtask]" in call for call in frontend.approval_calls), (
        "the prompt carries the delegated-origin marker"
    )
    assert target.exists(), "approved write executed on disk"


@pytest.mark.skipif(
    not _CONFIG_NO_MCP.llm.uses_ollama(), reason="real-LLM subagent approval flow needs Ollama"
)
@pytest.mark.asyncio
async def test_owned_subagent_propagated_approval_denied_blocks_write(tmp_path: Path) -> None:
    """A scripted denial blocks the subagent's write (no file on disk) while the driver
    still returns a structured result — the child adapts and summarizes."""
    await ensure_ollama_warm(TEST_LLM.model, TEST_LLM.host)
    target = tmp_path / "note.txt"
    deps = _write_deps(tmp_path)
    frontend = HeadlessFrontend(approval_response="n")

    async with asyncio.timeout(LLM_TOOL_CONTEXT_TIMEOUT_SECS * 2):
        result = await run_standalone_owned(
            _write_spec(),
            deps,
            _WRITE_ASK,
            settings=deps.model.settings_noreason,
            propagate_approvals=True,
            frontend=frontend,
        )

    assert frontend.approval_calls, "the subagent's file_write was gated for approval"
    assert not target.exists(), "denied write did not create the file"
    assert isinstance(result, _ReviewNote), "the driver still returns a structured result"
