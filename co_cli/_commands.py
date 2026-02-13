"""Slash command registry, handlers, and dispatch for the REPL."""

from __future__ import annotations

from collections.abc import Callable, Awaitable
from dataclasses import dataclass
from typing import Any

from pydantic_ai.messages import ModelRequest
from pydantic_ai.settings import ModelSettings

from co_cli.display import console, prompt_selection


# -- Types -----------------------------------------------------------------

@dataclass
class CommandContext:
    """Grab-bag passed to every slash-command handler.

    Mutable so handlers like /model can set model_settings for the chat loop.
    """

    message_history: list[Any]
    deps: Any  # CoDeps — typed as Any to avoid circular import
    agent: Any  # Agent[CoDeps, ...] — same reason
    tool_names: list[str]
    model_settings: ModelSettings | None = None


@dataclass(frozen=True)
class SlashCommand:
    """A registered slash command."""

    name: str
    description: str
    handler: Callable[[CommandContext, str], Awaitable[list[Any] | None]]


# -- Handlers --------------------------------------------------------------


async def _cmd_help(ctx: CommandContext, args: str) -> None:
    """List available slash commands."""
    from rich.table import Table

    table = Table(title="Slash Commands", border_style="accent", expand=False)
    table.add_column("Command", style="accent")
    table.add_column("Description")
    for cmd in COMMANDS.values():
        table.add_row(f"/{cmd.name}", cmd.description)
    console.print(table)
    return None


async def _cmd_clear(ctx: CommandContext, args: str) -> list[Any]:
    """Clear conversation history."""
    console.print("[info]Conversation history cleared.[/info]")
    return []


async def _cmd_status(ctx: CommandContext, args: str) -> None:
    """Show system health (same as `co status`)."""
    from co_cli.status import get_status, render_status_table

    info = get_status(tool_count=len(ctx.tool_names))
    console.print(render_status_table(info))
    return None


async def _cmd_tools(ctx: CommandContext, args: str) -> None:
    """List registered agent tools."""
    tools = sorted(ctx.tool_names)
    lines = [f"  [accent]{i + 1}.[/accent] {name}" for i, name in enumerate(tools)]
    console.print(f"[info]Registered tools ({len(tools)}):[/info]")
    console.print("\n".join(lines))
    return None


async def _cmd_history(ctx: CommandContext, args: str) -> None:
    """Show conversation turn count."""
    turns = sum(
        1 for msg in ctx.message_history
        if isinstance(msg, ModelRequest)
    )
    console.print(f"[info]Conversation: {turns} user turn(s), {len(ctx.message_history)} total message(s).[/info]")
    return None


async def _cmd_compact(ctx: CommandContext, args: str) -> list[Any] | None:
    """Summarize conversation via LLM to reduce context."""
    from pydantic_ai.messages import ModelResponse, TextPart as _TextPart, UserPromptPart

    from co_cli._history import summarize_messages

    if not ctx.message_history:
        console.print("[dim]Nothing to compact — history is empty.[/dim]")
        return None

    console.print("[dim]Compacting conversation...[/dim]")
    try:
        # Use the agent's model for /compact (user-initiated, quality matters)
        model = ctx.agent.model
        summary = await summarize_messages(ctx.message_history, model)

        # Build a minimal 2-message history: summary request + ack response
        new_history: list[Any] = [
            ModelRequest(parts=[
                UserPromptPart(content=f"[Compacted conversation summary]\n{summary}"),
            ]),
            ModelResponse(parts=[
                _TextPart(content="Understood. I have the conversation context."),
            ]),
        ]
        old_len = len(ctx.message_history)
        console.print(
            f"[info]Compacted: {old_len} messages → {len(new_history)} messages.[/info]"
        )
        return new_history
    except Exception as e:
        console.print(f"[bold red]Compact failed:[/bold red] {e}")
        return None


async def _cmd_yolo(ctx: CommandContext, args: str) -> None:
    """Toggle auto-approve mode."""
    ctx.deps.auto_confirm = not ctx.deps.auto_confirm
    if ctx.deps.auto_confirm:
        console.print("[yolo]YOLO mode enabled — auto-approving all tool calls.[/yolo]")
    else:
        console.print("[info]YOLO mode disabled — tool calls require approval.[/info]")
    return None


def _switch_ollama_model(agent: Any, model_name: str, ollama_host: str) -> ModelSettings:
    """Build a new OpenAIChatModel, system prompt, and ModelSettings for agent.

    Returns the new ModelSettings so the chat loop can use them.
    """
    from pydantic_ai.models.openai import OpenAIChatModel
    from pydantic_ai.providers.openai import OpenAIProvider
    from co_cli.prompts import assemble_prompt
    from co_cli.prompts.model_quirks import normalize_model_name, get_model_inference
    from co_cli.config import settings

    # Swap model
    provider = OpenAIProvider(base_url=f"{ollama_host}/v1", api_key="ollama")
    agent.model = OpenAIChatModel(model_name=model_name, provider=provider)

    # Rebuild system prompt with new model quirks
    normalized_model = normalize_model_name(model_name)
    new_system_prompt, _manifest = assemble_prompt(
        "ollama",
        model_name=normalized_model,
        personality=settings.personality,
    )
    agent.system_prompt = new_system_prompt

    # Build ModelSettings from inference profile
    inf = get_model_inference("ollama", normalized_model)
    num_ctx = inf.get("num_ctx", settings.ollama_num_ctx)
    extra: dict = {"num_ctx": num_ctx}
    extra.update(inf.get("extra_body", {}))

    return ModelSettings(
        temperature=inf.get("temperature", 0.7),
        top_p=inf.get("top_p", 1.0),
        max_tokens=inf.get("max_tokens", 16384),
        extra_body=extra,
    )


