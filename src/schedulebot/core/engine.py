"""Core scheduling engine — channel-agnostic."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime

from ..calendar.base import CalendarProvider
from ..config import Config
from ..core.availability import AvailabilityEngine
from ..database import Database
from ..llm.base import LLMProvider
from ..llm.prompts import build_system_prompt
from ..models import (
    Booking,
    Conversation,
    ConversationState,
    IncomingMessage,
    OutgoingMessage,
    TimeSlot,
)

logger = logging.getLogger(__name__)


class SchedulingEngine:
    """Main engine that processes messages and manages the scheduling flow."""

    def __init__(
        self,
        config: Config,
        calendar: CalendarProvider,
        llm: LLMProvider,
        db: Database,
    ):
        self.config = config
        self.calendar = calendar
        self.llm = llm
        self.db = db
        self.availability = AvailabilityEngine(config.availability, calendar)

    async def handle_message(self, msg: IncomingMessage) -> OutgoingMessage:
        """Process an incoming message and return a response.

        This is the single entry point that all channel adapters call.
        """
        conv = self.db.get_conversation(msg.sender_id)
        if not conv:
            conv = Conversation(
                sender_id=msg.sender_id,
                channel=msg.channel,
            )

        # Handle /cancel command
        if msg.text.strip().lower() in ("/cancel", "/start"):
            if msg.text.strip().lower() == "/cancel":
                self.db.delete_conversation(msg.sender_id)
                return OutgoingMessage(text="Scheduling cancelled. Send a message anytime to start over.")
            # /start — reset and begin fresh
            self.db.delete_conversation(msg.sender_id)
            conv = Conversation(sender_id=msg.sender_id, channel=msg.channel)

        # Add user message to history
        conv.add_message("user", msg.text)

        # Get available slots
        slots = await self.availability.get_available_slots()

        # Build system prompt with current slots
        system_prompt = build_system_prompt(
            owner_name=self.config.owner.name,
            slots=slots,
            conversation_state=conv.state,
            guest_name=conv.guest_name,
        )

        # Call LLM
        response_text = await self.llm.chat(system_prompt, conv.messages)

        # Parse LLM response for structured actions
        action = self._parse_action(response_text, slots, conv)

        if action == "book" and conv.selected_slot:
            booking = await self._create_booking(conv)
            if booking:
                confirmation = self._format_confirmation(booking)
                conv.state = ConversationState.BOOKED
                conv.add_message("assistant", confirmation)
                self.db.save_conversation(conv)
                return OutgoingMessage(
                    text=confirmation,
                    metadata={"booking_id": booking.id, "meet_link": booking.meet_link},
                )

        # Save conversation state
        conv.add_message("assistant", response_text)
        self.db.save_conversation(conv)

        return OutgoingMessage(text=response_text)

    def _parse_action(
        self, response: str, slots: list[TimeSlot], conv: Conversation
    ) -> str | None:
        """Check if the LLM response contains a booking action.

        Convention: LLM includes [BOOK:N] where N is 1-based slot index,
        or [BOOK:YYYY-MM-DDTHH:MM] for explicit time.
        """
        import re

        match = re.search(r"\[BOOK:(\d+)\]", response)
        if match:
            idx = int(match.group(1)) - 1
            if 0 <= idx < len(slots):
                conv.selected_slot = slots[idx]
                conv.state = ConversationState.CONFIRMATION
                return "book"

        match = re.search(r"\[BOOK:(\d{4}-\d{2}-\d{2}T\d{2}:\d{2})\]", response)
        if match:
            try:
                start = datetime.fromisoformat(match.group(1))
                from datetime import timedelta
                end = start + timedelta(minutes=self.config.availability.meeting_duration_minutes)
                conv.selected_slot = TimeSlot(start=start, end=end)
                conv.state = ConversationState.CONFIRMATION
                return "book"
            except ValueError:
                pass

        # Update state based on conversation flow
        if conv.state == ConversationState.GREETING:
            conv.state = ConversationState.COLLECTING_INFO

        return None

    async def _create_booking(self, conv: Conversation) -> Booking | None:
        """Create a calendar event and booking record."""
        if not conv.selected_slot:
            return None

        try:
            event = await self.calendar.create_event(
                summary=f"Meeting with {conv.guest_name or 'Guest'}",
                start=conv.selected_slot.start,
                end=conv.selected_slot.end,
                description=f"Scheduled via schedulebot. Channel: {conv.channel}",
                create_meet_link=self.config.calendar.create_meet_link,
            )

            booking = Booking(
                id=str(uuid.uuid4())[:8],
                guest_name=conv.guest_name or "Guest",
                guest_channel=conv.channel,
                guest_sender_id=conv.sender_id,
                slot=conv.selected_slot,
                calendar_event_id=event.get("event_id"),
                meet_link=event.get("meet_link"),
            )
            self.db.save_booking(booking)
            return booking

        except Exception as e:
            logger.error(f"Failed to create booking: {e}")
            return None

    def _format_confirmation(self, booking: Booking) -> str:
        """Format a booking confirmation message."""
        lines = [
            "Meeting confirmed!",
            f"  {booking.slot}",
        ]
        if booking.meet_link:
            lines.append(f"  Join: {booking.meet_link}")
        lines.append(f"  Booking ID: {booking.id}")
        return "\n".join(lines)
