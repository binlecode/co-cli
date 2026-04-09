#!/usr/bin/env python3

"""
Evaluates and validates the true co bootstrap sequence.

Runs the real `create_deps`, `build_agent`, and `restore_session` flow, tracking the
status emitted by the CLI components. It captures key configurations,
resource resolving (knowledge store, MCP, skills), validates the agent's
personalization role, and produces a Markdown report of the entire tracking log.

Usage:
    uv run python evals/eval_bootstrap_flow.py
"""

import asyncio
import logging
import pprint
from contextlib import AsyncExitStack
from pathlib import Path

from evals._frontend import CapturingFrontend

from co_cli.agent import build_agent
from co_cli.bootstrap.core import create_deps, restore_session
from co_cli.config._core import settings


class TrackingFrontend(CapturingFrontend):
    def __init__(self):
        super().__init__(verbose=True)


async def main():
    print("=== Starting True Bootstrap Eval ===\n")

    # Enable application-level logging to capture internal module debug/info
    logging.getLogger("co_cli.bootstrap").setLevel(logging.DEBUG)
    log_stream = []

    class ListHandler(logging.Handler):
        def emit(self, record):
            msg = self.format(record)
            log_stream.append(msg)
            print(f"    DEBUG: {msg}")

    list_handler = ListHandler()
    list_handler.setFormatter(logging.Formatter("%(levelname)s [%(name)s] %(message)s"))
    logging.getLogger("co_cli").addHandler(list_handler)

    frontend = TrackingFrontend()
    report_lines = [
        "# True Bootstrap Evaluation Report",
        "",
        "## Configuration Status",
    ]

    try:
        async with AsyncExitStack() as stack:
            frontend.on_status("Starting true create_deps()")

            # Print intermediate config states
            print(f"    DEBUG: LLM Settings before resolve: {settings.llm}")

            # 1. Run create_deps
            deps = await create_deps(frontend, stack)  # type: ignore
            frontend.on_status("create_deps() succeeded.")

            # 2. Build the agent (to validate default personalization role and system prompt)
            frontend.on_status(f"Building agent with personality: '{deps.config.personality}'")
            agent = build_agent(config=deps.config, model=deps.model)
            frontend.on_status("build_agent() succeeded.")

            # 3. Run restore_session
            frontend.on_status("Restoring session.")
            restore_session(deps, frontend)  # type: ignore

            # 4. Gather diagnostics
            provider = deps.config.llm.provider
            model = deps.config.llm.model
            personality = deps.config.personality
            backend = deps.config.knowledge.search_backend
            tool_count = len(deps.tool_index)
            skill_count = len(deps.skill_commands)
            system_prompt_length = (
                len(str(agent._system_prompts)) if hasattr(agent, "_system_prompts") else "Unknown"
            )

            # Analyze MCP tools
            mcp_tools = {k: v for k, v in deps.tool_index.items() if v.source.value == "mcp"}
            mcp_integrations = {}
            for t in mcp_tools.values():
                mcp_integrations[t.integration] = mcp_integrations.get(t.integration, 0) + 1

            # Print to stdout
            print("\n--- Key Configs & Resource Resolving ---")
            print(f"LLM Provider       : {provider}")
            print(f"LLM Model          : {model}")
            print(f"Personality Role   : {personality}")
            print(f"Knowledge Backend  : {backend}")
            print(
                f"Tools Discovered   : {tool_count} (Native: {tool_count - len(mcp_tools)}, MCP: {len(mcp_tools)})"
            )
            if mcp_integrations:
                print(f"  MCP breakdown    : {mcp_integrations}")
            print(f"Skills Discovered  : {skill_count}")
            if deps.skill_commands:
                print(f"  Skills List      : {list(deps.skill_commands.keys())}")
            print(f"System Prompts Len : {system_prompt_length}")
            print(f"Session ID         : {deps.session.session_id}")
            print("----------------------------------------\n")

            # Add to report
            report_lines.append(f"- **LLM Provider**: {provider}")
            report_lines.append(f"- **LLM Model**: {model}")
            report_lines.append(f"- **Personality Role**: {personality}")
            report_lines.append(
                f"- **Agent Configured**: Yes (System Prompts count/len approx: {system_prompt_length})"
            )
            report_lines.append(f"- **Knowledge Backend**: {backend}")
            if deps.knowledge_store:
                report_lines.append("- **Knowledge Store Configured**: Yes")
            else:
                report_lines.append("- **Knowledge Store Configured**: No")

            report_lines.append(f"- **Tools Discovered**: {tool_count} (MCP: {len(mcp_tools)})")
            if mcp_integrations:
                report_lines.append(f"  - MCP Integrations: {mcp_integrations}")
            report_lines.append(f"- **Skills Discovered**: {skill_count}")
            report_lines.append(f"- **Session ID**: {deps.session.session_id}")
            report_lines.append(f"- **Paths Resolved**: {deps.memory_dir}, {deps.library_dir}")

            report_lines.append("")
            report_lines.append("## Dependencies Inspection")
            report_lines.append("```python")
            formatted_deps = pprint.pformat(deps, indent=2, width=100)
            report_lines.append(f"deps = {formatted_deps}")
            report_lines.append("```")

    except Exception as e:
        frontend.on_status(f"Bootstrap failed with Exception: {e}")
        report_lines.append("## Failure")
        report_lines.append(f"Exception encountered: {e}")

    report_lines.append("")
    report_lines.append("## Execution Log & Debug Details")
    for status in frontend.statuses:
        report_lines.append(f"- **STATUS:** {status}")
    for log_msg in log_stream:
        report_lines.append(f"- **DEBUG:** {log_msg}")

    report_content = "\n".join(report_lines) + "\n"
    report_path = Path("docs/REPORT-eval-bootstrap.md")
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(report_content, encoding="utf-8")

    print(f"Eval completed. Report written to {report_path}")


if __name__ == "__main__":
    asyncio.run(main())
