"""delegate tool — hand a read/search/gather subtask to an isolated child agent.

Orchestrator-only flagship capability (ALWAYS visibility). The child runs in a forked,
context-isolated session with a read-mostly tool surface; only its distilled summary
returns as the tool result, so the child's intermediate tool transcript never enters the
parent's history. The driver and child spec live in co_cli/agent/delegation.py.
"""

from pydantic_ai import RunContext

from co_cli.agent.delegation import delegate_to_child
from co_cli.deps import CoDeps, VisibilityPolicyEnum
from co_cli.tools.agent_tool import agent_tool


@agent_tool(
    visibility=VisibilityPolicyEnum.ALWAYS,
    is_approval_required=False,
    is_concurrent_safe=False,
)
async def delegate(ctx: RunContext[CoDeps], task: str) -> str:
    """Delegate a multi-step subtask to a focused sub-agent.

    Use this when a subtask needs several steps whose intermediate results you won't need
    to retain — the sub-agent does the work in its own isolated context and returns only a
    concise summary, keeping your working context clean. Do small one-shot actions inline
    yourself; delegate only the multi-step ones.

    The sub-agent can read and act: file read/search, web search/fetch, memory and session
    search/view, todo read, capabilities, image view, shell commands, and file write/patch.
    Sensitive actions are gated — the user is asked before they run. It cannot delegate
    further. State the subtask completely — the sub-agent has no access to this
    conversation, only the task string you pass.

    Args:
        task: A self-contained description of the subtask, including any context the
            sub-agent needs (it cannot see this conversation).
    """
    return await delegate_to_child(ctx.deps, task)
