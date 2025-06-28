

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from datetime import datetime, timedelta
from typing import List, Dict
import logging
from pytz import timezone
import pytz
import os  # Add this import
from google.auth.transport.requests import Request  # Add this import
from tenacity import retry, stop_after_attempt, wait_exponential

logger = logging.getLogger(__name__)

class GoogleCalendarService:
    def __init__(self):
        self.scopes = ['https://www.googleapis.com/auth/calendar']
        self.timezone = timezone('Asia/Kolkata')
        
    def _get_credentials(self, user_id: str) -> Credentials:
        try:
            if not user_id:
                raise ValueError("User ID cannot be None")

            # Create tokens directory if it doesn't exist
            os.makedirs("tokens", exist_ok=True)
            
            creds = None
            token_file = f"tokens/{user_id}.json"
            
            if os.path.exists(token_file):
                creds = Credentials.from_authorized_user_file(token_file, self.scopes)

            if not creds or not creds.valid:
                if creds and creds.expired and creds.refresh_token:
                    creds.refresh(Request())
                else:
                    return None
                
                # Save the refreshed credentials
                with open(token_file, 'w') as token:
                    token.write(creds.to_json())

            return creds
        except Exception as e:
            logger.error(f"Authentication failed: {str(e)}")
            return None


    def _save_credentials(self, user_id: str, creds: Credentials):
        os.makedirs("tokens", exist_ok=True)
        with open(f"tokens/{user_id}.json", "w") as token:
            token.write(creds.to_json())

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=4, max=10)
    )
    def get_available_slots(self, user_id: str, date: str, duration_minutes: int = 30) -> List[Dict]:
        try:
            creds = self._get_credentials(user_id)
            if not creds:
                return []

            service = build('calendar', 'v3', credentials=creds, static_discovery=False)
            
            start_datetime = datetime.strptime(date, "%Y-%m-%d").replace(
                hour=9, minute=0, second=0, microsecond=0
            )
            start_datetime = self.timezone.localize(start_datetime)
            end_datetime = start_datetime.replace(hour=17, minute=0)
            
            freebusy = service.freebusy().query(body={
                "timeMin": start_datetime.astimezone(pytz.UTC).isoformat(),
                "timeMax": end_datetime.astimezone(pytz.UTC).isoformat(),
                "items": [{"id": "primary"}],
                "timeZone": str(self.timezone)
            }).execute()

            busy_slots = freebusy['calendars']['primary']['busy']
            slot_duration = timedelta(minutes=duration_minutes)
            possible_slots = []
            current_time = start_datetime

            while current_time + slot_duration <= end_datetime:
                possible_slots.append({
                    "start": current_time.isoformat(),
                    "end": (current_time + slot_duration).isoformat()
                })
                current_time += timedelta(minutes=15)

            available_slots = []
            for slot in possible_slots:
                is_available = True
                slot_start = datetime.fromisoformat(slot['start']).astimezone(pytz.UTC)
                slot_end = datetime.fromisoformat(slot['end']).astimezone(pytz.UTC)
                
                for busy in busy_slots:
                    busy_start = datetime.fromisoformat(busy['start'].replace('Z', '+00:00'))
                    busy_end = datetime.fromisoformat(busy['end'].replace('Z', '+00:00'))
                    
                    if not (slot_end <= busy_start or slot_start >= busy_end):
                        is_available = False
                        break

                if is_available:
                    available_slots.append({
                        "start": slot['start'],
                        "end": slot['end'],
                        "display": datetime.fromisoformat(slot['start']).strftime("%I:%M %p")
                    })

            return available_slots

        except Exception as e:
            logger.error(f"Error retrieving available slots: {str(e)}")
            return []

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=4, max=10)
    )
    def book_appointment(self, user_id: str, start_time: str, end_time: str, summary: str = "Meeting") -> Dict:
        try:
            creds = self._get_credentials(user_id)
            if not creds:
                return {}

            service = build('calendar', 'v3', credentials=creds, static_discovery=False)
            
            start_dt = datetime.fromisoformat(start_time.replace('Z', '')).astimezone(pytz.UTC)
            end_dt = datetime.fromisoformat(end_time.replace('Z', '')).astimezone(pytz.UTC)
            
            event = {
                'summary': summary,
                'start': {
                    'dateTime': start_dt.isoformat(),
                    'timeZone': str(self.timezone)
                },
                'end': {
                    'dateTime': end_dt.isoformat(),
                    'timeZone': str(self.timezone)
                },
            }

            event_result = service.events().insert(
                calendarId='primary', 
                body=event
            ).execute()

            return {
                "id": event_result['id'],
                "htmlLink": event_result['htmlLink'],
                "start": event_result['start']['dateTime'],
                "end": event_result['end']['dateTime']
            }
        except Exception as e:
            logger.error(f"Error booking appointment: {str(e)}")
            return {}