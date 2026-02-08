"""Google Calendar tools using RunContext pattern."""

from datetime import datetime, timezone, timedelta
from typing import Any

from googleapiclient.discovery import build
from pydantic_ai import RunContext, ModelRetry

from co_cli.deps import CoDeps
from co_cli.google_auth import get_cached_google_creds
from co_cli.tools._errors import (
    GOOGLE_NOT_CONFIGURED, terminal_error,
    classify_google_error, handle_tool_error,
)


def _get_calendar_service(ctx: RunContext[CoDeps]):
    """Extract and validate Calendar service from context.

    Returns (service, None) on success, or (None, error_dict) for terminal config errors.
    """
    creds = get_cached_google_creds(ctx.deps)
    if not creds:
        return None, terminal_error(GOOGLE_NOT_CONFIGURED.format(service="Calendar"))
    return build("calendar", "v3", credentials=creds), None


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

        event_link = event.get("htmlLink")
        if event_link:
            output += f"  Link: {event_link}\n"

        meet_link = event.get("hangoutLink")
        if meet_link:
            output += f"  Meet: {meet_link}\n"

    return output


def _handle_calendar_error(e: Exception):
    """Classify and dispatch calendar errors via shared error helpers."""
    kind, message = classify_google_error(e)
    return handle_tool_error(kind, message)


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
) -> dict[str, Any]:
    """List calendar events in a time window around today.

    Returns a dict with:
    - display: pre-formatted event list with clickable links — show this directly to the user
    - count: number of events returned

    Args:
        days_back: How many days in the past to include (default 0 = today onward).
        days_ahead: How many days ahead to include (default 1 = today only).
        max_results: Maximum number of events to return (default 25).
    """
    service, err = _get_calendar_service(ctx)
    if err:
        return err

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
            return {"display": "No events found in the requested time range.", "count": 0}
        display = f"Calendar Events ({len(events)}):\n" + _format_events(events)
        return {"display": display, "count": len(events)}
    except ModelRetry:
        raise
    except Exception as e:
        result = _handle_calendar_error(e)
        if result:
            return result


def search_calendar_events(
    ctx: RunContext[CoDeps],
    query: str,
    days_back: int = 0,
    days_ahead: int = 30,
    max_results: int = 25,
) -> dict[str, Any]:
    """Search calendar events by keyword.

    Returns a dict with:
    - display: pre-formatted event list with clickable links — show this directly to the user
    - count: number of events returned

    Args:
        query: Text to search for in event summaries, descriptions, and locations.
        days_back: How many days in the past to search (default 0 = today onward).
        days_ahead: How many days ahead to search (default 30).
        max_results: Maximum number of events to return (default 25).
    """
    service, err = _get_calendar_service(ctx)
    if err:
        return err

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
            return {"display": f"No events found matching '{query}' in the requested time range.", "count": 0}
        display = f"Events matching '{query}' ({len(events)}):\n" + _format_events(events)
        return {"display": display, "count": len(events)}
    except ModelRetry:
        raise
    except Exception as e:
        result = _handle_calendar_error(e)
        if result:
            return result
