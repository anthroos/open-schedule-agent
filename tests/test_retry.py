"""Tests for retry with exponential backoff."""

import urllib.error
from unittest.mock import MagicMock

import pytest

from schedulebot.retry import _is_retryable, retry_async


@pytest.mark.asyncio
async def test_retry_succeeds_first_try():
    fn = MagicMock(return_value="ok")
    result = await retry_async(fn, label="test")
    assert result == "ok"
    assert fn.call_count == 1


@pytest.mark.asyncio
async def test_retry_succeeds_after_transient_failure():
    fn = MagicMock(side_effect=[ConnectionError("reset"), "ok"])
    result = await retry_async(fn, max_retries=2, base_delay=0.01, label="test")
    assert result == "ok"
    assert fn.call_count == 2


@pytest.mark.asyncio
async def test_retry_exhausted_raises():
    fn = MagicMock(side_effect=ConnectionError("down"))
    with pytest.raises(ConnectionError):
        await retry_async(fn, max_retries=2, base_delay=0.01, label="test")
    assert fn.call_count == 3  # 1 initial + 2 retries


@pytest.mark.asyncio
async def test_non_retryable_raises_immediately():
    fn = MagicMock(side_effect=ValueError("bad input"))
    with pytest.raises(ValueError):
        await retry_async(fn, max_retries=3, base_delay=0.01, label="test")
    assert fn.call_count == 1


def test_is_retryable_connection_error():
    assert _is_retryable(ConnectionError("reset")) is True


def test_is_retryable_timeout():
    assert _is_retryable(TimeoutError("timed out")) is True


def test_is_retryable_os_error():
    assert _is_retryable(OSError("network unreachable")) is True


def test_is_retryable_value_error():
    assert _is_retryable(ValueError("bad")) is False


def test_is_retryable_url_error():
    assert _is_retryable(urllib.error.URLError("timeout")) is True


def test_is_retryable_http_429():
    exc = urllib.error.HTTPError(None, 429, "Too Many Requests", {}, None)
    assert _is_retryable(exc) is True


def test_is_retryable_http_400():
    exc = urllib.error.HTTPError(None, 400, "Bad Request", {}, None)
    assert _is_retryable(exc) is False
