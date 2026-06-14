"""Functional tests for image_view + the multimodal history-elision processor.

Real model per test policy (no mocks). The native path needs a vision-capable agent
model and skips cleanly on a text-only host. The negative cases (escape / oversize /
non-image), the capability gate, and the elision processor are deterministic and run
model-free.
"""

import asyncio
import urllib.parse
from pathlib import Path

import pytest
from pydantic_ai import RunContext
from pydantic_ai.messages import (
    BinaryContent,
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturn,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.usage import RunUsage
from tests._ollama import ensure_ollama_warm
from tests._settings import SETTINGS_NO_MCP, TEST_LLM
from tests._timeouts import LLM_NON_REASONING_TIMEOUT_SECS

from co_cli.agent.core import build_native_toolset
from co_cli.bootstrap.check import probe_ollama_model
from co_cli.context.history_processors import (
    _ELIDED_IMAGE_PLACEHOLDER,
    elide_old_multimodal_prompts,
)
from co_cli.deps import CoDeps, CoSessionState
from co_cli.llm.call import llm_call
from co_cli.llm.factory import build_model
from co_cli.tools.shell_backend import ShellBackend
from co_cli.tools.system.tool_view import tool_view
from co_cli.tools.vision.intake import (
    ImageRejection,
    detect_lone_image_path,
    read_image,
)
from co_cli.tools.vision.view import _vision_available, image_view

_REPO_ROOT = Path(__file__).resolve().parent.parent
_FIXTURE = _REPO_ROOT / "tests" / "fixtures" / "vision" / "red_square.png"

# Resolve the agent model's vision capability once (real probe). Gemini is natively
# multimodal; Ollama reports vision via /api/show. A text-only host skips the native path.
_AGENT_VISION_CAPABLE = (
    True if TEST_LLM.uses_gemini() else probe_ollama_model(TEST_LLM.host, TEST_LLM.model).vision
)


def _make_deps(tmp_path: Path, *, agent_vision_capable: bool) -> CoDeps:
    _, tool_catalog = build_native_toolset()
    return CoDeps(
        shell=ShellBackend(),
        config=SETTINGS_NO_MCP,
        model=build_model(SETTINGS_NO_MCP.llm),
        agent_vision_capable=agent_vision_capable,
        tool_catalog=tool_catalog,
        session=CoSessionState(),
        file_search_roots=[_REPO_ROOT],
        tool_results_dir=tmp_path / "tool-results",
    )


def _make_ctx(deps: CoDeps, *, tool_name: str | None = None) -> RunContext[CoDeps]:
    return RunContext(deps=deps, model=None, usage=RunUsage(), tool_name=tool_name)


def _is_error(result: ToolReturn) -> bool:
    return bool((result.metadata or {}).get("error"))


@pytest.mark.skipif(
    not _AGENT_VISION_CAPABLE, reason="agent model is not vision-capable; image_view N/A"
)
@pytest.mark.asyncio
async def test_image_view_lets_model_read_pixels(tmp_path: Path) -> None:
    """image_view attaches real pixels the vision-capable agent model can read.

    The tool returns the pixels via ToolReturn.content; feeding that content to a real
    model request (the next turn) yields a correct answer about the image's color.
    """
    deps = _make_deps(tmp_path, agent_vision_capable=True)
    ctx = _make_ctx(deps, tool_name="image_view")
    result = await image_view(
        ctx, path=str(_FIXTURE), prompt="What is the dominant color? Answer in one word."
    )
    pixels = [c for c in (result.content or []) if isinstance(c, BinaryContent)]
    assert pixels
    assert pixels[0].media_type == "image/png"

    await ensure_ollama_warm(TEST_LLM.model)
    async with asyncio.timeout(LLM_NON_REASONING_TIMEOUT_SECS):
        answer = await llm_call(deps, list(result.content))
    assert "red" in answer.lower()


@pytest.mark.asyncio
async def test_image_view_rejects_path_escape(tmp_path: Path) -> None:
    """A path escaping all read roots returns a terminal tool_error, never reads bytes."""
    deps = _make_deps(tmp_path, agent_vision_capable=True)
    ctx = _make_ctx(deps, tool_name="image_view")
    result = await image_view(ctx, path="../../../../etc/hosts.png", prompt="x")
    assert _is_error(result)


@pytest.mark.asyncio
async def test_image_view_rejects_oversize(tmp_path: Path) -> None:
    """An image over the size cap returns a terminal tool_error."""
    big = tmp_path / "big.png"
    with open(big, "wb") as fh:
        fh.seek(21 * 1024 * 1024)
        fh.write(b"\x00")
    deps = _make_deps(tmp_path, agent_vision_capable=True)
    deps.file_search_roots = [tmp_path]
    ctx = _make_ctx(deps, tool_name="image_view")
    result = await image_view(ctx, path="big.png", prompt="x")
    assert _is_error(result)
    assert "too large" in result.return_value


@pytest.mark.asyncio
async def test_image_view_rejects_non_image(tmp_path: Path) -> None:
    """A non-image file (e.g. a PDF or text) returns a terminal tool_error."""
    deps = _make_deps(tmp_path, agent_vision_capable=True)
    ctx = _make_ctx(deps, tool_name="image_view")
    result = await image_view(ctx, path="CLAUDE.md", prompt="x")
    assert _is_error(result)
    assert "Unsupported image type" in result.return_value


@pytest.mark.asyncio
async def test_image_view_unavailable_when_agent_text_only(tmp_path: Path) -> None:
    """Text-only agent model: tool hides and tool_view returns remediation, not a reveal.

    The honest capability gate — never unlock a tool that the per-turn filter would
    keep hidden.
    """
    deps = _make_deps(tmp_path, agent_vision_capable=False)
    assert _vision_available(deps) is False
    ctx = _make_ctx(deps, tool_name="tool_view")
    result = await tool_view(ctx, name="image_view")
    assert "not available" in result.return_value
    assert "image_view" not in deps.runtime.revealed_tools


@pytest.mark.skipif(
    not _AGENT_VISION_CAPABLE, reason="agent model is not vision-capable; image_view N/A"
)
@pytest.mark.asyncio
async def test_image_view_loadable_via_tool_view_when_available(tmp_path: Path) -> None:
    """When the agent model can see, tool_view loads image_view (DEFERRED) by name."""
    deps = _make_deps(tmp_path, agent_vision_capable=True)
    assert _vision_available(deps) is True
    ctx = _make_ctx(deps, tool_name="tool_view")
    result = await tool_view(ctx, name="image_view")
    assert "Loaded" in result.return_value
    assert "image_view" in deps.runtime.revealed_tools


def test_elide_old_multimodal_prompts_keeps_tail_drops_older() -> None:
    """Older turn's pixels are elided to a placeholder; the most-recent turn's are kept.

    Two image_view turns: each has a synthetic pixel-bearing UserPromptPart (what
    pydantic-ai materializes from ToolReturn.content). On replay the processor strips
    BinaryContent from the older turn and preserves it in the tail.
    """
    pixels = BinaryContent(data=b"\x89PNGfakepixels", media_type="image/png")

    def carries_pixels(part: UserPromptPart) -> bool:
        return isinstance(part.content, list) and any(
            isinstance(c, BinaryContent) for c in part.content
        )

    messages = [
        ModelRequest(parts=[UserPromptPart(content="look at image 1")]),
        ModelResponse(
            parts=[ToolCallPart(tool_name="image_view", args={"path": "a.png"}, tool_call_id="c1")]
        ),
        ModelRequest(
            parts=[
                ToolReturnPart(
                    tool_name="image_view", content="Image attached", tool_call_id="c1"
                ),
                UserPromptPart(content=["What color? One word.", pixels]),
            ]
        ),
        ModelResponse(parts=[TextPart(content="Red")]),
        ModelRequest(parts=[UserPromptPart(content="look at image 2")]),
        ModelResponse(
            parts=[ToolCallPart(tool_name="image_view", args={"path": "b.png"}, tool_call_id="c2")]
        ),
        ModelRequest(
            parts=[
                ToolReturnPart(
                    tool_name="image_view", content="Image attached", tool_call_id="c2"
                ),
                UserPromptPart(content=["What color? One word.", pixels]),
            ]
        ),
    ]

    out = elide_old_multimodal_prompts(None, messages)

    older = out[2].parts[1]
    assert not carries_pixels(older)
    assert _ELIDED_IMAGE_PLACEHOLDER in older.content
    assert "What color? One word." in older.content

    tail = out[6].parts[1]
    assert carries_pixels(tail)


def _seed_png(path: Path) -> Path:
    """Copy the real fixture PNG to ``path`` (creating parents); return the path."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_FIXTURE.read_bytes())
    return path


@pytest.mark.asyncio
async def test_image_view_and_core_produce_identical_pixels(tmp_path: Path) -> None:
    """TASK-1 parity: image_view's attached BinaryContent equals read_image's directly.

    Both read the same fixture; the tool path adds the boundary check but the pixels and
    media_type must be byte-identical to the shared core's output.
    """
    deps = _make_deps(tmp_path, agent_vision_capable=True)
    ctx = _make_ctx(deps, tool_name="image_view")
    result = await image_view(ctx, path=str(_FIXTURE), prompt="x")
    tool_pixels = next(c for c in (result.content or []) if isinstance(c, BinaryContent))

    core = read_image(_FIXTURE)
    assert isinstance(core, BinaryContent)
    assert core.data == tool_pixels.data
    assert core.media_type == tool_pixels.media_type == "image/png"


def test_read_image_rejects_missing_dir_and_unsupported(tmp_path: Path) -> None:
    """The shared core rejects missing / directory / unsupported with ImageRejection."""
    missing = read_image(tmp_path / "nope.png")
    assert isinstance(missing, ImageRejection)

    as_dir = read_image(tmp_path)
    assert isinstance(as_dir, ImageRejection)

    txt = tmp_path / "note.txt"
    txt.write_text("not an image")
    unsupported = read_image(txt)
    assert isinstance(unsupported, ImageRejection)
    assert "Unsupported image type" in unsupported.message


def test_detect_lone_image_path_resolves_drag_forms(tmp_path: Path) -> None:
    """TASK-2 (a,c,d,e,f,g): every lone drag-and-send path form resolves to one Path."""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    absolute = _seed_png(tmp_path / "shot.png")
    spaced = _seed_png(tmp_path / "My Screens" / "shot.png")
    relative = _seed_png(workspace / "rel.png")
    out_of_roots = _seed_png(tmp_path / "Desktop" / "out.png")

    # (a) lone absolute path
    assert detect_lone_image_path(str(absolute), workspace) == absolute.resolve()
    # (c) lone file:// URI with %20-encoded space
    uri = "file://" + urllib.parse.quote(str(spaced))
    assert detect_lone_image_path(uri, workspace) == spaced.resolve()
    # (d) backslash-escaped spaces
    escaped = str(spaced).replace(" ", "\\ ")
    assert detect_lone_image_path(escaped, workspace) == spaced.resolve()
    # (e) double-quoted path with spaces
    assert detect_lone_image_path(f'"{spaced}"', workspace) == spaced.resolve()
    # (f) lone relative path against workspace_dir
    assert detect_lone_image_path("rel.png", workspace) == relative.resolve()
    # (g) path outside any read root — user-gesture allowance still resolves it
    assert detect_lone_image_path(str(out_of_roots), workspace) == out_of_roots.resolve()


def test_detect_lone_image_path_expands_home(tmp_path: Path, monkeypatch) -> None:
    """TASK-2 (b): a lone ~-prefixed path resolves via expanduser."""
    monkeypatch.setenv("HOME", str(tmp_path))
    img = _seed_png(tmp_path / "shot.png")
    assert detect_lone_image_path("~/shot.png", tmp_path) == img.resolve()


def test_detect_lone_image_path_rejects_non_lone(tmp_path: Path) -> None:
    """TASK-2 (h,i,j,k): anything that is not solely an existing image path yields None."""
    workspace = tmp_path
    absolute = _seed_png(tmp_path / "shot.png")
    _seed_png(tmp_path / "logo.png")
    (tmp_path / "main.py").write_text("print('hi')")

    # (h) path followed by a question
    assert detect_lone_image_path(f"{absolute} what is this?", workspace) is None
    # (i) mid-sentence mention
    assert detect_lone_image_path("fix the diff that broke logo.png", workspace) is None
    # (j) lone non-image path
    assert detect_lone_image_path("main.py", workspace) is None
    # (k) image-suffixed path that does not exist
    assert detect_lone_image_path(str(tmp_path / "ghost.png"), workspace) is None
