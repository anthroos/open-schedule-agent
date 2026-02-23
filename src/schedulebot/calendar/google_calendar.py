"""Google Calendar provider implementation."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime

from googleapiclient.discovery import build

from ..config import CalendarConfig
from ..models import TimeSlot
from ..retry import retry_async
from .base import CalendarProvider
from .google_auth import get_google_credentials

logger = logging.getLogger(__name__)


class GoogleCalendarProvider(CalendarProvider):
    """Google Calendar API integration."""

    def __init__(self, config: CalendarConfig, timezone: str = "UTC"):
        self.config = config
        self.timezone = timezone
        self._service = None

    @property
    def service(self):
        if not self._service:
            creds = get_google_credentials(
                credentials_path=self.config.credentials_path,
                token_path=self.config.token_path,
            )
            self._service = build("calendar", "v3", credentials=creds)
        return self._service

    async def get_busy_times(self, start: datetime, end: datetime) -> list[TimeSlot]:
        """Query Google Calendar freebusy API."""
        body = {
            "timeMin": start.isoformat(),
            "timeMax": end.isoformat(),
            "timeZone": self.timezone,
            "items": [{"id": "primary"}],
        }

        result = await retry_async(
            self.service.freebusy().query(body=body).execute,
            label="google.freebusy",
        )
        busy_slots = []

        for period in result.get("calendars", {}).get("primary", {}).get("busy", []):
            busy_start = datetime.fromisoformat(period["start"].replace("Z", "+00:00"))
            busy_end = datetime.fromisoformat(period["end"].replace("Z", "+00:00"))
            busy_slots.append(TimeSlot(start=busy_start, end=busy_end))

        logger.info(f"Found {len(busy_slots)} busy periods")
        return busy_slots

    async def create_event(
        self,
        summary: str,
        start: datetime,
        end: datetime,
        description: str = "",
        attendee_email: str | None = None,
        attendee_emails: list[str] | None = None,
        create_meet_link: bool = False,
    ) -> dict:
        """Create a Google Calendar event with optional Meet link."""
        event_body: dict = {
            "summary": summary,
            "description": description,
            "start": {
                "dateTime": start.isoformat(),
                "timeZone": self.timezone,
            },
            "end": {
                "dateTime": end.isoformat(),
                "timeZone": self.timezone,
            },
        }

        all_emails: list[str] = []
        if attendee_email:
            all_emails.append(attendee_email)
        if attendee_emails:
            all_emails.extend(attendee_emails)
        if all_emails:
            event_body["attendees"] = [{"email": e} for e in dict.fromkeys(all_emails)]

        conference_version = 0
        if create_meet_link:
            event_body["conferenceData"] = {
                "createRequest": {
                    "requestId": str(uuid.uuid4()),
                    "conferenceSolutionKey": {"type": "hangoutsMeet"},
                }
            }
            conference_version = 1

        created = await retry_async(
            self.service.events()
            .insert(
                calendarId="primary",
                body=event_body,
                conferenceDataVersion=conference_version,
            )
            .execute,
            label="google.create_event",
        )

        result = {"event_id": created.get("id")}

        meet_link = None
        entry_points = created.get("conferenceData", {}).get("entryPoints", [])
        for ep in entry_points:
            if ep.get("entryPointType") == "video":
                meet_link = ep.get("uri")
                break
        if meet_link:
            result["meet_link"] = meet_link

        logger.info(f"Created event: {created.get('htmlLink')}")
        return result
