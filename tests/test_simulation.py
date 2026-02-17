"""End-to-end simulation tests for schedulebot.

Simulates full conversation flows:
- Guest booking a meeting (happy path)
- Guest with no available slots
- Owner setting up schedule via action tags
- Owner clearing rules
- Owner blocking time
- Mixed: owner sets rules, guest books a slot
- API endpoint CRUD for schedule rules
"""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

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
from schedulebot.models import AvailabilityRule, IncomingMessage, TimeSlot


# ── Mocks ──────────────────────────────────────────────


class MockCalendar:
    """Mock calendar: no busy times by default, tracks created events."""

    def __init__(self, busy: list[TimeSlot] | None = None):
        self.busy = busy or []
        self.created_events: list[dict] = []

    async def get_busy_times(self, start, end):
        return self.busy

    async def create_event(self, **kwargs):
        event = {
            "event_id": f"evt-{len(self.created_events) + 1}",
            "meet_link": "https://meet.google.com/test-abc",
            **kwargs,
        }
        self.created_events.append(event)
        return event


class MockCalendarFailing:
    """Mock calendar that always raises an error."""

    async def get_busy_times(self, start, end):
        raise ConnectionError("Google Calendar API unavailable")

    async def create_event(self, **kwargs):
        raise ConnectionError("Google Calendar API unavailable")


class MockLLM:
    """Mock LLM with scripted responses."""

    def __init__(self, responses: list[str]):
        self.responses = responses
        self._call_count = 0
        self.calls: list[dict] = []

    async def chat(self, system_prompt: str, messages: list[dict]) -> str:
        self.calls.append({"system_prompt": system_prompt, "messages": messages})
        idx = min(self._call_count, len(self.responses) - 1)
        self._call_count += 1
        return self.responses[idx]


# ── Fixtures ───────────────────────────────────────────


