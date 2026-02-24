"""Tests for the reminder loop feature."""

from __future__ import annotations

import asyncio
import secrets
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest

from schedulebot.database import Database
from schedulebot.models import Booking, OutgoingMessage, TimeSlot
from schedulebot.reminders import ReminderLoop


@pytest.fixture
def db(tmp_path):
    d = Database(tmp_path / "reminder.db")
    d.connect()
    yield d
    d.close()


def _make_booking(minutes_from_now: int = 30, reminder_sent: bool = False, **kwargs) -> Booking:
    now = datetime.now(timezone.utc)
    defaults = dict(
        id=secrets.token_urlsafe(16),
        guest_name="John",
        guest_channel="telegram",
        guest_sender_id="guest-1",
        guest_email="john@test.com",
        slot=TimeSlot(
            start=now + timedelta(minutes=minutes_from_now),
            end=now + timedelta(minutes=minutes_from_now + 30),
        ),
        calendar_event_id="evt-1",
        meet_link="https://meet.google.com/test",
        cancel_token=secrets.token_urlsafe(32),
        reminder_sent=reminder_sent,
    )
    defaults.update(kwargs)
    return Booking(**defaults)


class TestReminderQuery:
    def test_upcoming_booking_found(self, db):
        booking = _make_booking(minutes_from_now=30)
        db.save_booking(booking)
        now = datetime.now(timezone.utc)
        results = db.get_upcoming_bookings_needing_reminder(
            after=now, before=now + timedelta(minutes=60)
        )
        assert len(results) == 1
        assert results[0].id == booking.id

    def test_past_booking_not_found(self, db):
        booking = _make_booking(minutes_from_now=-30)
        db.save_booking(booking)
        now = datetime.now(timezone.utc)
        results = db.get_upcoming_bookings_needing_reminder(
            after=now, before=now + timedelta(minutes=60)
        )
        assert len(results) == 0

    def test_already_reminded_not_found(self, db):
        booking = _make_booking(minutes_from_now=30)
        db.save_booking(booking)
        db.mark_reminder_sent(booking.id)
        now = datetime.now(timezone.utc)
        results = db.get_upcoming_bookings_needing_reminder(
            after=now, before=now + timedelta(minutes=60)
        )
        assert len(results) == 0

    def test_booking_outside_window_not_found(self, db):
        booking = _make_booking(minutes_from_now=120)
        db.save_booking(booking)
        now = datetime.now(timezone.utc)
        results = db.get_upcoming_bookings_needing_reminder(
            after=now, before=now + timedelta(minutes=60)
        )
        assert len(results) == 0

    def test_mark_reminder_sent(self, db):
        booking = _make_booking(minutes_from_now=30)
        db.save_booking(booking)
        db.mark_reminder_sent(booking.id)
        fetched = db.get_booking_by_id(booking.id)
        assert fetched.reminder_sent is True


class TestReminderLoop:
    @pytest.mark.asyncio
    async def test_sends_guest_reminder(self, db):
        booking = _make_booking(minutes_from_now=30)
        db.save_booking(booking)

        mock_adapter = AsyncMock()
        mock_adapter.send_message = AsyncMock()

        loop = ReminderLoop(
            db=db,
            adapters={"telegram": mock_adapter},
            reminder_minutes=60,
        )
        await loop._check_and_send()

        mock_adapter.send_message.assert_called_once()
        call_args = mock_adapter.send_message.call_args
        assert call_args[0][0] == "guest-1"
        msg = call_args[0][1]
        assert "Reminder" in msg.text
        assert "meet.google.com" in msg.text

        # Verify marked as sent
        fetched = db.get_booking_by_id(booking.id)
        assert fetched.reminder_sent is True

    @pytest.mark.asyncio
    async def test_sends_owner_reminder(self, db):
        booking = _make_booking(minutes_from_now=30)
        db.save_booking(booking)

        guest_adapter = AsyncMock()
        owner_adapter = AsyncMock()

        loop = ReminderLoop(
            db=db,
            adapters={"telegram": guest_adapter},
            reminder_minutes=60,
            owner_adapter=owner_adapter,
            owner_id="owner-1",
        )
        await loop._check_and_send()

        owner_adapter.send_message.assert_called_once()
        call_args = owner_adapter.send_message.call_args
        assert call_args[0][0] == "owner-1"
        assert "John" in call_args[0][1].text

    @pytest.mark.asyncio
    async def test_no_double_send(self, db):
        booking = _make_booking(minutes_from_now=30)
        db.save_booking(booking)

        mock_adapter = AsyncMock()
        loop = ReminderLoop(
            db=db,
            adapters={"telegram": mock_adapter},
            reminder_minutes=60,
        )

        await loop._check_and_send()
        await loop._check_and_send()

        # Should only send once
        assert mock_adapter.send_message.call_count == 1

    @pytest.mark.asyncio
    async def test_no_reminder_for_past(self, db):
        booking = _make_booking(minutes_from_now=-30)
        db.save_booking(booking)

        mock_adapter = AsyncMock()
        loop = ReminderLoop(
            db=db,
            adapters={"telegram": mock_adapter},
            reminder_minutes=60,
        )
        await loop._check_and_send()

        mock_adapter.send_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_booking_within_window_gets_reminder(self, db):
        """Booking created 10 min from now with 60-min window still gets reminder."""
        booking = _make_booking(minutes_from_now=10)
        db.save_booking(booking)

        mock_adapter = AsyncMock()
        loop = ReminderLoop(
            db=db,
            adapters={"telegram": mock_adapter},
            reminder_minutes=60,
        )
        await loop._check_and_send()

        mock_adapter.send_message.assert_called_once()

    @pytest.mark.asyncio
    async def test_unknown_channel_no_crash(self, db):
        """Booking on unknown channel doesn't crash the loop."""
        booking = _make_booking(minutes_from_now=30, guest_channel="unknown")
        db.save_booking(booking)

        loop = ReminderLoop(
            db=db,
            adapters={"telegram": AsyncMock()},
            reminder_minutes=60,
        )
        # Should not raise
        await loop._check_and_send()

        # Still marked as sent
        fetched = db.get_booking_by_id(booking.id)
        assert fetched.reminder_sent is True
