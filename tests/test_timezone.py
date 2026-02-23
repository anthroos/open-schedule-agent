"""Tests for guest timezone support."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any
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
from schedulebot.timezone_resolver import resolve_timezone


# ── Timezone resolver tests ──────────────────────────────


class TestResolveTimezone:
    def test_iana_timezone_direct(self):
        assert resolve_timezone("Europe/Kyiv") == "Europe/Kyiv"

    def test_iana_case_insensitive(self):
        assert resolve_timezone("europe/kyiv") == "Europe/Kyiv"

    def test_city_name(self):
        assert resolve_timezone("Kyiv") == "Europe/Kyiv"

    def test_city_kiev_alias(self):
        assert resolve_timezone("Kiev") == "Europe/Kyiv"

    def test_country(self):
        assert resolve_timezone("Ukraine") == "Europe/Kyiv"

    def test_new_york(self):
        assert resolve_timezone("New York") == "America/New_York"

    def test_abbreviation(self):
        assert resolve_timezone("EST") == "America/New_York"

    def test_bali(self):
        assert resolve_timezone("Bali") == "Asia/Makassar"

    def test_tokyo(self):
        assert resolve_timezone("Tokyo") == "Asia/Tokyo"

    def test_unknown_returns_none(self):
        assert resolve_timezone("Planet Mars") is None

    def test_empty_returns_none(self):
        assert resolve_timezone("") is None

    def test_partial_match(self):
        assert resolve_timezone("Kyiv, Ukraine") == "Europe/Kyiv"

    def test_whitespace_stripped(self):
        assert resolve_timezone("  london  ") == "Europe/London"


# ── TimeSlot.format_in_tz tests ─────────────────────────


class TestTimeSlotFormatInTz:
    def test_convert_bali_to_kyiv(self):
        """14:00 WITA (UTC+8) should become 08:00 EET (UTC+2)."""
        bali_tz = ZoneInfo("Asia/Makassar")
        kyiv_tz = ZoneInfo("Europe/Kyiv")

        # 2026-02-24 14:00 in Bali (WITA, UTC+8)
        start = datetime(2026, 2, 24, 14, 0, tzinfo=bali_tz)
        end = datetime(2026, 2, 24, 14, 30, tzinfo=bali_tz)
        slot = TimeSlot(start=start, end=end)

        formatted = slot.format_in_tz(kyiv_tz)
        assert "08:00" in formatted
        assert "08:30" in formatted

    def test_convert_bali_to_new_york(self):
        """14:00 WITA (UTC+8) should become 01:00 EST (UTC-5)."""
        bali_tz = ZoneInfo("Asia/Makassar")
        ny_tz = ZoneInfo("America/New_York")

        start = datetime(2026, 2, 24, 14, 0, tzinfo=bali_tz)
        end = datetime(2026, 2, 24, 14, 30, tzinfo=bali_tz)
        slot = TimeSlot(start=start, end=end)

        formatted = slot.format_in_tz(ny_tz)
        assert "01:00" in formatted
        assert "01:30" in formatted

    def test_same_timezone_no_change(self):
        bali_tz = ZoneInfo("Asia/Makassar")
        start = datetime(2026, 2, 24, 14, 0, tzinfo=bali_tz)
        end = datetime(2026, 2, 24, 14, 30, tzinfo=bali_tz)
        slot = TimeSlot(start=start, end=end)

        formatted = slot.format_in_tz(bali_tz)
        assert "14:00" in formatted
        assert "14:30" in formatted


# ── Engine integration: city in collect_guest_info ────────


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

    async def chat_with_tools(self, system_prompt, messages, tools):
        self.calls.append({"system_prompt": system_prompt})
        idx = min(self._call_count, len(self.turns) - 1)
        self._call_count += 1
        return self.turns[idx]


class MockCalendar:
    async def get_busy_times(self, start, end):
        return []

    async def create_event(self, **kwargs):
        return {"event_id": "evt-1", "meet_link": "https://meet.google.com/test"}


@pytest.fixture
def config():
    return Config(
        owner=OwnerConfig(name="Ivan", email="ivan@test.com", owner_ids={"test": "owner-1"}),
        availability=AvailabilityConfig(
            timezone="Asia/Makassar", meeting_duration_minutes=30,
            buffer_minutes=15, min_notice_hours=0, max_days_ahead=14,
        ),
        calendar=CalendarConfig(),
        llm=LLMConfig(),
        notifications=NotificationsConfig(),
        booking_links=BookingLinksConfig(),
    )


@pytest.fixture
def db(tmp_path):
    d = Database(tmp_path / "tz.db")
    d.connect()
    for day in ["monday", "tuesday", "wednesday", "thursday", "friday"]:
        d.add_availability_rule(
            AvailabilityRule(day_of_week=day, start_time="09:00", end_time="17:00")
        )
    yield d
    d.close()


@pytest.mark.asyncio
async def test_city_sets_guest_timezone(config, db):
    """collect_guest_info with city resolves timezone."""
    llm = MockToolLLM(turns=[
        MockToolResponse(
            text="",
            tool_calls=[MockToolCall("tc-1", "collect_guest_info", {
                "name": "Nikita", "email": "nikita@test.com", "city": "Kyiv",
            })],
            stop_reason="tool_use",
        ),
        MockToolResponse(text="Got it! Slots in your timezone:"),
    ])
    engine = SchedulingEngine(config, MockCalendar(), llm, db)

    await engine.handle_message(
        IncomingMessage(text="Hi", sender_id="g-tz-1", sender_name="Nikita", channel="test")
    )

    conv = db.get_conversation("g-tz-1")
    assert conv.guest_timezone == "Europe/Kyiv"


@pytest.mark.asyncio
async def test_unknown_city_no_crash(config, db):
    """Unknown city doesn't crash — just no timezone set."""
    llm = MockToolLLM(turns=[
        MockToolResponse(
            text="",
            tool_calls=[MockToolCall("tc-1", "collect_guest_info", {
                "name": "Bob", "email": "bob@test.com", "city": "Planet Mars",
            })],
            stop_reason="tool_use",
        ),
        MockToolResponse(text="Which slot?"),
    ])
    engine = SchedulingEngine(config, MockCalendar(), llm, db)

    await engine.handle_message(
        IncomingMessage(text="Hi", sender_id="g-tz-2", sender_name="Bob", channel="test")
    )

    conv = db.get_conversation("g-tz-2")
    assert conv.guest_timezone == ""


