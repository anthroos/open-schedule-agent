"""Telegram channel adapter using python-telegram-bot."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

from ..models import IncomingMessage, OutgoingMessage
from .base import ChannelAdapter

logger = logging.getLogger(__name__)


class TelegramAdapter(ChannelAdapter):
    """Telegram Bot API adapter."""

    def __init__(
        self,
        config: dict,
        on_message: Callable[[IncomingMessage], Awaitable[OutgoingMessage]],
    ):
        super().__init__(config, on_message)
        self._app = None
        self.bot_token = config.get("bot_token", "")

    @property
    def name(self) -> str:
        return "telegram"

    async def start(self) -> None:
        try:
            from telegram import Update
            from telegram.ext import (
                Application,
                CommandHandler,
                MessageHandler,
                filters,
            )
        except ImportError:
            raise ImportError(
                "python-telegram-bot not installed. Run: pip install schedulebot[telegram]"
            )

        self._app = Application.builder().token(self.bot_token).build()

        async def handle_start(update: Update, context) -> None:
            msg = IncomingMessage(
                channel="telegram",
                sender_id=str(update.effective_user.id),
                sender_name=update.effective_user.full_name or "User",
                text="/start",
            )
            response = await self.on_message(msg)
            await update.message.reply_text(response.text)

        async def handle_cancel(update: Update, context) -> None:
            msg = IncomingMessage(
                channel="telegram",
                sender_id=str(update.effective_user.id),
                sender_name=update.effective_user.full_name or "User",
                text="/cancel",
            )
            response = await self.on_message(msg)
            await update.message.reply_text(response.text)

        async def handle_message(update: Update, context) -> None:
            if not update.message or not update.message.text:
                return
            msg = IncomingMessage(
                channel="telegram",
                sender_id=str(update.effective_user.id),
                sender_name=update.effective_user.full_name or "User",
                text=update.message.text,
                metadata={"chat_id": update.effective_chat.id},
            )
            response = await self.on_message(msg)
            # Strip [BOOK:N] tags from response before sending
            import re
            clean_text = re.sub(r"\s*\[BOOK:\S+\]", "", response.text)
            await update.message.reply_text(clean_text)

        self._app.add_handler(CommandHandler("start", handle_start))
        self._app.add_handler(CommandHandler("cancel", handle_cancel))
        self._app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

        logger.info("Telegram adapter starting (polling)")
        await self._app.initialize()
        await self._app.start()
        await self._app.updater.start_polling()

    async def stop(self) -> None:
        if self._app:
            await self._app.updater.stop()
            await self._app.stop()
            await self._app.shutdown()
            logger.info("Telegram adapter stopped")

    async def send_message(self, sender_id: str, message: OutgoingMessage) -> None:
        if self._app:
            await self._app.bot.send_message(chat_id=int(sender_id), text=message.text)
