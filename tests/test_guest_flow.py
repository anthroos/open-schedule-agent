"""Tests for the new guest flow: collect_guest_info + confirm_booking tools."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from schedulebot.config import (
    AvailabilityConfig,
    BookingLinksConfig,
    CalendarConfig,
    Config,
    LLMConfig,
    NotificationsConfig,
    OwnerConfig,
)
from schedulebot.core.engine import SchedulingEngine
from schedulebot.database import Database
from schedulebot.models import AvailabilityRule, IncomingMessage


# ── Mock helpers (same as test_owner_tools) ─────────────


@dataclass
class MockToolCall:
    id: str
    name: str
    input: dict[str, Any]


@dataclass
class MockToolResponse:
    text: str
    tool_calls: list[MockToolCall] = field(default_factory=list)
    stop_reason: str = "end_turn"


class MockToolLLM:
    def __init__(self, turns: list[MockToolResponse]):
        self.turns = turns
        self._call_count = 0
        self.calls: list[dict] = []

    async def chat(self, system_prompt: str, messages: list[dict]) -> str:
        raise AssertionError("chat() should not be called")

    async def chat_with_tools(
        self, system_prompt: str, messages: list[dict], tools: list[dict]
    ) -> MockToolResponse:
        self.calls.append({
            "system_prompt": system_prompt,
            "messages": messages,
            "tools": tools,
        })
        idx = min(self._call_count, len(self.turns) - 1)
        self._call_count += 1
        return self.turns[idx]


class MockCalendar:
    async def get_busy_times(self, start, end):
        return []

    async def create_event(self, **kwargs):
        self.last_call = kwargs
        return {"event_id": "evt-1", "meet_link": "https://meet.google.com/test"}


# ── Fixtures ─────────────────────────────────────────────


@pytest.fixture
def config():
    return Config(
        owner=OwnerConfig(
            name="Ivan",
            email="ivan@test.com",
            owner_ids={"test": "owner-123"},
        ),
        availability=AvailabilityConfig(
            timezone="UTC",
            meeting_duration_minutes=30,
            buffer_minutes=15,
            min_notice_hours=0,
            max_days_ahead=14,
        ),
        calendar=CalendarConfig(),
        llm=LLMConfig(),
        notifications=NotificationsConfig(),
        booking_links=BookingLinksConfig(),
    )


@pytest.fixture
def db(tmp_path):
    d = Database(tmp_path / "guest.db")
    d.connect()
    yield d
    d.close()


@pytest.fixture
def db_with_slots(db):
    for hour in ["11:00", "14:00", "16:00", "19:00"]:
        end_h = int(hour.split(":")[0])
        db.add_availability_rule(
            AvailabilityRule(day_of_week="monday", start_time=hour, end_time=f"{end_h}:30")
        )
    return db


# ── Tests: collect_guest_info ────────────────────────────


@pytest.mark.asyncio
async def test_collect_guest_info_saves_to_conv(config, db_with_slots):
    """collect_guest_info stores name, email, topic in conversation."""
    llm = MockToolLLM(turns=[
        MockToolResponse(
            text="",
            tool_calls=[MockToolCall(
                id="tc_1",
                name="collect_guest_info",
                input={"name": "Alex", "email": "alex@company.com", "topic": "Data labeling"},
            )],
            stop_reason="tool_use",
        ),
        MockToolResponse(text="Thanks Alex! Which slot works for you?", stop_reason="end_turn"),
    ])
    engine = SchedulingEngine(config, MockCalendar(), llm, db_with_slots)

    await engine.handle_message(
        IncomingMessage(text="I'm Alex, alex@company.com, data labeling", sender_id="g-1", sender_name="Alex", channel="test")
    )

    conv = db_with_slots.get_conversation("g-1")
    assert conv is not None
    assert conv.guest_name == "Alex"
    assert conv.guest_email == "alex@company.com"
    assert conv.guest_topic == "Data labeling"


@pytest.mark.asyncio
async def test_collect_guest_info_rejects_invalid_email(config, db_with_slots):
    """collect_guest_info rejects invalid email format."""
    llm = MockToolLLM(turns=[
        MockToolResponse(
            text="",
            tool_calls=[MockToolCall(
                id="tc_1",
                name="collect_guest_info",
                input={"name": "Alex", "email": "not-an-email"},
            )],
            stop_reason="tool_use",
        ),
        MockToolResponse(text="Could you double-check your email?", stop_reason="end_turn"),
    ])
    engine = SchedulingEngine(config, MockCalendar(), llm, db_with_slots)

    await engine.handle_message(
        IncomingMessage(text="I'm Alex, email is not-an-email", sender_id="g-2", sender_name="Alex", channel="test")
    )

    conv = db_with_slots.get_conversation("g-2")
    assert conv.guest_email == ""  # Not saved


@pytest.mark.asyncio
async def test_collect_guest_info_without_topic(config, db_with_slots):
    """collect_guest_info works without topic (optional field)."""
    llm = MockToolLLM(turns=[
        MockToolResponse(
            text="",
            tool_calls=[MockToolCall(
                id="tc_1",
                name="collect_guest_info",
                input={"name": "Bob", "email": "bob@test.com"},
            )],
            stop_reason="tool_use",
        ),
        MockToolResponse(text="Got it, Bob!", stop_reason="end_turn"),
    ])
    engine = SchedulingEngine(config, MockCalendar(), llm, db_with_slots)

    await engine.handle_message(
        IncomingMessage(text="I'm Bob, bob@test.com", sender_id="g-3", sender_name="Bob", channel="test")
    )

    conv = db_with_slots.get_conversation("g-3")
    assert conv.guest_name == "Bob"
    assert conv.guest_email == "bob@test.com"
    assert conv.guest_topic == ""


# ── Tests: confirm_booking with guest info ───────────────


@pytest.mark.asyncio
async def test_full_booking_flow(config, db_with_slots):
    """collect_guest_info → confirm_booking → booking created with all fields."""
    calendar = MockCalendar()
    llm = MockToolLLM(turns=[
        # Turn 1: collect info
        MockToolResponse(
            text="",
            tool_calls=[MockToolCall(
                id="tc_1",
                name="collect_guest_info",
                input={"name": "Alex", "email": "alex@co.com", "topic": "Demo"},
            )],
            stop_reason="tool_use",
        ),
        # Turn 2: after collecting info, LLM asks about slot
        MockToolResponse(text="Which slot works for you?", stop_reason="end_turn"),
    ])
    engine = SchedulingEngine(config, calendar, llm, db_with_slots)

    # First message: provide info
    await engine.handle_message(
        IncomingMessage(text="Alex, alex@co.com, Demo", sender_id="g-10", sender_name="Alex", channel="test")
    )

    # Second message: pick a slot
    llm2 = MockToolLLM(turns=[
        MockToolResponse(
            text="",
            tool_calls=[MockToolCall(
                id="tc_2",
                name="confirm_booking",
                input={"slot_number": 1},
            )],
            stop_reason="tool_use",
        ),
        MockToolResponse(text="All set! Meeting booked.", stop_reason="end_turn"),
    ])
    engine.llm = llm2

    result = await engine.handle_message(
        IncomingMessage(text="Slot 1 please", sender_id="g-10", sender_name="Alex", channel="test")
    )

    assert result.metadata.get("booking_id") is not None
    bookings = db_with_slots.get_bookings()
    assert len(bookings) == 1
    assert bookings[0].guest_name == "Alex"
    assert bookings[0].guest_email == "alex@co.com"
    assert bookings[0].topic == "Demo"


@pytest.mark.asyncio
async def test_confirm_booking_requires_guest_info(config, db_with_slots):
    """confirm_booking fails if collect_guest_info wasn't called first."""
    llm = MockToolLLM(turns=[
        MockToolResponse(
            text="",
            tool_calls=[MockToolCall(
                id="tc_1",
                name="confirm_booking",
                input={"slot_number": 1},
            )],
            stop_reason="tool_use",
        ),
        MockToolResponse(text="I need your name and email first.", stop_reason="end_turn"),
    ])
    engine = SchedulingEngine(config, MockCalendar(), llm, db_with_slots)

    result = await engine.handle_message(
        IncomingMessage(text="Book slot 1", sender_id="g-20", sender_name="Guest", channel="test")
    )

    assert result.metadata.get("booking_id") is None


