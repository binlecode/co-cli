"""Agent tools — external integrations."""

from co_cli.tools.shell import run_shell_command
from co_cli.tools.obsidian import search_notes, list_notes, read_note
from co_cli.tools.google_drive import search_drive, read_drive_file
from co_cli.tools.google_gmail import draft_email
from co_cli.tools.google_calendar import list_calendar_events
from co_cli.tools.slack import post_slack_message

__all__ = [
    "run_shell_command",
    "search_notes",
    "list_notes",
    "read_note",
    "search_drive",
    "read_drive_file",
    "draft_email",
    "list_calendar_events",
    "post_slack_message",
]
