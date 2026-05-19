"""@agent_tool decorator — attaches ToolInfo policy to native tool functions at definition site.

Import isolation (Behavioral Constraint 9): this module imports ONLY from co_cli.deps.
Never import CoDeps, agent internals, or tool implementations here.
"""

import inspect
from collections.abc import Callable
from functools import wraps
from typing import TypeVar

from co_cli.deps import ToolInfo, ToolSourceEnum, VisibilityPolicyEnum

F = TypeVar("F", bound=Callable)

AGENT_TOOL_ATTR = "__co_tool_info__"

# Self-populating registries — every @agent_tool(register=True) decorated function is appended
# at module import time. agent.toolset imports all tool modules as a side effect to ensure
# full population before build_native_toolset() runs.
TOOL_REGISTRY: list[Callable] = []
TOOL_REGISTRY_BY_NAME: dict[str, Callable] = {}


def agent_tool(
    *,
    visibility: VisibilityPolicyEnum,
    approval: bool = False,
    is_read_only: bool = False,
    is_concurrent_safe: bool = True,
    integration: str | None = None,
    requires_config: str | None = None,
    retries: int | None = None,
    spill_threshold_chars: int | float | None = None,
    check_fn: Callable | None = None,
    approval_subject_fn: Callable | None = None,
    register: bool = True,
) -> Callable[[F], F]:
    """Decorator that attaches ToolInfo policy metadata to a native tool function.

    Validates invariants at import time. The returned wrapper acquires
    deps.tool_dispatch_sem before each invocation (dispatch backstop:
    MAX_TOOL_DISPATCH_WORKERS concurrent calls per session). pydantic-ai
    introspects the wrapper via inspect.signature(follow_wrapped=True) —
    functools.wraps preserves __signature__, __doc__, and type hints.
    Pass register=False to attach metadata without adding to TOOL_REGISTRY.
    """
    # is_read_only implies is_concurrent_safe: read-only tools have no shared
    # mutable state to race on. Coerce rather than error so the author does
    # not need to repeat is_concurrent_safe=True alongside is_read_only=True.
    if is_read_only:
        is_concurrent_safe = True
    if is_read_only and approval:
        raise ValueError("@agent_tool: is_read_only=True is incompatible with approval=True")

    def decorator(fn: F) -> F:
        name = fn.__name__
        description = fn.__doc__.split("\n")[0].strip() if fn.__doc__ else name
        info = ToolInfo(
            name=name,
            description=description,
            source=ToolSourceEnum.NATIVE,
            visibility=visibility,
            approval=approval,
            is_read_only=is_read_only,
            is_concurrent_safe=is_concurrent_safe,
            integration=integration,
            requires_config=requires_config,
            retries=retries,
            spill_threshold_chars=spill_threshold_chars,
            check_fn=check_fn,
            approval_subject_fn=approval_subject_fn,
        )

        @wraps(fn)
        async def _dispatch_capped(ctx, *args, **kwargs):
            async with ctx.deps.tool_dispatch_sem:
                if inspect.iscoroutinefunction(fn):
                    return await fn(ctx, *args, **kwargs)
                return fn(ctx, *args, **kwargs)

        setattr(_dispatch_capped, AGENT_TOOL_ATTR, info)
        if register:
            TOOL_REGISTRY.append(_dispatch_capped)
            TOOL_REGISTRY_BY_NAME[name] = _dispatch_capped
        return _dispatch_capped  # type: ignore[return-value]

    return decorator