@pytest.mark.asyncio
async def test_booking_with_attendee_emails(config, db_with_slots):
    """confirm_booking with attendee_emails → saved in booking."""
    calendar = MockCalendar()

    # Pre-populate guest info
    from schedulebot.models import Conversation, ConversationState
    conv = Conversation(
        sender_id="g-30", channel="test",
        guest_name="Alex", guest_email="alex@co.com", guest_topic="Demo",
        state=ConversationState.COLLECTING_INFO,
    )
    db_with_slots.save_conversation(conv)

    llm = MockToolLLM(turns=[
        MockToolResponse(
            text="",
            tool_calls=[MockToolCall(
                id="tc_1",
                name="confirm_booking",
                input={"slot_number": 2, "attendee_emails": ["bob@co.com"]},
            )],
            stop_reason="tool_use",
        ),
        MockToolResponse(text="Booked! Invite sent to both.", stop_reason="end_turn"),
    ])
    engine = SchedulingEngine(config, calendar, llm, db_with_slots)

    result = await engine.handle_message(
        IncomingMessage(text="Slot 2, add bob@co.com", sender_id="g-30", sender_name="Alex", channel="test")
    )

    assert result.metadata.get("booking_id") is not None
    bookings = db_with_slots.get_bookings()
    assert len(bookings) == 1
    assert bookings[0].attendee_emails == ["bob@co.com"]


