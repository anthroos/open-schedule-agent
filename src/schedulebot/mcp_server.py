"""MCP server for schedulebot — exposes scheduling tools for AI agents."""

from __future__ import annotations

import logging
import secrets
from datetime import datetime, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

from .config import Config
from .core.availability import AvailabilityEngine
from .calendar.base import CalendarProvider
from .database import Database
from .models import Booking, TimeSlot

logger = logging.getLogger(__name__)


def _build_cancel_url(config: Config, cancel_token: str) -> str:
    """Build a cancel URL from config. Returns empty string if web is not available."""
    if not cancel_token:
        return ""
    base = ""
    if config.agent_card and config.agent_card.url:
        base = config.agent_card.url.rstrip("/")
    elif "web" in config.channels and config.channels["web"].enabled:
        web_cfg = config.channels["web"]
        host = web_cfg.get("host", "0.0.0.0")
        port = web_cfg.get("port", 8080)
        if host in ("0.0.0.0", "::"):
            return ""
        base = f"http://{host}:{port}"
    if not base:
        return ""
    return f"{base}/cancel/{cancel_token}"


def create_mcp_server(
    config: Config,
    availability: AvailabilityEngine,
    calendar: CalendarProvider,
    db: Database,
    notifier=None,
):
    """Create and configure the MCP server with scheduling tools."""
    from mcp.server.fastmcp import FastMCP

    mcp = FastMCP(
        "schedulebot",
        instructions=f"Schedule meetings with {config.owner.name}. "
        f"Timezone: {config.availability.timezone}. "
        f"Use get_available_slots() to see open times, then book_consultation() to book.",
        streamable_http_path="/",
        host="0.0.0.0",  # Disable auto DNS rebinding protection (runs behind reverse proxy)
    )

    tz = ZoneInfo(config.availability.timezone)

    @mcp.tool()
    async def get_services() -> list[dict]:
        """List available consultation services with duration, pricing, and description."""
        if not config.services:
            return [{
                "name": "Meeting",
                "slug": "meeting",
                "duration_minutes": config.availability.meeting_duration_minutes,
                "price": 0,
                "currency": "USD",
                "description": f"Meeting with {config.owner.name}",
            }]
        return [
            {
                "name": s.name,
                "slug": s.slug,
                "duration_minutes": s.duration_minutes,
                "price": s.price,
                "currency": s.currency,
                "description": s.description,
            }
            for s in config.services
        ]

    @mcp.tool()
    async def get_available_slots(
        date: Optional[str] = None,
        service: Optional[str] = None,
    ) -> list[dict]:
        """Get available time slots for booking a consultation.

        Args:
            date: Specific date in YYYY-MM-DD format. Returns slots for that day only.
                  If not given, returns slots for the next 14 days.
            service: Service slug to filter by duration. Use get_services() to see available slugs.
        """
        from_date = None
        if date:
            from_date = datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=tz)

        slots = await availability.get_available_slots(from_date)

        # Filter to single day when date is specified
        if from_date:
            day_end = from_date + timedelta(days=1)
            slots = [s for s in slots if s.start >= from_date and s.start < day_end]

        if service:
            svc = next((s for s in config.services if s.slug == service), None)
            if svc and svc.duration_minutes != config.availability.meeting_duration_minutes:
                duration = timedelta(minutes=svc.duration_minutes)
                slots = [s for s in slots if (s.end - s.start) >= duration]

        return [
            {
                "start": s.start.isoformat(),
                "end": s.end.isoformat(),
                "display": str(s),
            }
            for s in slots
        ]

    @mcp.tool()
    async def get_pricing() -> dict:
        """Get detailed pricing information for all consultation services."""
        services = config.services or []
        return {
            "owner": config.owner.name,
            "timezone": config.availability.timezone,
            "services": [
                {
                    "name": s.name,
                    "slug": s.slug,
                    "duration_minutes": s.duration_minutes,
                    "price": s.price,
                    "currency": s.currency,
                    "description": s.description,
                    "formatted_price": "Free" if s.price == 0 else f"{s.currency} {s.price:.2f}",
                }
                for s in services
            ],
        }

    @mcp.tool()
    async def book_consultation(
        date: str,
        time: str,
        client_name: str,
        client_email: str,
        service: Optional[str] = None,
    ) -> dict:
        """Book a consultation at the specified date and time. Creates a Google Calendar event with Meet link.

        Args:
            date: Date in YYYY-MM-DD format.
            time: Time in HH:MM format (24-hour).
            client_name: Full name of the person booking.
            client_email: Email address of the person booking.
            service: Optional service slug. Defaults to standard meeting duration.
        """
        # Input length validation
        if len(client_name) > 100:
            return {"error": "client_name too long (max 100 characters)."}
        if len(client_email) > 254:
            return {"error": "client_email too long (max 254 characters)."}
        if service and len(service) > 50:
            return {"error": "service slug too long (max 50 characters)."}

        # Sanitize text inputs (strip HTML)
        import re
        client_name = re.sub(r"<[^>]+>", "", client_name)

        # Basic email format check
        if not re.match(r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$", client_email):
            return {"error": "Invalid email format."}

        duration_minutes = config.availability.meeting_duration_minutes
        if service:
            svc = next((s for s in config.services if s.slug == service), None)
            if svc:
                duration_minutes = svc.duration_minutes
            else:
                return {"error": f"Unknown service: {service}. Use get_services() to see available options."}

        try:
            start = datetime.strptime(f"{date} {time}", "%Y-%m-%d %H:%M").replace(tzinfo=tz)
        except ValueError:
            return {"error": "Invalid date/time format. Use YYYY-MM-DD for date and HH:MM for time."}

        # Date bounds: not in the past, not too far ahead
        now = datetime.now(tz)
        if start < now:
            return {"error": "Cannot book in the past."}
        max_ahead = timedelta(days=config.availability.max_days_ahead)
        if start > now + max_ahead:
            return {"error": f"Cannot book more than {config.availability.max_days_ahead} days ahead."}

        end = start + timedelta(minutes=duration_minutes)
        slot = TimeSlot(start=start, end=end)

        # Verify slot is available
        day_start = datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=tz)
        available_slots = await availability.get_available_slots(day_start)
        slot_available = any(
            s.start <= start and s.end >= end for s in available_slots
        )
        if not slot_available:
            return {"error": "Requested time slot is not available. Use get_available_slots() to see open times."}

        # Atomic slot reservation to prevent double-booking
        reservation_id = secrets.token_urlsafe(16)
        if not db.reserve_slot(start, end, reservation_id):
            return {"error": "This slot was just booked by someone else. Use get_available_slots() for current openings."}

        cancel_token = secrets.token_urlsafe(32)

        if config.dry_run:
            booking = Booking(
                id=reservation_id,
                guest_name=client_name,
                guest_channel="mcp",
                guest_sender_id=client_email,
                guest_email=client_email,
                slot=slot,
                calendar_event_id="dry-run",
                meet_link="https://meet.google.com/dry-run",
                cancel_token=cancel_token,
            )
            db.finalize_booking(booking)
            result = {
                "status": "confirmed (dry-run)",
                "booking_id": booking.id,
                "datetime": start.isoformat(),
                "duration_minutes": duration_minutes,
                "meet_link": booking.meet_link,
            }
            cancel_url = _build_cancel_url(config, cancel_token)
            if cancel_url:
                result["cancel_url"] = cancel_url
            return result

        try:
            event = await calendar.create_event(
                summary=f"Meeting with {client_name}",
                start=start,
                end=end,
                description=f"Booked via MCP. Email: {client_email}",
                attendee_emails=[client_email],
                create_meet_link=config.calendar.create_meet_link,
            )
        except Exception as e:
            db.release_slot(reservation_id)
            logger.error(f"Failed to create calendar event via MCP: {e}")
            return {"error": "Failed to create calendar event. Please try again."}

        booking = Booking(
            id=reservation_id,
            guest_name=client_name,
            guest_channel="mcp",
            guest_sender_id=client_email,
            guest_email=client_email,
            slot=slot,
            calendar_event_id=event.get("event_id"),
            meet_link=event.get("meet_link"),
            notes=f"Service: {service}" if service else "",
            cancel_token=cancel_token,
        )
        db.finalize_booking(booking)

        # Notify owner (notifier may be a list holder [instance] for late binding)
        _notifier = notifier[0] if isinstance(notifier, list) else notifier
        if _notifier:
            try:
                await _notifier.notify_new_booking(booking)
            except Exception as e:
                logger.warning("Failed to notify owner about MCP booking: %s", e)

        result = {
            "status": "confirmed",
            "booking_id": booking.id,
            "datetime": start.isoformat(),
            "duration_minutes": duration_minutes,
            "client_name": client_name,
            "client_email": client_email,
        }
        if booking.meet_link:
            result["meet_link"] = booking.meet_link
        cancel_url = _build_cancel_url(config, cancel_token)
        if cancel_url:
            result["cancel_url"] = cancel_url
        return result

    @mcp.tool()
    async def cancel_booking(booking_id: str) -> dict:
        """Cancel a booking by its ID. Removes from database and attempts to delete the calendar event.

        Args:
            booking_id: The booking ID returned from book_consultation.
        """
        if not booking_id or len(booking_id) > 30:
            return {"error": "Invalid booking ID format."}

        booking = db.get_booking_by_id(booking_id)
        if not booking:
            return {"error": "Booking not found."}

        calendar_deleted = False
        if booking.calendar_event_id and booking.calendar_event_id != "dry-run":
            try:
                await calendar.delete_event(booking.calendar_event_id)
                calendar_deleted = True
            except Exception as e:
                logger.warning(f"Could not delete calendar event {booking.calendar_event_id}: {e}")

        db.delete_booking(booking_id)

        result = {
            "status": "cancelled",
            "booking_id": booking_id,
            "was_scheduled": str(booking.slot),
        }
        if not calendar_deleted and booking.calendar_event_id and booking.calendar_event_id != "dry-run":
            result["note"] = "Booking removed from database but calendar event could not be deleted automatically."
        return result

    return mcp
