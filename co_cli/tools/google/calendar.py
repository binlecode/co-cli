"""Google Calendar tools using RunContext pattern."""

from datetime import UTC, datetime, timedelta

from pydantic_ai import ModelRetry, RunContext
from pydantic_ai.messages import ToolReturn

from co_cli.deps import CoDeps
from co_cli.tools.google._auth import _get_google_service
from co_cli.tools.tool_io import handle_google_api_error, tool_output

_CALENDAR_NOT_CONFIGURED = (
    "Calendar: not configured. "
    "Set google_credentials_path in settings or run: "
    "gcloud auth application-default login"
)


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
            names = [a.get("displayName") or a.get("email", "unknown") for a in attendees]
            output += f"  Attendees: {', '.join(names)}\n"

        event_link = event.get("htmlLink")
        if event_link:
            output += f"  Link: {event_link}\n"

        meet_link = event.get("hangoutLink")
        if meet_link:
            output += f"  Meet: {meet_link}\n"

    return output


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
) -> ToolReturn:
    """List calendar events in a time window around today. Auto-paginates
    internally — all matching events up to max_results are returned in one call.

    Use this for schedule overviews (today, this week). For keyword search
    across events, use search_calendar_events instead.

    Returns a dict with:
    - display: pre-formatted event list with times, title, location, attendees,
      Calendar link, and Meet link — show directly to the user
    - count: number of events returned

    Caveats:
    - Reads primary calendar only (not shared or subscribed calendars)
    - Recurring events are expanded into individual occurrences

    Args:
        days_back: Days in the past to include (default 0 = today onward).
        days_ahead: Days ahead to include (default 1 = today only).
                    Use 7 for a week, 30 for a month.
        max_results: Max events to return (default 25, max 250).
    """
    service, err = _get_google_service(ctx, "calendar", "v3", _CALENDAR_NOT_CONFIGURED)
    if err:
        return err

    try:
        now = datetime.now(UTC)
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
            return tool_output("No events found in the requested time range.", ctx=ctx, count=0)
        display = f"Calendar Events ({len(events)}):\n" + _format_events(events)
        return tool_output(display, ctx=ctx, count=len(events))
    except ModelRetry:
        raise
    except Exception as e:
        return handle_google_api_error("Calendar", e)


def search_calendar_events(
    ctx: RunContext[CoDeps],
    query: str,
    days_back: int = 0,
    days_ahead: int = 30,
    max_results: int = 25,
) -> ToolReturn:
    """Search calendar events by keyword in titles, descriptions, and locations.
    Auto-paginates internally — all matching events up to max_results are
    returned in one call.

    Use this when looking for specific meetings or topics. For a general
    schedule overview, use list_calendar_events instead.

    Returns a dict with:
    - display: pre-formatted event list with times, title, location, attendees,
      Calendar link, and Meet link — show directly to the user
    - count: number of events returned

    Caveats:
    - Searches primary calendar only
    - To find events in the past, increase days_back (default is 0 = today onward)

    Args:
        query: Keywords to match (e.g. "standup", "1:1 with Alice", "sprint review").
        days_back: Days in the past to search (default 0 = today onward).
                   Use 365 for a full year of history.
        days_ahead: Days ahead to search (default 30).
        max_results: Max events to return (default 25, max 250).
    """
    service, err = _get_google_service(ctx, "calendar", "v3", _CALENDAR_NOT_CONFIGURED)
    if err:
        return err

    try:
        now = datetime.now(UTC)
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
            return tool_output(
                f"No events found matching '{query}' in the requested time range.",
                ctx=ctx,
                count=0,
            )
        display = f"Events matching '{query}' ({len(events)}):\n" + _format_events(events)
        return tool_output(display, ctx=ctx, count=len(events))
    except ModelRetry:
        raise
    except Exception as e:
        return handle_google_api_error("Calendar", e)
