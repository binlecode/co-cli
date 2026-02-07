"""Slack tools using RunContext pattern."""

from typing import Any

from slack_sdk.errors import SlackApiError
from pydantic_ai import RunContext, ModelRetry

from co_cli.deps import CoDeps

_SLACK_ERROR_HINTS: dict[str, str] = {
    "channel_not_found": "Channel not found. Check the name or use a channel ID.",
    "not_in_channel": "Bot is not in this channel. Invite it with /invite @bot.",
    "invalid_auth": "Slack token is invalid or expired. Refresh slack_bot_token.",
    "ratelimited": "Rate limited by Slack API. Wait a moment and retry.",
    "no_text": "Message text cannot be empty.",
    "msg_too_long": "Message exceeds Slack's length limit. Shorten the text.",
}


def post_slack_message(ctx: RunContext[CoDeps], channel: str, text: str) -> dict[str, Any]:
    """Send a message to a Slack channel.

    Returns a dict with:
    - display: confirmation message — show this directly to the user
    - channel: the channel posted to
    - ts: Slack message timestamp

    Args:
        channel: Slack channel name (e.g. '#general') or channel ID.
        text: Message text to send.
    """
    if not channel or not channel.strip():
        raise ModelRetry("Channel is required (e.g. '#general' or a channel ID).")
    if not text or not text.strip():
        raise ModelRetry("Message text cannot be empty.")

    client = ctx.deps.slack_client
    if not client:
        raise ModelRetry(
            "Slack not configured. Set slack_bot_token in settings or SLACK_BOT_TOKEN env var."
        )

    try:
        response = client.chat_postMessage(channel=channel, text=text)
        ts = response["ts"]
        return {
            "display": f"Message sent to {channel}. TS: {ts}",
            "channel": channel,
            "ts": ts,
        }
    except ModelRetry:
        raise
    except SlackApiError as e:
        code = e.response.get("error", "") if e.response else ""
        hint = _SLACK_ERROR_HINTS.get(code, f"Slack API error: {e}")
        raise ModelRetry(hint)
    except Exception as e:
        raise ModelRetry(f"Slack API error: {e}")
