"""Core scheduling engine — channel-agnostic, with owner/guest dual mode."""

from __future__ import annotations

import logging
import re
import secrets
import time
import uuid
from datetime import datetime

from ..calendar.base import CalendarProvider
from ..config import Config
from ..core.availability import AvailabilityEngine
from ..database import Database
from ..llm.base import LLMProvider
from ..llm.prompts import (
    build_owner_prompt,
    build_owner_prompt_tools,
    build_system_prompt,
    build_system_prompt_tools,
)
from ..llm.tools import GUEST_TOOLS, OWNER_TOOLS
from ..models import (
    AvailabilityRule,
    Booking,
    Conversation,
    ConversationState,
    IncomingMessage,
    OutgoingMessage,
    TimeSlot,
)

logger = logging.getLogger(__name__)

# --- Input validation constants ---
MAX_MESSAGE_LENGTH = 300
RATE_LIMIT_MESSAGES = 8
RATE_LIMIT_WINDOW = 60  # seconds
MAX_ATTENDEE_EMAILS = 2
EMAIL_RE = re.compile(r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$")
INJECTION_PATTERNS = [
    re.compile(r"ignore\s+(all\s+)?previous\s+instructions", re.IGNORECASE),
    re.compile(r"you\s+are\s+now\s+(a|an)\s+", re.IGNORECASE),
    re.compile(r"system\s*:\s*", re.IGNORECASE),
    re.compile(r"<\s*/?system\s*>", re.IGNORECASE),
    re.compile(r"\[INST\]", re.IGNORECASE),
    re.compile(r"<<\s*SYS\s*>>", re.IGNORECASE),
]

# In-memory rate limiter: sender_id -> list of timestamps
_rate_limiter: dict[str, list[float]] = {}


class SchedulingEngine:
    """Main engine that processes messages and manages the scheduling flow.

    Routes messages to owner mode (schedule management) or guest mode (booking)
    based on sender_id matching config.owner.owner_ids.
    """

    def __init__(
        self,
        config: Config,
        calendar: CalendarProvider,
        llm: LLMProvider,
        db: Database,
        notifier=None,
    ):
        self.config = config
        self.calendar = calendar
        self.llm = llm
        self.db = db
        self.notifier = notifier
        self.availability = AvailabilityEngine(config.availability, calendar, db)

    def _is_owner(self, channel: str, sender_id: str) -> bool:
        """Check if the sender is the owner."""
        owner_id = self.config.owner.owner_ids.get(channel, "")
        return owner_id != "" and owner_id == sender_id

    async def handle_message(self, msg: IncomingMessage) -> OutgoingMessage:
        """Process an incoming message. Routes to owner or guest flow."""
        if self._is_owner(msg.channel, msg.sender_id):
            return await self._handle_owner_message(msg)

        # Guest input validation (zero-token cost)
        rejection = self._validate_guest_input(msg)
        if rejection:
            return OutgoingMessage(text=rejection)

        return await self._handle_guest_message(msg)

    # --- Input validation ---

    def _validate_guest_input(self, msg: IncomingMessage) -> str | None:
        """Pre-LLM validation for guest messages. Returns rejection text or None."""
        text = msg.text.strip()

        # Skip validation for commands
        if text.startswith("/"):
            return None

        # Message length
        if len(text) > MAX_MESSAGE_LENGTH:
            return f"Please keep your message under {MAX_MESSAGE_LENGTH} characters."

        # Rate limit
        now = time.time()
        history = _rate_limiter.get(msg.sender_id, [])
        history = [t for t in history if now - t < RATE_LIMIT_WINDOW]
        if len(history) >= RATE_LIMIT_MESSAGES:
            return "You're sending messages too fast. Please wait a minute."
        history.append(now)
        _rate_limiter[msg.sender_id] = history

        # Prompt injection
        for pattern in INJECTION_PATTERNS:
            if pattern.search(text):
                logger.warning(f"Injection attempt from {msg.sender_id}: {text[:50]}")
                return "I can only help with scheduling meetings. How can I help you book a time?"

        return None

    @staticmethod
    def _validate_email(email: str) -> bool:
        """Validate email format."""
        return bool(EMAIL_RE.match(email))

    # ──────────────────────────────────────────────
    # OWNER MODE: schedule management
    # ──────────────────────────────────────────────

    async def _handle_owner_message(self, msg: IncomingMessage) -> OutgoingMessage:
        """Owner is managing their schedule. Routes to tool-use or text path."""
        # Quick commands without LLM
        text_lower = msg.text.strip().lower()
        if text_lower in ("/schedule", "/rules", "/show"):
            summary = self.db.format_availability_summary()
            return OutgoingMessage(text=summary)

        if text_lower == "/clear":
            count = self.db.clear_availability_rules()
            return OutgoingMessage(text=f"Cleared {count} availability rules.")

        # Get or create conversation
        conv = self.db.get_conversation(msg.sender_id)
        if not conv:
            conv = Conversation(sender_id=msg.sender_id, channel=msg.channel)
        conv._mode = "owner"

        if text_lower in ("/start", "/cancel"):
            self.db.delete_conversation(msg.sender_id)
            conv = Conversation(sender_id=msg.sender_id, channel=msg.channel)
            conv._mode = "owner"

        conv.add_message("user", msg.text)

        # Branch: tool-use path (Anthropic) vs text-tag path (other providers)
        if hasattr(self.llm, "chat_with_tools"):
            response_text = await self._handle_owner_message_tools(conv)
        else:
            response_text = await self._handle_owner_message_text(conv)

        conv.add_message("assistant", response_text)
        self.db.save_conversation(conv)

        return OutgoingMessage(text=response_text)

    async def _handle_owner_message_tools(self, conv: Conversation) -> str:
        """Owner mode via Anthropic tool use. Returns clean text for user."""
        rules_summary = self.db.format_availability_summary()
        system_prompt = build_owner_prompt_tools(
            owner_name=self.config.owner.name,
            current_rules_summary=rules_summary,
            booking_links=self.config.booking_links.links,
        )

        # Build API messages from conversation (only user/assistant text)
        api_messages = self._build_api_messages(conv.messages)

        # Tool-use loop: max 5 iterations
        for _ in range(5):
            try:
                result = await self.llm.chat_with_tools(
                    system_prompt, api_messages, OWNER_TOOLS
                )
            except Exception as e:
                logger.error(f"LLM tool call failed (owner): {e}")
                return "Sorry, LLM error. Use /schedule to view rules or /clear to reset."

            if not result.tool_calls:
                # No tools called — LLM produced final text
                return result.text

            # Execute each tool call and collect results
            tool_results = []
            for tc in result.tool_calls:
                output = self._execute_tool(tc.name, tc.input)
                tool_results.append({"tool_use_id": tc.id, "content": output})
                logger.info(f"Tool {tc.name}({tc.input}) → {output}")

            # Build assistant message with text + tool_use blocks
            assistant_content = []
            if result.text:
                assistant_content.append({"type": "text", "text": result.text})
            for tc in result.tool_calls:
                assistant_content.append({
                    "type": "tool_use",
                    "id": tc.id,
                    "name": tc.name,
                    "input": tc.input,
                })
            api_messages.append({"role": "assistant", "content": assistant_content})

            # Build tool_result message
            tool_result_content = []
            for tr in tool_results:
                tool_result_content.append({
                    "type": "tool_result",
                    "tool_use_id": tr["tool_use_id"],
                    "content": tr["content"],
                })
            api_messages.append({"role": "user", "content": tool_result_content})

        # Fallback if loop exhausted
        return result.text if result.text else "Done. Use /schedule to see your current rules."

    async def _handle_owner_message_text(self, conv: Conversation) -> str:
        """Owner mode via text-based action tags (legacy). Returns clean text."""
        rules_summary = self.db.format_availability_summary()
        system_prompt = build_owner_prompt(
            owner_name=self.config.owner.name,
            current_rules_summary=rules_summary,
            booking_links=self.config.booking_links.links,
        )

        try:
            response_text = await self.llm.chat(system_prompt, conv.messages)
        except Exception as e:
            logger.error(f"LLM call failed (owner): {e}")
            return "Sorry, LLM error. Use /schedule to view rules or /clear to reset."

        # Parse and execute owner actions
        response_text = self._execute_owner_actions(response_text)

        # Strip action tags from response
        clean_text = re.sub(
            r"\[(?:ADD_RULE|BLOCK_RULE|CLEAR_RULES|CLEAR_ALL|SHOW_RULES)[^\]]*\]", "", response_text
        )
        return clean_text.strip()

    def _execute_tool(self, name: str, params: dict) -> str:
        """Execute a single owner tool call. Returns result text."""
        if name == "add_rule":
            rule = AvailabilityRule(
                day_of_week=params.get("day", ""),
                specific_date=params.get("date", ""),
                start_time=params.get("start", ""),
                end_time=params.get("end", ""),
                is_blocked=False,
            )
            rule_id = self.db.add_availability_rule(rule)
            target = rule.day_of_week or rule.specific_date
            return f"Added availability rule #{rule_id}: {target} {rule.start_time}-{rule.end_time}"

        if name == "block_time":
            rule = AvailabilityRule(
                day_of_week=params.get("day", ""),
                specific_date=params.get("date", ""),
                start_time=params.get("start", ""),
                end_time=params.get("end", ""),
                is_blocked=True,
            )
            rule_id = self.db.add_availability_rule(rule)
            target = rule.day_of_week or rule.specific_date
            return f"Blocked #{rule_id}: {target} {rule.start_time}-{rule.end_time}"

        if name == "clear_rules":
            count = self.db.clear_availability_rules(
                day_of_week=params.get("day", ""),
                specific_date=params.get("date", ""),
            )
            target = params.get("day", "") or params.get("date", "")
            return f"Cleared {count} rules for {target}"

        if name == "clear_all":
            count = self.db.clear_availability_rules()
            return f"Cleared all {count} rules"

        if name == "show_rules":
            return self.db.format_availability_summary()

        return f"Unknown tool: {name}"

    def _build_api_messages(self, messages: list[dict[str, str]]) -> list[dict]:
        """Convert conversation messages to Anthropic API format."""
        api_msgs = []
        for m in messages:
            api_msgs.append({"role": m["role"], "content": m["content"]})
        return api_msgs

    def _execute_owner_actions(self, response: str) -> str:
        """Parse and execute action tags from LLM response."""
        actions_taken = []

        # ADD_RULE:day=monday,start=10:00,end=18:00
        # ADD_RULE:date=2026-02-20,start=10:00,end=14:00
        for match in re.finditer(r"\[ADD_RULE:([^\]]+)\]", response):
            params = self._parse_params(match.group(1))
            rule = AvailabilityRule(
                day_of_week=params.get("day", ""),
                specific_date=params.get("date", ""),
                start_time=params.get("start", ""),
                end_time=params.get("end", ""),
                is_blocked=False,
            )
            if rule.start_time and rule.end_time:
                rule_id = self.db.add_availability_rule(rule)
                target = rule.day_of_week or rule.specific_date
                actions_taken.append(f"Added: {target} {rule.start_time}-{rule.end_time}")
                logger.info(f"Added availability rule #{rule_id}: {target} {rule.start_time}-{rule.end_time}")

        # BLOCK_RULE:day=tuesday,start=14:30,end=23:59
        for match in re.finditer(r"\[BLOCK_RULE:([^\]]+)\]", response):
            params = self._parse_params(match.group(1))
            rule = AvailabilityRule(
                day_of_week=params.get("day", ""),
                specific_date=params.get("date", ""),
                start_time=params.get("start", ""),
                end_time=params.get("end", ""),
                is_blocked=True,
            )
            if rule.start_time and rule.end_time:
                rule_id = self.db.add_availability_rule(rule)
                target = rule.day_of_week or rule.specific_date
                actions_taken.append(f"Blocked: {target} {rule.start_time}-{rule.end_time}")
                logger.info(f"Added block rule #{rule_id}: {target} {rule.start_time}-{rule.end_time}")

        # CLEAR_RULES:day=monday  or  CLEAR_RULES:date=2026-02-20
        for match in re.finditer(r"\[CLEAR_RULES:([^\]]+)\]", response):
            params = self._parse_params(match.group(1))
            count = self.db.clear_availability_rules(
                day_of_week=params.get("day", ""),
                specific_date=params.get("date", ""),
            )
            target = params.get("day", "") or params.get("date", "")
            actions_taken.append(f"Cleared {count} rules for {target}")

        # CLEAR_ALL
        if "[CLEAR_ALL]" in response:
            count = self.db.clear_availability_rules()
            actions_taken.append(f"Cleared all {count} rules")

        # SHOW_RULES
        if "[SHOW_RULES]" in response:
            summary = self.db.format_availability_summary()
            response = response.replace("[SHOW_RULES]", f"\n{summary}")

        return response

    def _parse_params(self, params_str: str) -> dict[str, str]:
        """Parse 'key=value,key=value' into a dict."""
        result = {}
        for part in params_str.split(","):
            if "=" in part:
                k, v = part.strip().split("=", 1)
                result[k.strip()] = v.strip()
        return result

    # ──────────────────────────────────────────────
    # GUEST MODE: booking
    # ──────────────────────────────────────────────

    async def _handle_guest_message(self, msg: IncomingMessage) -> OutgoingMessage:
        """Guest is trying to book a meeting. Routes to tool-use or text path."""
        conv = self.db.get_conversation(msg.sender_id)
        if not conv:
            conv = Conversation(sender_id=msg.sender_id, channel=msg.channel)

        # Handle /cancel command
        if msg.text.strip().lower() in ("/cancel", "/start"):
            if msg.text.strip().lower() == "/cancel":
                self.db.delete_conversation(msg.sender_id)
                return OutgoingMessage(text="Scheduling cancelled. Send a message anytime to start over.")
            self.db.delete_conversation(msg.sender_id)
            conv = Conversation(sender_id=msg.sender_id, channel=msg.channel)

        conv.add_message("user", msg.text)

        # Get available slots
        try:
            slots = await self.availability.get_available_slots()
        except Exception as e:
            logger.error(f"Failed to get available slots: {e}")
            slots = []

        # Branch: tool-use path (Anthropic) vs text-tag path (other providers)
        if hasattr(self.llm, "chat_with_tools"):
            result = await self._handle_guest_message_tools(conv, slots)
        else:
            result = await self._handle_guest_message_text(conv, slots)

        self.db.save_conversation(conv)
        return result

    async def _handle_guest_message_tools(
        self, conv: Conversation, slots: list[TimeSlot]
    ) -> OutgoingMessage:
        """Guest mode via Anthropic tool use."""
        system_prompt = build_system_prompt_tools(
            owner_name=self.config.owner.name,
            slots=slots,
            conversation_state=conv.state,
            guest_name=conv.guest_name,
            guest_email=conv.guest_email,
            guest_topic=conv.guest_topic,
            guest_timezone=conv.guest_timezone,
            owner_timezone=self.config.availability.timezone,
        )

        api_messages = self._build_api_messages(conv.messages)

        # Tool-use loop: max 5 iterations (collect_guest_info + confirm_booking + final)
        for _ in range(5):
            try:
                result = await self.llm.chat_with_tools(
                    system_prompt, api_messages, GUEST_TOOLS
                )
            except Exception as e:
                logger.error(f"LLM tool call failed (guest): {e}")
                response = "Sorry, I'm having trouble right now. Please try again in a moment."
                conv.add_message("assistant", response)
                return OutgoingMessage(text=response)

            if not result.tool_calls:
                conv.add_message("assistant", result.text)
                return OutgoingMessage(text=result.text)

            # Build assistant content for API history
            assistant_content = []
            if result.text:
                assistant_content.append({"type": "text", "text": result.text})
            for tc in result.tool_calls:
                assistant_content.append({
                    "type": "tool_use",
                    "id": tc.id,
                    "name": tc.name,
                    "input": tc.input,
                })
            api_messages.append({"role": "assistant", "content": assistant_content})

            # Execute each tool and collect results
            tool_result_content = []
            booking = None

            for tc in result.tool_calls:
                tool_output = self._execute_guest_tool(tc.name, tc.input, conv, slots)

                # Check if a booking was created by confirm_booking
                if tc.name == "confirm_booking" and conv.state == ConversationState.CONFIRMATION:
                    # Race condition guard: check if slot is already booked
                    if conv.selected_slot and self.db.is_slot_booked(
                        conv.selected_slot.start, conv.selected_slot.end
                    ):
                        conv.state = ConversationState.COLLECTING_INFO
                        conv.selected_slot = None
                        tool_output = (
                            "Sorry, this slot was just booked by someone else. "
                            "Please pick a different slot."
                        )
                    else:
                        booking = await self._create_booking(conv)
                        if booking:
                            confirmation = self._format_confirmation(booking, guest_timezone=conv.guest_timezone)
                            conv.state = ConversationState.BOOKED
                            tool_output = f"Booking confirmed: {confirmation}"
                        else:
                            # Reset state so guest can retry without /cancel
                            conv.state = ConversationState.COLLECTING_INFO
                            conv.selected_slot = None
                            tool_output = (
                                "Failed to create booking. Calendar may be unavailable. "
                                "Please try picking a slot again."
                            )

                tool_result_content.append({
                    "type": "tool_result",
                    "tool_use_id": tc.id,
                    "content": tool_output,
                })
                logger.info(f"Guest tool {tc.name}({tc.input}) -> {tool_output[:80]}")

            api_messages.append({"role": "user", "content": tool_result_content})

            # If booking succeeded, get final text and return
            if booking:
                confirmation = self._format_confirmation(booking, guest_timezone=conv.guest_timezone)
                try:
                    final = await self.llm.chat_with_tools(
                        system_prompt, api_messages, GUEST_TOOLS
                    )
                    final_text = (final.text or "").strip() or confirmation
                except Exception:
                    final_text = confirmation
                conv.add_message("assistant", final_text)
                return OutgoingMessage(
                    text=final_text,
                    metadata={"booking_id": booking.id, "meet_link": booking.meet_link},
                )

            # Continue loop (LLM may call another tool)

        # Fallback
        response = result.text if result.text else "Something went wrong. Please try again."
        conv.add_message("assistant", response)
        return OutgoingMessage(text=response)

    def _execute_guest_tool(
        self, name: str, params: dict, conv: Conversation, slots: list[TimeSlot]
    ) -> str:
        """Execute a guest tool call. Returns result text."""
        if name == "collect_guest_info":
            guest_name = params.get("name", "").strip()
            guest_email = params.get("email", "").strip()
            topic = params.get("topic", "").strip()
            city = params.get("city", "").strip()

            if not guest_name:
                return "Error: name is required."
            if not guest_email or not self._validate_email(guest_email):
                return f"Error: valid email is required. Got: '{guest_email}'"

            conv.guest_name = guest_name
            conv.guest_email = guest_email
            conv.guest_topic = topic
            conv.state = ConversationState.COLLECTING_INFO

            # Resolve city to IANA timezone
            tz_info = ""
            if city:
                from ..timezone_resolver import resolve_timezone
                resolved = resolve_timezone(city)
                if resolved:
                    conv.guest_timezone = resolved
                    tz_info = f", timezone: {resolved}"
                else:
                    tz_info = f" (could not resolve timezone for '{city}' — slots shown in owner timezone)"

            logger.info(f"Guest info collected: {guest_name} <{guest_email}> topic={topic} tz={conv.guest_timezone}")
            return f"Saved: {guest_name}, {guest_email}" + (f", topic: {topic}" if topic else "") + tz_info

        if name == "confirm_booking":
            # Require guest info first
            if not conv.guest_name or not conv.guest_email:
                return "Error: must call collect_guest_info first (need name + email)."

            slot_number = params.get("slot_number", 0)
            slot_idx = slot_number - 1
            attendee_emails = params.get("attendee_emails", [])

            # Validate attendee emails
            if len(attendee_emails) > MAX_ATTENDEE_EMAILS:
                return f"Error: max {MAX_ATTENDEE_EMAILS} additional attendees allowed."
            for email in attendee_emails:
                if not self._validate_email(email):
                    return f"Error: invalid attendee email: '{email}'"

            if not (0 <= slot_idx < len(slots)):
                return f"Error: invalid slot number {slot_number}. Valid range: 1-{len(slots)}."

            conv.selected_slot = slots[slot_idx]
            conv.attendee_emails = attendee_emails
            conv.state = ConversationState.CONFIRMATION
            # Actual booking created in the caller (_handle_guest_message_tools)
            return "PENDING_BOOKING"

        return f"Unknown tool: {name}"

    async def _handle_guest_message_text(
        self, conv: Conversation, slots: list[TimeSlot]
    ) -> OutgoingMessage:
        """Guest mode via text-based [BOOK:N] tags (legacy)."""
        system_prompt = build_system_prompt(
            owner_name=self.config.owner.name,
            slots=slots,
            conversation_state=conv.state,
            guest_name=conv.guest_name,
        )

        try:
            response_text = await self.llm.chat(system_prompt, conv.messages)
        except Exception as e:
            logger.error(f"LLM call failed: {e}")
            response_text = "Sorry, I'm having trouble right now. Please try again in a moment."

        # Parse LLM response for structured actions
        action = self._parse_booking_action(response_text, slots, conv)

        if action == "book" and conv.selected_slot:
            # Race condition guard
            if self.db.is_slot_booked(conv.selected_slot.start, conv.selected_slot.end):
                conv.state = ConversationState.COLLECTING_INFO
                conv.selected_slot = None
                msg = "Sorry, this slot was just booked by someone else. Please pick a different slot."
                conv.add_message("assistant", msg)
                return OutgoingMessage(text=msg)

            booking = await self._create_booking(conv)
            if booking:
                confirmation = self._format_confirmation(booking, guest_timezone=conv.guest_timezone)
                conv.state = ConversationState.BOOKED
                conv.add_message("assistant", confirmation)
                return OutgoingMessage(
                    text=confirmation,
                    metadata={"booking_id": booking.id, "meet_link": booking.meet_link},
                )
            else:
                # Reset state so guest can retry
                conv.state = ConversationState.COLLECTING_INFO
                conv.selected_slot = None

        conv.add_message("assistant", response_text)
        clean_text = re.sub(r"\s*\[BOOK:\S+\]", "", response_text)
        return OutgoingMessage(text=clean_text)

    def _parse_booking_action(
        self, response: str, slots: list[TimeSlot], conv: Conversation
    ) -> str | None:
        """Check if the LLM response contains a booking action."""
        match = re.search(r"\[BOOK:(\d+)\]", response)
        if match:
            idx = int(match.group(1)) - 1
            if 0 <= idx < len(slots):
                conv.selected_slot = slots[idx]
                conv.state = ConversationState.CONFIRMATION
                return "book"

        match = re.search(r"\[BOOK:(\d{4}-\d{2}-\d{2}T\d{2}:\d{2})\]", response)
        if match:
            try:
                start = datetime.fromisoformat(match.group(1))
                from datetime import timedelta
                end = start + timedelta(minutes=self.config.availability.meeting_duration_minutes)
                conv.selected_slot = TimeSlot(start=start, end=end)
                conv.state = ConversationState.CONFIRMATION
                return "book"
            except ValueError:
                pass

        if conv.state == ConversationState.GREETING:
            conv.state = ConversationState.COLLECTING_INFO

        return None

    async def _create_booking(self, conv: Conversation) -> Booking | None:
        """Create a calendar event and booking record."""
        if not conv.selected_slot:
            return None

        guest_name = conv.guest_name or "Guest"
        topic = conv.guest_topic or ""
        summary = f"Meeting with {guest_name}"
        if topic:
            summary += f": {topic}"
        description = f"Scheduled via schedulebot.\nChannel: {conv.channel}"
        if conv.guest_email:
            description += f"\nGuest: {guest_name} <{conv.guest_email}>"
        if topic:
            description += f"\nTopic: {topic}"

        # Collect all attendee emails (guest + extras)
        all_attendee_emails = []
        if conv.guest_email:
            all_attendee_emails.append(conv.guest_email)
        all_attendee_emails.extend(conv.attendee_emails)

        if self.config.dry_run:
            booking = Booking(
                id=secrets.token_urlsafe(16),
                guest_name=guest_name,
                guest_channel=conv.channel,
                guest_sender_id=conv.sender_id,
                guest_email=conv.guest_email,
                topic=topic,
                attendee_emails=conv.attendee_emails,
                slot=conv.selected_slot,
                calendar_event_id="dry-run",
                meet_link="https://meet.google.com/dry-run",
            )
            self.db.save_booking(booking)
            await self._notify_owner(booking)
            return booking

        try:
            event = await self.calendar.create_event(
                summary=summary,
                start=conv.selected_slot.start,
                end=conv.selected_slot.end,
                description=description,
                attendee_emails=all_attendee_emails or None,
                create_meet_link=self.config.calendar.create_meet_link,
            )

            booking = Booking(
                id=secrets.token_urlsafe(16),
                guest_name=guest_name,
                guest_channel=conv.channel,
                guest_sender_id=conv.sender_id,
                guest_email=conv.guest_email,
                topic=topic,
                attendee_emails=conv.attendee_emails,
                slot=conv.selected_slot,
                calendar_event_id=event.get("event_id"),
                meet_link=event.get("meet_link"),
            )
            self.db.save_booking(booking)
            await self._notify_owner(booking)
            return booking

        except Exception as e:
            logger.error(f"Failed to create booking: {e}")
            return None

    async def _notify_owner(self, booking: Booking) -> None:
        """Send booking notification to owner (fire-and-forget)."""
        if self.notifier:
            try:
                await self.notifier.notify_new_booking(booking)
            except Exception as e:
                logger.error(f"Failed to notify owner: {e}")

    def _format_confirmation(self, booking: Booking, guest_timezone: str = "") -> str:
        """Format a booking confirmation message."""
        lines = ["Meeting confirmed!"]

        if guest_timezone:
            try:
                from zoneinfo import ZoneInfo
                tz = ZoneInfo(guest_timezone)
                lines.append(f"  {booking.slot.format_in_tz(tz)}")
            except (KeyError, ValueError):
                lines.append(f"  {booking.slot}")
        else:
            lines.append(f"  {booking.slot}")

        if booking.meet_link:
            lines.append(f"  Join: {booking.meet_link}")
        lines.append(f"  Booking ID: {booking.id}")
        if booking.guest_email:
            lines.append(f"  Calendar invite sent to {booking.guest_email}. Please check for the correct time in your timezone.")
        return "\n".join(lines)
