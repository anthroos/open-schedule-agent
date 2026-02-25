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
MAX_TEXT_LENGTH = 500


class WebAdapter(ChannelAdapter):
    """FastAPI-based web adapter. Exposes REST endpoints for scheduling + management API."""

    def __init__(
        self,
        config: dict,
        on_message: Callable[[IncomingMessage], Awaitable[OutgoingMessage]],
        db=None,
        mcp_app=None,
        mcp_path: str = "/mcp",
        mcp_server=None,
        owner_name: str = "Owner",
        owner_email: str = "",
        agent_card=None,
        calendar=None,
        notifier_holder=None,
        services=None,
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
        self.mcp_server = mcp_server
        self.owner_name = owner_name
        self.owner_email = owner_email
        self.agent_card = agent_card
        self.calendar = calendar
        self.notifier_holder = notifier_holder  # [notifier] mutable list for late binding
        self.services = services or []
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

        # Lifespan: start MCP session manager if present
        from contextlib import asynccontextmanager

        @asynccontextmanager
        async def lifespan(app):
            if adapter.mcp_server:
                async with adapter.mcp_server.session_manager.run():
                    yield
            else:
                yield

        app = FastAPI(title="schedulebot", version="0.1.0", lifespan=lifespan)
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
            # Use X-Forwarded-For when behind a reverse proxy, fall back to direct IP
            forwarded = request.headers.get("x-forwarded-for", "")
            if forwarded:
                # Take the first (leftmost) IP — the original client
                client_ip = forwarded.split(",")[0].strip()
            else:
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
            # Validate input lengths to prevent abuse
            if len(req.sender_id) > MAX_SENDER_ID_LENGTH:
                raise HTTPException(status_code=400, detail="sender_id too long")
            if len(req.text) > MAX_TEXT_LENGTH:
                raise HTTPException(status_code=400, detail=f"Message too long (max {MAX_TEXT_LENGTH} characters)")
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
            # Validate time format HH:MM with range check
            import re as _re

            def _valid_time(t: str) -> bool:
                m = _re.match(r"^(\d{1,2}):(\d{2})$", t.strip())
                if not m:
                    return False
                return int(m.group(1)) <= 23 and int(m.group(2)) <= 59

            if not _valid_time(req.start_time):
                raise HTTPException(status_code=400, detail=f"Invalid start_time: {req.start_time}. Use HH:MM (00:00-23:59).")
            if not _valid_time(req.end_time):
                raise HTTPException(status_code=400, detail=f"Invalid end_time: {req.end_time}. Use HH:MM (00:00-23:59).")
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

        from fastapi.responses import HTMLResponse, Response
        import hashlib
        import secrets as _secrets

        # CSRF nonce helpers: HMAC-based, valid for 1 hour
        _CSRF_TTL = 3600

        def _make_csrf_nonce(cancel_token: str) -> str:
            """Generate a time-limited CSRF nonce for the cancel form."""
            ts = str(int(time.time()) // _CSRF_TTL)
            key = (adapter.api_key or "schedulebot-csrf-fallback").encode()
            sig = hmac.new(key, f"{cancel_token}:{ts}".encode(), hashlib.sha256).hexdigest()[:16]
            return sig

        def _verify_csrf_nonce(cancel_token: str, nonce: str) -> bool:
            """Verify CSRF nonce, accepting current and previous time windows."""
            key = (adapter.api_key or "schedulebot-csrf-fallback").encode()
            now_ts = int(time.time()) // _CSRF_TTL
            for ts in (str(now_ts), str(now_ts - 1)):
                expected = hmac.new(key, f"{cancel_token}:{ts}".encode(), hashlib.sha256).hexdigest()[:16]
                if hmac.compare_digest(expected, nonce):
                    return True
            return False

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
                    media_type="text/html; charset=utf-8",
                    headers={"Referrer-Policy": "no-referrer"},
                )
            safe_owner = html.escape(adapter.owner_name)
            safe_slot = html.escape(str(booking.slot))
            safe_token = html.escape(cancel_token)
            csrf_nonce = _make_csrf_nonce(cancel_token)
            return HTMLResponse(
                f"<html><body>"
                f"<h1>Cancel your meeting?</h1>"
                f"<p>Meeting with {safe_owner}: {safe_slot}</p>"
                f'<form method="POST" action="/cancel/{safe_token}">'
                f'<input type="hidden" name="csrf" value="{csrf_nonce}">'
                f'<button type="submit">Yes, cancel my booking</button>'
                f"</form>"
                f"</body></html>",
                media_type="text/html; charset=utf-8",
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
            # Verify CSRF nonce
            form = await request.form()
            csrf_nonce = form.get("csrf", "")
            if not csrf_nonce or not _verify_csrf_nonce(cancel_token, csrf_nonce):
                raise HTTPException(status_code=403, detail="Invalid or expired form. Please reload the cancel page.")
            booking = adapter.db.get_booking_by_cancel_token(cancel_token)
            if not booking:
                return HTMLResponse(
                    "<html><body><h1>Booking not found</h1>"
                    "<p>This link may have expired or the booking was already cancelled.</p>"
                    "</body></html>",
                    status_code=404,
                    media_type="text/html; charset=utf-8",
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
                media_type="text/html; charset=utf-8",
                headers={"Referrer-Policy": "no-referrer"},
            )

        # --- MCP Server mount (public — agents must be able to discover and book) ---
        # Mount the raw ASGI handler, not the full Starlette sub-app,
        # because the parent lifespan manages the session manager lifecycle.
        if adapter.mcp_server:
            sm = adapter.mcp_server.session_manager
            app.mount(adapter.mcp_path, sm.handle_request)
            logger.info(f"MCP server mounted at {adapter.mcp_path} (public, no auth)")

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

        # --- Agent Card landing page & QR code ---

        @app.get("/agent", response_class=HTMLResponse)
        async def agent_page():
            """Human-readable agent business card page."""
            card = adapter.agent_card
            if not card or not card.enabled:
                return HTMLResponse(
                    "<html><body style='font-family:sans-serif;max-width:600px;"
                    "margin:40px auto;padding:0 20px'>"
                    "<h1>Not Available</h1>"
                    "<p>Agent card is not enabled for this instance.</p>"
                    "</body></html>",
                    status_code=404,
                )

            base_url = card.url or f"http://{adapter.host}:{adapter.port}"
            safe_name = html.escape(adapter.owner_name)
            safe_desc = html.escape(
                card.description or f"Scheduling agent for {adapter.owner_name}"
            )
            safe_org = html.escape(card.organization) if card.organization else ""
            mcp_url = f"{base_url}{adapter.mcp_path}"
            safe_mcp = html.escape(mcp_url)

            # Services section
            services_html = ""
            if adapter.services:
                items = ""
                for s in adapter.services:
                    sname = html.escape(s.name or s.slug)
                    sdur = s.duration_minutes
                    sprice = "Free" if s.price == 0 else f"{s.currency} {s.price:.2f}"
                    items += f"<li><strong>{sname}</strong> &mdash; {sdur} min, {sprice}</li>"
                services_html = f"<h2>Services</h2><ul>{items}</ul>"

            # QR section (only if library installed)
            qr_html = ""
            try:
                import qrcode as _qr  # noqa: F401
                qr_html = (
                    '<div class="qr">'
                    '<img src="/agent/qr" alt="QR Code" width="200" height="200">'
                    '<p class="dim">Scan to open this page</p>'
                    "</div>"
                )
            except ImportError:
                pass

            # MCP config slug
            slug = re.sub(r"[^a-z0-9]+", "-", adapter.owner_name.lower()).strip("-")
            mcp_snippet = (
                '{\n'
                '  "mcpServers": {\n'
                f'    "{html.escape(slug)}-schedule": {{\n'
                f'      "url": "{safe_mcp}"\n'
                '    }\n'
                '  }\n'
                '}'
            )

            prompt_snippet = (
                f"Name: {safe_name}\n"
                f"MCP: {safe_mcp}"
            )

            page = (
                "<!DOCTYPE html>\n<html lang='en'><head>"
                "<meta charset='utf-8'>"
                "<meta name='viewport' content='width=device-width,initial-scale=1'>"
                f"<title>{safe_name} &mdash; Agent Card</title>"
                "<style>"
                "*{margin:0;padding:0;box-sizing:border-box}"
                "body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;"
                "max-width:600px;margin:40px auto;padding:0 20px;color:#1a1a1a;line-height:1.6}"
                "h1{font-size:1.8em;margin-bottom:4px}"
                "h2{font-size:1.2em;margin:24px 0 8px;color:#444}"
                ".org{color:#666;font-size:0.95em}"
                ".desc{margin:12px 0 20px}"
                ".dim{color:#888;font-size:0.9em}"
                "ul{padding-left:20px}li{margin:6px 0}"
                ".features{margin:16px 0 20px}"
                ".features li{color:#444;font-size:0.95em}"
                ".snippet{background:#f5f5f5;border:1px solid #ddd;border-radius:6px;"
                "padding:12px 16px;margin:8px 0 16px;font-family:'SF Mono','Fira Code',"
                "monospace;font-size:0.85em;white-space:pre-wrap;word-break:break-all;"
                "position:relative}"
                ".copy-btn{position:absolute;top:8px;right:8px;background:#fff;"
                "border:1px solid #ccc;border-radius:4px;padding:4px 10px;"
                "cursor:pointer;font-size:0.8em}"
                ".copy-btn:hover{background:#eee}"
                ".qr{text-align:center;margin:24px 0}"
                ".qr img{border:1px solid #eee;border-radius:8px}"
                "a{color:#0066cc;text-decoration:none}a:hover{text-decoration:underline}"
                ".links{margin:20px 0}.links a{display:inline-block;margin-right:16px}"
                "hr{border:none;border-top:1px solid #eee;margin:24px 0}"
                "</style></head><body>"
                f"<h1>{safe_name}</h1>"
                + (f"<p class='org'>{safe_org}</p>" if safe_org else "")
                + f"<p class='desc'>{safe_desc}</p>"
                + services_html
                + "<h2>Features</h2>"
                "<ul class='features'>"
                "<li>Multi-calendar &mdash; checks availability across multiple Google accounts</li>"
                "<li>Guest timezone &mdash; shows slots in guest's local time</li>"
                "<li>Google Meet links &mdash; auto-generated for every booking</li>"
                "<li>Self-service cancel &mdash; guests can cancel via link</li>"
                "<li>MCP &amp; agent discovery &mdash; other AI agents book automatically</li>"
                "</ul>"
                + "<h2>Add to your AI agent</h2>"
                f"<div class='snippet' id='prompt-snippet'>{prompt_snippet}"
                "<button class='copy-btn' onclick=\"copyEl('prompt-snippet')\">Copy</button></div>"
                "<h2>Claude Code / Cursor config</h2>"
                f"<div class='snippet' id='mcp-snippet'>{mcp_snippet}"
                "<button class='copy-btn' onclick=\"copyEl('mcp-snippet')\">Copy</button></div>"
                + qr_html
                + "<hr><div class='links'>"
                "<a href='/.well-known/agent.json'>agent.json</a>"
                "<a href='/.well-known/mcp.json'>mcp.json</a>"
                "</div>"
                "<script>"
                "function copyEl(id){"
                "var el=document.getElementById(id);"
                "var btn=el.querySelector('.copy-btn');"
                "var t=el.textContent.replace(btn.textContent,'').trim();"
                "navigator.clipboard.writeText(t).then(function(){"
                "btn.textContent='Copied!';"
                "setTimeout(function(){btn.textContent='Copy'},1500)"
                "}).catch(function(){"
                "var a=document.createElement('textarea');a.value=t;"
                "document.body.appendChild(a);a.select();"
                "document.execCommand('copy');document.body.removeChild(a);"
                "btn.textContent='Copied!';"
                "setTimeout(function(){btn.textContent='Copy'},1500)"
                "})}"
                "</script></body></html>"
            )
            return HTMLResponse(page)

        @app.get("/agent/qr")
        async def agent_qr():
            """QR code image pointing to the agent card page."""
            card = adapter.agent_card
            if not card or not card.enabled:
                raise HTTPException(status_code=404, detail="Agent card is not enabled")

            try:
                import qrcode
                from io import BytesIO
            except ImportError:
                raise HTTPException(
                    status_code=501,
                    detail="QR code requires qrcode[pil]. "
                    "Install: pip install 'schedulebot[agent-card]'",
                )

            base_url = card.url or f"http://{adapter.host}:{adapter.port}"
            target_url = f"{base_url}/agent"

            qr = qrcode.QRCode(
                version=None,
                error_correction=qrcode.constants.ERROR_CORRECT_M,
                box_size=10,
                border=4,
            )
            qr.add_data(target_url)
            qr.make(fit=True)
            img = qr.make_image(fill_color="black", back_color="white")

            buf = BytesIO()
            img.save(buf, format="PNG")
            buf.seek(0)

            return Response(
                content=buf.getvalue(),
                media_type="image/png",
                headers={
                    "Cache-Control": "public, max-age=3600",
                    "Content-Disposition": 'inline; filename="agent-qr.png"',
                },
            )

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
