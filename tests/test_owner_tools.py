"""Tests for owner and guest mode with Anthropic tool use (function calling).

Verifies that when the LLM provider supports chat_with_tools(),
the engine executes tools and returns structured results instead
of parsing text tags.
"""

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
from schedulebot.models import IncomingMessage


# ── Mock LLM with tool use ──────────────────────────────


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
    """Mock LLM that supports chat_with_tools.

    Accepts a list of scripted turns. Each turn is a MockToolResponse.
    If a turn has tool_calls, the engine will execute them and call again,
    consuming the next turn in the list.
    """

    def __init__(self, turns: list[MockToolResponse]):
        self.turns = turns
        self._call_count = 0
        self.calls: list[dict] = []

    async def chat(self, system_prompt: str, messages: list[dict]) -> str:
        """Fallback text-only chat (should NOT be called in tool path)."""
        raise AssertionError("chat() called but chat_with_tools() should be used")

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
        booking_links=BookingLinksConfig(links={"telegram": "https://t.me/test_bot"}),
    )


@pytest.fixture
def db(tmp_path):
    d = Database(tmp_path / "tools.db")
    d.connect()
    yield d
    d.close()


# ── Tests ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_add_rule_via_tool(config, db):
    """LLM calls add_rule tool → rule saved in DB."""
    llm = MockToolLLM(turns=[
        # Turn 1: LLM calls add_rule
        MockToolResponse(
            text="",
            tool_calls=[MockToolCall(
                id="tc_1",
                name="add_rule",
                input={"day": "monday", "start": "10:00", "end": "18:00"},
            )],
            stop_reason="tool_use",
        ),
        # Turn 2: LLM responds with final text after tool result
        MockToolResponse(text="Done! Added Monday 10:00-18:00.", stop_reason="end_turn"),
    ])
    engine = SchedulingEngine(config, MockCalendar(), llm, db)

    result = await engine.handle_message(
        IncomingMessage(text="Add Monday 10-18", sender_id="owner-123", sender_name="Ivan", channel="test")
    )

    assert "Done" in result.text
    rules = db.get_availability_rules()
    assert len(rules) == 1
    assert rules[0].day_of_week == "monday"
    assert rules[0].start_time == "10:00"
    assert rules[0].end_time == "18:00"
    assert not rules[0].is_blocked


@pytest.mark.asyncio
async def test_multiple_tools_one_turn(config, db):
    """LLM calls multiple tools in one turn → all executed."""
    llm = MockToolLLM(turns=[
        MockToolResponse(
            text="",
            tool_calls=[
                MockToolCall(id="tc_1", name="add_rule", input={"day": "monday", "start": "10:00", "end": "12:00"}),
                MockToolCall(id="tc_2", name="add_rule", input={"day": "monday", "start": "14:00", "end": "16:00"}),
                MockToolCall(id="tc_3", name="add_rule", input={"day": "wednesday", "start": "09:00", "end": "17:00"}),
            ],
            stop_reason="tool_use",
        ),
        MockToolResponse(text="Added 3 rules!", stop_reason="end_turn"),
    ])
    engine = SchedulingEngine(config, MockCalendar(), llm, db)

    result = await engine.handle_message(
        IncomingMessage(text="Mon 10-12, 14-16, Wed 9-17", sender_id="owner-123", sender_name="Ivan", channel="test")
    )

    assert "3 rules" in result.text
    rules = db.get_availability_rules()
    assert len(rules) == 3


@pytest.mark.asyncio
async def test_block_time_via_tool(config, db):
    """LLM calls block_time tool → block rule saved."""
    llm = MockToolLLM(turns=[
        MockToolResponse(
            text="",
            tool_calls=[MockToolCall(
                id="tc_1",
                name="block_time",
                input={"day": "saturday", "start": "00:00", "end": "23:59"},
            )],
            stop_reason="tool_use",
        ),
        MockToolResponse(text="Saturday is now blocked.", stop_reason="end_turn"),
    ])
    engine = SchedulingEngine(config, MockCalendar(), llm, db)

    result = await engine.handle_message(
        IncomingMessage(text="Block Saturday", sender_id="owner-123", sender_name="Ivan", channel="test")
    )

    rules = db.get_availability_rules()
    assert len(rules) == 1
    assert rules[0].is_blocked
    assert rules[0].day_of_week == "saturday"


