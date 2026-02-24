"""Web channel adapter using FastAPI. Includes schedule management API."""

import hmac
import html
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
_web_rate_limiter_cleanup_counter = 0
WEB_RATE_LIMIT = 20  # max requests per window
WEB_RATE_WINDOW = 60  # seconds
WEB_RATE_LIMITER_MAX_KEYS = 10000
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
        owner_email: str = "",
        agent_card=None,
        calendar=None,
        notifier_holder=None,
    ):
        super().__init__(config, on_message)
        self.host = config.get("host", "0.0.0.0")
        port = int(config.get("port", 8080))
        if not (1 <= port <= 65535):
            raise ValueError(f"Invalid web port: {port}. Must be 1-65535.")
        self.port = port
        self.api_key = config.get("api_key", "")
        self.allowed_origins = config.get("allowed_origins", [])
        self.db = db
        self.mcp_app = mcp_app
        self.mcp_path = mcp_path
        self.owner_name = owner_name
        self.owner_email = owner_email
        self.agent_card = agent_card
        self.calendar = calendar
        self.notifier_holder = notifier_holder  # [notifier] mutable list for late binding
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

        adapter = self

        app = FastAPI(title="schedulebot", version="0.1.0")
        origins = adapter.allowed_origins or []
        if "*" in origins and adapter.api_key:
            logger.warning(
                "CORS allow_origins contains '*' while API key is set. "
                "This allows any website to call your schedule API."
            )
        if origins:
            app.add_middleware(
                CORSMiddleware,
                allow_origins=origins,
                allow_methods=["GET", "POST", "DELETE"],
                allow_headers=["Authorization", "Content-Type"],
            )

        # --- Auth helper (extracts header from Request object) ---
        def check_api_key(request: Request):
            if not adapter.api_key:
                raise HTTPException(
                    status_code=403,
                    detail="Schedule API disabled. Set SCHEDULEBOT_API_KEY to enable.",
                )
            auth = request.headers.get("authorization", "")
            if not auth or not hmac.compare_digest(auth.replace("Bearer ", ""), adapter.api_key):
                raise HTTPException(status_code=401, detail="Invalid API key")

        def check_rate_limit(request: Request):
            """Per-IP rate limiting for public endpoints."""
            global _web_rate_limiter_cleanup_counter
            client_ip = request.client.host if request.client else "unknown"
            now = time.time()
            history = _web_rate_limiter.get(client_ip, [])
            history = [t for t in history if now - t < WEB_RATE_WINDOW]
            if len(history) >= WEB_RATE_LIMIT:
                raise HTTPException(status_code=429, detail="Too many requests. Please wait.")
            history.append(now)
            _web_rate_limiter[client_ip] = history
            # Periodic cleanup: evict stale entries to prevent memory leak
            _web_rate_limiter_cleanup_counter += 1
            if _web_rate_limiter_cleanup_counter >= 100:
                _web_rate_limiter_cleanup_counter = 0
                stale = [k for k, v in _web_rate_limiter.items() if not v or now - v[-1] > WEB_RATE_WINDOW]
                for k in stale:
                    del _web_rate_limiter[k]
                # Hard cap: if still too large, drop oldest entries
                if len(_web_rate_limiter) > WEB_RATE_LIMITER_MAX_KEYS:
                    excess = len(_web_rate_limiter) - WEB_RATE_LIMITER_MAX_KEYS
                    for k in list(_web_rate_limiter)[:excess]:
                        del _web_rate_limiter[k]

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
            # Prevent owner impersonation via unauthenticated web endpoint:
            # prefix sender_id to ensure it never matches owner_ids.web
            safe_sender_id = f"web:{req.sender_id}"
            msg = IncomingMessage(
                channel="web",
                sender_id=safe_sender_id,
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
            # Validate day_of_week
            valid_days = {"monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday", ""}
            if req.day_of_week.lower() not in valid_days:
                raise HTTPException(status_code=400, detail=f"Invalid day_of_week: {req.day_of_week}")
            # Validate time format HH:MM
            import re as _re
            if not _re.match(r"^\d{1,2}:\d{2}$", req.start_time):
                raise HTTPException(status_code=400, detail=f"Invalid start_time format: {req.start_time}")
            if not _re.match(r"^\d{1,2}:\d{2}$", req.end_time):
                raise HTTPException(status_code=400, detail=f"Invalid end_time format: {req.end_time}")
            # Validate specific_date format
            if req.specific_date:
                try:
                    from datetime import datetime as _dt
                    _dt.strptime(req.specific_date, "%Y-%m-%d")
                except ValueError:
                    raise HTTPException(status_code=400, detail=f"Invalid date format: {req.specific_date}")
            from ..models import AvailabilityRule
            rule = AvailabilityRule(
                day_of_week=req.day_of_week.lower(),
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

        # --- Self-service cancel ---

        from fastapi.responses import HTMLResponse

        @app.get("/cancel/{cancel_token}")
        async def cancel_page(cancel_token: str, request: Request):
            """Show cancellation confirmation page."""
            check_rate_limit(request)
            if not adapter.db:
                raise HTTPException(status_code=500, detail="Database not available")
            if len(cancel_token) > 64:
                raise HTTPException(status_code=400, detail="Invalid token")
            booking = adapter.db.get_booking_by_cancel_token(cancel_token)
            if not booking:
                return HTMLResponse(
                    "<html><body><h1>Booking not found</h1>"
                    "<p>This link may have expired or the booking was already cancelled.</p>"
                    "</body></html>",
                    status_code=404,
                    headers={"Referrer-Policy": "no-referrer"},
                )
            safe_owner = html.escape(adapter.owner_name)
            safe_slot = html.escape(str(booking.slot))
            safe_token = html.escape(cancel_token)
            return HTMLResponse(
                f"<html><body>"
                f"<h1>Cancel your meeting?</h1>"
                f"<p>Meeting with {safe_owner}: {safe_slot}</p>"
                f'<form method="POST" action="/cancel/{safe_token}">'
                f'<button type="submit">Yes, cancel my booking</button>'
                f"</form>"
                f"</body></html>",
                headers={"Referrer-Policy": "no-referrer"},
            )

        @app.post("/cancel/{cancel_token}")
        async def execute_cancel(cancel_token: str, request: Request):
            """Execute the cancellation."""
            check_rate_limit(request)
            if not adapter.db:
                raise HTTPException(status_code=500, detail="Database not available")
            if len(cancel_token) > 64:
                raise HTTPException(status_code=400, detail="Invalid token")
            booking = adapter.db.get_booking_by_cancel_token(cancel_token)
            if not booking:
                return HTMLResponse(
                    "<html><body><h1>Booking not found</h1>"
                    "<p>This link may have expired or the booking was already cancelled.</p>"
                    "</body></html>",
                    status_code=404,
                    headers={"Referrer-Policy": "no-referrer"},
                )

            # Delete calendar event
            if booking.calendar_event_id and booking.calendar_event_id != "dry-run" and adapter.calendar:
                try:
                    await adapter.calendar.delete_event(booking.calendar_event_id)
                except Exception as e:
                    logger.warning("Could not delete calendar event %s: %s", booking.calendar_event_id, e)

            adapter.db.delete_booking(booking.id)

            # Notify owner
            _notifier = adapter.notifier_holder[0] if adapter.notifier_holder else None
            if _notifier:
                try:
                    from ..models import OutgoingMessage as _OM
                    safe_name = re.sub(r"<[^>]+>", "", booking.guest_name)
                    await _notifier.channel.send_message(
                        _notifier.owner_id,
                        _OM(text=f"Booking cancelled by guest.\n  Guest: {safe_name}\n  Was: {booking.slot}"),
                    )
                except Exception as e:
                    logger.warning("Failed to notify owner about cancellation: %s", e)

            safe_owner = html.escape(adapter.owner_name)
            safe_slot = html.escape(str(booking.slot))
            return HTMLResponse(
                f"<html><body>"
                f"<h1>Booking cancelled</h1>"
                f"<p>Your meeting with {safe_owner} on {safe_slot} has been cancelled.</p>"
                f"</body></html>",
                headers={"Referrer-Policy": "no-referrer"},
            )

        # --- MCP Server mount ---
        if adapter.mcp_app:
            app.mount(adapter.mcp_path, adapter.mcp_app)
            logger.info(f"MCP server mounted at {adapter.mcp_path}")

        # --- Discovery ---
        @app.get("/.well-known/mcp.json")
        async def mcp_discovery():
            base_url = adapter.agent_card.url if adapter.agent_card and adapter.agent_card.url else f"http://{adapter.host}:{adapter.port}"
            return {
                "mcpServers": {
                    "schedulebot": {
                        "url": f"{base_url}{adapter.mcp_path}",
                        "description": f"Schedule meetings with {adapter.owner_name}",
                        "transport": "streamable-http",
                    }
                }
            }

        @app.get("/.well-known/agent.json")
        async def agent_discovery():
            """Agent identity card for agent-to-agent discovery."""
            card = adapter.agent_card
            base_url = card.url if card and card.url else f"http://{adapter.host}:{adapter.port}"

            result = {
                "schema_version": "0.1",
                "name": adapter.owner_name,
                "description": card.description if card and card.description else f"Scheduling agent for {adapter.owner_name}",
                "capabilities": {
                    "scheduling": {
                        "protocol": "mcp",
                        "url": f"{base_url}{adapter.mcp_path}",
                        "transport": "streamable-http",
                        "tools": [
                            "get_services",
                            "get_available_slots",
                            "get_pricing",
                            "book_consultation",
                            "cancel_booking",
                        ],
                        "description": f"Book a meeting with {adapter.owner_name}. "
                            "Use get_available_slots() to see open times, "
                            "then book_consultation() to book.",
                    }
                },
                "contact": {},
            }

            if adapter.owner_email:
                result["contact"]["email"] = adapter.owner_email
            if card and card.organization:
                result["contact"]["organization"] = card.organization
            if card and card.url:
                result["url"] = card.url

            return result

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
