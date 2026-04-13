"""Cross-cutting tool lifecycle capability: path normalization, telemetry, audit."""

import logging
from dataclasses import dataclass
from typing import Any

from opentelemetry import trace as otel_trace
from pydantic_ai import RunContext
from pydantic_ai.capabilities import AbstractCapability, ValidatedToolArgs
from pydantic_ai.messages import ToolCallPart
from pydantic_ai.tools import ToolDefinition

from co_cli.context.tool_categories import PATH_NORMALIZATION_TOOLS
from co_cli.deps import CoDeps

logger = logging.getLogger(__name__)


@dataclass
class CoToolLifecycle(AbstractCapability[CoDeps]):
    """SDK capability for cross-cutting tool concerns.

    Hooks:
    - before_tool_execute: path normalization for file tools
    - after_tool_execute: span enrichment + audit logging
    """

    async def after_tool_execute(
        self,
        ctx: RunContext[CoDeps],
        *,
        call: ToolCallPart,
        tool_def: ToolDefinition,
        args: ValidatedToolArgs,
        result: Any,
    ) -> Any:
        span = otel_trace.get_current_span()
        info = ctx.deps.tool_index.get(call.tool_name)
        if span.is_recording() and info:
            span.set_attribute("co.tool.source", info.source.value)
            span.set_attribute("co.tool.requires_approval", info.approval)
            span.set_attribute("co.tool.result_size", len(str(result)))
        logger.debug("tool_executed tool_name=%s", call.tool_name)
        return result

    async def before_tool_execute(
        self,
        ctx: RunContext[CoDeps],
        *,
        call: ToolCallPart,
        tool_def: ToolDefinition,
        args: ValidatedToolArgs,
    ) -> ValidatedToolArgs:
        if call.tool_name in PATH_NORMALIZATION_TOOLS and "path" in args:
            workspace_root = ctx.deps.workspace_root
            args["path"] = str((workspace_root / args["path"]).resolve())
        return args
