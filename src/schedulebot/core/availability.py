"""Availability engine: combines YAML rules with calendar busy times."""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from ..calendar.base import CalendarProvider
from ..config import AvailabilityConfig
from ..models import TimeSlot

DAYS_OF_WEEK = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


def parse_time_range(time_range: str) -> tuple[tuple[int, int], tuple[int, int]]:
    """Parse 'HH:MM-HH:MM' into ((start_h, start_m), (end_h, end_m))."""
    start_str, end_str = time_range.strip().split("-")
    sh, sm = map(int, start_str.split(":"))
    eh, em = map(int, end_str.split(":"))
    return (sh, sm), (eh, em)


class AvailabilityEngine:
    """Computes available time slots by subtracting calendar busy times from YAML rules."""

    def __init__(self, config: AvailabilityConfig, calendar: CalendarProvider):
        self.config = config
        self.calendar = calendar
        self.tz = ZoneInfo(config.timezone)

    async def get_available_slots(self, from_date: datetime | None = None) -> list[TimeSlot]:
        """Get all available slots from now to max_days_ahead."""
        now = datetime.now(self.tz)
        if from_date:
            now = from_date

        min_start = now + timedelta(hours=self.config.min_notice_hours)
        end_date = now + timedelta(days=self.config.max_days_ahead)

        # Generate raw slots from working hours rules
        raw_slots = self._generate_rule_slots(min_start, end_date)

        if not raw_slots:
            return []

        # Get calendar busy times
        busy_times = await self.calendar.get_busy_times(min_start, end_date)

        # Subtract busy times from raw slots
        available = self._subtract_busy(raw_slots, busy_times)

        return available

    def _generate_rule_slots(self, start: datetime, end: datetime) -> list[TimeSlot]:
        """Generate time slots from YAML working_hours rules."""
        slots = []
        duration = timedelta(minutes=self.config.meeting_duration_minutes)
        buffer = timedelta(minutes=self.config.buffer_minutes)

        current_day = start.replace(hour=0, minute=0, second=0, microsecond=0)
        while current_day < end:
            day_name = DAYS_OF_WEEK[current_day.weekday()]
            day_ranges = self.config.working_hours.get(day_name, [])

            for time_range in day_ranges:
                (sh, sm), (eh, em) = parse_time_range(time_range)
                range_start = current_day.replace(hour=sh, minute=sm)
                range_end = current_day.replace(hour=eh, minute=em)

                # Generate slots within this range
                slot_start = range_start
                while slot_start + duration <= range_end:
                    slot_end = slot_start + duration
                    if slot_start >= start:  # skip past slots
                        slots.append(TimeSlot(start=slot_start, end=slot_end))
                    slot_start = slot_end + buffer

            current_day += timedelta(days=1)

        return slots

    def _subtract_busy(
        self, slots: list[TimeSlot], busy: list[TimeSlot]
    ) -> list[TimeSlot]:
        """Remove slots that overlap with busy times."""
        if not busy:
            return slots

        available = []
        for slot in slots:
            is_free = True
            for b in busy:
                # Check overlap: slot overlaps busy if slot.start < busy.end AND slot.end > busy.start
                if slot.start < b.end and slot.end > b.start:
                    is_free = False
                    break
            if is_free:
                available.append(slot)

        return available
