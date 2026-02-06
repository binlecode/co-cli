"""Google Calendar tools using RunContext pattern."""

from datetime import datetime, timezone, timedelta

from pydantic_ai import RunContext, ModelRetry

from co_cli.deps import CoDeps


def _get_calendar_service(ctx: RunContext[CoDeps]):
    """Extract and validate Calendar service from context."""
    service = ctx.deps.google_calendar
    if not service:
        raise ModelRetry(
            "Google Calendar not configured. "
            "Set google_credentials_path in settings or run: gcloud auth application-default login"
        )
    return service


def _format_events(events: list[dict]) -> str:
    """Format calendar events as readable string."""
    output = ""
    for event in events:
        start = event["start"].get("dateTime", event["start"].get("date"))
        summary = event.get("summary", "(no title)")
        output += f"- {start}: {summary}\n"
    return output


def _handle_calendar_error(e: Exception):
    """Raise ModelRetry with actionable message for calendar errors."""
    msg = str(e)
    if "has not been enabled" in msg or "accessNotConfigured" in msg.lower():
        raise ModelRetry(
            "Google Calendar API is not enabled for your project. "
            "Run: gcloud services enable calendar-json.googleapis.com"
        )
    raise ModelRetry(f"Calendar API error: {e}")


def list_calendar_events(ctx: RunContext[CoDeps]) -> str:
    """List today's calendar events."""
    service = _get_calendar_service(ctx)

    try:
        today_start = (
            datetime.now(timezone.utc)
            .replace(hour=0, minute=0, second=0, microsecond=0)
            .isoformat()
        )
        events_result = (
            service.events()
            .list(
                calendarId="primary",
                timeMin=today_start,
                maxResults=10,
                singleEvents=True,
                orderBy="startTime",
            )
            .execute()
        )
        events = events_result.get("items", [])
        if not events:
            return "No upcoming events found."
        return "Calendar Events:\n" + _format_events(events)
    except ModelRetry:
        raise
    except Exception as e:
        _handle_calendar_error(e)


def search_calendar_events(
    ctx: RunContext[CoDeps],
    query: str,
    days_ahead: int = 30,
    max_results: int = 10,
) -> str:
    """Search calendar events by keyword.

    Args:
        query: Text to search for in event summaries, descriptions, and locations.
        days_ahead: How many days ahead to search (default 30).
        max_results: Maximum number of events to return (default 10).
    """
    service = _get_calendar_service(ctx)

    try:
        now = datetime.now(timezone.utc)
        time_min = now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
        time_max = (now + timedelta(days=days_ahead)).isoformat()

        events_result = (
            service.events()
            .list(
                calendarId="primary",
                q=query,
                timeMin=time_min,
                timeMax=time_max,
                maxResults=max_results,
                singleEvents=True,
                orderBy="startTime",
            )
            .execute()
        )
        events = events_result.get("items", [])
        if not events:
            return f"No events found matching '{query}' in the next {days_ahead} days."
        return f"Events matching '{query}':\n" + _format_events(events)
    except ModelRetry:
        raise
    except Exception as e:
        _handle_calendar_error(e)