@pytest.fixture
def config():
    return Config(
        owner=OwnerConfig(
            name="Ivan Pasichnyk",
            email="ivan@welabeldata.com",
            owner_ids={"telegram": "owner-123", "test": "owner-123"},
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
        booking_links=BookingLinksConfig(
            links={"telegram": "https://t.me/test_bot"},
        ),
    )


@pytest.fixture
def db(tmp_path):
    d = Database(tmp_path / "sim.db")
    d.connect()
    yield d
    d.close()


@pytest.fixture
def db_with_rules(db):
    """DB pre-populated with Ivan's schedule."""
    # Monday/Wednesday/Thursday: 11:00, 14:00, 16:00, 19:00
    for day in ["monday", "wednesday", "thursday"]:
        for hour in ["11:00", "14:00", "16:00", "19:00"]:
            end_h = int(hour.split(":")[0])
            end = f"{end_h}:30"
            db.add_availability_rule(
                AvailabilityRule(day_of_week=day, start_time=hour, end_time=end)
            )
    # Block Tuesday/Friday afternoons
    db.add_availability_rule(
        AvailabilityRule(day_of_week="tuesday", start_time="14:30", end_time="23:59", is_blocked=True)
    )
    db.add_availability_rule(
        AvailabilityRule(day_of_week="friday", start_time="14:30", end_time="23:59", is_blocked=True)
    )
    # Block Saturday fully
    db.add_availability_rule(
        AvailabilityRule(day_of_week="saturday", start_time="00:00", end_time="23:59", is_blocked=True)
    )
    return db


# ── Guest Flow Tests ───────────────────────────────────


@pytest.mark.asyncio
async def test_guest_full_booking_flow(config, db_with_rules):
    """Simulate: guest writes 3 messages, books a slot."""
    llm = MockLLM([
        "Hi! I'm Ivan's scheduling assistant. What's your name?",
        "Nice to meet you, Alex! Here are available slots:\n"
        "1. Monday 11:00-11:30\n2. Monday 14:00-14:30\nWhich works for you?",
        "Monday 11:00 it is! Booking confirmed. [BOOK:1]",
    ])
    calendar = MockCalendar()
    engine = SchedulingEngine(config, calendar, llm, db_with_rules)

    # Message 1: greeting
    r1 = await engine.handle_message(
        IncomingMessage(channel="test", sender_id="guest-1", sender_name="Alex", text="Hello!")
    )
    assert "name" in r1.text.lower()

    # Message 2: give name
    r2 = await engine.handle_message(
        IncomingMessage(channel="test", sender_id="guest-1", sender_name="Alex", text="I'm Alex")
    )
    assert "slot" in r2.text.lower() or "monday" in r2.text.lower()

    # Message 3: select slot → booking
    r3 = await engine.handle_message(
        IncomingMessage(channel="test", sender_id="guest-1", sender_name="Alex", text="Slot 1 please")
    )
    assert "confirmed" in r3.text.lower()
    assert r3.metadata.get("meet_link") is not None

    # Calendar event was created
    assert len(calendar.created_events) == 1

    # Booking saved in DB
    bookings = db_with_rules.get_bookings()
    assert len(bookings) == 1
    assert bookings[0].guest_name == "Alex" or bookings[0].guest_name == "Guest"


@pytest.mark.asyncio
async def test_guest_no_slots_available(config, db):
    """Guest writes but no rules set → no slots."""
    llm = MockLLM([
        "Sorry, Ivan doesn't have any available slots right now. I'll check with him!"
    ])
    calendar = MockCalendar()
    engine = SchedulingEngine(config, calendar, llm, db)

    r = await engine.handle_message(
        IncomingMessage(channel="test", sender_id="guest-2", sender_name="Bob", text="Hi, can I book?")
    )
    # LLM should have received empty slots in the prompt
    assert "no available slots" in llm.calls[0]["system_prompt"].lower()


@pytest.mark.asyncio
async def test_guest_cancel(config, db_with_rules):
    """Guest starts then cancels."""
    llm = MockLLM(["Hi! What's your name?"])
    calendar = MockCalendar()
    engine = SchedulingEngine(config, calendar, llm, db_with_rules)

    await engine.handle_message(
        IncomingMessage(channel="test", sender_id="guest-3", sender_name="Carol", text="Hi")
    )
    assert db_with_rules.get_conversation("guest-3") is not None

    r = await engine.handle_message(
        IncomingMessage(channel="test", sender_id="guest-3", sender_name="Carol", text="/cancel")
    )
    assert "cancel" in r.text.lower()
    assert db_with_rules.get_conversation("guest-3") is None


@pytest.mark.asyncio
async def test_guest_calendar_failure_still_shows_slots(config, db_with_rules):
    """If Google Calendar is down, guest still sees rule-based slots."""
    llm = MockLLM([
        "Here are available times:\n1. Monday 11:00-11:30\nWant to book?"
    ])
    calendar = MockCalendarFailing()
    engine = SchedulingEngine(config, calendar, llm, db_with_rules)

    r = await engine.handle_message(
        IncomingMessage(channel="test", sender_id="guest-4", sender_name="Dave", text="Hi")
    )
    # LLM should have received slots (from rules, without calendar filtering)
    system_prompt = llm.calls[0]["system_prompt"]
    assert "no available slots" not in system_prompt.lower()


# ── Owner Flow Tests ───────────────────────────────────


@pytest.mark.asyncio
async def test_owner_add_rules_via_tags(config, db):
    """Owner says 'add Monday 10-18' → LLM returns ADD_RULE tag → rule saved."""
    llm = MockLLM([
        "Done! Added Monday 10:00-18:00.\n[ADD_RULE:day=monday,start=10:00,end=18:00]\n[SHOW_RULES]"
    ])
    calendar = MockCalendar()
    engine = SchedulingEngine(config, calendar, llm, db)

    r = await engine.handle_message(
        IncomingMessage(channel="test", sender_id="owner-123", sender_name="Ivan", text="Add Monday 10-18")
    )

    # Rule was created in DB
    rules = db.get_availability_rules()
    assert len(rules) == 1
    assert rules[0].day_of_week == "monday"
    assert rules[0].start_time == "10:00"
    assert rules[0].end_time == "18:00"

    # Action tags stripped from response
    assert "[ADD_RULE" not in r.text
    assert "[SHOW_RULES]" not in r.text


@pytest.mark.asyncio
async def test_owner_multiple_tags_one_message(config, db):
    """Owner asks for complex schedule → LLM returns multiple ADD_RULE tags."""
    llm = MockLLM([
        "Added 3 slots for Monday!\n"
        "[ADD_RULE:day=monday,start=11:00,end=11:30]\n"
        "[ADD_RULE:day=monday,start=14:00,end=14:30]\n"
        "[ADD_RULE:day=monday,start=16:00,end=16:30]"
    ])
    calendar = MockCalendar()
    engine = SchedulingEngine(config, calendar, llm, db)

    await engine.handle_message(
        IncomingMessage(channel="test", sender_id="owner-123", sender_name="Ivan",
                       text="Add slots Monday at 11, 14, 16")
    )

    rules = db.get_availability_rules()
    assert len(rules) == 3
    times = [(r.start_time, r.end_time) for r in rules]
    assert ("11:00", "11:30") in times
    assert ("14:00", "14:30") in times
    assert ("16:00", "16:30") in times


@pytest.mark.asyncio
async def test_owner_block_rule(config, db):
    """Owner blocks a time → BLOCK_RULE tag → blocked rule in DB."""
    llm = MockLLM([
        "Blocked Tuesday afternoons.\n[BLOCK_RULE:day=tuesday,start=14:30,end=23:59]"
    ])
    calendar = MockCalendar()
    engine = SchedulingEngine(config, calendar, llm, db)

    await engine.handle_message(
        IncomingMessage(channel="test", sender_id="owner-123", sender_name="Ivan",
                       text="Block Tuesday from 14:30")
    )

    rules = db.get_availability_rules()
    assert len(rules) == 1
    assert rules[0].is_blocked is True
    assert rules[0].day_of_week == "tuesday"


@pytest.mark.asyncio
async def test_owner_clear_rules(config, db):
    """Owner clears Monday rules → CLEAR_RULES tag → rules deleted."""
    # Pre-populate
    db.add_availability_rule(AvailabilityRule(day_of_week="monday", start_time="09:00", end_time="17:00"))
    db.add_availability_rule(AvailabilityRule(day_of_week="tuesday", start_time="09:00", end_time="17:00"))

    llm = MockLLM(["Cleared Monday rules.\n[CLEAR_RULES:day=monday]"])
    calendar = MockCalendar()
    engine = SchedulingEngine(config, calendar, llm, db)

    await engine.handle_message(
        IncomingMessage(channel="test", sender_id="owner-123", sender_name="Ivan",
                       text="Clear Monday")
    )

    rules = db.get_availability_rules()
    assert len(rules) == 1
    assert rules[0].day_of_week == "tuesday"


@pytest.mark.asyncio
async def test_owner_clear_all(config, db):
    """Owner clears ALL rules."""
    db.add_availability_rule(AvailabilityRule(day_of_week="monday", start_time="09:00", end_time="17:00"))
    db.add_availability_rule(AvailabilityRule(day_of_week="tuesday", start_time="09:00", end_time="17:00"))

    llm = MockLLM(["All rules cleared.\n[CLEAR_ALL]"])
    calendar = MockCalendar()
    engine = SchedulingEngine(config, calendar, llm, db)

    await engine.handle_message(
        IncomingMessage(channel="test", sender_id="owner-123", sender_name="Ivan", text="Clear everything")
    )

    assert len(db.get_availability_rules()) == 0


@pytest.mark.asyncio
async def test_owner_quick_commands(config, db):
    """/schedule and /clear work without LLM."""
    db.add_availability_rule(AvailabilityRule(day_of_week="monday", start_time="09:00", end_time="17:00"))

    llm = MockLLM(["should not be called"])
    calendar = MockCalendar()
    engine = SchedulingEngine(config, calendar, llm, db)

    # /schedule
    r1 = await engine.handle_message(
        IncomingMessage(channel="test", sender_id="owner-123", sender_name="Ivan", text="/schedule")
    )
    assert "monday" in r1.text.lower()
    assert llm._call_count == 0

    # /clear
    r2 = await engine.handle_message(
        IncomingMessage(channel="test", sender_id="owner-123", sender_name="Ivan", text="/clear")
    )
    assert "cleared" in r2.text.lower()
    assert len(db.get_availability_rules()) == 0
    assert llm._call_count == 0


@pytest.mark.asyncio
async def test_owner_booking_links_in_prompt(config, db):
    """Owner prompt includes booking links."""
    llm = MockLLM(["People can book via t.me/test_bot"])
    calendar = MockCalendar()
    engine = SchedulingEngine(config, calendar, llm, db)

    await engine.handle_message(
        IncomingMessage(channel="test", sender_id="owner-123", sender_name="Ivan",
                       text="How can people book?")
    )

    # Check that booking links were in the system prompt
    assert "t.me/test_bot" in llm.calls[0]["system_prompt"]


# ── Mixed Flow: Owner + Guest ──────────────────────────


@pytest.mark.asyncio
async def test_owner_sets_rules_then_guest_books(config, db):
    """Full scenario: owner creates rules, then guest books."""
    calendar = MockCalendar()

    # Step 1: Owner adds rules
    owner_llm = MockLLM([
        "Added!\n[ADD_RULE:day=monday,start=11:00,end=11:30]\n[ADD_RULE:day=monday,start=14:00,end=14:30]"
    ])
    engine = SchedulingEngine(config, calendar, owner_llm, db)

    await engine.handle_message(
        IncomingMessage(channel="test", sender_id="owner-123", sender_name="Ivan",
                       text="Monday slots at 11 and 14")
    )
    assert len(db.get_availability_rules()) == 2

    # Step 2: Guest books
    guest_llm = MockLLM([
        "Hi! Here are available times:\n1. Monday 11:00-11:30\n2. Monday 14:00-14:30",
        "Confirmed for Monday 11:00! [BOOK:1]",
    ])
    engine2 = SchedulingEngine(config, calendar, guest_llm, db)

    await engine2.handle_message(
        IncomingMessage(channel="test", sender_id="guest-10", sender_name="Eve", text="Hi, I want to book")
    )
    r = await engine2.handle_message(
        IncomingMessage(channel="test", sender_id="guest-10", sender_name="Eve", text="Slot 1")
    )
    assert "confirmed" in r.text.lower()
    assert len(calendar.created_events) == 1


# ── Availability Engine Tests ──────────────────────────


@pytest.mark.asyncio
async def test_blocked_slots_not_shown(config, db):
    """Blocked rules remove matching slots."""
    # Add available + block
    db.add_availability_rule(AvailabilityRule(day_of_week="tuesday", start_time="09:00", end_time="17:00"))
    db.add_availability_rule(
        AvailabilityRule(day_of_week="tuesday", start_time="14:30", end_time="23:59", is_blocked=True)
    )

    from schedulebot.core.availability import AvailabilityEngine
    calendar = MockCalendar()
    engine = AvailabilityEngine(config.availability, calendar, db)

    # Find a Tuesday
    tz = ZoneInfo("UTC")
    # Jan 7, 2025 is a Tuesday
    from_date = datetime(2025, 1, 7, 0, 0, tzinfo=tz)
    slots = await engine.get_available_slots(from_date)

    tuesday_slots = [s for s in slots if s.start.weekday() == 1]  # 1 = Tuesday
    for slot in tuesday_slots:
        # No slot should start at or after 14:30
        assert slot.start.hour < 14 or (slot.start.hour == 14 and slot.start.minute < 30), \
            f"Blocked slot leaked: {slot}"


@pytest.mark.asyncio
async def test_saturday_fully_blocked(config, db_with_rules):
    """Saturday is fully blocked, no slots should appear."""
    from schedulebot.core.availability import AvailabilityEngine
    calendar = MockCalendar()
    engine = AvailabilityEngine(config.availability, calendar, db_with_rules)

    tz = ZoneInfo("UTC")
    from_date = datetime(2025, 1, 6, 0, 0, tzinfo=tz)
    slots = await engine.get_available_slots(from_date)

    saturday_slots = [s for s in slots if s.start.weekday() == 5]
    assert len(saturday_slots) == 0, f"Saturday slots should be blocked, got: {saturday_slots}"


@pytest.mark.asyncio
async def test_busy_calendar_removes_slots(config, db):
    """Calendar busy times filter out overlapping slots."""
    db.add_availability_rule(AvailabilityRule(day_of_week="monday", start_time="09:00", end_time="12:00"))

    tz = ZoneInfo("UTC")
    busy = [
        TimeSlot(
            start=datetime(2025, 1, 6, 9, 0, tzinfo=tz),
            end=datetime(2025, 1, 6, 10, 0, tzinfo=tz),
        ),
    ]
    calendar = MockCalendar(busy=busy)

    from schedulebot.core.availability import AvailabilityEngine
    engine = AvailabilityEngine(config.availability, calendar, db)

    from_date = datetime(2025, 1, 6, 0, 0, tzinfo=tz)
    slots = await engine.get_available_slots(from_date)

    monday_slots = [s for s in slots if s.start.date() == from_date.date()]
    # 09:00-09:30 should be removed (overlaps busy 09:00-10:00)
    # 09:45-10:15 should also be removed
    for slot in monday_slots:
        assert slot.start >= datetime(2025, 1, 6, 10, 0, tzinfo=tz), \
            f"Busy slot should be filtered: {slot}"


# ── Database CRUD Tests ────────────────────────────────


def test_db_rules_persist_across_reconnect(tmp_path):
    """Rules survive DB close/reopen (simulates Railway redeploy with volume)."""
    db_path = tmp_path / "persist.db"

    # Session 1: add rules
    db1 = Database(db_path)
    db1.connect()
    db1.add_availability_rule(AvailabilityRule(day_of_week="monday", start_time="10:00", end_time="18:00"))
    db1.add_availability_rule(AvailabilityRule(day_of_week="wednesday", start_time="10:00", end_time="18:00"))
    db1.close()

    # Session 2: reopen, check rules
    db2 = Database(db_path)
    db2.connect()
    rules = db2.get_availability_rules()
    assert len(rules) == 2
    days = {r.day_of_week for r in rules}
    assert days == {"monday", "wednesday"}
    db2.close()


def test_db_summary_format(db_with_rules):
    """Summary shows correct format."""
    summary = db_with_rules.format_availability_summary()
    assert "Monday" in summary
    assert "Wednesday" in summary
    assert "Thursday" in summary
    assert "BLOCKED" in summary
    assert "Saturday" in summary


def test_db_booking_crud(db):
    """Save and retrieve bookings."""
    from schedulebot.models import Booking
    tz = ZoneInfo("UTC")

    booking = Booking(
        id="b-001",
        guest_name="Alice",
        guest_channel="telegram",
        guest_sender_id="alice-123",
        slot=TimeSlot(
            start=datetime(2025, 1, 6, 11, 0, tzinfo=tz),
            end=datetime(2025, 1, 6, 11, 30, tzinfo=tz),
        ),
        calendar_event_id="evt-1",
        meet_link="https://meet.google.com/abc",
    )
    db.save_booking(booking)

    bookings = db.get_bookings()
    assert len(bookings) == 1
    assert bookings[0].guest_name == "Alice"
    assert bookings[0].meet_link == "https://meet.google.com/abc"
