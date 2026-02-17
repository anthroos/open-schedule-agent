"""Web channel adapter using FastAPI."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

from ..models import IncomingMessage, OutgoingMessage
from .base import ChannelAdapter

logger = logging.getLogger(__name__)


class WebAdapter(ChannelAdapter):
    """FastAPI-based web adapter. Exposes REST endpoints for scheduling."""

    def __init__(
        self,
        config: dict,
        on_message: Callable[[IncomingMessage], Awaitable[OutgoingMessage]],
    ):
        super().__init__(config, on_message)
        self.host = config.get("host", "0.0.0.0")
        self.port = int(config.get("port", 8080))
        self._server = None

    @property
    def name(self) -> str:
        return "web"

    async def start(self) -> None:
        try:
            from fastapi import FastAPI
            from fastapi.middleware.cors import CORSMiddleware
            from pydantic import BaseModel
            import uvicorn
        except ImportError:
            raise ImportError(
                "fastapi/uvicorn not installed. Run: pip install schedulebot[web]"
            )

        app = FastAPI(title="schedulebot", version="0.1.0")
        app.add_middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_methods=["POST"],
            allow_headers=["*"],
        )

        adapter = self

        class MessageRequest(BaseModel):
            sender_id: str
            sender_name: str = "Web User"
            text: str

        from typing import Optional

        class MessageResponse(BaseModel):
            text: str
            booking_id: Optional[str] = None
            meet_link: Optional[str] = None

        @app.post("/api/message", response_model=MessageResponse)
        async def handle_message(req: MessageRequest):
            msg = IncomingMessage(
                channel="web",
                sender_id=req.sender_id,
                sender_name=req.sender_name,
                text=req.text,
            )
            response = await adapter.on_message(msg)
            # Strip [BOOK:N] tags from response
            import re
            clean_text = re.sub(r"\s*\[BOOK:\S+\]", "", response.text)
            return MessageResponse(
                text=clean_text,
                booking_id=response.metadata.get("booking_id"),
                meet_link=response.metadata.get("meet_link"),
            )

        @app.get("/api/health")
        async def health():
            return {"status": "ok"}

        config = uvicorn.Config(app, host=self.host, port=self.port, log_level="info")
        self._server = uvicorn.Server(config)
        logger.info(f"Web adapter starting on {self.host}:{self.port}")
        await self._server.serve()

    async def stop(self) -> None:
        if self._server:
            self._server.should_exit = True
            logger.info("Web adapter stopped")

    async def send_message(self, sender_id: str, message: OutgoingMessage) -> None:
        # Web adapter is request/response, no push capability
        logger.warning("Web adapter does not support push messages")
