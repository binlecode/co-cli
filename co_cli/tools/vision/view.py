"""image_view — read a local image and let the agent model see its pixels.

When the agent model is vision-capable (Gemini, or an Ollama model whose /api/show
reports the vision capability), image_view attaches the real pixels via
``ToolReturn.content`` so the agent model reads them on its next turn — the agent
answers directly, no separate model request. pydantic-ai materializes ``content`` as a
separate ``UserPromptPart`` (not the ``ToolReturnPart``), so a history processor elides
the base64 from older turns on replay (context/history_processors.py).

Capability-gated: when the agent model cannot see (a text-only Ollama model),
image_view self-hides via ``_vision_available`` and tool_view returns a remediation
rather than unlocking a tool that never materializes. There is no separate vision model
and no describe-fallback — vision is the agent model's own capability or nothing.
"""

from pathlib import Path

from pydantic_ai import RunContext
from pydantic_ai.messages import ToolReturn

from co_cli.deps import CoDeps, VisibilityPolicyEnum
from co_cli.tools.agent_tool import agent_tool
from co_cli.tools.files.fs_guards import enforce_read_boundary
from co_cli.tools.tool_io import tool_error
from co_cli.tools.vision.intake import ImageRejection, read_image


def _vision_available(deps: CoDeps) -> bool:
    """Per-turn gate: image_view is visible only when the agent model can see pixels.

    deps.agent_vision_capable is resolved at bootstrap — True for Gemini and for an
    Ollama model whose /api/show reports vision, False otherwise (honest gate).
    """
    return deps.agent_vision_capable


@agent_tool(
    visibility=VisibilityPolicyEnum.DEFERRED,
    is_concurrent_safe=True,
    check_fn=_vision_available,
)
async def image_view(
    ctx: RunContext[CoDeps],
    path: str,
    prompt: str = "Describe this image.",
) -> ToolReturn:
    """Look at a local image — read a screenshot, photo, diagram, or chart.

    Reads a local image file and attaches its pixels so you can answer about it directly
    on your next step. Use this for any "what's in this image?" question about a file on
    disk.

    When NOT to use: for a PDF's text, use the documents skill instead — this tool is
    image-only (png, jpeg, webp, gif).

    Args:
        path: Path to a local image, relative to the workspace root or an absolute path
            under a configured file-search root.
        prompt: What to ask about the image. Default "Describe this image."
    """
    try:
        resolved, _root = enforce_read_boundary(Path(path), ctx.deps.file_search_roots)
    except ValueError as e:
        return tool_error(str(e), ctx=ctx)

    image = read_image(resolved)
    if isinstance(image, ImageRejection):
        return tool_error(image.message, ctx=ctx)

    # Attach real pixels via ToolReturn.content. tool_output() has no content param, so
    # construct a raw ToolReturn directly. return_value is a short note — the pixels ride
    # content as a separate UserPromptPart the agent model reads next turn.
    return ToolReturn(
        return_value=f"Image attached ({resolved.name}); answer using vision.",
        content=[prompt, image],
        metadata={"path": str(resolved), "media_type": image.media_type},
    )
