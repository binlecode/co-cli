"""Slack tools using RunContext pattern."""

from datetime import datetime, timezone
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
    "thread_not_found": "Thread not found. Check the thread_ts value.",
}


def _get_slack_client(ctx: RunContext[CoDeps]):
    """Extract and validate Slack client from context."""
    client = ctx.deps.slack_client
    if not client:
        raise ModelRetry(
            "Slack not configured. Set slack_bot_token in settings or SLACK_BOT_TOKEN env var."
        )
    return client


def _slack_error_handler(e: SlackApiError) -> str:
    """Map SlackApiError to an actionable hint message."""
    code = e.response.get("error", "") if e.response else ""
    return _SLACK_ERROR_HINTS.get(code, f"Slack API error: {e}")


def _format_ts(ts: str) -> str:
    """Convert Slack timestamp (e.g. '1234567890.123456') to 'YYYY-MM-DD HH:MM'."""
    try:
        dt = datetime.fromtimestamp(float(ts), tz=timezone.utc)
        return dt.strftime("%Y-%m-%d %H:%M")
    except (ValueError, OSError):
        return ts


def _format_message(msg: dict[str, Any]) -> str:
    """Format a single Slack message for display."""
    ts = _format_ts(msg.get("ts", ""))
    user = msg.get("user", "unknown")
    text = msg.get("text", "")
    if len(text) > 200:
        text = text[:200] + "..."

    line = f"{ts} <@{user}>: {text}"

    reply_count = msg.get("reply_count", 0)
    if reply_count:
        line += f" [thread: {reply_count} replies]"

    return line


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

    client = _get_slack_client(ctx)

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
        raise ModelRetry(_slack_error_handler(e))
    except Exception as e:
        raise ModelRetry(f"Slack API error: {e}")


def list_slack_channels(
    ctx: RunContext[CoDeps], limit: int = 20, types: str = "public_channel"
) -> dict[str, Any]:
    """List Slack channels the bot can see.

    Returns a dict with:
    - display: formatted channel list — show this directly to the user
    - count: number of channels returned
    - has_more: whether more channels exist

    Args:
        limit: Maximum number of channels to return (default 20).
        types: Comma-separated channel types (default 'public_channel').
               Options: public_channel, private_channel, mpim, im.
    """
    client = _get_slack_client(ctx)

    try:
        response = client.conversations_list(
            types=types, limit=limit, exclude_archived=True
        )
        channels = response.get("channels", [])
        has_more = response.get("response_metadata", {}).get("next_cursor", "") != ""

        if not channels:
            return {"display": "No channels found.", "count": 0, "has_more": False}

        lines = []
        for ch in channels:
            name = ch.get("name", "unknown")
            ch_id = ch.get("id", "")
            purpose = ch.get("purpose", {}).get("value", "")
            line = f"#{name} ({ch_id})"
            if purpose:
                line += f" - {purpose}"
            lines.append(line)

        display = f"Channels ({len(channels)}):\n" + "\n".join(lines)
        return {"display": display, "count": len(channels), "has_more": has_more}
    except ModelRetry:
        raise
    except SlackApiError as e:
        raise ModelRetry(_slack_error_handler(e))
    except Exception as e:
        raise ModelRetry(f"Slack API error: {e}")