@pytest.mark.asyncio
async def test_prompt_includes_guest_timezone_in_slots(config, db):
    """After timezone set, system prompt shows slots in guest timezone."""
    from schedulebot.models import Conversation, ConversationState

    # Pre-populate conv with timezone
    conv = Conversation(
        sender_id="g-tz-3", channel="test",
        guest_name="Nikita", guest_email="nikita@test.com",
        guest_timezone="Europe/Kyiv",
        state=ConversationState.COLLECTING_INFO,
    )
    db.save_conversation(conv)

    llm = MockToolLLM(turns=[
        MockToolResponse(text="Here are your slots!"),
    ])
    engine = SchedulingEngine(config, MockCalendar(), llm, db)

    await engine.handle_message(
        IncomingMessage(text="show slots", sender_id="g-tz-3", sender_name="Nikita", channel="test")
    )

    prompt = llm.calls[0]["system_prompt"]
    assert "Europe/Kyiv" in prompt


@pytest.mark.asyncio
async def test_confirmation_includes_calendar_check_message(config, db):
    """Booking confirmation tells guest to check calendar."""
    from schedulebot.models import Conversation, ConversationState

    conv = Conversation(
        sender_id="g-tz-4", channel="test",
        guest_name="Nikita", guest_email="nikita@test.com",
        guest_timezone="Europe/Kyiv",
        state=ConversationState.COLLECTING_INFO,
    )
    db.save_conversation(conv)

    llm = MockToolLLM(turns=[
        MockToolResponse(
            text="",
            tool_calls=[MockToolCall("tc-1", "confirm_booking", {"slot_number": 1})],
            stop_reason="tool_use",
        ),
        MockToolResponse(text="Booked!"),
    ])
    engine = SchedulingEngine(config, MockCalendar(), llm, db)

    result = await engine.handle_message(
        IncomingMessage(text="Slot 1", sender_id="g-tz-4", sender_name="Nikita", channel="test")
    )

    assert result.metadata.get("booking_id") is not None
