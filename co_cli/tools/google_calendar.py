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
    """Format calendar events as readable string with key details."""
    output = ""
    for event in events:
        start = event["start"].get("dateTime", event["start"].get("date"))
        end = event["end"].get("dateTime", event["end"].get("date"))
        summary = event.get("summary", "(no title)")
        output += f"- {start} → {end}: {summary}\n"

        location = event.get("location")
        if location:
            output += f"  Location: {location}\n"

        description = event.get("description")
        if description:
            # Truncate long descriptions
            desc = description.strip().replace("\n", " ")
            if len(desc) > 200:
                desc = desc[:200] + "..."
            output += f"  Description: {desc}\n"

        attendees = event.get("attendees", [])
        if attendees:
            names = [
                a.get("displayName") or a.get("email", "unknown")
                for a in attendees
            ]
            output += f"  Attendees: {', '.join(names)}\n"

        meet_link = event.get("hangoutLink")
        if meet_link:
            output += f"  Meet: {meet_link}\n"

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


def _fetch_events(service, **kwargs) -> list[dict]:
    """Fetch events with auto-pagination up to max_results."""
    max_results = kwargs.pop("maxResults", 250)
    all_events = []
    page_token = None

    while len(all_events) < max_results:
        page_size = min(250, max_results - len(all_events))
        result = (
            service.events()
            .list(
                **kwargs,
                maxResults=page_size,
                pageToken=page_token,
            )
            .execute()
        )
        all_events.extend(result.get("items", []))
        page_token = result.get("nextPageToken")
        if not page_token:
            break

    return all_events[:max_results]


def list_calendar_events(
    ctx: RunContext[CoDeps],
    days_back: int = 0,
    days_ahead: int = 1,
    max_results: int = 25,
) -> str:
    """List calendar events in a time window around today.

    Args:
        days_back: How many days in the past to include (default 0 = today onward).
        days_ahead: How many days ahead to include (default 1 = today only).
        max_results: Maximum number of events to return (default 25).
    """
    service = _get_calendar_service(ctx)

    try:
        now = datetime.now(timezone.utc)
        time_min = (
            (now - timedelta(days=days_back))
            .replace(hour=0, minute=0, second=0, microsecond=0)
            .isoformat()
        )
        time_max = (
            (now + timedelta(days=days_ahead))
            .replace(hour=23, minute=59, second=59, microsecond=0)
            .isoformat()
        )

        events = _fetch_events(
            service,
            calendarId="primary",
            timeMin=time_min,
            timeMax=time_max,
            maxResults=max_results,
            singleEvents=True,
            orderBy="startTime",
        )
        if not events:
            return "No events found in the requested time range."
        return f"Calendar Events ({len(events)}):\n" + _format_events(events)
    except ModelRetry:
        raise
    except Exception as e:
        _handle_calendar_error(e)


def search_calendar_events(
    ctx: RunContext[CoDeps],
    query: str,
    days_back: int = 0,
    days_ahead: int = 30,
    max_results: int = 25,
) -> str:
    """Search calendar events by keyword.

    Args:
        query: Text to search for in event summaries, descriptions, and locations.
        days_back: How many days in the past to search (default 0 = today onward).
        days_ahead: How many days ahead to search (default 30).
        max_results: Maximum number of events to return (default 25).
    """
    service = _get_calendar_service(ctx)

    try:
        now = datetime.now(timezone.utc)
        time_min = (
            (now - timedelta(days=days_back))
            .replace(hour=0, minute=0, second=0, microsecond=0)
            .isoformat()
        )
        time_max = (now + timedelta(days=days_ahead)).isoformat()

        events = _fetch_events(
            service,
            calendarId="primary",
            q=query,
            timeMin=time_min,
            timeMax=time_max,
            maxResults=max_results,
            singleEvents=True,
            orderBy="startTime",
        )
        if not events:
            return f"No events found matching '{query}' in the requested time range."
        return f"Events matching '{query}' ({len(events)}):\n" + _format_events(events)
    except ModelRetry:
        raise
    except Exception as e:
        _handle_calendar_error(e)
