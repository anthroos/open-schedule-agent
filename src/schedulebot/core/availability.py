"""Availability engine: combines DB rules with calendar busy times."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from ..calendar.base import CalendarProvider
from ..config import AvailabilityConfig
from ..database import Database
from ..models import AvailabilityRule, TimeSlot

logger = logging.getLogger(__name__)

DAYS_OF_WEEK = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


def parse_time_range(time_range: str) -> tuple[tuple[int, int], tuple[int, int]]:
    """Parse 'HH:MM-HH:MM' into ((start_h, start_m), (end_h, end_m))."""
    start_str, end_str = time_range.strip().split("-")
    sh, sm = map(int, start_str.split(":"))
    eh, em = map(int, end_str.split(":"))
    return (sh, sm), (eh, em)


class AvailabilityEngine:
    """Computes available time slots by subtracting calendar busy times from DB rules."""

    def __init__(self, config: AvailabilityConfig, calendar: CalendarProvider, db: Database):
        self.config = config
        self.calendar = calendar
        self.db = db
        self.tz = ZoneInfo(config.timezone)

    async def get_available_slots(self, from_date: datetime | None = None) -> list[TimeSlot]:
        """Get all available slots from now to max_days_ahead."""
        now = datetime.now(self.tz)
        if from_date:
            now = from_date

        min_start = now + timedelta(hours=self.config.min_notice_hours)
        end_date = now + timedelta(days=self.config.max_days_ahead)

        # Get rules from database
        rules = self.db.get_availability_rules()
        if not rules:
            return []

        # Generate raw slots from rules
        raw_slots = self._generate_rule_slots(rules, min_start, end_date)

        if not raw_slots:
            return []

        # Get calendar busy times (if calendar fails, return all slots)
        try:
            busy_times = await self.calendar.get_busy_times(min_start, end_date)
        except Exception as e:
            logger.warning(f"Calendar API failed, returning slots without busy check: {e}")
            return raw_slots

        # Subtract busy times from raw slots
        available = self._subtract_busy(raw_slots, busy_times)

        return available

    def _generate_rule_slots(
        self, rules: list[AvailabilityRule], start: datetime, end: datetime
    ) -> list[TimeSlot]:
        """Generate time slots from DB availability rules."""
        slots = []
        duration = timedelta(minutes=self.config.meeting_duration_minutes)
        buffer = timedelta(minutes=self.config.buffer_minutes)

        # Separate recurring vs specific-date rules
        recurring = {}
        specific = {}
        blocked_recurring = {}
        blocked_specific = {}

        for rule in rules:
            if rule.is_blocked:
                if rule.day_of_week:
                    blocked_recurring.setdefault(rule.day_of_week, []).append(rule)
                elif rule.specific_date:
                    blocked_specific.setdefault(rule.specific_date, []).append(rule)
            else:
                if rule.day_of_week:
                    recurring.setdefault(rule.day_of_week, []).append(rule)
                elif rule.specific_date:
                    specific.setdefault(rule.specific_date, []).append(rule)

        current_day = start.replace(hour=0, minute=0, second=0, microsecond=0)
        while current_day < end:
            day_name = DAYS_OF_WEEK[current_day.weekday()]
            date_str = current_day.strftime("%Y-%m-%d")

            # Collect time ranges for this day (specific date overrides recurring)
            day_rules = specific.get(date_str, recurring.get(day_name, []))
            day_blocked = blocked_specific.get(date_str, []) + blocked_recurring.get(day_name, [])

            for rule in day_rules:
                (sh, sm), (eh, em) = parse_time_range(f"{rule.start_time}-{rule.end_time}")
                range_start = current_day.replace(hour=sh, minute=sm)
                range_end = current_day.replace(hour=eh, minute=em)

                # Generate slots within this range
                slot_start = range_start
                while slot_start + duration <= range_end:
                    slot_end = slot_start + duration

                    # Check not blocked
                    is_blocked = False
                    for blocked in day_blocked:
                        (bsh, bsm), (beh, bem) = parse_time_range(
                            f"{blocked.start_time}-{blocked.end_time}"
                        )
                        block_start = current_day.replace(hour=bsh, minute=bsm)
                        block_end = current_day.replace(hour=beh, minute=bem)
                        if slot_start < block_end and slot_end > block_start:
                            is_blocked = True
                            break

                    if not is_blocked and slot_start >= start:
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
                if slot.start < b.end and slot.end > b.start:
                    is_free = False
                    break
            if is_free:
                available.append(slot)

        return available
