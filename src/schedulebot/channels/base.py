"""Abstract base for channel adapters."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable

from ..models import IncomingMessage, OutgoingMessage


class ChannelAdapter(ABC):
    """Base class for all channel adapters (Telegram, Slack, Discord, Web, etc.)."""

    def __init__(
        self,
        config: dict,
        on_message: Callable[[IncomingMessage], Awaitable[OutgoingMessage]],
    ):
        self.config = config
        self.on_message = on_message

    @property
    @abstractmethod
    def name(self) -> str:
        """Channel name identifier (e.g., 'telegram', 'slack')."""
        ...

    @abstractmethod
    async def start(self) -> None:
        """Start listening for messages."""
        ...

    @abstractmethod
    async def stop(self) -> None:
        """Graceful shutdown."""
        ...

    @abstractmethod
    async def send_message(self, sender_id: str, message: OutgoingMessage) -> None:
        """Send a message to a specific user."""
        ...
