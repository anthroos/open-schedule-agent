"""Web channel adapter using FastAPI. Includes schedule management API."""

from __future__ import annotations

import logging
import re
from collections.abc import Awaitable, Callable
from typing import Optional

from ..models import IncomingMessage, OutgoingMessage
from .base import ChannelAdapter

logger = logging.getLogger(__name__)


class WebAdapter(ChannelAdapter):
    """FastAPI-based web adapter. Exposes REST endpoints for scheduling + management API."""

    def __init__(
        self,
        config: dict,
        on_message: Callable[[IncomingMessage], Awaitable[OutgoingMessage]],
        db=None,
        mcp_app=None,
        mcp_path: str = "/mcp",
        owner_name: str = "Owner",
    ):
        super().__init__(config, on_message)
        self.host = config.get("host", "0.0.0.0")
        self.port = int(config.get("port", 8080))
        self.api_key = config.get("api_key", "")
        self.db = db
        self.mcp_app = mcp_app
        self.mcp_path = mcp_path
        self.owner_name = owner_name
        self._server = None

    @property
    def name(self) -> str:
        return "web"

    async def start(self) -> None:
        try:
            from fastapi import FastAPI, Header, HTTPException
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
            allow_methods=["*"],
            allow_headers=["*"],
        )

        adapter = self

        # --- Auth helper ---
        def check_api_key(authorization: str | None):
            if adapter.api_key:
                if not authorization or authorization.replace("Bearer ", "") != adapter.api_key:
                    raise HTTPException(status_code=401, detail="Invalid API key")

        # --- Guest messaging ---

        class MessageRequest(BaseModel):
            sender_id: str
            sender_name: str = "Web User"
            text: str

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
            clean_text = re.sub(r"\s*\[BOOK:\S+\]", "", response.text)
            return MessageResponse(
                text=clean_text,
                booking_id=response.metadata.get("booking_id"),
                meet_link=response.metadata.get("meet_link"),
            )

        # --- Schedule Management API (for agents / Claude Code) ---

        class RuleRequest(BaseModel):
            day_of_week: str = ""
            specific_date: str = ""
            start_time: str
            end_time: str
            is_blocked: bool = False

        class RuleResponse(BaseModel):
            id: int
            day_of_week: str
            specific_date: str
            start_time: str
            end_time: str
            is_blocked: bool

        class RulesListResponse(BaseModel):
            rules: list
            summary: str

        @app.get("/api/schedule")
        async def get_schedule(authorization: Optional[str] = Header(None)):
            check_api_key(authorization)
            if not adapter.db:
                raise HTTPException(status_code=500, detail="Database not available")
            rules = adapter.db.get_availability_rules()
            return {
                "rules": [
                    {
                        "id": r.id,
                        "day_of_week": r.day_of_week,
                        "specific_date": r.specific_date,
                        "start_time": r.start_time,
                        "end_time": r.end_time,
                        "is_blocked": r.is_blocked,
                    }
                    for r in rules
                ],
                "summary": adapter.db.format_availability_summary(),
            }

        @app.post("/api/schedule/rules")
        async def add_rule(req: RuleRequest, authorization: Optional[str] = Header(None)):
            check_api_key(authorization)
            if not adapter.db:
                raise HTTPException(status_code=500, detail="Database not available")
            from ..models import AvailabilityRule
            rule = AvailabilityRule(
                day_of_week=req.day_of_week,
                specific_date=req.specific_date,
                start_time=req.start_time,
                end_time=req.end_time,
                is_blocked=req.is_blocked,
            )
            rule_id = adapter.db.add_availability_rule(rule)
            return {"id": rule_id, "status": "created"}

        @app.delete("/api/schedule/rules/{rule_id}")
        async def delete_rule(rule_id: int, authorization: Optional[str] = Header(None)):
            check_api_key(authorization)
            if not adapter.db:
                raise HTTPException(status_code=500, detail="Database not available")
            deleted = adapter.db.delete_availability_rule(rule_id)
            if not deleted:
                raise HTTPException(status_code=404, detail="Rule not found")
            return {"status": "deleted"}

        @app.delete("/api/schedule/rules")
        async def clear_rules(
            day: str = "",
            date: str = "",
            authorization: Optional[str] = Header(None),
        ):
            check_api_key(authorization)
            if not adapter.db:
                raise HTTPException(status_code=500, detail="Database not available")
            count = adapter.db.clear_availability_rules(day_of_week=day, specific_date=date)
            return {"cleared": count}

        @app.get("/api/health")
        async def health():
            return {"status": "ok"}

        # --- MCP Server mount ---
        if adapter.mcp_app:
            app.mount(adapter.mcp_path, adapter.mcp_app)
            logger.info(f"MCP server mounted at {adapter.mcp_path}")

        # --- MCP Discovery ---
        @app.get("/.well-known/mcp.json")
        async def mcp_discovery():
            return {
                "mcpServers": {
                    "schedulebot": {
                        "url": f"http://{adapter.host}:{adapter.port}{adapter.mcp_path}",
                        "description": f"Schedule meetings with {adapter.owner_name}",
                        "transport": "streamable-http",
                    }
                }
            }

        config = uvicorn.Config(app, host=self.host, port=self.port, log_level="info")
        self._server = uvicorn.Server(config)
        logger.info(f"Web adapter starting on {self.host}:{self.port}")
        await self._server.serve()

    async def stop(self) -> None:
        if self._server:
            self._server.should_exit = True
            logger.info("Web adapter stopped")

    async def send_message(self, sender_id: str, message: OutgoingMessage) -> None:
        logger.warning("Web adapter does not support push messages")
