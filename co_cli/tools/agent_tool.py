"""@agent_tool decorator — attaches ToolInfo policy to native tool functions at definition site.

Import isolation (Behavioral Constraint 9): this module imports ONLY from co_cli.deps.
Never import CoDeps, agent internals, or tool implementations here.
"""

from collections.abc import Callable
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
    is_concurrent_safe: bool = False,
    integration: str | None = None,
    requires_config: str | None = None,
    retries: int | None = None,
    spill_threshold_chars: int | float | None = None,
    check_fn: Callable | None = None,
    approval_subject_fn: Callable | None = None,
    register: bool = True,
) -> Callable[[F], F]:
    """Decorator that attaches ToolInfo policy metadata to a native tool function.

    Validates invariants at import time. The decorated function is returned unchanged —
    pydantic-ai introspects __signature__, __doc__, and type hints directly.
    Pass register=False to attach metadata without adding to TOOL_REGISTRY.
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
            spill_threshold_chars=spill_threshold_chars,
            check_fn=check_fn,
            approval_subject_fn=approval_subject_fn,
        )
        setattr(fn, AGENT_TOOL_ATTR, info)
        if register:
            TOOL_REGISTRY.append(fn)
            TOOL_REGISTRY_BY_NAME[name] = fn
        return fn

    return decorator
