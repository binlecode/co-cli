import os
import google.auth
from google.oauth2 import service_account
from googleapiclient.discovery import build
from typing import List, Dict, Any
from co_cli.config import settings

def get_drive_service():
    key_path = settings.gcp_key_path
    scopes = ['https://www.googleapis.com/auth/drive.readonly']
    
    try:
        if key_path and os.path.exists(key_path):
            creds = service_account.Credentials.from_service_account_file(key_path, scopes=scopes)
        else:
            # Fallback to Application Default Credentials (ADC)
            creds, _ = google.auth.default(scopes=scopes)
        return build('drive', 'v3', credentials=creds)
    except Exception:
        return None

def search_drive(query: str) -> List[Dict[str, Any]]:
    """
    Search for files in Google Drive.
    
    Args:
        query: Search keywords or metadata query.
    """
    service = get_drive_service()
    if not service:
        return [{"error": f"Google Drive API not configured. Key missing at {settings.gcp_key_path}"}]
    
    try:
        # Step 1: API Level filtering
        q = f"name contains '{query}' or fullText contains '{query}'"
        results = service.files().list(
            q=q,
            pageSize=10,
            fields="nextPageToken, files(id, name, mimeType, modifiedTime)"
        ).execute()
        items = results.get('files', [])
        return items
    except Exception as e:
        return [{"error": f"Drive search failed: {e}"}]

def read_drive_file(file_id: str) -> str:
    """
    Fetch the content of a text-based file from Google Drive.
    """
    service = get_drive_service()
    if not service:
        return "Error: Drive API not configured."
    
    try:
        file = service.files().get(fileId=file_id, fields="name, mimeType").execute()
        if 'application/vnd.google-apps' in file['mimeType']:
            # Export Google Docs as text
            content = service.files().export(fileId=file_id, mimeType='text/plain').execute()
        else:
            content = service.files().get_media(fileId=file_id).execute()
        
        return content.decode('utf-8')
    except Exception as e:
        return f"Error reading drive file: {e}"