async def _cmd_forget(ctx: CommandContext, args: str) -> None:
    """Delete a memory by ID."""
    from pathlib import Path

    if not args.strip():
        console.print("[bold red]Usage:[/bold red] /forget <memory_id>")
        console.print("[dim]Example: /forget 5[/dim]")
        return None

    try:
        memory_id = int(args.strip())
    except ValueError:
        console.print(f"[bold red]Invalid memory ID:[/bold red] {args}")
        console.print("[dim]Memory ID must be a number.[/dim]")
        return None

    memory_dir = Path.cwd() / ".co-cli/knowledge/memories"
    if not memory_dir.exists():
        console.print("[dim]No memories directory found.[/dim]")
        return None

    # Find file with this ID
    matching_files = list(memory_dir.glob(f"{memory_id:03d}-*.md"))
    if not matching_files:
        console.print(f"[bold red]Memory {memory_id} not found[/bold red]")
        console.print("[dim]Use /list_memories to see available IDs.[/dim]")
        return None

    # Delete file
    matching_files[0].unlink()
    console.print(f"[success]✓ Deleted memory {memory_id}: {matching_files[0].name}[/success]")
    return None


async def _cmd_model(ctx: CommandContext, args: str) -> None:
    """Switch Ollama model or show current model."""
    from co_cli.config import settings

    if settings.llm_provider.lower() != "ollama":
        console.print(f"[info]Provider: {settings.llm_provider} — model: {settings.gemini_model}[/info]")
        console.print("[dim]Model switching is only supported for Ollama.[/dim]")
        return None

    current = getattr(ctx.agent.model, 'model_name', str(ctx.agent.model))

    # Explicit name given — switch directly
    if args.strip():
        try:
            ctx.model_settings = _switch_ollama_model(ctx.agent, args.strip(), settings.ollama_host)
            console.print(f"[success]Switched to model: [accent]{args.strip()}[/accent][/success]")
        except Exception as e:
            console.print(f"[bold red]Failed to switch model:[/bold red] {e}")
        return None

    # No args — interactive selection
    console.print(f"[info]Current model: [accent]{current}[/accent][/info]")
    try:
        import httpx
        resp = httpx.get(f"{settings.ollama_host}/api/tags", timeout=5)
        resp.raise_for_status()
        models = sorted(m["name"] for m in resp.json().get("models", []))
    except Exception as e:
        console.print(f"[dim]Could not list models: {e}[/dim]")
        return None

    if not models:
        console.print("[dim]No models available.[/dim]")
        return None

    selected = prompt_selection(models, title="Select model", current=current)
    if not selected:
        return None
    if selected == current:
        console.print(f"[dim]Already using {current}.[/dim]")
        return None

    try:
        ctx.model_settings = _switch_ollama_model(ctx.agent, selected, settings.ollama_host)
        console.print(f"[success]Switched to model: [accent]{selected}[/accent][/success]")
    except Exception as e:
        console.print(f"[bold red]Failed to switch model:[/bold red] {e}")
    return None


# -- Registry --------------------------------------------------------------

COMMANDS: dict[str, SlashCommand] = {
    "help": SlashCommand("help", "List available slash commands", _cmd_help),
    "clear": SlashCommand("clear", "Clear conversation history", _cmd_clear),
    "status": SlashCommand("status", "Show system health", _cmd_status),
    "tools": SlashCommand("tools", "List registered agent tools", _cmd_tools),
    "history": SlashCommand("history", "Show conversation turn count", _cmd_history),
    "compact": SlashCommand("compact", "Summarize conversation via LLM to reduce context", _cmd_compact),
    "yolo": SlashCommand("yolo", "Toggle auto-approve mode", _cmd_yolo),
    "model": SlashCommand("model", "Switch Ollama model or show current", _cmd_model),
    "forget": SlashCommand("forget", "Delete a memory by ID", _cmd_forget),
}


# -- Dispatch --------------------------------------------------------------


async def dispatch(raw_input: str, ctx: CommandContext) -> tuple[bool, list[Any] | None]:
    """Route slash-command input to the appropriate handler.

    Returns (handled, new_history):
      - handled=False  → input was not a slash command, caller should proceed normally
      - handled=True, new_history=None → command executed, history unchanged
      - handled=True, new_history=list → command executed, caller must rebind history
    """
    if not raw_input.startswith("/"):
        return False, None

    parts = raw_input[1:].split(maxsplit=1)
    name = parts[0].lower() if parts else ""
    args = parts[1] if len(parts) > 1 else ""

    cmd = COMMANDS.get(name)
    if cmd is None:
        console.print(f"[bold red]Unknown command:[/bold red] /{name}")
        console.print("[dim]Type /help to see available commands.[/dim]")
        return True, None

    result = await cmd.handler(ctx, args)
    return True, result