@pytest.mark.asyncio
async def test_clear_all_via_tool(config, db):
    """LLM calls clear_all tool → all rules removed."""
    # Pre-populate
    from schedulebot.models import AvailabilityRule

    db.add_availability_rule(AvailabilityRule(day_of_week="monday", start_time="10:00", end_time="18:00"))
    db.add_availability_rule(AvailabilityRule(day_of_week="tuesday", start_time="09:00", end_time="17:00"))
    assert len(db.get_availability_rules()) == 2

    llm = MockToolLLM(turns=[
        MockToolResponse(
            text="",
            tool_calls=[MockToolCall(id="tc_1", name="clear_all", input={})],
            stop_reason="tool_use",
        ),
        MockToolResponse(text="All rules cleared.", stop_reason="end_turn"),
    ])
    engine = SchedulingEngine(config, MockCalendar(), llm, db)

    result = await engine.handle_message(
        IncomingMessage(text="Clear everything", sender_id="owner-123", sender_name="Ivan", channel="test")
    )

    assert len(db.get_availability_rules()) == 0
    assert "cleared" in result.text.lower()


@pytest.mark.asyncio
async def test_clear_rules_for_day(config, db):
    """LLM calls clear_rules for specific day → only that day cleared."""
    from schedulebot.models import AvailabilityRule

    db.add_availability_rule(AvailabilityRule(day_of_week="monday", start_time="10:00", end_time="18:00"))
    db.add_availability_rule(AvailabilityRule(day_of_week="tuesday", start_time="09:00", end_time="17:00"))

    llm = MockToolLLM(turns=[
        MockToolResponse(
            text="",
            tool_calls=[MockToolCall(id="tc_1", name="clear_rules", input={"day": "monday"})],
            stop_reason="tool_use",
        ),
        MockToolResponse(text="Monday rules cleared.", stop_reason="end_turn"),
    ])
    engine = SchedulingEngine(config, MockCalendar(), llm, db)

    await engine.handle_message(
        IncomingMessage(text="Clear Monday", sender_id="owner-123", sender_name="Ivan", channel="test")
    )

    rules = db.get_availability_rules()
    assert len(rules) == 1
    assert rules[0].day_of_week == "tuesday"


@pytest.mark.asyncio
async def test_show_rules_via_tool(config, db):
    """LLM calls show_rules → gets summary back as tool result."""
    from schedulebot.models import AvailabilityRule

    db.add_availability_rule(AvailabilityRule(day_of_week="friday", start_time="10:00", end_time="14:00"))

    llm = MockToolLLM(turns=[
        MockToolResponse(
            text="",
            tool_calls=[MockToolCall(id="tc_1", name="show_rules", input={})],
            stop_reason="tool_use",
        ),
        MockToolResponse(text="Here's your schedule:\nFriday: 10:00-14:00", stop_reason="end_turn"),
    ])
    engine = SchedulingEngine(config, MockCalendar(), llm, db)

    result = await engine.handle_message(
        IncomingMessage(text="Show my schedule", sender_id="owner-123", sender_name="Ivan", channel="test")
    )

    # Verify the tool got called and summary was passed back
    assert llm._call_count == 2
    # Second call should have tool_result in messages
    second_call_msgs = llm.calls[1]["messages"]
    # Last message should be user role with tool_result content
    last_msg = second_call_msgs[-1]
    assert last_msg["role"] == "user"
    assert any("tool_result" in str(c) for c in last_msg["content"])


@pytest.mark.asyncio
async def test_specific_date_rule(config, db):
    """LLM adds rule for specific date → date stored correctly."""
    llm = MockToolLLM(turns=[
        MockToolResponse(
            text="",
            tool_calls=[MockToolCall(
                id="tc_1",
                name="add_rule",
                input={"date": "2026-02-20", "start": "10:00", "end": "14:00"},
            )],
            stop_reason="tool_use",
        ),
        MockToolResponse(text="Added slot for Feb 20.", stop_reason="end_turn"),
    ])
    engine = SchedulingEngine(config, MockCalendar(), llm, db)

    await engine.handle_message(
        IncomingMessage(text="Add Feb 20 10-14", sender_id="owner-123", sender_name="Ivan", channel="test")
    )

    rules = db.get_availability_rules()
    assert len(rules) == 1
    assert rules[0].specific_date == "2026-02-20"
    assert rules[0].day_of_week == ""


