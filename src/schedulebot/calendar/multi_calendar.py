"""Multi-calendar manager: aggregates busy times and routes bookings."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime

from ..models import TimeSlot
from .base import CalendarProvider

logger = logging.getLogger(__name__)


class MultiCalendarManager(CalendarProvider):
    """Wraps multiple CalendarProvider instances.

    - get_busy_times: returns the union of busy times from ALL providers
    - create_event: creates in the "book" provider, creates blocker events in "watch" providers
    - delete_event: deletes from the "book" provider only

    Note (v1 limitation): blocker events in watch calendars are not tracked.
    Cancelling a booking removes the event from the book calendar only.
    Watch calendar blockers remain as orphans and must be cleaned up manually.
    """

    def __init__(
        self,
        book_provider: CalendarProvider,
        watch_providers: list[CalendarProvider],
        book_name: str = "primary",
    ):
        self.book_provider = book_provider
        self.watch_providers = watch_providers
        self.book_name = book_name

    async def get_busy_times(self, start: datetime, end: datetime) -> list[TimeSlot]:
        """Union of busy times from all calendars.

        Book provider failure propagates (prevents double-bookings).
        Watch provider failures are logged and tolerated.
        """
        # Book provider must succeed -- its failure would hide existing bookings
        book_busy = await self.book_provider.get_busy_times(start, end)

        # Watch providers run concurrently, failures tolerated
        async def _safe_get_busy(provider: CalendarProvider) -> list[TimeSlot]:
            try:
                return await provider.get_busy_times(start, end)
            except Exception:
                logger.exception("Failed to get busy times from a watch calendar")
                return []

        watch_results = await asyncio.gather(
            *[_safe_get_busy(p) for p in self.watch_providers]
        )

        all_busy = list(book_busy)
        for slots in watch_results:
            all_busy.extend(slots)

        all_busy.sort(key=lambda s: s.start)
        return all_busy

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
        """Create event in the book calendar. Create blockers in watch calendars."""
        # Create the real event in the book calendar
        result = await self.book_provider.create_event(
            summary=summary,
            start=start,
            end=end,
            description=description,
            attendee_email=attendee_email,
            attendee_emails=attendee_emails,
            create_meet_link=create_meet_link,
        )

        result["calendar_name"] = self.book_name

        # Create blocker events in watch calendars concurrently (errors tolerated)
        blocker_summary = f"[Blocked] {summary}"

        async def _safe_create_blocker(provider: CalendarProvider) -> None:
            try:
                await provider.create_event(
                    summary=blocker_summary,
                    start=start,
                    end=end,
                    description=f"Auto-blocked by schedulebot booking in {self.book_name}",
                    create_meet_link=False,
                )
            except Exception:
                logger.exception("Failed to create blocker event in watch calendar")

        await asyncio.gather(*[_safe_create_blocker(p) for p in self.watch_providers])

        return result

    async def delete_event(self, event_id: str) -> None:
        """Delete event from the book calendar only.

        Note: blocker events in watch calendars are NOT deleted (v1 limitation).
        """
        await self.book_provider.delete_event(event_id)
