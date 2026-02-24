"""Background reminder loop for upcoming bookings."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from .channels.base import ChannelAdapter
from .database import Database
from .models import OutgoingMessage

logger = logging.getLogger(__name__)


class ReminderLoop:
    """Sends reminders N minutes before meetings via the booking channel."""

    def __init__(
        self,
        db: Database,
        adapters: dict[str, ChannelAdapter],
        reminder_minutes: int = 60,
        owner_adapter: ChannelAdapter | None = None,
        owner_id: str = "",
        check_interval: int = 60,
    ):
        self.db = db
        self.adapters = adapters
        self.reminder_minutes = reminder_minutes
        self.owner_adapter = owner_adapter
        self.owner_id = owner_id
        self.check_interval = check_interval
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        self._task = asyncio.create_task(self._loop())
        logger.info("Reminder loop started (remind %d min before)", self.reminder_minutes)

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            logger.info("Reminder loop stopped")

    async def _loop(self) -> None:
        while True:
            try:
                await self._check_and_send()
            except Exception as e:
                logger.error("Reminder loop error: %s", e, exc_info=True)
            await asyncio.sleep(self.check_interval)

    _BATCH_LIMIT = 50

    async def _check_and_send(self) -> None:
        now = datetime.now(timezone.utc)
        window_end = now + timedelta(minutes=self.reminder_minutes)

        bookings = self.db.get_upcoming_bookings_needing_reminder(
            after=now, before=window_end
        )

        for booking in bookings[: self._BATCH_LIMIT]:
            minutes_left = max(1, int((booking.slot.start - now).total_seconds() / 60))

            # Send guest reminder
            adapter = self.adapters.get(booking.guest_channel)
            if adapter and booking.guest_sender_id:
                try:
                    text = f"Reminder: Your meeting is in ~{minutes_left} minutes.\n  Time: {booking.slot}"
                    if booking.meet_link:
                        text += f"\n  Join: {booking.meet_link}"
                    await adapter.send_message(
                        booking.guest_sender_id, OutgoingMessage(text=text)
                    )
                    logger.info("Sent reminder for booking %s", booking.id)
                except Exception as e:
                    logger.error("Failed to send guest reminder for %s: %s", booking.id, e)

            # Send owner reminder
            if self.owner_adapter and self.owner_id:
                try:
                    text = f"Reminder: Meeting with {booking.guest_name} in ~{minutes_left} minutes.\n  Time: {booking.slot}"
                    if booking.meet_link:
                        text += f"\n  Join: {booking.meet_link}"
                    await self.owner_adapter.send_message(
                        self.owner_id, OutgoingMessage(text=text)
                    )
                except Exception as e:
                    logger.error("Failed to send owner reminder for %s: %s", booking.id, e)

            # Mark as sent (even if sending failed, to avoid retrying forever)
            self.db.mark_reminder_sent(booking.id)

            # Brief pause between sends to avoid flooding adapters
            await asyncio.sleep(0.5)