@pytest.mark.asyncio
async def test_quick_commands_bypass_tools(config, db):
    """Quick commands (/schedule, /clear) don't go through LLM."""
    from schedulebot.models import AvailabilityRule

    db.add_availability_rule(AvailabilityRule(day_of_week="monday", start_time="10:00", end_time="18:00"))

    llm = MockToolLLM(turns=[])  # No turns — should never be called
    engine = SchedulingEngine(config, MockCalendar(), llm, db)

    # /schedule shows rules
    result = await engine.handle_message(
        IncomingMessage(text="/schedule", sender_id="owner-123", sender_name="Ivan", channel="test")
    )
    assert "monday" in result.text.lower() or "Monday" in result.text

    # /clear removes rules
    result = await engine.handle_message(
        IncomingMessage(text="/clear", sender_id="owner-123", sender_name="Ivan", channel="test")
    )
    assert "Cleared" in result.text
    assert len(db.get_availability_rules()) == 0
    assert llm._call_count == 0


@pytest.mark.asyncio
async def test_no_tools_called_text_only(config, db):
    """LLM responds with just text, no tool calls → text returned directly."""
    llm = MockToolLLM(turns=[
        MockToolResponse(text="Hi! How can I help manage your schedule?", stop_reason="end_turn"),
    ])
    engine = SchedulingEngine(config, MockCalendar(), llm, db)

    result = await engine.handle_message(
        IncomingMessage(text="Hello", sender_id="owner-123", sender_name="Ivan", channel="test")
    )

    assert "help" in result.text.lower()
    assert llm._call_count == 1


@pytest.mark.asyncio
async def test_guest_flow_uses_tools_when_available(config, db):
    """Guest flow uses chat_with_tools() when provider supports it."""
    from schedulebot.models import AvailabilityRule

    db.add_availability_rule(AvailabilityRule(day_of_week="monday", start_time="10:00", end_time="18:00"))

    llm = MockToolLLM(turns=[
        MockToolResponse(text="Hi! I'd love to help you book a meeting with Ivan.", stop_reason="end_turn"),
    ])
    engine = SchedulingEngine(config, MockCalendar(), llm, db)

    result = await engine.handle_message(
        IncomingMessage(text="Hello", sender_id="guest-456", sender_name="Guest", channel="test")
    )

    assert "book" in result.text.lower()
    assert llm._call_count == 1
    # Verify both guest tools were passed
    tool_names = {t["name"] for t in llm.calls[0]["tools"]}
    assert "collect_guest_info" in tool_names
    assert "confirm_booking" in tool_names


@pytest.mark.asyncio
async def test_tools_definitions_passed_to_llm(config, db):
    """Verify OWNER_TOOLS schemas are passed to chat_with_tools."""
    llm = MockToolLLM(turns=[
        MockToolResponse(text="OK", stop_reason="end_turn"),
    ])
    engine = SchedulingEngine(config, MockCalendar(), llm, db)

    await engine.handle_message(
        IncomingMessage(text="Show schedule", sender_id="owner-123", sender_name="Ivan", channel="test")
    )

    assert len(llm.calls) == 1
    tools = llm.calls[0]["tools"]
    tool_names = {t["name"] for t in tools}
    assert tool_names == {"add_rule", "block_time", "clear_rules", "clear_all", "show_rules"}


@pytest.mark.asyncio
async def test_conversation_persisted_after_tool_use(config, db):
    """Conversation messages are saved to DB after tool-use flow."""
    llm = MockToolLLM(turns=[
        MockToolResponse(
            text="",
            tool_calls=[MockToolCall(id="tc_1", name="add_rule", input={"day": "monday", "start": "10:00", "end": "18:00"})],
            stop_reason="tool_use",
        ),
        MockToolResponse(text="Monday 10-18 added!", stop_reason="end_turn"),
    ])
    engine = SchedulingEngine(config, MockCalendar(), llm, db)

    await engine.handle_message(
        IncomingMessage(text="Add Monday 10-18", sender_id="owner-123", sender_name="Ivan", channel="test")
    )

    conv = db.get_conversation("owner-123")
    assert conv is not None
    assert len(conv.messages) == 2  # user + assistant
    assert conv.messages[0]["role"] == "user"
    assert conv.messages[1]["role"] == "assistant"
    assert "Monday" in conv.messages[1]["content"]


# ── Guest Tool-Use Tests ─────────────────────────────────


