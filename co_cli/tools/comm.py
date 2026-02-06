import os
import base64
from datetime import datetime, timezone
from email.mime.text import MIMEText
import typer
import google.auth
from slack_sdk import WebClient
from google.oauth2 import service_account
from googleapiclient.discovery import build
from co_cli.config import settings

def post_slack_message(channel: str, text: str) -> str:
    """
    Send a message to a Slack channel.
    """
    token = settings.slack_bot_token
    if not token:
        return "Error: SLACK_BOT_TOKEN not found in settings or environment."
    
    if not settings.auto_confirm:
        if not typer.confirm(f"Send Slack message to {channel}?", default=False):
            return "Slack post cancelled by user."
    
    try:
        client = WebClient(token=token)
        response = client.chat_postMessage(channel=channel, text=text)
        return f"Message sent to {channel}. TS: {response['ts']}"
    except Exception as e:
        return f"Slack error: {e}"

def get_google_service(name: str, version: str):
    key_path = settings.gcp_key_path
    scopes = [
        'https://www.googleapis.com/auth/gmail.modify',
        'https://www.googleapis.com/auth/calendar'
    ]
    
    try:
        if key_path and os.path.exists(key_path):
            creds = service_account.Credentials.from_service_account_file(key_path, scopes=scopes)
        else:
            # Fallback to Application Default Credentials (ADC)
            creds, _ = google.auth.default(scopes=scopes)
        return build(name, version, credentials=creds)
    except Exception:
        return None

def draft_email(to: str, subject: str, body: str) -> str:
    """
    Draft an email in Gmail.
    """
    service = get_google_service('gmail', 'v1')
    if not service:
        return "Error: Gmail API not configured."

    if not settings.auto_confirm:
        if not typer.confirm(f"Draft email to {to}?", default=False):
            return "Email draft cancelled by user."

    try:
        message = MIMEText(body)
        message['to'] = to
        message['subject'] = subject
        raw = base64.urlsafe_b64encode(message.as_bytes()).decode('utf-8')
        draft_body = {'message': {'raw': raw}}
        draft = service.users().drafts().create(userId='me', body=draft_body).execute()
        return f"Draft created for {to} with subject '{subject}'. Draft ID: {draft['id']}"
    except Exception as e:
        return f"Gmail error: {e}"

def list_calendar_events() -> str:
    """
    List today's calendar events.
    """
    service = get_google_service('calendar', 'v3')
    if not service:
        return "Error: Calendar API not configured."
    
    try:
        today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
        events_result = service.events().list(calendarId='primary', timeMin=today_start,
                                            maxResults=10, singleEvents=True,
                                            orderBy='startTime').execute()
        events = events_result.get('items', [])
        if not events:
            return "No upcoming events found."
        
        output = "Calendar Events:\n"
        for event in events:
            start = event['start'].get('dateTime', event['start'].get('date'))
            output += f"- {start}: {event['summary']}\n"
        return output
    except Exception as e:
        return f"Calendar error: {e}"
