"""Behavioral tests for the scanned/image-only PDF (tier-2) vision path.

Three layers, all no-mock:
- Render mode (always-run): the real ``co-extract-pdf --render`` console command
  rasterizes the committed image-only fixture to PNGs and reports ``total_pages`` so a
  caller can detect truncation. Real pymupdf, real subprocess.
- Honest degradation (always-run): on a text-only host rendering still works but
  ``image_view`` is gated off, so the pdf skill's scanned branch must degrade
  rather than fake a read. Deterministic, no model.
- Vision E2E (skipped on text-only hosts): a vision-capable model reads a rendered
  page's known content, proving page-keyed grounding end to end. Real model.
"""

from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

import pytest
from pydantic_ai import RunContext
from pydantic_ai.messages import BinaryContent
from pydantic_ai.usage import RunUsage
from tests._ollama import ensure_ollama_warm
from tests._settings import SETTINGS_NO_MCP, TEST_LLM
from tests._timeouts import LLM_NON_REASONING_TIMEOUT_SECS

from co_cli.agent.core import build_native_toolset
from co_cli.check import probe_ollama_model
from co_cli.deps import CoDeps, CoSessionState
from co_cli.llm.call import llm_call
from co_cli.llm.factory import build_model
from co_cli.skills.pdf.scripts.extract_pdf import SCANNED_SENTINEL
from co_cli.tools.shell_backend import ShellBackend
from co_cli.tools.system.tool_view import tool_view
from co_cli.tools.vision.view import _vision_available, image_view

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SCANNED_PDF = _REPO_ROOT / "tests" / "skills" / "fixtures" / "scanned_invoice.pdf"

# Resolve the agent model's vision capability once (real probe), mirroring the vision
# test suite: Gemini is natively multimodal; Ollama reports vision via /api/show. A
# text-only host skips the positive vision path.
_AGENT_VISION_CAPABLE = (
    True if TEST_LLM.uses_gemini() else probe_ollama_model(TEST_LLM.host, TEST_LLM.model).vision
)


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    """Invoke the installed co-extract-pdf command with the given arguments."""
    return subprocess.run(["co-extract-pdf", *args], capture_output=True, text=True)


def _parse_render(stdout: str) -> tuple[dict[int, str], int | None]:
    """Parse the render contract: page->path lines plus a final total_pages=M line."""
    pages: dict[int, str] = {}
    total: int | None = None
    for line in stdout.splitlines():
        if line.startswith("total_pages="):
            total = int(line.split("=", 1)[1])
        elif "\t" in line:
            number, png_path = line.split("\t", 1)
            pages[int(number)] = png_path
    return pages, total


def _make_deps(tmp_path: Path, *, agent_vision_capable: bool) -> CoDeps:
    _, tool_catalog = build_native_toolset()
    return CoDeps(
        shell=ShellBackend(),
        config=SETTINGS_NO_MCP,
        model=build_model(SETTINGS_NO_MCP.llm),
        agent_vision_capable=agent_vision_capable,
        tool_catalog=tool_catalog,
        session=CoSessionState(),
        file_search_roots=[tmp_path],
        tool_results_dir=tmp_path / "tool-results",
    )


def _make_ctx(deps: CoDeps, *, tool_name: str) -> RunContext[CoDeps]:
    return RunContext(deps=deps, model=None, usage=RunUsage(), tool_name=tool_name)


def test_image_only_fixture_routes_to_scanned_sentinel() -> None:
    """The committed fixture has no text layer, so plain extraction emits the sentinel.

    This is the trigger for the whole scanned branch — without it the body never
    reaches Step 5.
    """
    result = _run(str(_SCANNED_PDF))
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == SCANNED_SENTINEL


def test_render_produces_one_png_per_page_with_total(tmp_path: Path) -> None:
    """--render rasterizes every page to a real PNG and reports the full page count."""
    result = _run("--render", "--outdir", str(tmp_path), str(_SCANNED_PDF))
    assert result.returncode == 0, result.stderr
    pages, total = _parse_render(result.stdout)
    assert total == 3
    assert sorted(pages) == [1, 2, 3]
    for png_path in pages.values():
        assert Path(png_path).is_file()


def test_render_max_pages_caps_and_reports_truncation(tmp_path: Path) -> None:
    """--max-pages renders fewer pages than total_pages, so a caller detects truncation."""
    result = _run("--render", "--outdir", str(tmp_path), "--max-pages", "2", str(_SCANNED_PDF))
    assert result.returncode == 0, result.stderr
    pages, total = _parse_render(result.stdout)
    assert total == 3
    assert len(pages) == 2


@pytest.mark.asyncio
async def test_text_only_host_renders_but_cannot_view(tmp_path: Path) -> None:
    """Honest degradation: rendering is capability-free, but a text-only model cannot view.

    The scanned branch's "if you do not have image_view" path is reachable precisely
    because the tool is gated off on a text-only host — proven here without a model.
    """
    result = _run("--render", "--outdir", str(tmp_path), str(_SCANNED_PDF))
    assert result.returncode == 0, result.stderr
    pages, _ = _parse_render(result.stdout)
    assert pages

    deps = _make_deps(tmp_path, agent_vision_capable=False)
    assert _vision_available(deps) is False
    ctx = _make_ctx(deps, tool_name="tool_view")
    view = await tool_view(ctx, name="image_view")
    assert "not available" in view.return_value


@pytest.mark.skipif(
    not _AGENT_VISION_CAPABLE, reason="agent model is not vision-capable; scanned vision path N/A"
)
@pytest.mark.asyncio
async def test_vision_reads_rendered_page_with_grounding(tmp_path: Path) -> None:
    """A vision-capable model reads a rendered page's known content, keyed to its page.

    Page 3 of the fixture carries the total "540.00 USD"; reading the PNG the render
    contract labelled page 3 returns that number — page-keyed grounding end to end.
    """
    result = _run("--render", "--outdir", str(tmp_path), str(_SCANNED_PDF))
    assert result.returncode == 0, result.stderr
    pages, total = _parse_render(result.stdout)
    assert total == 3
    page_three_png = pages[3]

    deps = _make_deps(tmp_path, agent_vision_capable=True)
    ctx = _make_ctx(deps, tool_name="image_view")

    # Warm the model AND the vision pipeline outside the timeout: the first image read
    # pays a one-time projector/mmproj load (~15s) that ensure_ollama_warm (text path)
    # does not cover. Cold-start is infrastructure prep, not behavior under test — the
    # asserted call below runs at warm latency (sub-second).
    await ensure_ollama_warm(TEST_LLM.model)
    warmup = await image_view(ctx, path=page_three_png, prompt="Describe this page.")
    await llm_call(deps, list(warmup.content))

    view = await image_view(
        ctx,
        path=page_three_png,
        prompt="What is the total due on this page? Include the number.",
    )
    pixels = [c for c in (view.content or []) if isinstance(c, BinaryContent)]
    assert pixels

    async with asyncio.timeout(LLM_NON_REASONING_TIMEOUT_SECS):
        answer = await llm_call(deps, list(view.content))
    assert "540" in answer