@pytest.fixture
def db_with_slots(db):
    """DB pre-populated with Monday slots for guest testing."""
    from schedulebot.models import AvailabilityRule

    for hour in ["11:00", "14:00", "16:00", "19:00"]:
        end_h = int(hour.split(":")[0])
        db.add_availability_rule(
            AvailabilityRule(day_of_week="monday", start_time=hour, end_time=f"{end_h}:30")
        )
    return db


@pytest.mark.asyncio
async def test_guest_booking_via_tool(config, db_with_slots):
    """Guest picks a slot → LLM calls confirm_booking → booking created."""
    from schedulebot.models import Conversation, ConversationState

    # Pre-populate guest info (collect_guest_info already called)
    conv = Conversation(
        sender_id="guest-100", channel="test",
        guest_name="TestGuest", guest_email="guest@test.com",
        state=ConversationState.COLLECTING_INFO,
    )
    db_with_slots.save_conversation(conv)

    llm = MockToolLLM(turns=[
        # Turn 1: LLM calls confirm_booking
        MockToolResponse(
            text="",
            tool_calls=[MockToolCall(
                id="tc_1",
                name="confirm_booking",
                input={"slot_number": 2},
            )],
            stop_reason="tool_use",
        ),
        # Turn 2: LLM produces confirmation text after tool result
        MockToolResponse(
            text="You're all set! Meeting booked for Monday 14:00-14:30.",
            stop_reason="end_turn",
        ),
    ])
    engine = SchedulingEngine(config, MockCalendar(), llm, db_with_slots)

    result = await engine.handle_message(
        IncomingMessage(text="I'll take slot 2", sender_id="guest-100", sender_name="Guest", channel="test")
    )

    assert result.metadata.get("booking_id") is not None
    assert result.metadata.get("meet_link") is not None
    assert "booked" in result.text.lower() or "meeting" in result.text.lower()


@pytest.mark.asyncio
async def test_guest_conversation_no_tools(config, db_with_slots):
    """Guest says hello → LLM responds with text only, no tool calls."""
    llm = MockToolLLM(turns=[
        MockToolResponse(
            text="Hi! I'm here to help you schedule a meeting. What's your name?",
            stop_reason="end_turn",
        ),
    ])
    engine = SchedulingEngine(config, MockCalendar(), llm, db_with_slots)

    result = await engine.handle_message(
        IncomingMessage(text="Hello", sender_id="guest-101", sender_name="Guest", channel="test")
    )

    assert "name" in result.text.lower()
    assert result.metadata.get("booking_id") is None


@pytest.mark.asyncio
async def test_guest_invalid_slot_number(config, db_with_slots):
    """Guest picks invalid slot number → error returned, no booking."""
    from schedulebot.models import Conversation, ConversationState

    conv = Conversation(
        sender_id="guest-102", channel="test",
        guest_name="TestGuest", guest_email="guest@test.com",
        state=ConversationState.COLLECTING_INFO,
    )
    db_with_slots.save_conversation(conv)

    llm = MockToolLLM(turns=[
        MockToolResponse(
            text="",
            tool_calls=[MockToolCall(
                id="tc_1",
                name="confirm_booking",
                input={"slot_number": 999},
            )],
            stop_reason="tool_use",
        ),
        MockToolResponse(
            text="Sorry, that slot number doesn't seem valid. Could you pick from the available list?",
            stop_reason="end_turn",
        ),
    ])
    engine = SchedulingEngine(config, MockCalendar(), llm, db_with_slots)

    result = await engine.handle_message(
        IncomingMessage(text="Slot 999", sender_id="guest-102", sender_name="Guest", channel="test")
    )

    # No booking should be created
    assert result.metadata.get("booking_id") is None


@pytest.mark.asyncio
async def test_guest_tools_passed_to_llm(config, db_with_slots):
    """Verify GUEST_TOOLS are passed to chat_with_tools."""
    llm = MockToolLLM(turns=[
        MockToolResponse(text="Hi there!", stop_reason="end_turn"),
    ])
    engine = SchedulingEngine(config, MockCalendar(), llm, db_with_slots)

    await engine.handle_message(
        IncomingMessage(text="Hello", sender_id="guest-103", sender_name="Guest", channel="test")
    )

    assert len(llm.calls) == 1
    tools = llm.calls[0]["tools"]
    tool_names = {t["name"] for t in tools}
    assert tool_names == {"collect_guest_info", "confirm_booking"}
