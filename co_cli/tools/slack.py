"""Slack tools using RunContext pattern."""

from pydantic_ai import RunContext, ModelRetry

from co_cli.deps import CoDeps
from co_cli.tools._confirm import confirm_or_yolo


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

    if not confirm_or_yolo(ctx, f"Send Slack message to [bold]{channel}[/bold]?"):
        return "Slack post cancelled by user."

    try:
        response = client.chat_postMessage(channel=channel, text=text)
        return f"Message sent to {channel}. TS: {response['ts']}"
    except ModelRetry:
        raise
    except Exception as e:
        raise ModelRetry(f"Slack API error: {e}")
