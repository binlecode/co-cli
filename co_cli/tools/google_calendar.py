"""Google Calendar tools using RunContext pattern."""

from datetime import datetime, timezone

from pydantic_ai import RunContext, ModelRetry

from co_cli.deps import CoDeps


def list_calendar_events(ctx: RunContext[CoDeps]) -> str:
    """List today's calendar events."""
    service = ctx.deps.google_calendar
    if not service:
        raise ModelRetry(
            "Google Calendar not configured. "
            "Set google_credentials_path in settings or run: gcloud auth application-default login"
        )

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

        output = "Calendar Events:\n"
        for event in events:
            start = event["start"].get("dateTime", event["start"].get("date"))
            output += f"- {start}: {event['summary']}\n"
        return output
    except ModelRetry:
        raise
    except Exception as e:
        msg = str(e)
        if "has not been enabled" in msg or "accessNotConfigured" in msg.lower():
            raise ModelRetry(
                "Google Calendar API is not enabled for your project. "
                "Run: gcloud services enable calendar-json.googleapis.com"
            )
        raise ModelRetry(f"Calendar API error: {e}")
