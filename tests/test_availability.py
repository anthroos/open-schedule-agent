"""Tests for the availability engine."""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Optional, List
from zoneinfo import ZoneInfo

import pytest

from schedulebot.config import AvailabilityConfig
from schedulebot.core.availability import AvailabilityEngine, parse_time_range
from schedulebot.models import TimeSlot


class MockCalendar:
    """Mock calendar that returns configurable busy times."""

    def __init__(self, busy: Optional[List[TimeSlot]] = None):
        self.busy = busy or []

    async def get_busy_times(self, start, end):
        return self.busy

    async def create_event(self, **kwargs):
        return {"event_id": "mock-123"}


def test_parse_time_range():
    (sh, sm), (eh, em) = parse_time_range("09:00-17:00")
    assert (sh, sm) == (9, 0)
    assert (eh, em) == (17, 0)


def test_parse_time_range_half_hours():
    (sh, sm), (eh, em) = parse_time_range("10:30-13:30")
    assert (sh, sm) == (10, 30)
    assert (eh, em) == (13, 30)


@pytest.fixture
def config():
    return AvailabilityConfig(
        timezone="UTC",
        working_hours={
            "monday": ["09:00-12:00"],
            "tuesday": ["10:00-13:00"],
        },
        meeting_duration_minutes=30,
        buffer_minutes=15,
        min_notice_hours=0,
        max_days_ahead=7,
    )


def test_generate_rule_slots(config):
    calendar = MockCalendar()
    engine = AvailabilityEngine(config, calendar)

    # Monday Jan 6, 2025 — known Monday
    start = datetime(2025, 1, 6, 0, 0, tzinfo=ZoneInfo("UTC"))
    end = datetime(2025, 1, 7, 0, 0, tzinfo=ZoneInfo("UTC"))
    slots = engine._generate_rule_slots(start, end)

    # 09:00-12:00 with 30min duration + 15min buffer = 4 slots
    # 09:00-09:30, 09:45-10:15, 10:30-11:00, 11:15-11:45
    assert len(slots) == 4
    assert slots[0].start.hour == 9
    assert slots[0].start.minute == 0
    assert slots[0].end.hour == 9
    assert slots[0].end.minute == 30


def test_subtract_busy(config):
    calendar = MockCalendar()
    engine = AvailabilityEngine(config, calendar)

    tz = ZoneInfo("UTC")
    slots = [
        TimeSlot(start=datetime(2025, 1, 6, 9, 0, tzinfo=tz), end=datetime(2025, 1, 6, 9, 30, tzinfo=tz)),
        TimeSlot(start=datetime(2025, 1, 6, 10, 0, tzinfo=tz), end=datetime(2025, 1, 6, 10, 30, tzinfo=tz)),
        TimeSlot(start=datetime(2025, 1, 6, 11, 0, tzinfo=tz), end=datetime(2025, 1, 6, 11, 30, tzinfo=tz)),
    ]
    busy = [
        TimeSlot(start=datetime(2025, 1, 6, 9, 0, tzinfo=tz), end=datetime(2025, 1, 6, 10, 0, tzinfo=tz)),
    ]

    available = engine._subtract_busy(slots, busy)
    assert len(available) == 2
    assert available[0].start.hour == 10
    assert available[1].start.hour == 11


@pytest.mark.asyncio
async def test_get_available_slots_empty_calendar(config):
    calendar = MockCalendar()
    engine = AvailabilityEngine(config, calendar)

    # Start from a known Monday
    from_date = datetime(2025, 1, 6, 0, 0, tzinfo=ZoneInfo("UTC"))
    slots = await engine.get_available_slots(from_date)
    # Should have slots for Monday + Tuesday in the 7-day window
    assert len(slots) > 0


@pytest.mark.asyncio
async def test_get_available_slots_with_busy(config):
    tz = ZoneInfo("UTC")
    busy = [
        TimeSlot(
            start=datetime(2025, 1, 6, 9, 0, tzinfo=tz),
            end=datetime(2025, 1, 6, 12, 0, tzinfo=tz),
        ),
    ]
    calendar = MockCalendar(busy=busy)
    engine = AvailabilityEngine(config, calendar)

    from_date = datetime(2025, 1, 6, 0, 0, tzinfo=tz)
    slots = await engine.get_available_slots(from_date)
    # All Monday slots should be blocked (09:00-12:00 is entirely busy)
    monday_slots = [s for s in slots if s.start.date() == from_date.date()]
    assert len(monday_slots) == 0