@pytest.mark.asyncio
async def test_booking_rejects_too_many_attendees(config, db_with_slots):
    """confirm_booking rejects more than 2 attendee emails."""
    from schedulebot.models import Conversation, ConversationState
    conv = Conversation(
        sender_id="g-31", channel="test",
        guest_name="Alex", guest_email="alex@co.com",
        state=ConversationState.COLLECTING_INFO,
    )
    db_with_slots.save_conversation(conv)

    llm = MockToolLLM(turns=[
        MockToolResponse(
            text="",
            tool_calls=[MockToolCall(
                id="tc_1",
                name="confirm_booking",
                input={"slot_number": 1, "attendee_emails": ["a@co.com", "b@co.com", "c@co.com"]},
            )],
            stop_reason="tool_use",
        ),
        MockToolResponse(text="Max 2 extra attendees. Try again.", stop_reason="end_turn"),
    ])
    engine = SchedulingEngine(config, MockCalendar(), llm, db_with_slots)

    result = await engine.handle_message(
        IncomingMessage(text="Add a, b, c to meeting", sender_id="g-31", sender_name="Alex", channel="test")
    )

    assert result.metadata.get("booking_id") is None


@pytest.mark.asyncio
async def test_booking_rejects_invalid_attendee_email(config, db_with_slots):
    """confirm_booking rejects invalid attendee email format."""
    from schedulebot.models import Conversation, ConversationState
    conv = Conversation(
        sender_id="g-32", channel="test",
        guest_name="Alex", guest_email="alex@co.com",
        state=ConversationState.COLLECTING_INFO,
    )
    db_with_slots.save_conversation(conv)

    llm = MockToolLLM(turns=[
        MockToolResponse(
            text="",
            tool_calls=[MockToolCall(
                id="tc_1",
                name="confirm_booking",
                input={"slot_number": 1, "attendee_emails": ["not-valid"]},
            )],
            stop_reason="tool_use",
        ),
        MockToolResponse(text="That email doesn't look right.", stop_reason="end_turn"),
    ])
    engine = SchedulingEngine(config, MockCalendar(), llm, db_with_slots)

    result = await engine.handle_message(
        IncomingMessage(text="Add not-valid", sender_id="g-32", sender_name="Alex", channel="test")
    )

    assert result.metadata.get("booking_id") is None


# ── Tests: prompt includes guest info ────────────────────


@pytest.mark.asyncio
async def test_prompt_includes_guest_tools(config, db_with_slots):
    """Both collect_guest_info and confirm_booking are in GUEST_TOOLS."""
    llm = MockToolLLM(turns=[
        MockToolResponse(text="Hi!", stop_reason="end_turn"),
    ])
    engine = SchedulingEngine(config, MockCalendar(), llm, db_with_slots)

    await engine.handle_message(
        IncomingMessage(text="Hello", sender_id="g-40", sender_name="Guest", channel="test")
    )

    tools = llm.calls[0]["tools"]
    names = {t["name"] for t in tools}
    assert names == {"collect_guest_info", "confirm_booking"}


@pytest.mark.asyncio
async def test_prompt_shows_guest_info_status(config, db_with_slots):
    """System prompt reflects guest info status after collect_guest_info."""
    from schedulebot.models import Conversation, ConversationState
    conv = Conversation(
        sender_id="g-41", channel="test",
        guest_name="Alex", guest_email="alex@co.com",
        state=ConversationState.COLLECTING_INFO,
    )
    db_with_slots.save_conversation(conv)

    llm = MockToolLLM(turns=[
        MockToolResponse(text="Which slot?", stop_reason="end_turn"),
    ])
    engine = SchedulingEngine(config, MockCalendar(), llm, db_with_slots)

    await engine.handle_message(
        IncomingMessage(text="When can we meet?", sender_id="g-41", sender_name="Alex", channel="test")
    )

    prompt = llm.calls[0]["system_prompt"]
    assert "Alex" in prompt
    assert "alex@co.com" in prompt
    assert "ready to book" in prompt.lower()
