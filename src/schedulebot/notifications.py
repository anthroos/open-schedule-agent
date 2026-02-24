"""Owner notifications via configured channel."""

from __future__ import annotations

import logging

from zoneinfo import ZoneInfo

from .channels.base import ChannelAdapter
from .models import Booking, OutgoingMessage

logger = logging.getLogger(__name__)


class Notifier:
    """Sends booking notifications to the owner."""

    def __init__(self, channel: ChannelAdapter, owner_id: str):
        self.channel = channel
        self.owner_id = owner_id

    async def notify_new_booking(self, booking: Booking) -> None:
        """Notify the owner about a new booking."""
        time_str = str(booking.slot)
        if booking.guest_timezone:
            try:
                guest_tz = ZoneInfo(booking.guest_timezone)
                guest_start = booking.slot.start.astimezone(guest_tz)
                guest_end = booking.slot.end.astimezone(guest_tz)
                guest_hhmm = f"{guest_start.strftime('%H:%M')}-{guest_end.strftime('%H:%M')}"
                tz_short = booking.guest_timezone.split("/")[-1].replace("_", " ")
                time_str += f" ({guest_hhmm} {tz_short})"
            except (KeyError, ValueError):
                pass

        text = (
            f"New booking!\n"
            f"  Guest: {booking.guest_name}\n"
            f"  Time: {time_str}\n"
            f"  Channel: {booking.guest_channel}"
        )
        if booking.guest_email:
            text += f"\n  Email: {booking.guest_email}"
        if booking.topic:
            text += f"\n  Topic: {booking.topic}"
        if booking.guest_timezone:
            text += f"\n  Guest TZ: {booking.guest_timezone}"
        if booking.attendee_emails:
            text += f"\n  +Attendees: {', '.join(booking.attendee_emails)}"
        if booking.meet_link:
            text += f"\n  Meet: {booking.meet_link}"
        text += f"\n  ID: {booking.id}"

        try:
            await self.channel.send_message(self.owner_id, OutgoingMessage(text=text))
        except Exception as e:
            logger.error(f"Failed to send notification: {e}")
