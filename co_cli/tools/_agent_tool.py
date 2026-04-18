"""@agent_tool decorator — attaches ToolInfo policy to native tool functions at definition site.

Import isolation (Behavioral Constraint 9): this module imports ONLY from co_cli.deps.
Never import CoDeps, agent internals, or tool implementations here.
"""

from collections.abc import Callable
from typing import TypeVar

from co_cli.deps import ToolInfo, ToolSourceEnum, VisibilityPolicyEnum

F = TypeVar("F", bound=Callable)

AGENT_TOOL_ATTR = "__co_tool_info__"


def agent_tool(
    *,
    visibility: VisibilityPolicyEnum,
    approval: bool = False,
    is_read_only: bool = False,
    is_concurrent_safe: bool = False,
    integration: str | None = None,
    requires_config: str | None = None,
    retries: int | None = None,
    max_result_size: int = 50_000,
) -> Callable[[F], F]:
    """Decorator that attaches ToolInfo policy metadata to a native tool function.

    Validates invariants at import time. The decorated function is returned unchanged —
    pydantic-ai introspects __signature__, __doc__, and type hints directly.
    """
    if is_read_only and not is_concurrent_safe:
        raise ValueError("@agent_tool: is_read_only=True requires is_concurrent_safe=True")
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
            max_result_size=max_result_size,
        )
        setattr(fn, AGENT_TOOL_ATTR, info)
        return fn

    return decorator
