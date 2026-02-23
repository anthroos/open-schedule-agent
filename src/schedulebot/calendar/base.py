"""Abstract base for calendar providers."""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime

from ..models import TimeSlot


class CalendarProvider(ABC):
    """Base class for calendar backends (Google, CalDAV, etc.)."""

    @abstractmethod
    async def get_busy_times(self, start: datetime, end: datetime) -> list[TimeSlot]:
        """Get busy time slots from the calendar."""
        ...

    @abstractmethod
    async def create_event(
        self,
        summary: str,
        start: datetime,
        end: datetime,
        description: str = "",
        attendee_email: str | None = None,
        create_meet_link: bool = False,
    ) -> dict:
        """Create a calendar event. Returns dict with 'event_id' and optionally 'meet_link'."""
        ...

    async def delete_event(self, event_id: str) -> None:
        """Delete a calendar event by ID. Optional — not all providers support this."""
        raise NotImplementedError("delete_event not supported by this calendar provider")
