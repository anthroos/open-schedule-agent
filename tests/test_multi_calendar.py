"""Tests for multi-calendar support."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from schedulebot.calendar.base import CalendarProvider
from schedulebot.calendar.factory import build_calendar_provider
from schedulebot.calendar.google_calendar import GoogleCalendarProvider
from schedulebot.calendar.multi_calendar import MultiCalendarManager
from schedulebot.config import CalendarConfig, CalendarSourceConfig
from schedulebot.models import TimeSlot


# --- Fixtures ---


class FakeCalendar(CalendarProvider):
    """Fake calendar provider for testing."""

    def __init__(self, busy: list[TimeSlot] | None = None, name: str = "fake"):
        self.busy = busy or []
        self.name = name
        self.created_events: list[dict] = []
        self.deleted_events: list[str] = []

    async def get_busy_times(self, start: datetime, end: datetime) -> list[TimeSlot]:
        return self.busy

    async def create_event(
        self,
        summary: str,
        start: datetime,
        end: datetime,
        description: str = "",
        attendee_email: str | None = None,
        attendee_emails: list[str] | None = None,
        create_meet_link: bool = False,
    ) -> dict:
        event = {"event_id": f"{self.name}-evt-{len(self.created_events)+1}", "summary": summary}
        self.created_events.append(event)
        return event

    async def delete_event(self, event_id: str) -> None:
        self.deleted_events.append(event_id)


# --- GoogleCalendarProvider calendar_id ---


def test_google_provider_stores_calendar_id():
    config = CalendarConfig(credentials_path="/tmp/c.json", token_path="/tmp/t.json")
    provider = GoogleCalendarProvider(config, "UTC", calendar_id="work@group.calendar.google.com")
    assert provider.calendar_id == "work@group.calendar.google.com"


def test_google_provider_default_calendar_id():
    config = CalendarConfig(credentials_path="/tmp/c.json", token_path="/tmp/t.json")
    provider = GoogleCalendarProvider(config, "UTC")
    assert provider.calendar_id == "primary"


# --- MultiCalendarManager ---


@pytest.fixture
def book_cal():
    return FakeCalendar(name="work")


@pytest.fixture
def watch_cal():
    return FakeCalendar(name="personal")


@pytest.fixture
def multi(book_cal, watch_cal):
    return MultiCalendarManager(book_cal, [watch_cal], book_name="Work")


@pytest.mark.asyncio
async def test_multi_get_busy_times_union(multi, book_cal, watch_cal):
    """Busy times from all calendars are merged."""
    now = datetime(2026, 3, 1, 10, 0, tzinfo=timezone.utc)
    slot1 = TimeSlot(start=now, end=now.replace(hour=11))
    slot2 = TimeSlot(start=now.replace(hour=14), end=now.replace(hour=15))
    book_cal.busy = [slot1]
    watch_cal.busy = [slot2]

    result = await multi.get_busy_times(now, now.replace(hour=18))
    assert len(result) == 2
    assert result[0] == slot1
    assert result[1] == slot2


@pytest.mark.asyncio
async def test_multi_get_busy_times_sorted(multi, book_cal, watch_cal):
    """Merged busy times are sorted by start."""
    now = datetime(2026, 3, 1, 10, 0, tzinfo=timezone.utc)
    late = TimeSlot(start=now.replace(hour=16), end=now.replace(hour=17))
    early = TimeSlot(start=now.replace(hour=9), end=now.replace(hour=10))
    book_cal.busy = [late]
    watch_cal.busy = [early]

    result = await multi.get_busy_times(now.replace(hour=8), now.replace(hour=18))
    assert result[0].start < result[1].start


@pytest.mark.asyncio
async def test_multi_create_event_books_and_blocks(multi, book_cal, watch_cal):
    """create_event creates in book calendar and blocker in watch calendar."""
    now = datetime(2026, 3, 1, 10, 0, tzinfo=timezone.utc)
    result = await multi.create_event(
        summary="Meeting with client",
        start=now,
        end=now.replace(hour=11),
    )

    # Book calendar gets the real event
    assert result["event_id"] == "work-evt-1"
    assert len(book_cal.created_events) == 1
    assert book_cal.created_events[0]["summary"] == "Meeting with client"

    # Watch calendar gets a blocker
    assert len(watch_cal.created_events) == 1
    assert "[Blocked]" in watch_cal.created_events[0]["summary"]


@pytest.mark.asyncio
async def test_multi_delete_event_only_book(multi, book_cal, watch_cal):
    """delete_event only deletes from book calendar."""
    await multi.delete_event("evt-123")
    assert "evt-123" in book_cal.deleted_events
    assert len(watch_cal.deleted_events) == 0


@pytest.mark.asyncio
async def test_multi_watch_failure_doesnt_break_booking(book_cal):
    """If watch calendar fails, booking still succeeds."""
    failing_watch = FakeCalendar(name="broken")

    async def fail_create(*args, **kwargs):
        raise RuntimeError("API error")

    failing_watch.create_event = fail_create
    multi = MultiCalendarManager(book_cal, [failing_watch], book_name="Work")

    now = datetime(2026, 3, 1, 10, 0, tzinfo=timezone.utc)
    result = await multi.create_event(summary="Test", start=now, end=now.replace(hour=11))
    assert result["event_id"] == "work-evt-1"


# --- Factory ---


def test_factory_single_calendar_no_sources():
    """Empty sources = backward compatible single GoogleCalendarProvider."""
    config = CalendarConfig(
        provider="google",
        credentials_path="/tmp/c.json",
        token_path="/tmp/t.json",
    )
    with patch("schedulebot.calendar.factory.GoogleCalendarProvider") as mock_cls:
        mock_cls.return_value = MagicMock()
        provider = build_calendar_provider(config, "UTC")
        mock_cls.assert_called_once_with(config, "UTC")


def test_factory_multi_calendar():
    """Multiple sources returns MultiCalendarManager."""
    config = CalendarConfig(
        provider="google",
        credentials_path="/tmp/c.json",
        token_path="/tmp/t.json",
        sources=[
            CalendarSourceConfig(
                name="Work",
                calendar_id="primary",
                credentials_path="/tmp/c-work.json",
                token_path="/tmp/t-work.json",
                role="book",
            ),
            CalendarSourceConfig(
                name="Personal",
                calendar_id="primary",
                credentials_path="/tmp/c-personal.json",
                token_path="/tmp/t-personal.json",
                role="watch",
            ),
        ],
    )
    provider = build_calendar_provider(config, "UTC")
    assert isinstance(provider, MultiCalendarManager)
    assert isinstance(provider.book_provider, GoogleCalendarProvider)
    assert len(provider.watch_providers) == 1
    assert provider.book_name == "Work"


def test_factory_single_source_no_manager():
    """Single source with role=book returns direct GoogleCalendarProvider (no manager)."""
    config = CalendarConfig(
        provider="google",
        credentials_path="/tmp/c.json",
        token_path="/tmp/t.json",
        sources=[
            CalendarSourceConfig(
                name="Work",
                calendar_id="work@group.calendar.google.com",
                credentials_path="/tmp/c.json",
                token_path="/tmp/t.json",
                role="book",
            ),
        ],
    )
    provider = build_calendar_provider(config, "UTC")
    assert isinstance(provider, GoogleCalendarProvider)
    assert provider.calendar_id == "work@group.calendar.google.com"


# --- Config parsing ---


def test_config_sources_default_empty():
    """CalendarConfig defaults to empty sources list."""
    config = CalendarConfig()
    assert config.sources == []


def test_calendar_source_config_defaults():
    """CalendarSourceConfig has sensible defaults."""
    src = CalendarSourceConfig()
    assert src.name == "primary"
    assert src.calendar_id == "primary"
    assert src.role == "book"
    assert src.create_meet_link is True


# --- Error handling ---


@pytest.mark.asyncio
async def test_book_provider_failure_propagates():
    """If book provider fails get_busy_times, exception propagates (prevents double-bookings)."""
    failing_book = FakeCalendar(name="broken-book")

    async def fail_busy(*args, **kwargs):
        raise RuntimeError("Google API down")

    failing_book.get_busy_times = fail_busy
    watch = FakeCalendar(name="personal")
    multi = MultiCalendarManager(failing_book, [watch], book_name="Work")

    now = datetime(2026, 3, 1, 10, 0, tzinfo=timezone.utc)
    with pytest.raises(RuntimeError, match="Google API down"):
        await multi.get_busy_times(now, now.replace(hour=18))


@pytest.mark.asyncio
async def test_watch_provider_failure_tolerated_in_busy():
    """If watch provider fails get_busy_times, book provider results still returned."""
    book = FakeCalendar(name="work")
    now = datetime(2026, 3, 1, 10, 0, tzinfo=timezone.utc)
    book.busy = [TimeSlot(start=now, end=now.replace(hour=11))]

    failing_watch = FakeCalendar(name="broken-watch")

    async def fail_busy(*args, **kwargs):
        raise RuntimeError("Watch API down")

    failing_watch.get_busy_times = fail_busy
    multi = MultiCalendarManager(book, [failing_watch], book_name="Work")

    result = await multi.get_busy_times(now, now.replace(hour=18))
    assert len(result) == 1  # only book provider's busy time


@pytest.mark.asyncio
async def test_create_event_includes_calendar_name(multi, book_cal):
    """create_event result includes calendar_name from MultiCalendarManager."""
    now = datetime(2026, 3, 1, 10, 0, tzinfo=timezone.utc)
    result = await multi.create_event(summary="Test", start=now, end=now.replace(hour=11))
    assert result["calendar_name"] == "Work"


# --- Role validation ---


def test_invalid_role_raises():
    """Invalid calendar role in config raises ValueError."""
    from schedulebot.config import load_config
    import tempfile, os
    config_content = """
owner:
  name: Test
calendar:
  provider: google
calendars:
  - name: Work
    calendar_id: primary
    credentials_path: c.json
    token_path: t.json
    role: boook
"""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write(config_content)
        f.flush()
        try:
            with pytest.raises(ValueError, match="must be 'book' or 'watch'"):
                load_config(f.name)
        finally:
            os.unlink(f.name)
