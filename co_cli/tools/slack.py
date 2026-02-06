"""Slack tools using RunContext pattern."""

from rich.console import Console
from rich.prompt import Confirm
from pydantic_ai import RunContext, ModelRetry

from co_cli.deps import CoDeps

_console = Console()


def post_slack_message(ctx: RunContext[CoDeps], channel: str, text: str) -> str:
    """Send a message to a Slack channel.

    Args:
        channel: Slack channel name (e.g. '#general') or channel ID.
        text: Message text to send.
    """
    client = ctx.deps.slack_client
    if not client:
        raise ModelRetry(
            "Slack not configured. Set slack_bot_token in settings or SLACK_BOT_TOKEN env var."
        )

    if not ctx.deps.auto_confirm:
        if not Confirm.ask(
            f"Send Slack message to [bold]{channel}[/bold]?",
            default=False,
            console=_console,
        ):
            return "Slack post cancelled by user."

    try:
        response = client.chat_postMessage(channel=channel, text=text)
        return f"Message sent to {channel}. TS: {response['ts']}"
    except ModelRetry:
        raise
    except Exception as e:
        raise ModelRetry(f"Slack API error: {e}")
