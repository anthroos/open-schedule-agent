"""Retry with exponential backoff for external API calls."""

from __future__ import annotations

import asyncio
import logging
import urllib.error
from typing import Callable, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")

TRANSIENT_HTTP_CODES = {429, 500, 502, 503, 504}


async def retry_async(
    fn: Callable[..., T],
    *args,
    max_retries: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
    label: str = "api_call",
    **kwargs,
) -> T:
    """Call fn with retries and exponential backoff.

    Retries on transient errors (rate limits, server errors, network issues).
    Non-retryable errors (auth, bad request) are raised immediately.
    """
    last_exc = None
    for attempt in range(max_retries + 1):
        try:
            return fn(*args, **kwargs)
        except Exception as exc:
            last_exc = exc
            if not _is_retryable(exc) or attempt == max_retries:
                raise
            delay = min(base_delay * (2 ** attempt), max_delay)
            logger.warning(
                "%s failed (attempt %d/%d): %s. Retrying in %.1fs",
                label, attempt + 1, max_retries + 1, exc, delay,
            )
            await asyncio.sleep(delay)

    raise last_exc  # type: ignore[misc]


def _is_retryable(exc: Exception) -> bool:
    """Check if an exception is transient and worth retrying."""
    exc_type = type(exc).__name__

    # Anthropic SDK errors
    if exc_type in ("RateLimitError", "InternalServerError", "APIConnectionError"):
        return True
    if exc_type == "APIStatusError" and hasattr(exc, "status_code"):
        return exc.status_code in TRANSIENT_HTTP_CODES  # type: ignore[union-attr]

    # OpenAI SDK errors
    if exc_type in ("RateLimitError", "APIConnectionError", "InternalServerError"):
        return True
    if exc_type == "APIStatusError" and hasattr(exc, "status_code"):
        return exc.status_code in TRANSIENT_HTTP_CODES  # type: ignore[union-attr]

    # urllib errors (Ollama) — HTTPError before URLError (HTTPError is a subclass)
    if isinstance(exc, urllib.error.HTTPError):
        return exc.code in TRANSIENT_HTTP_CODES
    if isinstance(exc, urllib.error.URLError):
        return True

    # Google API errors
    if exc_type == "HttpError" and hasattr(exc, "resp"):
        return int(exc.resp.get("status", 0)) in TRANSIENT_HTTP_CODES  # type: ignore[union-attr]

    # Generic network errors
    if isinstance(exc, (ConnectionError, TimeoutError, OSError)):
        return True

    return False
