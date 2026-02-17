"""Owner notifications via configured channel."""

from __future__ import annotations

import logging

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
        text = (
            f"New booking!\n"
            f"  Guest: {booking.guest_name}\n"
            f"  Time: {booking.slot}\n"
            f"  Channel: {booking.guest_channel}\n"
            f"  ID: {booking.id}"
        )
        if booking.meet_link:
            text += f"\n  Meet: {booking.meet_link}"

        try:
            await self.channel.send_message(self.owner_id, OutgoingMessage(text=text))
        except Exception as e:
            logger.error(f"Failed to send notification: {e}")
