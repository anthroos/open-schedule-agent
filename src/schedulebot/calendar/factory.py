"""Factory for building calendar providers from config."""

from __future__ import annotations

import logging

from ..config import CalendarConfig
from .base import CalendarProvider
from .google_calendar import GoogleCalendarProvider
from .multi_calendar import MultiCalendarManager

logger = logging.getLogger(__name__)


def build_calendar_provider(config: CalendarConfig, timezone: str = "UTC") -> CalendarProvider:
    """Build a CalendarProvider from config.

    - If config.sources is empty: returns a single GoogleCalendarProvider (backward compatible)
    - If config.sources is populated: returns a MultiCalendarManager wrapping multiple providers
    """
    if not config.sources:
        return GoogleCalendarProvider(config, timezone)

    book_provider = None
    book_name = "primary"
    watch_providers = []

    for src in config.sources:
        # Build a minimal CalendarConfig for each source
        src_config = CalendarConfig(
            provider=config.provider,
            create_meet_link=src.create_meet_link,
            credentials_path=src.credentials_path,
            token_path=src.token_path,
        )
        provider = GoogleCalendarProvider(src_config, timezone, calendar_id=src.calendar_id)

        if src.role == "book":
            if book_provider is not None:
                logger.warning(
                    "Multiple calendars with role='book' found. Using '%s', ignoring '%s'.",
                    book_name, src.name,
                )
                continue
            book_provider = provider
            book_name = src.name
        else:
            watch_providers.append(provider)

    if book_provider is None:
        logger.warning("No calendar with role='book' found. Using first source as book calendar.")
        if watch_providers:
            book_provider = watch_providers.pop(0)
            book_name = config.sources[0].name
        else:
            # Fallback to single-calendar mode
            return GoogleCalendarProvider(config, timezone)

    if not watch_providers:
        # Only one calendar — no need for MultiCalendarManager
        logger.info("Single calendar source '%s' configured, using direct provider.", book_name)
        return book_provider

    logger.info(
        "Multi-calendar: book='%s', watch=%d calendar(s)",
        book_name, len(watch_providers),
    )
    return MultiCalendarManager(book_provider, watch_providers, book_name)
