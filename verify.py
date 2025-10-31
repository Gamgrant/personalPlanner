from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
import os

# Calendar API scope
SCOPES = ["https://www.googleapis.com/auth/calendar.readonly"]

def verify_credentials():
    """Verify that credentials.json works and can list calendar events."""
    creds = None
    credentials_path = "credentials.json"

    if not os.path.exists(credentials_path):
        print("Error: credentials.json not found.")
        return

    try:
        flow = InstalledAppFlow.from_client_secrets_file(credentials_path, SCOPES)
        creds = flow.run_local_server(port=0)  # Opens browser for auth
        print("Successfully authenticated with Google.")
    except Exception as e:
        print(f"Error verifying credentials: {e}")
        return

    # Try to call Calendar API
    try:
        service = build("calendar", "v3", credentials=creds)
        events_result = service.events().list(
            calendarId="primary",
            maxResults=3,
            singleEvents=True,
            orderBy="startTime"
        ).execute()
        events = events_result.get("items", [])

        if not events:
            print("Authentication succeeded. No upcoming events found.")
        else:
            print("Authentication succeeded. Upcoming events:")
            for event in events:
                start_time = event["start"].get("dateTime", event["start"].get("date"))
                print(f" - {event.get('summary', 'No Title')} → {start_time}")

    except Exception as e:
        print(f"Authentication succeeded, but Calendar API call failed: {e}")

if __name__ == "__main__":
    verify_credentials()