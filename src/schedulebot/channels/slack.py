"""Slack channel adapter using slack-bolt."""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Awaitable, Callable

from ..models import IncomingMessage, OutgoingMessage
from .base import ChannelAdapter

logger = logging.getLogger(__name__)


class SlackAdapter(ChannelAdapter):
    """Slack Bot adapter using Socket Mode."""

    def __init__(
        self,
        config: dict,
        on_message: Callable[[IncomingMessage], Awaitable[OutgoingMessage]],
    ):
        super().__init__(config, on_message)
        self.bot_token = config.get("bot_token", "")
        self.app_token = config.get("app_token", "")
        self._app = None
        self._handler = None

    @property
    def name(self) -> str:
        return "slack"

    async def start(self) -> None:
        try:
            from slack_bolt.adapter.socket_mode.async_handler import (
                AsyncSocketModeHandler,
            )
            from slack_bolt.async_app import AsyncApp
        except ImportError:
            raise ImportError(
                "slack-bolt not installed. Run: pip install schedulebot[slack]"
            )

        self._app = AsyncApp(token=self.bot_token)
        on_message_cb = self.on_message

        @self._app.event("message")
        async def handle_message(event, say):
            text = event.get("text", "")
            user_id = event.get("user", "")
            # Ignore bot messages and message edits
            if event.get("bot_id") or event.get("subtype"):
                return
            if not text or not user_id:
                return

            try:
                logger.info("Slack message from %s: %s", user_id, text[:50])
                msg = IncomingMessage(
                    channel="slack",
                    sender_id=user_id,
                    sender_name=user_id,  # Slack doesn't include name in event
                    text=text,
                    metadata={"channel": event.get("channel", "")},
                )
                response = await on_message_cb(msg)
                clean_text = re.sub(r"\s*\[BOOK:\S+\]", "", response.text).strip()
                if not clean_text:
                    clean_text = "Done! Check your email for the calendar invite."
                await say(clean_text)
            except Exception as e:
                logger.error("Error handling Slack message: %s", e, exc_info=True)
                await say("Internal error. Please try again.")

        @self._app.event("app_mention")
        async def handle_mention(event, say):
            text = event.get("text", "")
            user_id = event.get("user", "")
            if not text or not user_id:
                return

            # Strip the bot mention from the text
            text = re.sub(r"<@\w+>\s*", "", text).strip()
            if not text:
                text = "/start"

            try:
                logger.info("Slack mention from %s: %s", user_id, text[:50])
                msg = IncomingMessage(
                    channel="slack",
                    sender_id=user_id,
                    sender_name=user_id,
                    text=text,
                    metadata={"channel": event.get("channel", "")},
                )
                response = await on_message_cb(msg)
                clean_text = re.sub(r"\s*\[BOOK:\S+\]", "", response.text).strip()
                if not clean_text:
                    clean_text = "Done! Check your email for the calendar invite."
                await say(clean_text)
            except Exception as e:
                logger.error("Error handling Slack mention: %s", e, exc_info=True)
                await say("Internal error. Please try again.")

        self._handler = AsyncSocketModeHandler(self._app, self.app_token)
        logger.info("Slack adapter starting (Socket Mode)")
        await self._handler.start_async()

    async def stop(self) -> None:
        if self._handler:
            await self._handler.close_async()
            logger.info("Slack adapter stopped")

    async def send_message(self, sender_id: str, message: OutgoingMessage) -> None:
        if self._app:
            await self._app.client.chat_postMessage(
                channel=sender_id, text=message.text,
            )
