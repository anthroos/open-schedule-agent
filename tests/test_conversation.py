"""Tests for the conversation state machine and engine."""

import asyncio

import pytest

from schedulebot.config import (
    AvailabilityConfig,
    CalendarConfig,
    Config,
    LLMConfig,
    NotificationsConfig,
    OwnerConfig,
)
from schedulebot.core.engine import SchedulingEngine
from schedulebot.database import Database
from schedulebot.models import ConversationState, IncomingMessage, TimeSlot


class MockCalendar:
    async def get_busy_times(self, start, end):
        return []

    async def create_event(self, **kwargs):
        return {"event_id": "mock-evt-1", "meet_link": "https://meet.example.com/abc"}


class MockLLM:
    def __init__(self, responses=None):
        self.responses = responses or ["Hi! What's your name?"]
        self._call_count = 0
        self.last_system_prompt = None
        self.last_messages = None

    async def chat(self, system_prompt, messages):
        self.last_system_prompt = system_prompt
        self.last_messages = messages
        idx = min(self._call_count, len(self.responses) - 1)
        self._call_count += 1
        return self.responses[idx]


@pytest.fixture
def config():
    return Config(
        owner=OwnerConfig(name="Test Owner"),
        availability=AvailabilityConfig(
            timezone="UTC",
            working_hours={"monday": ["09:00-17:00"]},
            meeting_duration_minutes=30,
            buffer_minutes=15,
            min_notice_hours=0,
            max_days_ahead=7,
        ),
        calendar=CalendarConfig(),
        llm=LLMConfig(),
        notifications=NotificationsConfig(),
    )


@pytest.fixture
def db(tmp_path):
    d = Database(tmp_path / "test.db")
    d.connect()
    yield d
    d.close()


@pytest.mark.asyncio
async def test_first_message_creates_conversation(config, db):
    llm = MockLLM(["Hi! What's your name?"])
    calendar = MockCalendar()
    engine = SchedulingEngine(config, calendar, llm, db)

    msg = IncomingMessage(
        channel="test",
        sender_id="user-1",
        sender_name="Test User",
        text="Hello, I'd like to book a meeting",
    )
    response = await engine.handle_message(msg)
    assert response.text == "Hi! What's your name?"

    # Conversation should be saved
    conv = db.get_conversation("user-1")
    assert conv is not None
    assert len(conv.messages) == 2  # user + assistant


@pytest.mark.asyncio
async def test_cancel_clears_conversation(config, db):
    llm = MockLLM(["Bye!"])
    calendar = MockCalendar()
    engine = SchedulingEngine(config, calendar, llm, db)

    # First create a conversation
    msg1 = IncomingMessage(channel="test", sender_id="user-2", sender_name="User", text="Hi")
    await engine.handle_message(msg1)
    assert db.get_conversation("user-2") is not None

    # Cancel it
    msg2 = IncomingMessage(channel="test", sender_id="user-2", sender_name="User", text="/cancel")
    response = await engine.handle_message(msg2)
    assert "cancelled" in response.text.lower()
    assert db.get_conversation("user-2") is None


@pytest.mark.asyncio
async def test_booking_flow(config, db):
    llm = MockLLM([
        "Nice to meet you! Here are available slots:\n1. Monday 09:00-09:30\nWhich one?",
        "Monday 09:00 it is! Confirmed. [BOOK:1]",
    ])
    calendar = MockCalendar()
    engine = SchedulingEngine(config, calendar, llm, db)

    # First message
    msg1 = IncomingMessage(channel="test", sender_id="user-3", sender_name="Alice", text="Hi, I'm Alice")
    await engine.handle_message(msg1)

    # Second message - select slot
    msg2 = IncomingMessage(channel="test", sender_id="user-3", sender_name="Alice", text="Slot 1 please")
    response = await engine.handle_message(msg2)

    # Should have created a booking
    assert "confirmed" in response.text.lower() or "Meeting confirmed" in response.text
