"""Discord channel adapter using discord.py."""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Awaitable, Callable

from ..models import IncomingMessage, OutgoingMessage
from .base import ChannelAdapter

logger = logging.getLogger(__name__)


class DiscordAdapter(ChannelAdapter):
    """Discord Bot adapter."""

    def __init__(
        self,
        config: dict,
        on_message: Callable[[IncomingMessage], Awaitable[OutgoingMessage]],
    ):
        super().__init__(config, on_message)
        self.bot_token = config.get("bot_token", "")
        self._client = None

    @property
    def name(self) -> str:
        return "discord"

    async def start(self) -> None:
        try:
            import discord
        except ImportError:
            raise ImportError(
                "discord.py not installed. Run: pip install schedulebot[discord]"
            )

        intents = discord.Intents.default()
        intents.message_content = True
        self._client = discord.Client(intents=intents)
        on_message_cb = self.on_message
        bot_token = self.bot_token

        @self._client.event
        async def on_ready():
            logger.info("Discord bot connected as %s", self._client.user)

        @self._client.event
        async def on_message(message):
            # Ignore own messages
            if message.author == self._client.user:
                return
            # Ignore other bots
            if message.author.bot:
                return

            text = message.content
            # In guild channels, only respond to mentions or DMs
            if message.guild:
                if not self._client.user.mentioned_in(message):
                    return
                # Strip bot mention from text
                text = re.sub(r"<@!?\d+>\s*", "", text).strip()
                if not text:
                    text = "/start"

            try:
                sender_id = str(message.author.id)
                sender_name = message.author.display_name or message.author.name
                logger.info("Discord message from %s: %s", sender_name, text[:50])

                msg = IncomingMessage(
                    channel="discord",
                    sender_id=sender_id,
                    sender_name=sender_name,
                    text=text,
                    metadata={
                        "channel_id": str(message.channel.id),
                        "guild_id": str(message.guild.id) if message.guild else "",
                    },
                )
                response = await on_message_cb(msg)
                clean_text = re.sub(r"\s*\[BOOK:\S+\]", "", response.text).strip()
                if not clean_text:
                    clean_text = "Done! Check your email for the calendar invite."
                await message.channel.send(clean_text)
            except Exception as e:
                logger.error("Error handling Discord message: %s", e, exc_info=True)
                await message.channel.send("Internal error. Please try again.")

        logger.info("Discord adapter starting")
        await self._client.start(bot_token)

    async def stop(self) -> None:
        if self._client:
            await self._client.close()
            logger.info("Discord adapter stopped")

    async def send_message(self, sender_id: str, message: OutgoingMessage) -> None:
        if self._client:
            user = await self._client.fetch_user(int(sender_id))
            if user:
                await user.send(message.text)