def get_slack_channel_history(
    ctx: RunContext[CoDeps], channel: str, limit: int = 15
) -> dict[str, Any]:
    """Get recent messages from a Slack channel.

    Returns a dict with:
    - display: formatted message history — show this directly to the user
    - count: number of messages returned
    - has_more: whether more messages exist

    Args:
        channel: Channel ID (e.g. 'C01ABC123'). Use list_slack_channels to find IDs.
        limit: Maximum number of messages to return (default 15, max 50).
    """
    if not channel or not channel.strip():
        raise ModelRetry("Channel ID is required. Use list_slack_channels to find channel IDs.")

    client = _get_slack_client(ctx)
    capped_limit = min(limit, 50)

    try:
        response = client.conversations_history(channel=channel, limit=capped_limit)
        messages = response.get("messages", [])
        has_more = response.get("has_more", False)

        if not messages:
            return {"display": f"No messages in {channel}.", "count": 0, "has_more": False}

        lines = [_format_message(msg) for msg in messages]
        display = f"Messages in {channel} ({len(messages)}):\n" + "\n".join(lines)
        return {"display": display, "count": len(messages), "has_more": has_more}
    except ModelRetry:
        raise
    except SlackApiError as e:
        raise ModelRetry(_slack_error_handler(e))
    except Exception as e:
        raise ModelRetry(f"Slack API error: {e}")


def get_slack_thread_replies(
    ctx: RunContext[CoDeps], channel: str, thread_ts: str, limit: int = 20
) -> dict[str, Any]:
    """Get replies in a Slack thread.

    Returns a dict with:
    - display: formatted thread replies — show this directly to the user
    - count: number of replies returned
    - has_more: whether more replies exist

    Args:
        channel: Channel ID where the thread lives.
        thread_ts: Timestamp of the parent message (e.g. '1234567890.123456').
        limit: Maximum number of replies to return (default 20).
    """
    if not channel or not channel.strip():
        raise ModelRetry("Channel ID is required.")
    if not thread_ts or not thread_ts.strip():
        raise ModelRetry("thread_ts is required (timestamp of the parent message).")

    client = _get_slack_client(ctx)

    try:
        response = client.conversations_replies(
            channel=channel, ts=thread_ts, limit=limit
        )
        messages = response.get("messages", [])
        has_more = response.get("has_more", False)

        if not messages:
            return {"display": "No replies found.", "count": 0, "has_more": False}

        lines = [_format_message(msg) for msg in messages]
        display = f"Thread replies ({len(messages)}):\n" + "\n".join(lines)
        return {"display": display, "count": len(messages), "has_more": has_more}
    except ModelRetry:
        raise
    except SlackApiError as e:
        raise ModelRetry(_slack_error_handler(e))
    except Exception as e:
        raise ModelRetry(f"Slack API error: {e}")


def list_slack_users(ctx: RunContext[CoDeps], limit: int = 30) -> dict[str, Any]:
    """List active (non-bot, non-deleted) users in the Slack workspace.

    Returns a dict with:
    - display: formatted user list — show this directly to the user
    - count: number of users returned
    - has_more: whether more users exist

    Args:
        limit: Maximum number of users to return (default 30).
    """
    client = _get_slack_client(ctx)

    try:
        response = client.users_list(limit=limit)
        members = response.get("members", [])
        has_more = response.get("response_metadata", {}).get("next_cursor", "") != ""

        # Filter out deleted and bot users
        active_users = [
            m for m in members
            if not m.get("deleted", False) and not m.get("is_bot", False)
        ]

        if not active_users:
            return {"display": "No active users found.", "count": 0, "has_more": has_more}

        lines = []
        for u in active_users:
            profile = u.get("profile", {})
            display_name = profile.get("display_name") or profile.get("real_name", "unknown")
            user_id = u.get("id", "")
            real_name = profile.get("real_name", "")
            title = profile.get("title", "")

            line = f"@{display_name} ({user_id})"
            parts = []
            if real_name and real_name != display_name:
                parts.append(real_name)
            if title:
                parts.append(title)
            if parts:
                line += f" - {', '.join(parts)}"
            lines.append(line)

        display = f"Users ({len(active_users)}):\n" + "\n".join(lines)
        return {"display": display, "count": len(active_users), "has_more": has_more}
    except ModelRetry:
        raise
    except SlackApiError as e:
        raise ModelRetry(_slack_error_handler(e))
    except Exception as e:
        raise ModelRetry(f"Slack API error: {e}")
