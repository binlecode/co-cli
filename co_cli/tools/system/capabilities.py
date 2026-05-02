"""Canonical agent self-check surface.

Exposes `capabilities_check`, the always-visible tool the model calls when it
needs to reason about its own runtime capability surface — what tools are
available right now, what is gated behind approval or deferred discovery, which
integrations are configured but degraded, and which fallbacks are active. The
bundled `/doctor` skill wraps this tool with a triage format; it is not the
only consumer.
"""

from pydantic_ai import RunContext
from pydantic_ai.messages import ToolReturn

from co_cli.bootstrap.check import CheckResult, RuntimeCheckResult, check_runtime
from co_cli.deps import CoDeps, ToolSourceEnum, VisibilityPolicyEnum
from co_cli.tools.agent_tool import agent_tool
from co_cli.tools.tool_io import tool_output


def _format_tool_list(names: list[str], empty_label: str = "none") -> str:
    return ", ".join(names) if names else empty_label


def _resolve_reranker(deps: CoDeps) -> str:
    ce_url = deps.config.knowledge.cross_encoder_reranker_url
    return f"tei ({ce_url})" if ce_url else "none"


def _mcp_probe_line(name: str, probe: CheckResult) -> str:
    """Render a single MCP probe as evidence-based wording (never 'connected')."""
    extra_value = str(probe.extra.get("value", ""))
    if not probe.ok:
        detail = f"probe failed — {probe.detail}"
    elif probe.detail == "remote url":
        detail = f"url configured: {extra_value}" if extra_value else "url configured"
    elif "found" in probe.detail:
        detail = f"command found: {extra_value}" if extra_value else "command found"
    else:
        detail = probe.detail
    return f"  - {name}: {detail}"


def _build_tool_surface_lines(deps: CoDeps) -> list[str]:
    tool_index = deps.tool_index
    always_visible = sorted(
        name for name, tc in tool_index.items() if tc.visibility == VisibilityPolicyEnum.ALWAYS
    )
    deferred = sorted(
        name for name, tc in tool_index.items() if tc.visibility == VisibilityPolicyEnum.DEFERRED
    )
    approval_required = sorted(name for name, tc in tool_index.items() if tc.approval)
    return [
        "Capability summary:",
        f"  Available now: {_format_tool_list(always_visible)}",
        f"  Discoverable on demand: {_format_tool_list(deferred)}",
        f"  Approval-gated: {_format_tool_list(approval_required)}",
    ]


def _build_component_lines(result: RuntimeCheckResult) -> list[str]:
    unavailable = [
        cs
        for cs in result.component_status
        if cs["state"] in ("degraded", "unavailable") and not cs["component"].startswith("mcp:")
    ]
    lines = ["", "Unavailable or limited:"]
    if unavailable:
        lines.extend(f"  - {cs['component']} — {cs['detail']}" for cs in unavailable)
    else:
        lines.append("  · none")
    lines.append("")
    lines.append("Active fallbacks:")
    if result.fallbacks:
        lines.extend(f"  - {fb}" for fb in result.fallbacks)
    else:
        lines.append("  · none")
    return lines


def _build_runtime_lines(
    deps: CoDeps,
    result: RuntimeCheckResult,
    reranker: str,
) -> list[str]:
    caps = result.capabilities
    st = result.status
    reasoning_model = caps["reasoning_model"]
    reasoning_ready = caps["reasoning_ready"]
    lines: list[str] = [""]
    if reasoning_model:
        status_tag = "ready" if reasoning_ready else "not ready"
        lines.append(f"Reasoning model: {reasoning_model} ({status_tag})")
    else:
        lines.append("Reasoning model: not configured")
    if reranker == "none":
        lines.append("Search reranker: disabled (search quality may be degraded)")
    else:
        lines.append(f"Search reranker: {reranker}")
    short_id = deps.session.session_path.stem[-8:]
    if short_id:
        lines.append(f"Session: {short_id}...")
    source_counts = st.get("source_counts", {})
    if source_counts:
        source_parts = ", ".join(
            f"{source}: {count}" for source, count in sorted(source_counts.items())
        )
        lines.append(f"Tools by source: {source_parts}")
    return lines


