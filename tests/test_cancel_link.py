"""Tests for self-service cancel link feature."""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone

import pytest

from schedulebot.database import Database
from schedulebot.models import Booking, TimeSlot


@pytest.fixture
def db(tmp_path):
    d = Database(tmp_path / "cancel.db")
    d.connect()
    yield d
    d.close()


def _make_booking(cancel_token: str = "", **kwargs) -> Booking:
    now = datetime.now(timezone.utc)
    defaults = dict(
        id=secrets.token_urlsafe(16),
        guest_name="John",
        guest_channel="telegram",
        guest_sender_id="guest-1",
        guest_email="john@test.com",
        slot=TimeSlot(start=now + timedelta(hours=2), end=now + timedelta(hours=2, minutes=30)),
        calendar_event_id="evt-1",
        meet_link="https://meet.google.com/test",
        cancel_token=cancel_token or secrets.token_urlsafe(32),
    )
    defaults.update(kwargs)
    return Booking(**defaults)


class TestCancelTokenDB:
    def test_cancel_token_stored_and_retrieved(self, db):
        booking = _make_booking()
        db.save_booking(booking)
        fetched = db.get_booking_by_cancel_token(booking.cancel_token)
        assert fetched is not None
        assert fetched.id == booking.id
        assert fetched.cancel_token == booking.cancel_token

    def test_cancel_token_via_finalize(self, db):
        booking = _make_booking()
        # Simulate reserve + finalize flow
        db.reserve_slot(booking.slot.start, booking.slot.end, booking.id)
        db.finalize_booking(booking)
        fetched = db.get_booking_by_cancel_token(booking.cancel_token)
        assert fetched is not None
        assert fetched.cancel_token == booking.cancel_token

    def test_unknown_cancel_token_returns_none(self, db):
        assert db.get_booking_by_cancel_token("nonexistent") is None

    def test_empty_cancel_token_returns_none(self, db):
        assert db.get_booking_by_cancel_token("") is None

    def test_cancel_token_unique_per_booking(self, db):
        b1 = _make_booking()
        b2 = _make_booking()
        db.save_booking(b1)
        db.save_booking(b2)
        assert b1.cancel_token != b2.cancel_token
        assert db.get_booking_by_cancel_token(b1.cancel_token).id == b1.id
        assert db.get_booking_by_cancel_token(b2.cancel_token).id == b2.id

    def test_delete_booking_clears_cancel_token(self, db):
        booking = _make_booking()
        db.save_booking(booking)
        db.delete_booking(booking.id)
        assert db.get_booking_by_cancel_token(booking.cancel_token) is None


class TestCancelURLInConfirmation:
    def test_format_confirmation_includes_cancel_url(self):
        """Engine._format_confirmation includes cancel link when web is enabled."""
        from schedulebot.config import (
            AvailabilityConfig, CalendarConfig, ChannelConfig, Config,
            LLMConfig, NotificationsConfig, OwnerConfig,
        )
        from schedulebot.core.engine import SchedulingEngine

        config = Config(
            owner=OwnerConfig(name="Test"),
            availability=AvailabilityConfig(timezone="UTC"),
            calendar=CalendarConfig(),
            llm=LLMConfig(),
            notifications=NotificationsConfig(),
            channels={"web": ChannelConfig(enabled=True, extra={"host": "localhost", "port": 8080})},
        )

        class DummyCalendar:
            async def get_busy_times(self, *a):
                return []

        class DummyLLM:
            async def chat(self, *a):
                return ""

        db_dummy = type("DB", (), {"get_availability_rules": lambda s: [], "get_setting": lambda s, k, default=None: default})()
        engine = SchedulingEngine(config, DummyCalendar(), DummyLLM(), db_dummy)

        now = datetime.now(timezone.utc)
        booking = _make_booking()
        text = engine._format_confirmation(booking)
        assert "Cancel:" in text
        assert booking.cancel_token in text

    def test_no_cancel_url_without_web(self):
        """No cancel link when web channel is disabled and no agent_card.url."""
        from schedulebot.config import (
            AvailabilityConfig, CalendarConfig, Config,
            LLMConfig, NotificationsConfig, OwnerConfig,
        )
        from schedulebot.core.engine import SchedulingEngine

        config = Config(
            owner=OwnerConfig(name="Test"),
            availability=AvailabilityConfig(timezone="UTC"),
            calendar=CalendarConfig(),
            llm=LLMConfig(),
            notifications=NotificationsConfig(),
        )

        class DummyCalendar:
            async def get_busy_times(self, *a):
                return []

        class DummyLLM:
            async def chat(self, *a):
                return ""

        db_dummy = type("DB", (), {"get_availability_rules": lambda s: [], "get_setting": lambda s, k, default=None: default})()
        engine = SchedulingEngine(config, DummyCalendar(), DummyLLM(), db_dummy)

        booking = _make_booking()
        text = engine._format_confirmation(booking)
        assert "Cancel:" not in text
