"""Core scheduling engine — channel-agnostic, with owner/guest dual mode."""

from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime

from ..calendar.base import CalendarProvider
from ..config import Config
from ..core.availability import AvailabilityEngine
from ..database import Database
from ..llm.base import LLMProvider
from ..llm.prompts import build_owner_prompt, build_owner_prompt_tools, build_system_prompt
from ..llm.tools import OWNER_TOOLS
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
    ):
        self.config = config
        self.calendar = calendar
        self.llm = llm
        self.db = db
        self.availability = AvailabilityEngine(config.availability, calendar, db)

    def _is_owner(self, channel: str, sender_id: str) -> bool:
        """Check if the sender is the owner."""
        owner_id = self.config.owner.owner_ids.get(channel, "")
        return owner_id != "" and owner_id == sender_id

    async def handle_message(self, msg: IncomingMessage) -> OutgoingMessage:
        """Process an incoming message. Routes to owner or guest flow."""
        if self._is_owner(msg.channel, msg.sender_id):
            return await self._handle_owner_message(msg)
        return await self._handle_guest_message(msg)

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
        """Guest is trying to book a meeting."""
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

        # Build system prompt with current slots
        system_prompt = build_system_prompt(
            owner_name=self.config.owner.name,
            slots=slots,
            conversation_state=conv.state,
            guest_name=conv.guest_name,
        )

        # Call LLM
        try:
            response_text = await self.llm.chat(system_prompt, conv.messages)
        except Exception as e:
            logger.error(f"LLM call failed: {e}")
            response_text = "Sorry, I'm having trouble right now. Please try again in a moment."

        # Parse LLM response for structured actions
        action = self._parse_booking_action(response_text, slots, conv)

        if action == "book" and conv.selected_slot:
            booking = await self._create_booking(conv)
            if booking:
                confirmation = self._format_confirmation(booking)
                conv.state = ConversationState.BOOKED
                conv.add_message("assistant", confirmation)
                self.db.save_conversation(conv)
                return OutgoingMessage(
                    text=confirmation,
                    metadata={"booking_id": booking.id, "meet_link": booking.meet_link},
                )

        # Save conversation state
        conv.add_message("assistant", response_text)
        self.db.save_conversation(conv)

        # Strip action tags
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

        if self.config.dry_run:
            booking = Booking(
                id=str(uuid.uuid4())[:8],
                guest_name=conv.guest_name or "Guest",
                guest_channel=conv.channel,
                guest_sender_id=conv.sender_id,
                slot=conv.selected_slot,
                calendar_event_id="dry-run",
                meet_link="https://meet.google.com/dry-run",
            )
            self.db.save_booking(booking)
            return booking

        try:
            event = await self.calendar.create_event(
                summary=f"Meeting with {conv.guest_name or 'Guest'}",
                start=conv.selected_slot.start,
                end=conv.selected_slot.end,
                description=f"Scheduled via schedulebot. Channel: {conv.channel}",
                create_meet_link=self.config.calendar.create_meet_link,
            )

            booking = Booking(
                id=str(uuid.uuid4())[:8],
                guest_name=conv.guest_name or "Guest",
                guest_channel=conv.channel,
                guest_sender_id=conv.sender_id,
                slot=conv.selected_slot,
                calendar_event_id=event.get("event_id"),
                meet_link=event.get("meet_link"),
            )
            self.db.save_booking(booking)
            return booking

        except Exception as e:
            logger.error(f"Failed to create booking: {e}")
            return None

    def _format_confirmation(self, booking: Booking) -> str:
        """Format a booking confirmation message."""
        lines = [
            "Meeting confirmed!",
            f"  {booking.slot}",
        ]
        if booking.meet_link:
            lines.append(f"  Join: {booking.meet_link}")
        lines.append(f"  Booking ID: {booking.id}")
        return "\n".join(lines)