def _build_mcp_lines(
    deps: CoDeps,
    result: RuntimeCheckResult,
    mcp_tool_count: int,
) -> list[str]:
    configured_count = len(deps.config.mcp_servers or {})
    lines: list[str] = ["", "MCP:"]
    if configured_count == 0:
        lines.append("  no servers configured")
        return lines
    lines.append(f"  configured servers: {configured_count} · discovered tools: {mcp_tool_count}")
    for name, probe in result.mcp_probes:
        lines.append(_mcp_probe_line(name, probe))
    degraded = sorted(
        (key.removeprefix("mcp."), detail)
        for key, detail in deps.degradations.items()
        if key.startswith("mcp.")
    )
    if degraded:
        lines.append("  degraded servers:")
        for name, reason in degraded:
            lines.append(f"    - {name}: tool discovery failed — {reason}")
    return lines


@agent_tool(visibility=VisibilityPolicyEnum.ALWAYS, is_read_only=True, is_concurrent_safe=True)
async def capabilities_check(ctx: RunContext[CoDeps]) -> ToolReturn:
    """Report the current runtime capability surface: available tools, approval-gated actions, degraded integrations, active fallbacks.

    Call this when the user asks what you can do, whether a specific capability
    is available, or why something is unavailable or degraded.
    Also use for runtime health checks and system check questions: is X up, why is Y degraded, can I do Z right now.
    """
    progress = ctx.deps.runtime.tool_progress_callback
    if progress is not None:
        progress("Doctor: starting runtime diagnostics...")
    result = check_runtime(ctx.deps, progress=progress)

    caps = result.capabilities
    st = result.status
    tool_index = ctx.deps.tool_index

    reranker = _resolve_reranker(ctx.deps)
    native_tool_count = sum(1 for tc in tool_index.values() if tc.source == ToolSourceEnum.NATIVE)
    mcp_tool_count = sum(1 for tc in tool_index.values() if tc.source == ToolSourceEnum.MCP)

    lines: list[str] = []
    lines.extend(_build_tool_surface_lines(ctx.deps))
    lines.extend(_build_component_lines(result))
    lines.extend(_build_runtime_lines(ctx.deps, result, reranker))
    lines.extend(_build_mcp_lines(ctx.deps, result, mcp_tool_count))
    display = "\n".join(lines)

    return tool_output(
        display,
        ctx=ctx,
        # Tool surface
        always_visible_tools=sorted(
            name for name, tc in tool_index.items() if tc.visibility == VisibilityPolicyEnum.ALWAYS
        ),
        deferred_tools=sorted(
            name
            for name, tc in tool_index.items()
            if tc.visibility == VisibilityPolicyEnum.DEFERRED
        ),
        approval_required_tools=sorted(name for name, tc in tool_index.items() if tc.approval),
        tool_count=st["tool_count"],
        native_tool_count=native_tool_count,
        mcp_tool_count=mcp_tool_count,
        source_counts=st.get("source_counts", {}),
        # Component surface
        component_status=result.component_status,
        degradations=dict(ctx.deps.degradations),
        fallbacks=result.fallbacks,
        # Integration summary (retained for UI / trace compatibility)
        knowledge_backend=caps["knowledge_backend"],
        reranker=reranker,
        google=caps["google"],
        obsidian=caps["obsidian"],
        brave=caps["brave"],
        mcp_count=caps["mcp_count"],
        mcp_configured_server_count=len(ctx.deps.config.mcp_servers or {}),
        mcp_server_health=[
            {"name": n, "ok": r.ok, "detail": r.detail} for n, r in result.mcp_probes
        ],
        reasoning_model=caps["reasoning_model"],
        reasoning_ready=caps["reasoning_ready"],
        checks=caps["checks"],
        active_skill=st["active_skill"],
        mcp_mode=st["mcp_mode"],
        knowledge_mode=st["knowledge_mode"],
    )
