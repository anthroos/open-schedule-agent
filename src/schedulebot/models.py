"""Core data models for schedulebot."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


class ConversationState(str, Enum):
    """States of the scheduling conversation."""

    GREETING = "greeting"
    COLLECTING_INFO = "collecting_info"
    SLOT_SELECTION = "slot_selection"
    CONFIRMATION = "confirmation"
    BOOKED = "booked"
    CANCELLED = "cancelled"


class ConversationMode(str, Enum):
    """Whether this is an owner managing schedule or a guest booking."""

    OWNER = "owner"
    GUEST = "guest"


@dataclass
class AvailabilityRule:
    """A single availability rule stored in DB."""

    id: int | None = None
    day_of_week: str = ""  # "monday", "tuesday", etc. or "" for specific date
    specific_date: str = ""  # "2026-02-20" or "" for recurring
    start_time: str = ""  # "10:00"
    end_time: str = ""  # "18:00"
    is_blocked: bool = False  # True = explicitly unavailable
    created_at: datetime = field(default_factory=datetime.now)


@dataclass
class IncomingMessage:
    """Channel-agnostic incoming message."""

    channel: str  # "telegram", "slack", "discord", "web"
    sender_id: str  # channel-specific user ID
    sender_name: str
    text: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class OutgoingMessage:
    """Channel-agnostic outgoing message."""

    text: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class TimeSlot:
    """A single available time slot."""

    start: datetime
    end: datetime

    def __str__(self) -> str:
        day = self.start.strftime("%A, %B %d")
        start_time = self.start.strftime("%H:%M")
        end_time = self.end.strftime("%H:%M")
        return f"{day} {start_time}-{end_time}"


@dataclass
class Booking:
    """A confirmed booking."""

    id: str
    guest_name: str
    guest_channel: str
    guest_sender_id: str
    slot: TimeSlot
    calendar_event_id: str | None = None
    meet_link: str | None = None
    notes: str = ""
    created_at: datetime = field(default_factory=datetime.now)


@dataclass
class Conversation:
    """Tracks the state of a scheduling conversation."""

    sender_id: str
    channel: str
    state: ConversationState = ConversationState.GREETING
    guest_name: str = ""
    selected_slot: TimeSlot | None = None
    messages: list[dict[str, str]] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)

    def add_message(self, role: str, content: str) -> None:
        self.messages.append({"role": role, "content": content})
        self.updated_at = datetime.now()
