"""Web channel adapter using FastAPI. Includes schedule management API."""

import logging
import re
import time
from collections.abc import Awaitable, Callable
from typing import Optional

from ..models import IncomingMessage, OutgoingMessage
from .base import ChannelAdapter

logger = logging.getLogger(__name__)

# Web endpoint rate limiter: IP -> list of timestamps
_web_rate_limiter: dict[str, list[float]] = {}
WEB_RATE_LIMIT = 20  # max requests per window
WEB_RATE_WINDOW = 60  # seconds
MAX_SENDER_ID_LENGTH = 64


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
        self.allowed_origins = config.get("allowed_origins", [])
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
            from fastapi import FastAPI, HTTPException, Request
            from fastapi.middleware.cors import CORSMiddleware
            from pydantic import BaseModel
            import uvicorn
        except ImportError:
            raise ImportError(
                "fastapi/uvicorn not installed. Run: pip install schedulebot[web]"
            )

        app = FastAPI(title="schedulebot", version="0.1.0")
        origins = adapter.allowed_origins or []
        app.add_middleware(
            CORSMiddleware,
            allow_origins=origins,
            allow_methods=["GET", "POST", "DELETE"],
            allow_headers=["Authorization", "Content-Type"],
        )

        adapter = self

        # --- Auth helper (extracts header from Request object) ---
        def check_api_key(request: Request):
            if adapter.api_key:
                auth = request.headers.get("authorization", "")
                if not auth or auth.replace("Bearer ", "") != adapter.api_key:
                    raise HTTPException(status_code=401, detail="Invalid API key")

        def check_rate_limit(request: Request):
            """Per-IP rate limiting for public endpoints."""
            client_ip = request.client.host if request.client else "unknown"
            now = time.time()
            history = _web_rate_limiter.get(client_ip, [])
            history = [t for t in history if now - t < WEB_RATE_WINDOW]
            if len(history) >= WEB_RATE_LIMIT:
                raise HTTPException(status_code=429, detail="Too many requests. Please wait.")
            history.append(now)
            _web_rate_limiter[client_ip] = history

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
        async def handle_message(req: MessageRequest, request: Request):
            check_rate_limit(request)
            # Validate sender_id length to prevent storage abuse
            if len(req.sender_id) > MAX_SENDER_ID_LENGTH:
                raise HTTPException(status_code=400, detail="sender_id too long")
            msg = IncomingMessage(
                channel="web",
                sender_id=req.sender_id,
                sender_name=req.sender_name[:100],
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

        @app.get("/api/schedule")
        async def get_schedule(request: Request):
            check_api_key(request)
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
        async def add_rule(req: RuleRequest, request: Request):
            check_api_key(request)
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
        async def delete_rule(rule_id: int, request: Request):
            check_api_key(request)
            if not adapter.db:
                raise HTTPException(status_code=500, detail="Database not available")
            deleted = adapter.db.delete_availability_rule(rule_id)
            if not deleted:
                raise HTTPException(status_code=404, detail="Rule not found")
            return {"status": "deleted"}

        @app.delete("/api/schedule/rules")
        async def clear_rules(request: Request, day: str = "", date: str = ""):
            check_api_key(request)
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
